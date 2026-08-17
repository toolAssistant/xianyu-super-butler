import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

import reply_server

from utils.manual_recovery import (
    ManualRecoveryManager,
    XianyuManualRecoveryRuntime,
    merge_cookie_values,
)


class _FakeRecoveryRuntime:
    def __init__(
        self,
        *,
        connected=False,
        latest_url="https://h5api.m.goofish.com/punish?challenge=latest",
        probed_url="https://h5api.m.goofish.com/punish?challenge=probed",
        validation_results=None,
    ):
        self.connected = connected
        self.latest_url = latest_url
        self.probed_url = probed_url
        self.validation_results = list(
            validation_results
            or [(True, "unb=1; token=validated", "success")]
        )
        self.opened_urls = []
        self.controlled_probe_calls = 0
        self.read_calls = 0
        self.persisted = []
        self.closed = []
        self.browser = object()

    async def is_connected(self, _cookie_id):
        return self.connected

    async def get_latest_risk_url(self, _cookie_id):
        return self.latest_url

    async def controlled_probe(self, _cookie_id):
        self.controlled_probe_calls += 1
        return self.probed_url

    async def open_browser(self, _cookie_id, risk_url):
        self.opened_urls.append(risk_url)
        return self.browser

    async def read_candidate_cookie(self, _cookie_id, _browser):
        self.read_calls += 1
        return f"unb=1; token=candidate-{self.read_calls}"

    async def validate_candidate(self, _cookie_id, _candidate):
        if len(self.validation_results) > 1:
            return self.validation_results.pop(0)
        return self.validation_results[0]

    async def persist_and_restart(self, cookie_id, validated_cookie):
        self.persisted.append((cookie_id, validated_cookie))

    async def close_browser(self, browser):
        self.closed.append(browser)


class _BlockingCloseRuntime(_FakeRecoveryRuntime):
    def __init__(self):
        super().__init__()
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def close_browser(self, browser):
        self.close_started.set()
        await self.allow_close.wait()
        await super().close_browser(browser)


class _MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


