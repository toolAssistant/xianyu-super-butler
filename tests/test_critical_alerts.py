import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from db_manager import DBManager
from XianyuAutoAsync import XianyuLive
from utils.critical_alerts import CriticalAlertService, sanitize_alert_text
from utils.xianyu_slider_stealth import XianyuSliderStealth


class _FakeAlertDB:
    def __init__(self, channels=None, last_sent_at=None):
        self.channels = channels or []
        self.last_sent_at = last_sent_at
        self.marked = []

    def get_critical_alert_state(self, cookie_id, alert_key):
        if self.last_sent_at is None:
            return None
        return {
            "cookie_id": cookie_id,
            "alert_key": alert_key,
            "last_sent_at": self.last_sent_at,
        }

    def get_enabled_pushplus_channels_for_cookie(self, _cookie_id):
        return self.channels

    def mark_critical_alert_sent(self, cookie_id, alert_key, sent_at):
        self.marked.append((cookie_id, alert_key, sent_at))
        return True


class CriticalAlertServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_through_owned_pushplus_channel(self):
        db = _FakeAlertDB(
            channels=[{"id": 1, "config": '{"token":"secret"}'}]
        )
        sender = AsyncMock(return_value=True)
        service = CriticalAlertService(
            db,
            sender=sender,
            now=lambda: 1000,
            sleep=AsyncMock(),
        )

        result = await service.send(
            "account-1",
            "captcha_manual_required",
            "需要人工验证",
            "请打开控制页",
        )

        self.assertEqual(result, "sent")
        sender.assert_awaited_once_with(
            {"token": "secret"},
            "需要人工验证",
            "请打开控制页",
        )
        self.assertEqual(
            db.marked,
            [("account-1", "captcha_manual_required", 1000)],
        )

    async def test_persistent_cooldown_suppresses_duplicate(self):
        db = _FakeAlertDB(
            channels=[{"id": 1, "config": '{"token":"secret"}'}],
            last_sent_at=900,
        )
        sender = AsyncMock(return_value=True)
        service = CriticalAlertService(
            db,
            sender=sender,
            now=lambda: 1000,
            sleep=AsyncMock(),
        )

        result = await service.send(
            "account-1",
            "captcha_manual_required",
            "需要人工验证",
            "请打开控制页",
            cooldown_seconds=1800,
        )

        self.assertEqual(result, "suppressed")
        sender.assert_not_awaited()
        self.assertEqual(db.marked, [])

    async def test_retries_temporary_failure_then_marks_success(self):
        db = _FakeAlertDB(
            channels=[{"id": 1, "config": '{"token":"secret"}'}]
        )
        sender = AsyncMock(side_effect=[False, True])
        sleep = AsyncMock()
        service = CriticalAlertService(
            db,
            sender=sender,
            now=lambda: 1000,
            sleep=sleep,
            max_attempts=3,
        )

        result = await service.send(
            "account-1",
            "delivery:order-1",
            "自动发货失败",
            "库存不足",
        )

        self.assertEqual(result, "sent")
        self.assertEqual(sender.await_count, 2)
        sleep.assert_awaited_once_with(1)
        self.assertEqual(db.marked, [("account-1", "delivery:order-1", 1000)])

    async def test_failed_channels_do_not_start_cooldown(self):
        db = _FakeAlertDB(
            channels=[{"id": 1, "config": '{"token":"secret"}'}]
        )
        sender = AsyncMock(return_value=False)
        service = CriticalAlertService(
            db,
            sender=sender,
            now=lambda: 1000,
            sleep=AsyncMock(),
            max_attempts=2,
        )

        result = await service.send(
            "account-1",
            "delivery:order-1",
            "自动发货失败",
            "库存不足",
        )

        self.assertEqual(result, "failed")
        self.assertEqual(sender.await_count, 2)
        self.assertEqual(db.marked, [])

    async def test_reports_missing_channel_without_starting_cooldown(self):
        db = _FakeAlertDB()
        sender = AsyncMock(return_value=True)
        service = CriticalAlertService(db, sender=sender, now=lambda: 1000)

        result = await service.send(
            "account-1",
            "connection_recovery_failed",
            "连接恢复失败",
            "请人工检查",
        )

        self.assertEqual(result, "no_channel")
        sender.assert_not_awaited()
        self.assertEqual(db.marked, [])


