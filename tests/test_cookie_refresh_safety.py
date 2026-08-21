import unittest
from unittest.mock import AsyncMock, patch

import reply_server
from XianyuAutoAsync import XianyuLive


class CookieRefreshIntervalTests(unittest.TestCase):
    def test_background_refresh_uses_twenty_hour_cooldown(self):
        live = XianyuLive(
            "unb=account-1",
            cookie_id="account-1",
            user_id=1,
            register_instance=False,
        )

        self.assertEqual(live.token_refresh_interval, 20 * 60 * 60)
        self.assertEqual(live.cookie_refresh_interval, live.token_refresh_interval)
        self.assertEqual(live.token_retry_interval, 2 * 60 * 60)


class DailyQrRefreshIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_qr_candidate_does_not_register_as_live_account(self):
        constructor_args = {}

        class CandidateLive:
            async def refresh_cookies_from_qr_login(self, **_kwargs):
                return False

        def build_candidate(**kwargs):
            constructor_args.update(kwargs)
            return CandidateLive()

        with patch.object(
            reply_server.db_manager,
            "get_all_cookies",
            return_value={"account-1": "unb=account-1; cookie2=old"},
        ), patch(
            "XianyuAutoAsync.XianyuLive",
            side_effect=build_candidate,
        ), patch.object(
            reply_server,
            "_notify_qr_push_result",
            new=AsyncMock(),
        ):
            with self.assertRaisesRegex(ValueError, "已保留旧Cookie"):
                await reply_server._process_pushed_qr_login_cookies(
                    session_id="session-1",
                    cookies="unb=account-1; cookie2=new",
                    unb="account-1",
                    target_account_id="account-1",
                    current_user={"user_id": 1},
                    settings={"channel_ids": []},
                )

        self.assertIs(constructor_args.get("register_instance"), False)


if __name__ == "__main__":
    unittest.main()