async def _wait_for_status(manager, cookie_id, expected, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.get(cookie_id)
        if snapshot and snapshot["status"] == expected:
            return snapshot
        await asyncio.sleep(0.005)
    raise AssertionError(
        f"recovery session {cookie_id} did not reach {expected}: {manager.get(cookie_id)}"
    )


class ManualRecoveryManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_connected_account_returns_noop_without_browser(self):
        runtime = _FakeRecoveryRuntime(connected=True)
        manager = ManualRecoveryManager(runtime, novnc_url="http://127.0.0.1:6080/vnc.html")

        result = await manager.start("account-1", owner_user_id=7)

        self.assertEqual(result["status"], "noop")
        self.assertEqual(result["message"], "当前连接正常，无需人工恢复")
        self.assertFalse(result["active"])
        self.assertEqual(runtime.opened_urls, [])
        self.assertIsNone(manager.get("account-1"))

    async def test_duplicate_start_reuses_active_session(self):
        runtime = _FakeRecoveryRuntime(
            validation_results=[(False, "candidate", "仍需人工验证")]
        )
        manager = ManualRecoveryManager(
            runtime,
            poll_interval_seconds=60,
            timeout_seconds=900,
        )

        first = await manager.start("account-1", owner_user_id=7)
        second = await manager.start("account-1", owner_user_id=7)

        self.assertEqual(first["created_at"], second["created_at"])
        self.assertTrue(second["active"])
        self.assertEqual(len(manager.sessions), 1)
        await manager.cancel("account-1")

    async def test_retry_waits_for_previous_browser_cleanup(self):
        runtime = _BlockingCloseRuntime()
        manager = ManualRecoveryManager(runtime, poll_interval_seconds=0)

        await manager.start("account-1", owner_user_id=7)
        await _wait_for_status(manager, "account-1", "success")
        await runtime.close_started.wait()

        retry = asyncio.create_task(
            manager.start("account-1", owner_user_id=7)
        )
        await asyncio.sleep(0)

        self.assertFalse(retry.done())
        self.assertEqual(len(runtime.opened_urls), 1)

        runtime.allow_close.set()
        await retry
        await _wait_for_status(manager, "account-1", "success")
        self.assertEqual(len(runtime.opened_urls), 2)
        await manager.cancel("account-1")

    async def test_terminal_session_is_pruned_after_retention_window(self):
        clock = _MutableClock(100)
        runtime = _FakeRecoveryRuntime()
        manager = ManualRecoveryManager(
            runtime,
            poll_interval_seconds=0,
            terminal_retention_seconds=900,
            now=clock,
        )

        await manager.start("account-1", owner_user_id=7)
        await _wait_for_status(manager, "account-1", "success")

        clock.value = 1001

        self.assertIsNone(manager.get("account-1"))

    async def test_start_prunes_expired_terminal_sessions(self):
        clock = _MutableClock(100)
        runtime = _FakeRecoveryRuntime()
        manager = ManualRecoveryManager(
            runtime,
            poll_interval_seconds=0,
            terminal_retention_seconds=900,
            now=clock,
        )

        await manager.start("account-1", owner_user_id=7)
        await _wait_for_status(manager, "account-1", "success")
        clock.value = 1001

        await manager.start("account-2", owner_user_id=7)

        self.assertNotIn("account-1", manager.sessions)
        await _wait_for_status(manager, "account-2", "success")

    async def test_cancel_prunes_expired_terminal_session(self):
        clock = _MutableClock(100)
        runtime = _FakeRecoveryRuntime()
        manager = ManualRecoveryManager(
            runtime,
            poll_interval_seconds=0,
            terminal_retention_seconds=900,
            now=clock,
        )

        await manager.start("account-1", owner_user_id=7)
        await _wait_for_status(manager, "account-1", "success")
        clock.value = 1001

        self.assertIsNone(await manager.cancel("account-1"))


class _FakeDB:
    def __init__(self, cookie="unb=1; cookie2=old; keep=base", update_result=True):
        self.cookie = cookie
        self.update_result = update_result
        self.updated = []

    def get_cookie_details(self, cookie_id):
        return {
            "id": cookie_id,
            "value": self.cookie,
            "user_id": 7,
        }

    def update_cookie_account_info(self, cookie_id, cookie_value=None, **_kwargs):
        self.updated.append((cookie_id, cookie_value))
        return self.update_result


class _FakePage:
    def __init__(self):
        self.goto_calls = []

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))


class _FakeContext:
    def __init__(self):
        self.pages = []
        self.page = _FakePage()
        self.added_cookies = []
        self.closed = False
        self.browser_cookies = [
            {"name": "cookie2", "value": "profile"},
            {"name": "profile_only", "value": "yes"},
        ]

    async def cookies(self):
        return list(self.browser_cookies)

    async def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    async def new_page(self):
        self.pages.append(self.page)
        return self.page

    async def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, context):
        self.context = context
        self.launch_calls = []

    async def launch_persistent_context(self, path, **kwargs):
        self.launch_calls.append((path, kwargs))
        return self.context


class _FakePlaywright:
    def __init__(self, context):
        self.chromium = _FakeChromium(context)
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _FakePlaywrightStarter:
    def __init__(self, playwright):
        self.playwright = playwright

    async def start(self):
        return self.playwright


class _FakeLive:
    def __init__(self):
        self.connection_state = "failed"
        self.ws = None
        self.last_captcha_verification_url = None
        self.probe_calls = []

    async def _probe_token_with_cookie(self, cookie, source, max_retries=1):
        self.probe_calls.append((cookie, source, max_retries))
        self.last_captcha_verification_url = (
            "https://h5api.m.goofish.com/punish?challenge=controlled"
        )
        return False, cookie, "Token验证触发风控"

    def mark_connected(self):
        self.connection_state = "connected"
        self.ws = type("_OpenWebSocket", (), {"closed": False})()


