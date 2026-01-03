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

# --- Chromatics 古典风格模板 (v3.6.0 稳健渲染版) ---
# 采用 TRPG 插件的 fit-content 布局，完美适配各种分辨率
CHROMATICS_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
    body {
        margin: 0;
        padding: 40px;
        background-color: transparent; /* 透明背景，由容器接管 */
        /* 优先使用系统衬线字体，无网络依赖 */
        font-family: 'Georgia', 'Times New Roman', 'Songti SC', 'SimSun', serif;
        display: flex;
        justify-content: center;
        align-items: flex-start;
        /* 核心布局：适应内容宽度，防止截图截断 */
        width: fit-content;
        min-width: 100%;
    }

    .frame {
        /* 羊皮纸背景 */
        background-color: #f0e6d2;
        background-image: url("data:image/svg+xml,%3Csvg width='100' height='100' viewBox='0 0 100 100' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100' height='100' filter='url(%23noise)' opacity='0.15'/%3E%3C/svg%3E");
        
        border: 12px double #5c4b37;
        padding: 50px;
        box-shadow: 15px 15px 40px rgba(0,0,0,0.3);
        width: 900px; /* 固定宽度确保排版一致 */
        color: #2c241b;
        position: relative;
        box-sizing: border-box;
        border-radius: 6px;
    }

    .frame::before {
        content: "";
        position: absolute;
        top: 12px; left: 12px; right: 12px; bottom: 12px;
        border: 2px solid #8c7b66;
        pointer-events: none;
    }

    h1 {
        font-size: 60px;
        text-align: center;
        color: #8b0000;
        margin: 0 0 10px 0;
        text-transform: uppercase;
        letter-spacing: 10px;
        text-shadow: 2px 2px 0px rgba(0,0,0,0.1);
        font-weight: bold;
    }

    .subtitle {
        text-align: center;
        font-style: italic;
        color: #5c4b37;
        font-size: 20px;
        margin-bottom: 40px;
        border-bottom: 3px solid #5c4b37;
        padding-bottom: 20px;
        display: block;
        margin-left: auto;
        margin-right: auto;
        width: 80%;
    }

    h2 {
        font-size: 28px;
        color: #2c241b;
        border-bottom: 3px solid #e0d0b8;
        padding-bottom: 10px;
        margin-top: 40px;
        margin-bottom: 20px;
        display: flex;
        align-items: center;
        font-weight: bold;
    }

    h2::before {
        content: "❖";
        margin-right: 15px;
        color: #8b0000;
        font-size: 22px;
    }

    ul {
        list-style: none;
        padding: 0;
    }

    li {
        margin-bottom: 15px;
        font-size: 16px;
        line-height: 1.6;
        border-bottom: 1px dashed rgba(92, 75, 55, 0.2);
        padding-bottom: 12px;
        display: block;
    }

    li strong {
        color: #8b0000;
        font-weight: 700;
        margin-bottom: 6px;
        display: inline-block;
        font-family: 'Courier New', monospace; /* 等宽字体显示指令 */
        font-size: 20px;
    }

    li .desc {
        display: block;
        margin-top: 4px;
        color: #4a3b2a;
        padding-left: 10px;
    }

    li .param {
        background: rgba(139, 0, 0, 0.1);
        padding: 0 6px;
        border-radius: 4px;
        font-family: monospace;
        font-size: 0.9em;
        margin-right: 4px;
    }

    li code {
        background: rgba(0, 0, 0, 0.05);
        padding: 2px 5px;
        border-radius: 3px;
        font-family: 'Courier New', monospace;
        color: #8b0000;
    }

    .info-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 20px;
    }

    .info-box {
        background: rgba(255, 255, 255, 0.5);
        padding: 20px;
        border-radius: 4px;
        border: 1px solid #dcd0c0;
    }

    .info-title {
        font-weight: bold;
        color: #5c4b37;
        margin-bottom: 8px;
        font-size: 16px;
        text-transform: uppercase;
    }

    .info-value {
        font-size: 24px;
        color: #2c241b;
        font-weight: bold;
    }
    
    .info-sub {
        font-size: 14px;
        color: #666;
        margin-top: 5px;
    }

    .preset-container {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
    }

    .preset-tag {
        background: #fff;
        border: 2px solid #8b0000;
        color: #8b0000;
        padding: 6px 14px;
        font-size: 15px;
        border-radius: 4px;
        text-transform: uppercase;
        font-weight: bold;
        box-shadow: 2px 2px 0px rgba(139, 0, 0, 0.15);
    }

    .footer {
        margin-top: 60px;
        text-align: center;
        font-size: 14px;
        color: #8c7b66;
        text-transform: uppercase;
        letter-spacing: 3px;
        border-top: 1px solid #8c7b66;
        padding-top: 20px;
    }