class CriticalAlertSanitizerTests(unittest.TestCase):
    def test_redacts_sensitive_assignments_but_keeps_control_url(self):
        text = sanitize_alert_text(
            "token=abc cookie2=def password:ghi x5secdata=jkl "
            "_m_h5_tk=mno https://xianyu.example/control"
        )

        for secret in ("abc", "def", "ghi", "jkl", "mno"):
            self.assertNotIn(secret, text)
        self.assertIn("https://xianyu.example/control", text)
        self.assertIn("token=[REDACTED]", text)

    def test_headless_password_login_waits_only_with_external_verification_url(self):
        self.assertFalse(
            XianyuSliderStealth._should_wait_for_human_verification(False, None)
        )
        self.assertTrue(
            XianyuSliderStealth._should_wait_for_human_verification(
                False,
                "https://passport.goofish.com/iv/mini/identity_verify.htm?htoken=fresh",
            )
        )
        self.assertTrue(
            XianyuSliderStealth._should_wait_for_human_verification(True, None)
        )

    def test_password_login_accepts_explicit_browser_executable(self):
        with tempfile.NamedTemporaryFile() as browser_binary, patch.dict(
            os.environ,
            {"PLAYWRIGHT_CHROMIUM_EXECUTABLE": browser_binary.name},
        ):
            self.assertEqual(
                XianyuSliderStealth._get_browser_executable_path(),
                browser_binary.name,
            )


class CriticalAlertDBTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_sql_log = os.environ.get("SQL_LOG_ENABLED")
        os.environ["SQL_LOG_ENABLED"] = "false"
        self.manager = DBManager(str(Path(self.temp_dir.name) / "alerts.db"))
        self.manager.conn.execute(
            "INSERT INTO users (id, username, email, password_hash) "
            "VALUES (1, 'admin', 'admin@example.com', 'hash')"
        )
        self.manager.conn.execute(
            "INSERT INTO users (id, username, email, password_hash) "
            "VALUES (2, 'other', 'other@example.com', 'hash')"
        )
        self.manager.conn.execute(
            "INSERT INTO cookies (id, value, user_id) VALUES ('account-1', 'unb=1', 1)"
        )
        self.manager.conn.execute(
            "INSERT INTO notification_channels "
            "(id, name, user_id, type, config, enabled) "
            "VALUES (1, 'critical', 1, 'pushplus', '{\"token\":\"secret\"}', 1)"
        )
        self.manager.conn.execute(
            "INSERT INTO notification_channels "
            "(id, name, user_id, type, config, enabled) "
            "VALUES (2, 'disabled', 1, 'pushplus', '{\"token\":\"disabled\"}', 0)"
        )
        self.manager.conn.execute(
            "INSERT INTO notification_channels "
            "(id, name, user_id, type, config, enabled) "
            "VALUES (3, 'other-user', 2, 'pushplus', '{\"token\":\"other\"}', 1)"
        )
        self.manager.conn.commit()

    def tearDown(self):
        self.manager.conn.close()
        self.temp_dir.cleanup()
        if self.previous_sql_log is None:
            os.environ.pop("SQL_LOG_ENABLED", None)
        else:
            os.environ["SQL_LOG_ENABLED"] = self.previous_sql_log

    def test_resolves_only_enabled_pushplus_channels_owned_by_account_user(self):
        channels = self.manager.get_enabled_pushplus_channels_for_cookie("account-1")

        self.assertEqual([channel["id"] for channel in channels], [1])
        self.assertEqual(channels[0]["name"], "critical")

    def test_persists_and_updates_alert_cooldown_state(self):
        self.assertIsNone(
            self.manager.get_critical_alert_state("account-1", "captcha")
        )

        self.assertTrue(
            self.manager.mark_critical_alert_sent("account-1", "captcha", 1000)
        )
        self.assertEqual(
            self.manager.get_critical_alert_state("account-1", "captcha")[
                "last_sent_at"
            ],
            1000,
        )

        self.assertTrue(
            self.manager.mark_critical_alert_sent("account-1", "captcha", 2000)
        )
        self.assertEqual(
            self.manager.get_critical_alert_state("account-1", "captcha")[
                "last_sent_at"
            ],
            2000,
        )


class CriticalAlertBusinessIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_failure_sends_critical_alert_but_success_does_not(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.send_critical_alert = AsyncMock(return_value="sent")

        with patch(
            "db_manager.db_manager.get_account_notifications",
            return_value=[],
        ):
            await live.send_delivery_failure_notification(
                "buyer",
                "buyer-1",
                "item-1",
                "发货成功",
                "chat-1",
                "order-1",
            )
            live.send_critical_alert.assert_not_awaited()

            await live.send_delivery_failure_notification(
                "buyer",
                "buyer-1",
                "item-1",
                "卡密发送失败",
                "chat-1",
                "order-1",
            )

        live.send_critical_alert.assert_awaited_once()
        args = live.send_critical_alert.await_args.args
        self.assertEqual(args[0], "delivery_failure:order-1")
        self.assertIn("卡密发送失败", args[2])
        self.assertIn("item-1", args[2])

    async def test_captcha_attempts_password_recovery_before_manual_flow(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookies_str = "unb=1"
        live.last_token_refresh_status = None
        call_order = []

        async def password_recovery(_reason):
            call_order.append("password")
            return False

        async def manual_recovery(_url):
            call_order.append("manual")
            return None

        live._try_password_login_refresh = AsyncMock(side_effect=password_recovery)
        live._handle_manual_captcha_verification = AsyncMock(
            side_effect=manual_recovery
        )

        with patch.dict(os.environ, {"AUTO_CAPTCHA_SOLVE_ENABLED": "false"}), patch(
            "XianyuAutoAsync.log_captcha_event"
        ):
            result = await live._handle_captcha_verification(
                {"data": {"url": "https://h5api.m.goofish.com/punish"}}
            )

        self.assertIsNone(result)
        self.assertEqual(call_order, ["password", "manual"])
        live._try_password_login_refresh.assert_awaited_once_with("风控验证")

    async def test_successful_password_recovery_skips_manual_captcha(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.cookies_str = "unb=1; token=fresh"
        live.last_token_refresh_status = None
        live._try_password_login_refresh = AsyncMock(return_value=True)
        live._handle_manual_captcha_verification = AsyncMock(return_value=None)

        with patch.dict(os.environ, {"AUTO_CAPTCHA_SOLVE_ENABLED": "false"}), patch(
            "XianyuAutoAsync.log_captcha_event"
        ):
            result = await live._handle_captcha_verification(
                {"data": {"url": "https://h5api.m.goofish.com/punish"}}
            )

        self.assertEqual(result, live.cookies_str)
        live._handle_manual_captcha_verification.assert_not_awaited()

    async def test_manual_captcha_alert_contains_only_control_url(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.send_critical_alert = AsyncMock(return_value="sent")
        control_url = "https://xianyu.example/api/captcha/control/account-1-token"

        await live._send_manual_captcha_critical_alert(control_url)

        live.send_critical_alert.assert_awaited_once()
        args = live.send_critical_alert.await_args.args
        self.assertEqual(args[0], "captcha_manual_required")
        self.assertIn(control_url, args[2])
        self.assertNotIn("h5api.m.goofish.com", args[2])

    async def test_password_refresh_cookie_must_pass_token_probe(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.last_token_refresh_status = None
        live._probe_token_with_cookie = AsyncMock(
            return_value=(False, "unb=1; token=bad", "仍然触发风控")
        )

        result = await live._validate_password_refresh_cookie(
            "unb=1; token=bad"
        )

        self.assertIsNone(result)
        self.assertEqual(
            live.last_token_refresh_status,
            "password_login_refresh_failed",
        )

    async def test_password_refresh_uses_probe_merged_cookie(self):
        live = XianyuLive.__new__(XianyuLive)
        live.cookie_id = "account-1"
        live.last_token_refresh_status = None
        live._probe_token_with_cookie = AsyncMock(
            return_value=(True, "unb=1; token=merged", "success")
        )

        result = await live._validate_password_refresh_cookie(
            "unb=1; token=candidate"
        )

        self.assertEqual(result, "unb=1; token=merged")


if __name__ == "__main__":
    unittest.main()