class _FakeCookieManager:
    def __init__(self, on_update=None):
        self.updated = []
        self.on_update = on_update

    def update_cookie(self, cookie_id, value, save_to_db=True):
        self.updated.append((cookie_id, value, save_to_db))
        if self.on_update:
            self.on_update()


class XianyuManualRecoveryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_merge_cookie_values_preserves_base_and_overlays_browser(self):
        merged = merge_cookie_values(
            "unb=1; cookie2=old; keep=base",
            [
                {"name": "cookie2", "value": "fresh"},
                {"name": "browser_only", "value": "yes"},
            ],
        )

        self.assertEqual(
            merged,
            "unb=1; cookie2=fresh; keep=base; browser_only=yes",
        )

    async def test_open_browser_requires_vnc_and_display(self):
        runtime = XianyuManualRecoveryRuntime(
            db=_FakeDB(),
            live_provider=lambda _cookie_id: _FakeLive(),
            cookie_manager_provider=lambda: _FakeCookieManager(),
            environ={"ENABLE_VNC": "false", "DISPLAY": ":99"},
        )

        with self.assertRaisesRegex(RuntimeError, "VNC"):
            await runtime.open_browser(
                "account-1",
                "https://h5api.m.goofish.com/punish",
            )

        runtime.environ = {"ENABLE_VNC": "true", "DISPLAY": ""}
        with self.assertRaisesRegex(RuntimeError, "DISPLAY"):
            await runtime.open_browser(
                "account-1",
                "https://h5api.m.goofish.com/punish",
            )

    async def test_open_browser_uses_persistent_headed_profile(self):
        context = _FakeContext()
        playwright = _FakePlaywright(context)
        with tempfile.TemporaryDirectory() as browser_root:
            runtime = XianyuManualRecoveryRuntime(
                db=_FakeDB(),
                live_provider=lambda _cookie_id: _FakeLive(),
                cookie_manager_provider=lambda: _FakeCookieManager(),
                environ={"ENABLE_VNC": "true", "DISPLAY": ":99"},
                browser_data_dir=Path(browser_root),
                playwright_factory=lambda: _FakePlaywrightStarter(playwright),
            )

            handle = await runtime.open_browser(
                "account-1",
                "https://h5api.m.goofish.com/punish?x5secdata=challenge",
            )

        launch_path, launch_options = playwright.chromium.launch_calls[0]
        self.assertEqual(launch_path, str(Path(browser_root) / "user_account-1"))
        self.assertFalse(launch_options["headless"])
        self.assertEqual(context.page.goto_calls[0][0], "https://h5api.m.goofish.com/punish?x5secdata=challenge")
        seeded = {cookie["name"]: cookie["value"] for cookie in context.added_cookies}
        self.assertEqual(seeded["cookie2"], "profile")
        self.assertEqual(seeded["x5secdata"], "challenge")

        candidate = await runtime.read_candidate_cookie("account-1", handle)
        self.assertEqual(
            candidate,
            "unb=1; cookie2=profile; keep=base; profile_only=yes",
        )
        await runtime.close_browser(handle)
        self.assertTrue(context.closed)
        self.assertTrue(playwright.stopped)

    async def test_controlled_probe_uses_saved_cookie_and_returns_captured_url(self):
        live = _FakeLive()
        runtime = XianyuManualRecoveryRuntime(
            db=_FakeDB(cookie="unb=1; cookie2=saved"),
            live_provider=lambda _cookie_id: live,
            cookie_manager_provider=lambda: _FakeCookieManager(),
        )

        risk_url = await runtime.controlled_probe("account-1")

        self.assertEqual(
            risk_url,
            "https://h5api.m.goofish.com/punish?challenge=controlled",
        )
        self.assertEqual(
            live.probe_calls,
            [("unb=1; cookie2=saved", "人工恢复受控Token探测", 0)],
        )

    async def test_controlled_probe_builds_unregistered_probe_when_live_is_missing(self):
        created = []

        def live_factory(cookie, *, cookie_id, user_id, register_instance):
            created.append((cookie, cookie_id, user_id, register_instance))
            return _FakeLive()

        runtime = XianyuManualRecoveryRuntime(
            db=_FakeDB(cookie="unb=1; cookie2=saved"),
            live_provider=lambda _cookie_id: None,
            live_factory=live_factory,
            cookie_manager_provider=lambda: _FakeCookieManager(),
        )

        risk_url = await runtime.controlled_probe("account-1")

        self.assertEqual(
            created,
            [("unb=1; cookie2=saved", "account-1", 7, False)],
        )
        self.assertIn("challenge=controlled", risk_url)

    async def test_persist_requires_database_success_before_restart(self):
        db = _FakeDB(update_result=False)
        cookie_manager = _FakeCookieManager()
        runtime = XianyuManualRecoveryRuntime(
            db=db,
            live_provider=lambda _cookie_id: _FakeLive(),
            cookie_manager_provider=lambda: cookie_manager,
        )

        with self.assertRaisesRegex(RuntimeError, "保存"):
            await runtime.persist_and_restart("account-1", "unb=1; token=fresh")

        self.assertEqual(cookie_manager.updated, [])

    async def test_persist_updates_database_then_restarts_account(self):
        db = _FakeDB(update_result=True)
        live = _FakeLive()
        cookie_manager = _FakeCookieManager(on_update=live.mark_connected)
        runtime = XianyuManualRecoveryRuntime(
            db=db,
            live_provider=lambda _cookie_id: live,
            cookie_manager_provider=lambda: cookie_manager,
            reconnect_timeout_seconds=0.1,
            reconnect_poll_seconds=0,
        )

        await runtime.persist_and_restart("account-1", "unb=1; token=fresh")

        self.assertEqual(db.updated, [("account-1", "unb=1; token=fresh")])
        self.assertEqual(
            cookie_manager.updated,
            [("account-1", "unb=1; token=fresh", False)],
        )

    async def test_persist_reports_failure_when_account_does_not_reconnect(self):
        db = _FakeDB(update_result=True)
        live = _FakeLive()
        cookie_manager = _FakeCookieManager()
        runtime = XianyuManualRecoveryRuntime(
            db=db,
            live_provider=lambda _cookie_id: live,
            cookie_manager_provider=lambda: cookie_manager,
            reconnect_timeout_seconds=0.01,
            reconnect_poll_seconds=0,
        )

        with self.assertRaisesRegex(RuntimeError, "未恢复连接"):
            await runtime.persist_and_restart("account-1", "unb=1; token=fresh")

    async def test_latest_risk_url_is_used_before_controlled_probe(self):
        runtime = _FakeRecoveryRuntime()
        manager = ManualRecoveryManager(runtime, poll_interval_seconds=0)

        await manager.start("account-1", owner_user_id=7)
        await _wait_for_status(manager, "account-1", "success")

        self.assertEqual(runtime.opened_urls, [runtime.latest_url])
        self.assertEqual(runtime.controlled_probe_calls, 0)

    async def test_controlled_probe_supplies_missing_risk_url(self):
        runtime = _FakeRecoveryRuntime(latest_url=None)
        manager = ManualRecoveryManager(runtime, poll_interval_seconds=0)

        await manager.start("account-1", owner_user_id=7)
        await _wait_for_status(manager, "account-1", "success")

        self.assertEqual(runtime.controlled_probe_calls, 1)
        self.assertEqual(runtime.opened_urls, [runtime.probed_url])

    async def test_invalid_candidate_never_persists_cookie(self):
        runtime = _FakeRecoveryRuntime(
            validation_results=[(False, "candidate", "Token仍触发风控")]
        )
        manager = ManualRecoveryManager(
            runtime,
            poll_interval_seconds=0.005,
            timeout_seconds=0.03,
        )

        await manager.start("account-1", owner_user_id=7)
        await _wait_for_status(manager, "account-1", "timed_out")

        self.assertEqual(runtime.persisted, [])
        self.assertEqual(runtime.closed, [runtime.browser])

    async def test_valid_candidate_persists_then_restarts(self):
        runtime = _FakeRecoveryRuntime(
            validation_results=[(True, "unb=1; token=validated", "success")]
        )
        manager = ManualRecoveryManager(runtime, poll_interval_seconds=0)

        await manager.start("account-1", owner_user_id=7)
        snapshot = await _wait_for_status(manager, "account-1", "success")

        self.assertEqual(
            runtime.persisted,
            [("account-1", "unb=1; token=validated")],
        )
        self.assertEqual(snapshot["message"], "人工恢复成功，账号已重新连接")
        self.assertEqual(runtime.closed, [runtime.browser])

    async def test_timeout_closes_browser_and_preserves_cookie(self):
        runtime = _FakeRecoveryRuntime(
            validation_results=[(False, "candidate", "尚未完成验证")]
        )
        manager = ManualRecoveryManager(
            runtime,
            poll_interval_seconds=0.005,
            timeout_seconds=0.02,
        )

        await manager.start("account-1", owner_user_id=7)
        snapshot = await _wait_for_status(manager, "account-1", "timed_out")

        self.assertFalse(snapshot["active"])
        self.assertIn("15分钟", snapshot["message"])
        self.assertEqual(runtime.persisted, [])
        self.assertEqual(runtime.closed, [runtime.browser])

    async def test_cancel_closes_browser_and_marks_cancelled(self):
        runtime = _FakeRecoveryRuntime(
            validation_results=[(False, "candidate", "尚未完成验证")]
        )
        manager = ManualRecoveryManager(
            runtime,
            poll_interval_seconds=60,
            timeout_seconds=900,
        )

        await manager.start("account-1", owner_user_id=7)
        await _wait_for_status(manager, "account-1", "waiting_for_operator")
        snapshot = await manager.cancel("account-1")

        self.assertEqual(snapshot["status"], "cancelled")
        self.assertFalse(snapshot["active"])
        self.assertEqual(runtime.persisted, [])
        self.assertEqual(runtime.closed, [runtime.browser])

    async def test_snapshot_never_exposes_cookie_or_risk_url(self):
        runtime = _FakeRecoveryRuntime(
            validation_results=[(False, "secret-cookie", "尚未完成验证")]
        )
        manager = ManualRecoveryManager(
            runtime,
            poll_interval_seconds=60,
            timeout_seconds=900,
            novnc_url="http://127.0.0.1:6080/vnc.html",
        )

        snapshot = await manager.start("account-1", owner_user_id=7)

        self.assertEqual(
            set(snapshot),
            {
                "cookie_id",
                "status",
                "message",
                "active",
                "novnc_url",
                "created_at",
                "expires_at",
                "updated_at",
            },
        )
        serialized = repr(snapshot)
        self.assertNotIn("challenge=", serialized)
        self.assertNotIn("secret-cookie", serialized)
        await manager.cancel("account-1")