</style>
</head>
<body>
    <div class="frame">
        <h1>Chromatics</h1>
        <div class="subtitle">The Artificer's Guide to Digital Manifestation</div>

        <!-- 1. 指令帮助 -->
        <h2>Ordinances (指令法则)</h2>
        <ul>
            <li>
                <strong>/imago <span class="param">[pro|flash]</span> <span class="param">[预设]</span> <span class="param">[提示词]</span></strong>
                <div class="desc">
                    ❖ <b>核心绘图</b>：支持别名 <code>/draw</code>, <code>/生成</code>, <code>/画图</code>。<br>
                    ❖ <b>模型</b>：默认 <code>flash</code> (小香蕉，极速)；指定 <code>pro</code> 启用高画质模型 (大香蕉，消耗更高)。<br>
                    ❖ <b>预设</b>：输入下方列表中的名称可自动应用风格 (如 <code>手办化</code>)。
                </div>
            </li>
            <li>
                <strong>图生图 / 垫图重绘 (Image-to-Image)</strong>
                <div class="desc">
                    ❖ <b>@用户</b>：指令中包含 <code>@某人</code>，将提取其头像作为底图。<br>
                    ❖ <b>附图/回复</b>：发送指令时附带图片，或回复一张图片，即可进行参考重绘。<br>
                    ❖ <b>混合</b>：例如 <code>/imago pro @用户 像素</code> (将用户头像转为像素风格)。
                </div>
            </li>
            {% if economy.enabled %}
            <li>
                <strong>/签到</strong>
                <div class="desc">每日祈愿，获取灵感点数 (随机 {{ economy.checkin_min }} ~ {{ economy.checkin_max }} pts)。</div>
            </li>
            <li>
                <strong>/积分 & /兑换码 <span class="param">&lt;Code&gt;</span></strong>
                <div class="desc">查询当前持有的灵感点数，或使用密文兑换点数。</div>
            </li>
            {% endif %}
        </ul>

        <!-- 2. 经济与限制 -->
        <h2>Limitations & Specie (限制与经济)</h2>
        <div class="info-grid">
            <div class="info-box">
                <div class="info-title">Flash Model (小香蕉)</div>
                <div class="info-value">
                    {% if economy.enabled %}Cost: {{ economy.cost_flash }}{% else %}Free{% endif %} 
                    <span style="font-size:16px; color:#666">/ Image</span>
                </div>
                <div class="info-sub">Daily: {{ quota.flash }} | Rate: {{ rate.flash_rpm }} rpm</div>
            </div>
            <div class="info-box">
                <div class="info-title">Pro Model (大香蕉)</div>
                <div class="info-value">
                    {% if economy.enabled %}Cost: {{ economy.cost_pro }}{% else %}Free{% endif %}
                    <span style="font-size:16px; color:#666">/ Image</span>
                </div>
                <div class="info-sub">Daily: {{ quota.pro }} | CoolDown: {{ rate.pro_cooldown }}s</div>
            </div>
        </div>

        <!-- 3. 预设列表 -->
        <h2>Manifestations (风格预设)</h2>
        <div class="preset-container">
            {% for name in presets %}
            <span class="preset-tag">{{ name }}</span>
            {% endfor %}
        </div>

        <div class="footer">
            Gemini Drawer Plugin v3.6.0 | Sub Rosa Imago
        </div>
    </div>
