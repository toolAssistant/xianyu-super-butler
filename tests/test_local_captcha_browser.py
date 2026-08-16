import sqlite3
import tempfile
import unittest
from pathlib import Path

from utils.local_captcha_browser import (
    build_context_cookies,
    cookie_dict_to_header,
    extract_latest_challenge_url,
    has_fresh_verification_cookie,
    merge_browser_cookies,
    parse_cookie_header,
    read_account_login,
)


class LocalCaptchaBrowserTests(unittest.TestCase):
    def test_extracts_latest_challenge_url(self):
        logs = "\n".join(
            [
                "验证URL: https://h5api.m.goofish.com/punish?x5secdata=old&x5step=2",
                "other log",
                "验证URL: https://h5api.m.goofish.com/punish?x5secdata=fresh&x5step=2",
            ]
        )

        self.assertEqual(
            extract_latest_challenge_url(logs),
            "https://h5api.m.goofish.com/punish?x5secdata=fresh&x5step=2",
        )

    def test_challenge_url_extractor_accepts_an_account_filter(self):
        self.assertEqual(extract_latest_challenge_url.__code__.co_argcount, 2)

    def test_extracts_latest_challenge_url_for_requested_account(self):
        logs = "\n".join(
            [
                "【account-a】验证URL: https://h5api.m.goofish.com/punish?x5secdata=account-a",
                "【account-b】验证URL: https://h5api.m.goofish.com/punish?x5secdata=account-b",
            ]
        )

        self.assertEqual(
            extract_latest_challenge_url(logs, "account-a"),
            "https://h5api.m.goofish.com/punish?x5secdata=account-a",
        )

    def test_context_cookies_replace_stale_challenge_cookie(self):
        cookie_header = "unb=1; cookie2=abc; x5secdata=stale"
        challenge_url = (
            "https://h5api.m.goofish.com/punish?x5secdata=current&x5step=2"
        )

        cookies = build_context_cookies(cookie_header, challenge_url)
        by_name = {cookie["name"]: cookie for cookie in cookies}

        self.assertEqual(by_name["x5secdata"]["value"], "current")
        self.assertEqual(by_name["cookie2"]["domain"], ".goofish.com")
        self.assertEqual(len([c for c in cookies if c["name"] == "x5secdata"]), 1)

    def test_browser_cookie_merge_preserves_required_fields(self):
        merged = merge_browser_cookies(
            "unb=1; cookie2=old; t=keep",
            [
                {"name": "cookie2", "value": "new"},
                {"name": "x5sec", "value": "verified"},
            ],
        )

        parsed = parse_cookie_header(merged)
        self.assertEqual(parsed["unb"], "1")
        self.assertEqual(parsed["t"], "keep")
        self.assertEqual(parsed["cookie2"], "new")
        self.assertEqual(parsed["x5sec"], "verified")

    def test_requires_changed_x5sec_before_token_probe(self):
        original = "unb=1; x5sec=old"

        self.assertFalse(
            has_fresh_verification_cookie(original, "unb=1; x5sec=old")
        )
        self.assertTrue(
            has_fresh_verification_cookie(original, "unb=1; x5sec=new")
        )

    def test_reads_login_and_converts_cookie_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "account.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                "CREATE TABLE cookies (id TEXT, username TEXT, password TEXT)"
            )
            connection.execute(
                "INSERT INTO cookies VALUES (?, ?, ?)",
                ("account-1", "login-name", "login-password"),
            )
            connection.commit()
            connection.close()

            username, password = read_account_login(db_path, "account-1")

        self.assertEqual(username, "login-name")
        self.assertEqual(password, "login-password")
        self.assertEqual(
            cookie_dict_to_header({"unb": "1", "cookie2": "fresh"}),
            "unb=1; cookie2=fresh",
        )


if __name__ == "__main__":
    unittest.main()
