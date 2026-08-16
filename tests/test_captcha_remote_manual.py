import os
import unittest
from pathlib import Path
from unittest.mock import patch

from XianyuAutoAsync import XianyuLive
from api_captcha_remote import should_process_verification_result
from utils.captcha_remote_control import CaptchaRemoteController


class _FakeCaptchaPage:
    viewport_size = {"width": 1920, "height": 1080}

    @property
    def frames(self):
        return [self]

    @property
    def main_frame(self):
        return self

    async def query_selector(self, _selector):
        return None

    async def evaluate(self, _script):
        return "Please drag the slider to verify"


class CaptchaRemoteManualTests(unittest.TestCase):
    def setUp(self):
        self.previous_public_base_url = os.environ.get("PUBLIC_BASE_URL")

    def tearDown(self):
        if self.previous_public_base_url is None:
            os.environ.pop("PUBLIC_BASE_URL", None)
        else:
            os.environ["PUBLIC_BASE_URL"] = self.previous_public_base_url

    def test_captcha_control_url_uses_public_base_url(self):
        os.environ["PUBLIC_BASE_URL"] = "https://xianyu.muxing.cc.cd/"

        control_url = XianyuLive._build_captcha_control_url("2217118657958-token")

        self.assertEqual(
            control_url,
            "https://xianyu.muxing.cc.cd/api/captcha/control/2217118657958-token",
        )

    def test_docker_captcha_control_url_requires_an_external_base_url(self):
        with patch.dict(
            os.environ,
            {
                "PUBLIC_BASE_URL": "",
                "SERVER_HOST": "0.0.0.0",
                "PUBLIC_IP": "",
                "WEB_HOST": "",
                "DOCKER_ENV": "true",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "PUBLIC_BASE_URL"):
                XianyuLive._build_captcha_control_url("account-1-token")

    def test_captcha_control_page_uses_secure_websocket_on_https(self):
        html = Path("captcha_control.html").read_text(encoding="utf-8")

        self.assertIn("window.location.protocol === 'https:' ? 'wss' : 'ws'", html)
        self.assertIn(
            "`${wsProtocol}://${window.location.host}/api/captcha/ws/${sessionId}`",
            html,
        )

    def test_captcha_control_page_supports_pointer_drag_and_reconnect(self):
        html = Path("captcha_control.html").read_text(encoding="utf-8")

        self.assertIn("touch-action: none", html)
        self.assertIn("pointerdown", html)
        self.assertIn("handlePointerDown", html)
        self.assertIn("scheduleReconnect", html)
        self.assertIn("reconnectAttempts", html)
        self.assertIn("width: 100%", html)

    def test_only_pointer_up_triggers_screenshot_and_completion_check(self):
        self.assertFalse(should_process_verification_result("down"))
        self.assertFalse(should_process_verification_result("move"))
        self.assertTrue(should_process_verification_result("up"))


class CaptchaRemoteControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_crops_alibaba_slider_when_selector_is_missing(self):
        controller = CaptchaRemoteController()

        captcha_info = await controller._get_captcha_info(_FakeCaptchaPage())

        self.assertEqual(captcha_info["selector"], "fallback-alibaba-slider")
        self.assertEqual(captcha_info["x"], 750)
        self.assertEqual(captcha_info["y"], 640)
        self.assertEqual(captcha_info["width"], 420)
        self.assertEqual(captcha_info["height"], 180)
        self.assertFalse(captcha_info["in_iframe"])


if __name__ == "__main__":
    unittest.main()
