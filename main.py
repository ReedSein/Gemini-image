import asyncio
import base64
import io
import os
import re
import time
import json
import random
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
from PIL import Image as PILImage

from google import genai
from google.genai import types

import astrbot.core.message.components as Comp
from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Image, Plain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.api.event import AstrMessageEvent, MessageEventResult

class ImageWorkflow:
    def __init__(self, proxy_url: str = None):
        self.proxy_url = proxy_url
        self.session = aiohttp.ClientSession()
        
    async def _download_image(self, url: str) -> bytes | None:
        try:
            # 如果 proxy_url 为空，则不使用代理
            proxy = self.proxy_url if self.proxy_url else None
            async with self.session.get(url, proxy=proxy) as resp:
                resp.raise_for_status()
                return await resp.read()
        except Exception as e:
            logger.error(f"图片下载失败: {e}")
            return None

    def _extract_first_frame_sync(self, raw: bytes) -> bytes:
        img_io = io.BytesIO(raw)
        try:
            img = PILImage.open(img_io)
            out_io = io.BytesIO()
            img.convert("RGBA").save(out_io, format="PNG")
            return out_io.getvalue()
        except PILImage.UnidentifiedImageError:
            logger.error("无法识别的图片格式")
            return raw

    async def _load_bytes(self, src: str) -> bytes | None:
        raw: bytes | None = None
        loop = asyncio.get_running_loop()

        if Path(src).is_file():
            raw = await loop.run_in_executor(None, Path(src).read_bytes)
        elif src.startswith("http"):
            raw = await self._download_image(src)
        elif src.startswith("base64://"):
            raw = await loop.run_in_executor(None, base64.b64decode, src[9:])

        if not raw: return None
        return await loop.run_in_executor(None, self._extract_first_frame_sync, raw)

    async def extract_image_from_event(self, event: AstrMessageEvent) -> list[bytes]:
        images: list[bytes] = []
        all_segments_to_check = []
        for s in event.message_obj.message:
            if isinstance(s, Comp.Reply) and s.chain:
                all_segments_to_check.extend(s.chain)
        
        all_segments_to_check.extend(event.message_obj.message)
        for seg in all_segments_to_check:
            if isinstance(seg, Comp.Image):
                img_data = None
                if seg.url: img_data = await self._load_bytes(seg.url)
                elif seg.file: img_data = await self._load_bytes(seg.file)
                if img_data: images.append(img_data)
        return images

    async def terminate(self):
        if self.session and not self.session.closed:
            await self.session.close()