</body>
</html>
"""

class ImageWorkflow:
    def __init__(self, proxy_url: str = None):
        self.proxy_url = proxy_url
        self.session = aiohttp.ClientSession()
        
    async def _download_image(self, url: str) -> bytes | None:
        try:
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
    "3.6.0",
)
class GeminiDrawerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.save_image = config.get("save_image", False)
        self.plugin_data_dir = StarTools.get_data_dir("astrbot_plugin_gemini_drawer")
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
        
        self.proxy_enabled = bool(self.conf.get("proxy_enabled", True))
        self.proxy_url = self.conf.get("proxy_url", "")
        self._proxy_env_enabled = False
        self.admin_id = self.conf.get("admin_id", "")
        self.generation_timeout = self.conf.get("generation_timeout_seconds", 90)
        self.incantation_fallback_reply = self.conf.get(
            "incantation_fallback_reply",
            "咒语生图失败，请稍后再试。",
        )
        
        # --- 交互反馈配置 ---
        self.feedback_conf = self.conf.get("feedback", {})
        
        # --- 经济系统配置 ---
        self.eco_conf = self.conf.get("economy", {})
        self.enable_economy = self.eco_conf.get("enabled", True)
        self.points_file = self.plugin_data_dir / "user_points.json"
        self.user_points_data = {}
        self._load_points_data()
        
        self.redeem_history_file = self.plugin_data_dir / "redeem_history.json"
        self.redeem_history = {}
        self._load_redeem_data()

        # --- 配额配置 ---
        quota_conf = self.conf.get("quota", {})
        self.rate_limit_conf = self.conf.get("rate_limits", {})
        
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

        flash_rpm = self.rate_limit_conf.get("flash_rpm", 3)
        pro_cooldown = self.rate_limit_conf.get("pro_cooldown", 90)
        
        self.rpm_limits = {
            "flash": flash_rpm,
            "pro": 60.0 / max(1, pro_cooldown), 
        }
        self.default_rpm = 5
        self.usage_history = defaultdict(lambda: defaultdict(deque))
        self.gen_lock = asyncio.Lock()
        
        # --- 预设加载 (支持 \n 换行) ---
        self.presets = {}
        style_presets = self.conf.get("style_presets", [])
        if style_presets:
            count = 0
            for item in style_presets:
                if isinstance(item, str) and ":" in item:
                    name, prompt = item.split(":", 1)
                    # 核心修改：支持将字符串中的 \n 替换为真实的换行符
                    clean_prompt = prompt.strip().replace("\\n", "\n")
                    self.presets[name.strip()] = clean_prompt
                    count += 1
            logger.info(f"从配置中加载了 {count} 个风格预设。")
        else:
            logger.warning("未检测到任何风格预设，请在配置中添加。")


    def _apply_proxy_env(self):
        if self.proxy_enabled and self.proxy_url:
            os.environ["http_proxy"] = self.proxy_url
            os.environ["https_proxy"] = self.proxy_url
            self._proxy_env_enabled = True
            return
        self._clear_proxy_env()

    def _clear_proxy_env(self):
        os.environ.pop("http_proxy", None)
        os.environ.pop("https_proxy", None)
        self._proxy_env_enabled = False

    async def initialize(self):
        proxy_url = self.proxy_url if self.proxy_enabled else ""
        self.iwf = ImageWorkflow(proxy_url or None)
        self._apply_proxy_env()
            
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

    def _load_redeem_data(self):
        if self.redeem_history_file.exists():
            try:
                self.redeem_history = json.loads(self.redeem_history_file.read_text(encoding='utf-8'))
            except: self.redeem_history = {}
    
    def _save_redeem_data(self):
        try:
            self.redeem_history_file.write_text(json.dumps(self.redeem_history, indent=2, ensure_ascii=False), encoding='utf-8')
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

    def _incantation_fail_result(self, reason: str) -> MessageEventResult | None:
        logger.warning(f"[GeminiDrawer] 咒语生图失败: {reason}")
        if not self.incantation_fallback_reply:
            return None
        return MessageEventResult().message(self.incantation_fallback_reply)

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

    @filter.command("subrosa_ima")
    async def subrosa_imago(self, event: AstrMessageEvent):
        """生成古典风格的帮助菜单 (Chromatics)"""
        logger.info("Rendering Chromatics menu...")
        
        if self.feedback_conf.get("menu_start", True):
            yield event.plain_result("📜 正在绘制 Chromatics 卷轴，请稍候...")
        
        render_data = {
            "economy": self.eco_conf,
            "quota": self.daily_limits,
            "rate": self.rate_limit_conf,
            "presets": list(self.presets.keys())
        }
        
        try:
            # 使用 TRPG 风格的 full_page 截图，配合 CSS 的 fit-content 确保完美渲染
            img_url = await self.html_render(CHROMATICS_TEMPLATE, render_data, options={"full_page": True})
            yield event.image_result(img_url)
        except Exception as e:
            logger.error(f"Chromatics 渲染失败: {e}")
            yield event.plain_result(f"渲染失败: {e}")

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

    @filter.command("兑换码")
    async def redeem(self, event: AstrMessageEvent, code: str = ""):
        """使用兑换码获取积分"""
        if not self.enable_economy:
            yield event.plain_result("本机器人未开启积分系统。")
            return
        
        if not code:
            yield event.plain_result("请输入兑换码，例如：/兑换码 VIP888")
            return
            
        uid = str(event.get_sender_id())
        
        valid_codes = {}
        raw_codes = self.eco_conf.get("redeem_codes", [])
        for item in raw_codes:
            if ":" in item:
                c, amount = item.split(":", 1)
                try:
                    valid_codes[c.strip()] = int(amount)
                except: pass
        
        if code not in valid_codes:
            yield event.plain_result("❌ 无效的兑换码。")
            return
            
        used_users = self.redeem_history.get(code, [])
        if uid in used_users:
            yield event.plain_result("❌ 您已经使用过这个兑换码了。")
            return
            
        amount = valid_codes[code]
        self._add_points(uid, amount)
        
        if code not in self.redeem_history:
            self.redeem_history[code] = []
        self.redeem_history[code].append(uid)
        self._save_redeem_data()
        
        yield event.plain_result(f"🎉 兑换成功！获得 {amount} 积分。\n当前余额: {self._get_points(uid)}")

    @filter.command("imago", aliases={"draw", "生成", "画图"})
    async def on_imago(self, event: AstrMessageEvent):
        incantation = bool(event.get_extra("incantation_command", False))
        if incantation:
            logger.info(
                f"[GeminiDrawer] 🪄 咒语生图触发: {event.get_message_str()}",
            )

        if not self.is_initialized:
            if incantation:
                if res := self._incantation_fail_result("Vertex AI 未初始化"):
                    yield res
            else:
                yield event.plain_result("Vertex AI 未初始化，请检查配置。")
            return

        parse_result = self._parse_imago_input(event)
        target_model_alias = parse_result["model_alias"]
        preset_name = parse_result["preset_name"]
        user_prompt = parse_result["user_prompt"]

        if parse_result["raw_text"].lower() == "list":
            if incantation:
                if res := self._incantation_fail_result("咒语指令请求列表"):
                    yield res
            else:
                async for item in self.subrosa_imago(event):
                    yield item
            return

        preset_prompt = self.presets[preset_name] if preset_name else None
        
        selected_model_name = self.model_map.get(target_model_alias, self.model_map["flash"])
        user_id = event.get_sender_id()
        quota_user_id = f"incantation:{user_id}" if incantation else user_id
        image_bytes_list = await self.iwf.extract_image_from_event(event)
        if not image_bytes_list:
            avatar_refs = await self._get_avatar_references(event)
            if avatar_refs:
                image_bytes_list.extend(avatar_refs)

        if not image_bytes_list and not user_prompt and not preset_prompt:
            if incantation:
                if res := self._incantation_fail_result("缺少有效提示词或图片"):
                    yield res
            else:
                yield event.plain_result("请提供文字描述、预设名或图片。")
            return
            
        if not incantation:
            is_daily_allowed, wait_time_str = self._check_daily_limit(
                quota_user_id,
                target_model_alias,
            )
            if not is_daily_allowed:
                yield event.plain_result(f"你已超出当前模型({target_model_alias})的每日配额，请于 {wait_time_str} 后重试。")
                return
            
            is_allowed, wait_seconds = self._check_quota(
                quota_user_id,
                selected_model_name,
            )
            if not is_allowed:
                yield event.plain_result(f"请求太快了！\n请在 {wait_seconds} 秒后重试")
                return

        cost = 0
        if self.enable_economy:
            if not incantation:
                cost = self.eco_conf.get(f"cost_{target_model_alias}", 0)
                if not self._check_balance(user_id, cost):
                    yield event.plain_result(f"💸 积分不足！\n{target_model_alias} 模型需 {cost} 积分，当前余额 {self._get_points(user_id)}。\n请发送 /签到 或使用 /兑换码")
                    return

        mode = "图生图" if image_bytes_list else "文生图"
        
        if self.feedback_conf.get("draw_start", True):
            if not incantation:
                template = self.feedback_conf.get("draw_start_text", "OK，正在{mode} (模型: {model}，预计消耗 {cost} 积分)...")
                try:
                    msg = template.format(mode=mode, model=target_model_alias, cost=cost)
                    yield event.plain_result(msg)
                except Exception:
                    yield event.plain_result(f"OK，正在{mode}...")
        
        if self.gen_lock.locked():
            if incantation:
                if res := self._incantation_fail_result("生成锁被占用"):
                    yield res
            else:
                yield event.plain_result("正在生成中，请稍等，当前有人在使用。")
            return

        await self.gen_lock.acquire()
        res = None
        try:
            # 超时保护，避免长时间占用队列
            res = await asyncio.wait_for(
                self._generate_image_with_gemini(selected_model_name, image_bytes_list, user_prompt, preset_prompt),
                timeout=float(max(10, self.generation_timeout)),
            )
        except asyncio.TimeoutError:
            if incantation:
                if res := self._incantation_fail_result("生成超时"):
                    yield res
            else:
                yield event.plain_result("生成耗时过长，已终止，请稍后再试。")
            res = None
        finally:
            if self.gen_lock.locked():
                self.gen_lock.release()
        
        if isinstance(res, bytes):
            if self.enable_economy and cost > 0 and not incantation:
                self._deduct_points(user_id, cost)
            self._increment_daily_usage(quota_user_id, target_model_alias)
            
            msg_chain = [Image.fromBytes(res)]
            if self.enable_economy and cost > 0:
                msg_chain.append(Plain(f" | ✅ -{cost}积分"))
                
            yield event.chain_result(msg_chain)
            if incantation:
                logger.info("[GeminiDrawer] ✅ 咒语生图成功")
            
            if self.save_image:
                save_path = self.plugin_data_dir / f"gemini_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
                def write_file():
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    with save_path.open("wb") as f: f.write(res)
                await asyncio.to_thread(write_file)
        else:
            if incantation:
                if res_msg := self._incantation_fail_result(f"生成失败: {res}"):
                    yield res_msg
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
        if preset_prompt and user_prompt:
            final_prompt = f"{base_prompt}\n\nUser Request: {preset_prompt}\nAdditional Detail: {user_prompt}\n"
        elif preset_prompt:
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
            "image_config": types.ImageConfig(image_size="2K")
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

    async def _get_avatar_references(self, event: AstrMessageEvent) -> list[bytes]:
        """根据 @ 提取头像，供图生图参考"""
        refs: list[bytes] = []
        try:
            mentioned_ids: list[str] = []
            for seg in event.message_obj.message:
                if isinstance(seg, Comp.At):
                    seg_id = str(getattr(seg, "qq", "") or "")
                    if seg_id and seg_id != str(event.get_self_id()):
                        mentioned_ids.append(seg_id)
            if not mentioned_ids:
                return refs

            # 限制最多取两张头像，避免无谓消耗
            for seg_id in mentioned_ids[:2]:
                try:
                    avatar_bytes = await self._download_avatar_bytes(seg_id)
                    if avatar_bytes:
                        refs.append(avatar_bytes)
                except Exception as e:
                    logger.warning(f"获取头像失败({seg_id}): {e}")
        except Exception as e:
            logger.warning(f"解析 @ 用户头像时出错: {e}")
        return refs

    async def _download_avatar_bytes(self, user_id: str) -> bytes | None:
        """简化版头像获取：使用 qlogo 直链，失败则返回 None"""
        try:
            url = f"http://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"
            timeout = aiohttp.ClientTimeout(total=8, connect=4)
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        logger.debug(f"头像拉取失败 HTTP{resp.status}")
                        return None
                    data = await resp.read()
                    if not data or len(data) < 1000:
                        return None
                    return data
        except Exception as e:
            logger.debug(f"头像下载异常: {e}")
            return None

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

    @staticmethod
    def _strip_command_prefix(text: str) -> str:
        return re.sub(r"^[\/&!#]?(imago|draw|生成|画图)\s*", "", text, count=1, flags=re.IGNORECASE)

    @staticmethod
    def _strip_at_tokens(text: str, at_names: list[str]) -> str:
        text = re.sub(r"\[At:\d+\]", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\[CQ:at,[^\]]+\]", " ", text, flags=re.IGNORECASE)
        for name in at_names:
            clean_name = name.strip()
            if not clean_name:
                continue
            simplified = re.sub(r"[（(].*?[）)]", "", clean_name).strip()
            escaped_name = re.escape(clean_name)
            text = re.sub(rf"@{escaped_name}[（(][^）)]*[）)]", " ", text)
            text = text.replace(f"@{clean_name}", " ")
            text = text.replace(clean_name, " ")
            if simplified and simplified != clean_name:
                escaped_simplified = re.escape(simplified)
                text = re.sub(rf"@{escaped_simplified}[（(][^）)]*[）)]", " ", text)
                text = text.replace(f"@{simplified}", " ")
                text = text.replace(simplified, " ")
        return text

    @staticmethod
    def _extract_text_without_command_and_mentions(event: AstrMessageEvent) -> str:
        """
        提取用户输入（移除指令前缀与@昵称），避免将@显示名带入提示词
        """
        try:
            at_names: list[str] = []
            for seg in getattr(event.message_obj, "message", []):
                if isinstance(seg, Comp.At):
                    name = getattr(seg, "name", "") or ""
                    if name:
                        at_names.append(name)
            text = event.get_message_str() or getattr(event.message_obj, "message_str", "")
        except Exception:
            text = getattr(event.message_obj, "message_str", "") or ""

        text = GeminiDrawerPlugin._strip_command_prefix(text)
        text = GeminiDrawerPlugin._strip_at_tokens(text, at_names)
        # 移除纯文本中的 @ 提及（包括昵称与可选ID）
        text = re.sub(r"[@＠][^\s（()]+(?:[（(][^）)]*[）)])?", " ", text)
        # 压缩空白
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _parse_imago_input(self, event: AstrMessageEvent) -> dict:
        """
        解析 /imago 指令文本，支持：
        /imago [pro|flash] [@目标] [预设名] [自定义提示词]
        """
        raw_text = self._extract_text_without_command_and_mentions(event)
        normalized = re.sub(r"\s+", " ", raw_text).strip()

        model_alias = "flash"
        if normalized:
            alias_match = re.match(r"^(pro|flash)\b", normalized, flags=re.IGNORECASE)
            if alias_match:
                model_alias = alias_match.group(1).lower()
                normalized = normalized[alias_match.end():].strip()

        preset_name = None
        user_prompt = normalized
        if normalized:
            preset_name = self._match_preset_name(normalized)
            if preset_name:
                user_prompt = self._strip_preset_from_text(normalized, preset_name)

        return {
            "model_alias": model_alias,
            "preset_name": preset_name,
            "user_prompt": user_prompt,
            "raw_text": normalized,
        }

    def _match_preset_name(self, text: str) -> str | None:
        """在文本开头匹配预设名（支持中英文标点分隔）"""
        if not text:
            return None
        # 清理前导空白/标点，防止残留符号阻断匹配
        text = re.sub(r"^[\s，,。.!！？；;:：“”\"'‘’（）()【】《》]+", "", text)
        candidates = []
        for name in self.presets.keys():
            pattern = rf"^{re.escape(name)}(?:\s|[，,。.!！？；;:：]|$)"
            if re.match(pattern, text):
                candidates.append(name)
        if not candidates:
            return None
        return max(candidates, key=len)

    @staticmethod
    def _strip_preset_from_text(text: str, preset_name: str) -> str:
        """从文本开头移除预设名及其分隔符"""
        text = re.sub(r"^[\s，,。.!！？；;:：“”\"'‘’（）()【】《》]+", "", text)
        pattern = rf"^{re.escape(preset_name)}(?:\s|[，,。.!！？；;:：“”\"'‘’（）()【】《》]|$)+"
        stripped = re.sub(pattern, "", text, count=1).strip()
        return stripped

    async def terminate(self):
        if self.iwf: await self.iwf.terminate()
        self.client = None # 释放 Client 引用
        self._clear_proxy_env()
