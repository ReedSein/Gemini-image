import unittest

import astrbot.api.message_components as Comp


class DummyMessage:
    def __init__(self, message_str, segments):
        self.message_str = message_str
        self.message = segments


class DummyEvent:
    def __init__(self, message_str, segments):
        self.message_obj = DummyMessage(message_str, segments)


from data.plugins.gemini_image.main import GeminiDrawerPlugin


class PromptCleanTests(unittest.TestCase):
    def test_remove_at_nickname_fullwidth_parentheses(self):
        msg = "/imago @AI Iva（内部测试版） 美女在公园"
        segments = [Comp.At(qq="123", name="AI Iva（内部测试版）"), Comp.Plain(" 美女在公园")]
        event = DummyEvent(msg, segments)
        out = GeminiDrawerPlugin._extract_user_text(event)
        self.assertEqual(out, "美女在公园")

    def test_pro_with_preset_and_at(self):
        msg = "/imago pro @u1 手办化 漂亮"  # pro + preset + custom
        segments = [Comp.Plain("/imago pro "), Comp.At(qq="999", name="u1"), Comp.Plain(" 手办化 漂亮")]
        event = DummyEvent(msg, segments)
        out = GeminiDrawerPlugin._extract_user_text(event)
        self.assertEqual(out, "pro 手办化 漂亮")

    def test_pro_alias_kept_when_at_present(self):
        # Simulate parsing path: model alias should remain pro even with @ mention in between
        msg = "/imago pro @iva 元旦拼图女"
        segments = [Comp.Plain("/imago pro "), Comp.At(qq="555", name="iva"), Comp.Plain(" 元旦拼图女")]
        event = DummyEvent(msg, segments)
        out = GeminiDrawerPlugin._extract_user_text(event)
        self.assertTrue(out.startswith("pro "))


if __name__ == "__main__":
    unittest.main()