@register(
    "astrbot_plugin_gemini_drawer",
    "Rin & Architect",
    "Gemini 专业生图 (含经济系统)",
    "2.8.0",
)
class GeminiDrawerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.save_image = config.get("save_image", False)
        self.plugin_data_dir = StarTools.get_data_dir("astrbot_plugin_gemini_drawer")
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
        
        # === 核心修改：移除默认代理 ===
        # 如果配置中未填写，则默认为空字符串，不启用代理
        self.proxy_url = self.conf.get("proxy_url", "")
        self.admin_id = self.conf.get("admin_id", "")
        
        # --- 经济系统配置 ---
        self.eco_conf = self.conf.get("economy", {})
        self.enable_economy = self.eco_conf.get("enabled", True)
        self.points_file = self.plugin_data_dir / "user_points.json"
        self.user_points_data = {}
        self._load_points_data()

        # --- 配额配置 ---
        quota_conf = self.conf.get("quota", {})
        rate_limit_conf = self.conf.get("rate_limits", {})
        
        self.daily_limits = {
            "flash": quota_conf.get("flash", 50),
            "pro": quota_conf.get("pro", 10)
        }
        self.daily_quota_file = self.plugin_data_dir / "daily_quota.json"
        
        # --- API 配置 ---
        self.api_key = self.conf.get("api_key") or os.environ.get("GOOGLE_API_KEY")
        self.vertex_project = self.conf.get("vertex_project")
        self.vertex_location = self.conf.get("vertex_location")
        self.auth_json_path = self.conf.get("auth_json_path") 
        self.vertex_auth_json = self.conf.get("vertex_auth_json")
        self.model_name = self.conf.get("model_name", "gemini-2.5-flash-image")
        self.is_initialized = False

        self.max_retries = 10
        self.retry_delay = 2
        
        self.model_map = {
            "flash": "gemini-2.5-flash-image",
            "pro": "gemini-3-pro-image-preview"
        }

        flash_rpm = rate_limit_conf.get("flash_rpm", 3)
        pro_cooldown = rate_limit_conf.get("pro_cooldown", 90)
        
        self.rpm_limits = {
            "flash": flash_rpm,
            "pro": 60.0 / max(1, pro_cooldown), 
        }
        self.default_rpm = 5
        self.usage_history = defaultdict(lambda: defaultdict(deque))
        
        self.presets = {}
        style_presets = self.conf.get("style_presets", [])
        if style_presets:
            count = 0
            for item in style_presets:
                if item.get("enabled", True):
                    self.presets[item["name"]] = item["prompt"]
                    count += 1
            logger.info(f"从配置中加载了 {count} 个风格预设。")
        else:
            logger.warning("未检测到任何风格预设，请在配置中添加。")


    async def initialize(self):
        # 传入可能为空的代理地址
        self.iwf = ImageWorkflow(self.proxy_url)
        
        # === 核心修改：只有在配置了代理时才设置环境变量 ===
        if self.proxy_url:
            logger.info(f"检测到代理配置: {self.proxy_url}，正在应用...")
            os.environ["http_proxy"] = self.proxy_url
            os.environ["https_proxy"] = self.proxy_url
        else:
            # 如果之前设置过（例如热重载），尝试清除，避免残留
            os.environ.pop("http_proxy", None)
            os.environ.pop("https_proxy", None)
            
        final_auth_path = None
        if self.auth_json_path and Path(self.auth_json_path).is_file():
            final_auth_path = self.auth_json_path
        elif self.vertex_auth_json:
            try:
                json.loads(self.vertex_auth_json) 
                temp_auth_file = self.plugin_data_dir / "vertex_auth_temp.json"
                temp_auth_file.write_text(self.vertex_auth_json, encoding='utf-8')
                final_auth_path = str(temp_auth_file)
            except Exception as e:
                logger.error(f"解析 vertex_auth_json 失败: {e}")
        
        if final_auth_path:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = final_auth_path
        
        try:
            if self.vertex_project and self.vertex_location:
                logger.info(f"初始化 Vertex AI Client (Project: {self.vertex_project})...")
                self.client = genai.Client(vertexai=True, project=self.vertex_project, location=self.vertex_location)
            elif self.api_key:
                logger.info("初始化 Google AI Studio Client (API Key)...")
                self.client = genai.Client(api_key=self.api_key)
            else:
                logger.error("初始化失败：缺少认证信息。")
                return
            self.is_initialized = True
            logger.info(f"Client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Client: {e}")

    # ==========================
    #      经济系统方法
    # ==========================
    def _load_points_data(self):
        if self.points_file.exists():
            try:
                self.user_points_data = json.loads(self.points_file.read_text(encoding='utf-8'))
            except: self.user_points_data = {}

    def _save_points_data(self):
        try:
            self.points_file.write_text(json.dumps(self.user_points_data, indent=2, ensure_ascii=False), encoding='utf-8')
        except: pass

    def _get_points(self, user_id: str) -> int:
        return self.user_points_data.get(str(user_id), {}).get("points", 0)

    def _add_points(self, user_id: str, amount: int):
        uid = str(user_id)
        if uid not in self.user_points_data: self.user_points_data[uid] = {}
        self.user_points_data[uid]["points"] = self.user_points_data[uid].get("points", 0) + amount
        self._save_points_data()

    def _deduct_points(self, user_id: str, amount: int) -> bool:
        uid = str(user_id)
        if self.admin_id and uid == self.admin_id: return True
        if not self.enable_economy: return True
        
        curr = self._get_points(uid)
        if curr >= amount:
            self.user_points_data[uid]["points"] = curr - amount
            self._save_points_data()
            return True
        return False
    
    def _check_balance(self, user_id: str, amount: int) -> bool:
        if self.admin_id and str(user_id) == self.admin_id: return True
        if not self.enable_economy: return True
        return self._get_points(user_id) >= amount

    # ==========================
    #      配额管理方法
    # ==========================
    def _check_quota(self, user_id: str, model_name: str) -> tuple[bool, float]:
        if self.admin_id and user_id == self.admin_id: return True, 0.0
        current_time = time.time()
        
        rpm = self.default_rpm
        for key, val in self.rpm_limits.items():
            if key in model_name.lower():
                rpm = val
                break
        
        if rpm < 1.0:
            window_size = 60.0 / rpm
            limit = 1
        else:
            window_size = 60.0
            limit = int(rpm)
        
        user_history = self.usage_history[user_id][model_name]
        while user_history and current_time - user_history[0] > window_size:
            user_history.popleft()
            
        if len(user_history) >= limit:
            wait_seconds = window_size - (current_time - user_history[0])
            return False, max(1.0, round(wait_seconds, 1))
            
        user_history.append(current_time)
        return True, 0.0
    
    def _check_daily_limit(self, user_id: str, model_alias: str) -> tuple[bool, str]:
        if self.admin_id and user_id == self.admin_id: return True, ""

        data = {}
        if self.daily_quota_file.exists():
            try: data = json.loads(self.daily_quota_file.read_text(encoding='utf-8'))
            except: pass

        today_str = datetime.now().strftime("%Y-%m-%d")
        user_data = data.get(user_id, {})

        if user_data.get("date") != today_str:
            user_data = {"date": today_str, "usage": {}}

        current_usage = user_data.get("usage", {}).get(model_alias, 0)
        limit = self.daily_limits.get(model_alias, 20)

        if current_usage >= limit:
            now = datetime.now()
            tomorrow = datetime(now.year, now.month, now.day) + timedelta(days=1)
            remaining_seconds = (tomorrow - now).seconds
            hours, remainder = divmod(remaining_seconds, 3600)
            return False, f"{hours}小时{int(remainder/60)}分"

        return True, ""

    def _increment_daily_usage(self, user_id: str, model_alias: str):
        if self.admin_id and user_id == self.admin_id: return
        
        data = {}
        if self.daily_quota_file.exists():
            try: data = json.loads(self.daily_quota_file.read_text(encoding='utf-8'))
            except: pass
            
        today_str = datetime.now().strftime("%Y-%m-%d")
        user_data = data.get(user_id, {"date": today_str, "usage": {}})
        if user_data.get("date") != today_str:
             user_data = {"date": today_str, "usage": {}}
             
        current = user_data.get("usage", {}).get(model_alias, 0)
        if "usage" not in user_data: user_data["usage"] = {}
        user_data["usage"][model_alias] = current + 1
        data[user_id] = user_data
        
        try: self.daily_quota_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except: pass

    # ==========================
    #        指令处理
    # ==========================

    @filter.command("签到")
    async def checkin(self, event: AstrMessageEvent):
        """每日签到领取积分"""
        if not self.enable_economy:
            yield event.plain_result("本机器人未开启积分系统。")
            return
            
        uid = str(event.get_sender_id())
        today = time.strftime("%Y-%m-%d")
        
        if uid not in self.user_points_data: self.user_points_data[uid] = {}
        last = self.user_points_data[uid].get("last_checkin", "")
        
        if last == today:
            yield event.plain_result(f"📅 今天已签到！\n当前积分: {self._get_points(uid)}")
            return

        min_r = self.eco_conf.get("checkin_min", 20)
        max_r = self.eco_conf.get("checkin_max", 100)
        reward = random.randint(min_r, max_r)
        
        self._add_points(uid, reward)
        self.user_points_data[uid]["last_checkin"] = today
        self._save_points_data()
        
        yield event.plain_result(f"🎉 签到成功 +{reward} 积分！\n当前余额: {self._get_points(uid)}")

    @filter.command("积分")
    async def query_points(self, event: AstrMessageEvent):
        if not self.enable_economy: return
        uid = event.get_sender_id()
        yield event.plain_result(f"💰 当前积分: {self._get_points(uid)}")

    @filter.command("met", aliases={"draw", "生成", "画图"})
    async def on_met(self, event: AstrMessageEvent):
        if not self.is_initialized:
            yield event.plain_result("Vertex AI 未初始化，请检查配置。")
            return
        
        raw_content = re.sub(r"^(met|draw|生成|画图)\s*", "", event.message_obj.message_str, count=1, flags=re.IGNORECASE).strip()
        
        if raw_content == "list":
            if not self.presets:
                yield event.plain_result("当前没有可用的预设。")
            else:
                msg = "可用预设列表：\n" + "\n".join([f"- {name}" for name in self.presets.keys()])
                msg += "\n\n使用方法: met [flash/pro] <预设名> <描述>"
                yield event.plain_result(msg)
            return

        parts = raw_content.split()
        target_model_alias = "flash"
        preset_prompt = None
        current_idx = 0
        
        if parts and parts[0].lower() in self.model_map:
            target_model_alias = parts[0].lower()
            current_idx += 1
        
        if len(parts) > current_idx:
            possible_preset = parts[current_idx]
            if possible_preset in self.presets:
                preset_prompt = self.presets[possible_preset]
                current_idx += 1
        
        user_prompt = " ".join(parts[current_idx:])
        
        selected_model_name = self.model_map.get(target_model_alias, self.model_map["flash"])
        user_id = event.get_sender_id()
        image_bytes_list = await self.iwf.extract_image_from_event(event)

        if not image_bytes_list and not user_prompt and not preset_prompt:
            yield event.plain_result("请提供文字描述、预设名或图片。")
            return
            
        is_daily_allowed, wait_time_str = self._check_daily_limit(user_id, target_model_alias)
        if not is_daily_allowed:
            yield event.plain_result(f"你已超出当前模型({target_model_alias})的每日配额，请于 {wait_time_str} 后重试。")
            return
        
        is_allowed, wait_seconds = self._check_quota(user_id, selected_model_name)
        if not is_allowed:
            yield event.plain_result(f"请求太快了！\n请在 {wait_seconds} 秒后重试")
            return

        cost = 0
        if self.enable_economy:
            cost = self.eco_conf.get(f"cost_{target_model_alias}", 0)
            if not self._check_balance(user_id, cost):
                yield event.plain_result(f"💸 积分不足！\n{target_model_alias} 模型需 {cost} 积分，当前余额 {self._get_points(user_id)}。\n请发送 /签到")
                return

        mode = "图生图" if image_bytes_list else "文生图"
        yield event.plain_result(f"OK，正在{mode} (模型: {target_model_alias}，预计消耗 {cost} 积分)...")
        
        res = await self._generate_image_with_gemini(selected_model_name, image_bytes_list, user_prompt, preset_prompt)
        
        if isinstance(res, bytes):
            if self.enable_economy and cost > 0:
                self._deduct_points(user_id, cost)
            self._increment_daily_usage(user_id, target_model_alias)
            
            msg_chain = [Image.fromBytes(res)]
            if self.enable_economy and cost > 0:
                msg_chain.append(Plain(f" | ✅ -{cost}积分"))
                
            yield event.chain_result(msg_chain)
            
            if self.save_image:
                save_path = self.plugin_data_dir / f"gemini_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
                def write_file():
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    with save_path.open("wb") as f: f.write(res)
                await asyncio.to_thread(write_file)
        else:
            yield event.plain_result(f"生成失败: {res}")

    async def _generate_image_with_gemini(
        self, model_name: str, image_bytes_list: list[bytes], user_prompt: str, preset_prompt: str | None = None
    ) -> bytes | str:
        prompts_config = self.conf.get("base_prompts", {})
        base_prompt = prompts_config.get(
            "image_to_image_base_prompt" if image_bytes_list else "text_to_image_base_prompt",
            "Create a image based on the following description."
        )
        
        system_instruction = self.conf.get("system_instruction", "")
        
        final_prompt = ""
        if preset_prompt:
            final_prompt = f"{base_prompt}\n\nUser Request: {preset_prompt}\n"
        else:
            final_prompt = f"{base_prompt}\n\nUser Request: {user_prompt}\n"
        
        logger.info(f"GenAI Prompt ({model_name}): {final_prompt[:100]}...")

        parts = []
        for img_bytes in image_bytes_list:
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
        parts.append(types.Part.from_text(text=final_prompt))

        config_kwargs = {
            "temperature": 1,
            "response_modalities": ["IMAGE"],
            "safety_settings": [
                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="OFF"),
                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="OFF")
            ],
            "image_config": types.ImageConfig(image_size="2K", output_mime_type="image/png")
        }
        
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        
        if "gemini-3" in model_name:
            config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        
        generate_content_config = types.GenerateContentConfig(**config_kwargs)

        async def generation_operation():
            try:
                response = await self.client.aio.models.generate_content(
                    model=model_name,
                    contents=[types.Content(role="user", parts=parts)],
                    config=generate_content_config,
                )
                if not response or not response.candidates: return "空回复，可能被审查"
                
                if response.candidates:
                    for part in response.candidates[0].content.parts:
                        if part.inline_data: return part.inline_data.data
                return response.text or "未找到图片内容"
            except Exception as api_e:
                raise api_e

        result = await self._with_retry(generation_operation)
        return result if isinstance(result, (bytes, str)) else "所有重试均失败。"

    async def _with_retry(self, operation, *args, **kwargs):
        for attempt in range(self.max_retries + 1):
            try:
                return await operation(*args, **kwargs)
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "503" in error_str:
                    if attempt >= self.max_retries: return str(e)
                    await asyncio.sleep(self.retry_delay)
                else:
                    return str(e)
        return "未知错误。"

    async def terminate(self):
        if self.iwf: await self.iwf.terminate()
