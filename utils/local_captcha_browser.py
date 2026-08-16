"""Run a responsive, host-native browser for a Docker CAPTCHA challenge."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


CHALLENGE_URL_RE = re.compile(r"验证URL:\s*(https://\S+)")
GOOFISH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)


def parse_cookie_header(cookie_header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in (cookie_header or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            cookies[name] = value.strip()
    return cookies


def extract_latest_challenge_url(
    logs: str, cookie_id: str | None = None
) -> str | None:
    if cookie_id:
        account_pattern = re.compile(
            rf"【{re.escape(cookie_id)}】[^\n]*?验证URL:\s*(https://\S+)"
        )
        matches = account_pattern.findall(logs or "")
    else:
        matches = CHALLENGE_URL_RE.findall(logs or "")
    return matches[-1] if matches else None


def build_context_cookies(
    cookie_header: str, challenge_url: str
) -> list[dict[str, object]]:
    cookies = parse_cookie_header(cookie_header)
    challenge_value = parse_qs(urlsplit(challenge_url).query).get(
        "x5secdata", [""]
    )[0]
    if challenge_value:
        cookies["x5secdata"] = challenge_value

    return [
        {
            "name": name,
            "value": value,
            "domain": ".goofish.com",
            "path": "/",
            "secure": True,
        }
        for name, value in cookies.items()
    ]


def merge_browser_cookies(
    base_cookie_header: str, browser_cookies: list[dict[str, object]]
) -> str:
    merged = parse_cookie_header(base_cookie_header)
    for cookie in browser_cookies:
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        if name and value is not None:
            merged[name] = str(value)
    return "; ".join(f"{name}={value}" for name, value in merged.items())


def has_fresh_verification_cookie(
    original_cookie_header: str, candidate_cookie_header: str
) -> bool:
    original = parse_cookie_header(original_cookie_header)
    candidate = parse_cookie_header(candidate_cookie_header)
    candidate_x5sec = candidate.get("x5sec", "")
    return bool(candidate_x5sec and candidate_x5sec != original.get("x5sec", ""))


def _read_account_cookie(db_path: Path, cookie_id: str) -> str:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT value FROM cookies WHERE id = ?", (cookie_id,)
        ).fetchone()
    finally:
        connection.close()
    if not row or not row[0]:
        raise RuntimeError(f"未找到账号 {cookie_id} 的 Cookie")
    return str(row[0])


def read_account_login(db_path: Path, cookie_id: str) -> tuple[str, str]:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT username, password FROM cookies WHERE id = ?", (cookie_id,)
        ).fetchone()
    finally:
        connection.close()
    if not row or not row[0] or not row[1]:
        raise RuntimeError(f"账号 {cookie_id} 未配置登录账号或密码")
    return str(row[0]), str(row[1])


def cookie_dict_to_header(cookies: dict[str, object]) -> str:
    return "; ".join(
        f"{name}={value}"
        for name, value in cookies.items()
        if name and value is not None
    )


def _read_active_challenge(project_root: Path, cookie_id: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "logs",
            "--since=10m",
            "--tail=2000",
            "xianyu-app",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    challenge_url = extract_latest_challenge_url(
        result.stdout + result.stderr,
        cookie_id,
    )
    if not challenge_url:
        raise RuntimeError("当前没有有效的闲鱼验证挑战，请先重启账号实例")
    return challenge_url


async def _validate_and_save_cookie(
    db_path: Path, cookie_id: str, candidate_cookie_header: str
) -> tuple[bool, str]:
    os.environ["DB_PATH"] = str(db_path)

    from XianyuAutoAsync import XianyuLive
    from db_manager import db_manager
    from utils.xianyu_utils import generate_device_id, trans_cookies

    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = cookie_id
    parsed = trans_cookies(candidate_cookie_header)
    live.device_id = generate_device_id(parsed.get("unb", cookie_id))
    live.ssl_context = live._build_ssl_context()

    success, validated_cookie, reason = await live._probe_token_with_cookie(
        candidate_cookie_header,
        "本机人工滑块Cookie",
    )
    if not success:
        return False, reason

    saved = db_manager.update_cookie_account_info(
        cookie_id,
        cookie_value=validated_cookie,
    )
    if not saved:
        return False, "Token校验成功，但写入数据库失败"
    return True, "success"


async def run_local_browser(
    project_root: Path,
    cookie_id: str,
    timeout_seconds: int,
) -> int:
    from playwright.async_api import async_playwright

    db_path = project_root / "data" / "xianyu_data.db"
    original_cookie = _read_account_cookie(db_path, cookie_id)
    challenge_url = _read_active_challenge(project_root, cookie_id)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        context = await browser.new_context(
            user_agent=GOOFISH_USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        await context.add_cookies(
            build_context_cookies(original_cookie, challenge_url)
        )
        page = await context.new_page()
        await page.goto(challenge_url, wait_until="domcontentloaded", timeout=30000)
        await page.bring_to_front()

        page_title = await page.title()
        try:
            page_text = " ".join(
                (await page.locator("body").inner_text(timeout=5000)).split()
            )[:240]
        except Exception:
            page_text = ""
        print(
            f"本机闲鱼验证窗口已打开: title={page_title!r}, text={page_text!r}",
            flush=True,
        )
        deadline = time.monotonic() + timeout_seconds
        last_probed_x5sec = parse_cookie_header(original_cookie).get("x5sec", "")

        while time.monotonic() < deadline and browser.is_connected():
            await asyncio.sleep(1)
            browser_cookies = await context.cookies()
            candidate_cookie = merge_browser_cookies(
                original_cookie, browser_cookies
            )
            candidate_x5sec = parse_cookie_header(candidate_cookie).get(
                "x5sec", ""
            )
            if not has_fresh_verification_cookie(
                original_cookie, candidate_cookie
            ) or candidate_x5sec == last_probed_x5sec:
                continue

            last_probed_x5sec = candidate_x5sec
            print("检测到新验证 Cookie，正在校验 Token...", flush=True)
            success, reason = await _validate_and_save_cookie(
                db_path, cookie_id, candidate_cookie
            )
            if not success:
                print(f"Cookie 尚未通过 Token 校验: {reason}", flush=True)
                continue

            print("验证成功，Cookie 已安全写入，正在重启服务...", flush=True)
            subprocess.run(
                ["docker", "compose", "restart", "xianyu-app"],
                cwd=project_root,
                check=True,
            )
            await browser.close()
            return 0

        if browser.is_connected():
            await browser.close()
        print("本机验证超时或窗口已关闭，未修改 Cookie。", file=sys.stderr)
        return 1


async def run_local_password_recovery(
    project_root: Path,
    cookie_id: str,
) -> int:
    db_path = project_root / "data" / "xianyu_data.db"
    os.environ["DB_PATH"] = str(db_path)
    system_chrome = Path(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    if system_chrome.is_file():
        os.environ.setdefault(
            "PLAYWRIGHT_CHROMIUM_EXECUTABLE",
            str(system_chrome),
        )
    username, password = read_account_login(db_path, cookie_id)

    from utils.xianyu_slider_stealth import XianyuSliderStealth

    slider = XianyuSliderStealth(
        user_id=cookie_id,
        enable_learning=False,
        headless=False,
    )
    print("本机闲鱼登录窗口即将打开，请在该窗口完成人工验证。", flush=True)
    result = await asyncio.to_thread(
        slider.login_with_password_playwright,
        account=username,
        password=password,
        show_browser=True,
        notification_callback=None,
    )
    if not result:
        print("本机密码登录未获得有效 Cookie。", file=sys.stderr, flush=True)
        return 1

    candidate_cookie = cookie_dict_to_header(result)
    print("登录完成，正在校验新 Cookie...", flush=True)
    success, reason = await _validate_and_save_cookie(
        db_path,
        cookie_id,
        candidate_cookie,
    )
    if not success:
        print(f"新 Cookie 未通过 Token 校验: {reason}", file=sys.stderr, flush=True)
        return 1

    print("Cookie 已安全写入，正在重启服务...", flush=True)
    subprocess.run(
        ["docker", "compose", "restart", "xianyu-app"],
        cwd=project_root,
        check=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="在本机原生 Chrome 完成闲鱼验证")
    parser.add_argument("--cookie-id", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument(
        "--password-login",
        action="store_true",
        help="在本机运行完整密码登录并自动写回 Cookie",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    if args.password_login:
        return asyncio.run(
            run_local_password_recovery(project_root, args.cookie_id)
        )
    return asyncio.run(
        run_local_browser(
            project_root,
            args.cookie_id,
            max(60, args.timeout),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