class _FakeApiRecoveryManager:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.start = AsyncMock(
            return_value=snapshot
            or {
                "cookie_id": "account-1",
                "status": "starting",
                "message": "正在启动人工恢复浏览器",
                "active": True,
                "novnc_url": "http://127.0.0.1:6080/vnc.html",
                "created_at": 1,
                "expires_at": 901,
                "updated_at": 1,
            }
        )
        self.cancel = AsyncMock(return_value=snapshot)

    def get(self, _cookie_id):
        return self.snapshot


class ManualRecoveryApiTests(unittest.IsolatedAsyncioTestCase):
    def test_admin_account_list_includes_all_accounts(self):
        manager = MagicMock()
        manager.get_cookie_status.return_value = True

        def get_all_cookies(user_id=None):
            return {"all-account": "secret"} if user_id is None else {"own-account": "secret"}

        with patch.object(reply_server.cookie_manager, "manager", manager), patch.object(
            reply_server.db_manager,
            "get_all_cookies",
            side_effect=get_all_cookies,
        ), patch.object(
            reply_server.db_manager,
            "get_auto_confirm",
            return_value=True,
        ), patch.object(
            reply_server.db_manager,
            "get_cookie_details",
            return_value={"remark": "", "pause_duration": 10},
        ):
            result = reply_server.get_cookies_details(
                {"user_id": 1, "is_admin": True}
            )

        self.assertEqual([account["id"] for account in result], ["all-account"])

    def test_owner_can_access_account(self):
        details = {"id": "account-1", "user_id": 7, "value": "secret"}
        with patch.object(
            reply_server.db_manager,
            "get_cookie_details",
            return_value=details,
        ):
            result = reply_server._require_cookie_access(
                "account-1",
                {"user_id": 7, "is_admin": False},
            )

        self.assertEqual(result, details)

    def test_other_user_cannot_access_account(self):
        with patch.object(
            reply_server.db_manager,
            "get_cookie_details",
            return_value={"id": "account-1", "user_id": 8, "value": "secret"},
        ):
            with self.assertRaises(HTTPException) as raised:
                reply_server._require_cookie_access(
                    "account-1",
                    {"user_id": 7, "is_admin": False},
                )

        self.assertEqual(raised.exception.status_code, 403)

    def test_admin_can_access_any_existing_account(self):
        details = {"id": "account-1", "user_id": 8, "value": "secret"}
        with patch.object(
            reply_server.db_manager,
            "get_cookie_details",
            return_value=details,
        ):
            result = reply_server._require_cookie_access(
                "account-1",
                {"user_id": 1, "is_admin": True},
            )

        self.assertEqual(result, details)

    def test_missing_account_returns_404(self):
        with patch.object(
            reply_server.db_manager,
            "get_cookie_details",
            return_value=None,
        ):
            with self.assertRaises(HTTPException) as raised:
                reply_server._require_cookie_access(
                    "missing",
                    {"user_id": 7, "is_admin": True},
                )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_start_route_uses_authenticated_user_and_safe_snapshot(self):
        manager = _FakeApiRecoveryManager()
        account = {"id": "account-1", "user_id": 7, "value": "secret"}
        with patch.object(
            reply_server,
            "_require_cookie_access",
            return_value=account,
        ), patch.object(
            reply_server,
            "_get_manual_recovery_manager",
            return_value=manager,
        ):
            result = await reply_server.start_manual_recovery(
                "account-1",
                {"user_id": 7, "is_admin": False},
            )

        manager.start.assert_awaited_once_with("account-1", owner_user_id=7)
        serialized = repr(result)
        for forbidden in ("risk_url", "verification_url", "secret", "password", "browser"):
            self.assertNotIn(forbidden, serialized)

    async def test_get_and_delete_missing_session_return_404(self):
        manager = _FakeApiRecoveryManager(snapshot=None)
        with patch.object(
            reply_server,
            "_require_cookie_access",
            return_value={"id": "account-1", "user_id": 7},
        ), patch.object(
            reply_server,
            "_get_manual_recovery_manager",
            return_value=manager,
        ):
            with self.assertRaises(HTTPException) as get_error:
                await reply_server.get_manual_recovery(
                    "account-1",
                    {"user_id": 7, "is_admin": False},
                )
            with self.assertRaises(HTTPException) as delete_error:
                await reply_server.cancel_manual_recovery(
                    "account-1",
                    {"user_id": 7, "is_admin": False},
                )

        self.assertEqual(get_error.exception.status_code, 404)
        self.assertEqual(delete_error.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
