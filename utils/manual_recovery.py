from __future__ import annotations

import asyncio
import inspect
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, Tuple
from urllib.parse import parse_qs, urlsplit

from utils.critical_alerts import sanitize_alert_text


ACTIVE_STAGES = {
    "starting",
    "waiting_for_operator",
    "validating",
    "reconnecting",
}
TERMINAL_STAGES = {"success", "failed", "timed_out", "cancelled"}
_SAFE_COOKIE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TRUSTED_RISK_HOST_SUFFIXES = (
    ".goofish.com",
    ".taobao.com",
    ".tmall.com",
    ".alibaba.com",
)


def merge_cookie_values(base_cookie: str, browser_cookies: list[dict[str, Any]]) -> str:
    merged: dict[str, str] = {}
    for pair in str(base_cookie or "").split(";"):
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        if name:
            merged[name] = value.strip()

    for cookie in browser_cookies:
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        if name and value is not None:
            merged[name] = str(value)

    return "; ".join(f"{name}={value}" for name, value in merged.items())


@dataclass
class RecoveryBrowserHandle:
    playwright: Any
    context: Any
    base_cookie: str


class ManualRecoveryRuntime(Protocol):
    async def is_connected(self, cookie_id: str) -> bool: ...

    async def get_latest_risk_url(self, cookie_id: str) -> Optional[str]: ...

    async def controlled_probe(self, cookie_id: str) -> Optional[str]: ...

    async def open_browser(self, cookie_id: str, risk_url: str) -> Any: ...

    async def read_candidate_cookie(self, cookie_id: str, browser: Any) -> str: ...

    async def validate_candidate(
        self,
        cookie_id: str,
        candidate_cookie: str,
    ) -> Tuple[bool, str, str]: ...

    async def persist_and_restart(
        self,
        cookie_id: str,
        validated_cookie: str,
    ) -> None: ...

    async def close_browser(self, browser: Any) -> None: ...


@dataclass
class ManualRecoverySession:
    cookie_id: str
    owner_user_id: int
    status: str
    message: str
    created_at: float
    expires_at: float
    updated_at: float
    finished_at: Optional[float] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    browser: Any = field(default=None, repr=False)


class ManualRecoveryManager:
    def __init__(
        self,
        runtime: ManualRecoveryRuntime,
        *,
        timeout_seconds: float = 15 * 60,
        poll_interval_seconds: float = 5,
        terminal_retention_seconds: float = 15 * 60,
        novnc_url: str = (
            "http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale"
        ),
        now=time.time,
        sleep=asyncio.sleep,
    ):
        self.runtime = runtime
        self.timeout_seconds = max(0.01, float(timeout_seconds))
        self.poll_interval_seconds = max(0, float(poll_interval_seconds))
        self.terminal_retention_seconds = max(
            0,
            float(terminal_retention_seconds),
        )
        self.novnc_url = novnc_url
        self.now = now
        self.sleep = sleep
        self.sessions: dict[str, ManualRecoverySession] = {}
        self._lock = asyncio.Lock()

    async def start(self, cookie_id: str, owner_user_id: int) -> dict[str, Any]:
        async with self._lock:
            self._prune_terminal_sessions()
            existing = self.sessions.get(cookie_id)
            if existing and existing.status in ACTIVE_STAGES:
                return self._snapshot(existing)
            if existing and existing.task and not existing.task.done():
                await asyncio.gather(existing.task, return_exceptions=True)

            if await self.runtime.is_connected(cookie_id):
                current_time = self.now()
                return {
                    "cookie_id": cookie_id,
                    "status": "noop",
                    "message": "当前连接正常，无需人工恢复",
                    "active": False,
                    "novnc_url": self.novnc_url,
                    "created_at": current_time,
                    "expires_at": current_time,
                    "updated_at": current_time,
                }

            current_time = self.now()
            session = ManualRecoverySession(
                cookie_id=cookie_id,
                owner_user_id=owner_user_id,
                status="starting",
                message="正在启动人工恢复浏览器",
                created_at=current_time,
                expires_at=current_time + self.timeout_seconds,
                updated_at=current_time,
            )
            self.sessions[cookie_id] = session
            session.task = asyncio.create_task(self._run(session))
            return self._snapshot(session)

    def get(self, cookie_id: str) -> Optional[dict[str, Any]]:
        self._prune_terminal_sessions()
        session = self.sessions.get(cookie_id)
        return self._snapshot(session) if session else None

    async def cancel(self, cookie_id: str) -> Optional[dict[str, Any]]:
        self._prune_terminal_sessions()
        session = self.sessions.get(cookie_id)
        if session is None:
            return None
        if session.status in TERMINAL_STAGES:
            return self._snapshot(session)

        self._set_status(session, "cancelled", "人工恢复已取消")
        task = session.task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return self._snapshot(session)

    async def _run(self, session: ManualRecoverySession) -> None:
        try:
            risk_url = await self.runtime.get_latest_risk_url(session.cookie_id)
            if not risk_url:
                risk_url = await self.runtime.controlled_probe(session.cookie_id)
            if not risk_url:
                self._set_status(
                    session,
                    "failed",
                    "未获取到可用的风控页面，请等待下一次告警后重试",
                )
                return

            session.browser = await self.runtime.open_browser(
                session.cookie_id,
                risk_url,
            )
            self._set_status(
                session,
                "waiting_for_operator",
                "浏览器已打开，等待人工处理风控验证",
            )

            while self.now() < session.expires_at:
                remaining = max(0, session.expires_at - self.now())
                if self.poll_interval_seconds:
                    await self.sleep(min(self.poll_interval_seconds, remaining))
                else:
                    await self.sleep(0)

                if self.now() >= session.expires_at:
                    break

                candidate_cookie = await self.runtime.read_candidate_cookie(
                    session.cookie_id,
                    session.browser,
                )
                if not candidate_cookie:
                    self._set_status(
                        session,
                        "waiting_for_operator",
                        "等待浏览器生成可验证的Cookie",
                    )
                    continue

                self._set_status(session, "validating", "正在验证Token")
                valid, validated_cookie, reason = (
                    await self.runtime.validate_candidate(
                        session.cookie_id,
                        candidate_cookie,
                    )
                )
                if not valid:
                    safe_reason = sanitize_alert_text(reason)
                    self._set_status(
                        session,
                        "waiting_for_operator",
                        f"尚未通过验证：{safe_reason}",
                    )
                    continue

                self._set_status(session, "reconnecting", "Token验证通过，正在重连账号")
                await self.runtime.persist_and_restart(
                    session.cookie_id,
                    validated_cookie,
                )
                self._set_status(
                    session,
                    "success",
                    "人工恢复成功，账号已重新连接",
                )
                return

            self._set_status(
                session,
                "timed_out",
                "人工恢复已超过15分钟，请重新触发",
            )
        except asyncio.CancelledError:
            if session.status in ACTIVE_STAGES:
                self._set_status(session, "cancelled", "人工恢复已取消")
            raise
        except Exception as exc:
            self._set_status(
                session,
                "failed",
                f"人工恢复失败：{sanitize_alert_text(exc)}",
            )
        finally:
            if session.browser is not None:
                try:
                    await self.runtime.close_browser(session.browser)
                finally:
                    session.browser = None

    def _set_status(
        self,
        session: ManualRecoverySession,
        status: str,
        message: str,
    ) -> None:
        current_time = self.now()
        session.status = status
        session.message = message
        session.updated_at = current_time
        if status in TERMINAL_STAGES and session.finished_at is None:
            session.finished_at = current_time

    def _prune_terminal_sessions(self) -> None:
        current_time = self.now()
        expired_cookie_ids = [
            cookie_id
            for cookie_id, session in self.sessions.items()
            if session.status in TERMINAL_STAGES
            and session.finished_at is not None
            and current_time - session.finished_at
            >= self.terminal_retention_seconds
            and (session.task is None or session.task.done())
        ]
        for cookie_id in expired_cookie_ids:
            self.sessions.pop(cookie_id, None)

    def _snapshot(self, session: ManualRecoverySession) -> dict[str, Any]:
        return {
            "cookie_id": session.cookie_id,
            "status": session.status,
            "message": session.message,
            "active": session.status in ACTIVE_STAGES,
            "novnc_url": self.novnc_url,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
            "updated_at": session.updated_at,
        }


class XianyuManualRecoveryRuntime:
    def __init__(
        self,
        *,
        db=None,
        live_provider=None,
        live_factory=None,
        cookie_manager_provider=None,
        environ=None,
        browser_data_dir: Optional[Path] = None,
        playwright_factory=None,
        reconnect_timeout_seconds: float = 60,
        reconnect_poll_seconds: float = 1,
        sleep=asyncio.sleep,
        monotonic=time.monotonic,
    ):
        if db is None:
            from db_manager import db_manager

            db = db_manager
        if live_provider is None or live_factory is None:
            from XianyuAutoAsync import XianyuLive

        if live_provider is None:
            live_provider = XianyuLive.get_instance
        if live_factory is None:
            live_factory = XianyuLive
        if cookie_manager_provider is None:
            cookie_manager_provider = self._default_cookie_manager_provider
        if playwright_factory is None:
            from playwright.async_api import async_playwright

            playwright_factory = async_playwright

        self.db = db
        self.live_provider = live_provider
        self.live_factory = live_factory
        self.cookie_manager_provider = cookie_manager_provider
        self.environ = environ if environ is not None else os.environ
        self.browser_data_dir = browser_data_dir or Path(
            self.environ.get("BROWSER_DATA_DIR", "browser_data")
        )
        self.playwright_factory = playwright_factory
        self.reconnect_timeout_seconds = max(0, float(reconnect_timeout_seconds))
        self.reconnect_poll_seconds = max(0, float(reconnect_poll_seconds))
        self.sleep = sleep
        self.monotonic = monotonic

    @staticmethod
    def _default_cookie_manager_provider():
        import cookie_manager

        return cookie_manager.manager

    async def is_connected(self, cookie_id: str) -> bool:
        live = self.live_provider(cookie_id)
        if live is None:
            return False
        state = getattr(live, "connection_state", None)
        state_value = getattr(state, "value", state)
        ws = getattr(live, "ws", None)
        return bool(
            state_value == "connected"
            and ws is not None
            and not getattr(ws, "closed", True)
        )

    async def get_latest_risk_url(self, cookie_id: str) -> Optional[str]:
        live = self.live_provider(cookie_id)
        if live is None:
            return None
        risk_url = getattr(live, "last_captcha_verification_url", None)
        return self._validated_risk_url(risk_url) if risk_url else None

    async def controlled_probe(self, cookie_id: str) -> Optional[str]:
        live = self._require_live(cookie_id)
        account = self._require_account(cookie_id)
        saved_cookie = account.get("value") or account.get("cookie_value") or ""
        await live._probe_token_with_cookie(
            saved_cookie,
            "人工恢复受控Token探测",
            max_retries=0,
        )
        risk_url = getattr(live, "last_captcha_verification_url", None)
        return self._validated_risk_url(risk_url) if risk_url else None

    async def open_browser(
        self,
        cookie_id: str,
        risk_url: str,
    ) -> RecoveryBrowserHandle:
        if str(self.environ.get("ENABLE_VNC", "")).lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise RuntimeError("VNC未启用，请使用docker-compose.vnc.yml部署")
        if not str(self.environ.get("DISPLAY", "")).strip():
            raise RuntimeError("DISPLAY未配置，无法启动有头浏览器")
        if not _SAFE_COOKIE_ID_RE.fullmatch(cookie_id):
            raise RuntimeError("账号ID包含不安全字符")

        safe_risk_url = self._validated_risk_url(risk_url)
        account = self._require_account(cookie_id)
        saved_cookie = account.get("value") or account.get("cookie_value") or ""
        user_data_dir = self.browser_data_dir / f"user_{cookie_id}"
        user_data_dir.mkdir(parents=True, exist_ok=True)

        playwright = await self.playwright_factory().start()
        context = None
        try:
            launch_options: dict[str, Any] = {
                "headless": False,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--lang=zh-CN",
                ],
                "viewport": {"width": 1980, "height": 1024},
                "locale": "zh-CN",
                "accept_downloads": True,
                "ignore_https_errors": True,
            }
            executable = str(
                self.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE", "")
            ).strip()
            if executable and Path(executable).is_file():
                launch_options["executable_path"] = executable

            context = await playwright.chromium.launch_persistent_context(
                str(user_data_dir),
                **launch_options,
            )

            existing_cookies = await context.cookies()
            merged_cookie = merge_cookie_values(saved_cookie, existing_cookies)
            seed_values = self._parse_cookie_string(merged_cookie)
            challenge = parse_qs(urlsplit(safe_risk_url).query).get(
                "x5secdata",
                [""],
            )[0]
            if challenge:
                seed_values["x5secdata"] = challenge
            await context.add_cookies(
                [
                    {
                        "name": name,
                        "value": value,
                        "domain": ".goofish.com",
                        "path": "/",
                    }
                    for name, value in seed_values.items()
                ]
            )

            pages = list(getattr(context, "pages", []))
            page = pages[0] if pages else await context.new_page()
            try:
                await page.goto(
                    safe_risk_url,
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except Exception as exc:
                if "timeout" not in str(exc).lower():
                    raise

            return RecoveryBrowserHandle(
                playwright=playwright,
                context=context,
                base_cookie=saved_cookie,
            )
        except Exception:
            if context is not None:
                await context.close()
            await playwright.stop()
            raise

    async def read_candidate_cookie(
        self,
        _cookie_id: str,
        browser: RecoveryBrowserHandle,
    ) -> str:
        return merge_cookie_values(
            browser.base_cookie,
            await browser.context.cookies(),
        )

    async def validate_candidate(
        self,
        cookie_id: str,
        candidate_cookie: str,
    ) -> Tuple[bool, str, str]:
        live = self._require_live(cookie_id)
        return await live._probe_token_with_cookie(
            candidate_cookie,
            "人工恢复Cookie",
            max_retries=1,
        )

    async def persist_and_restart(
        self,
        cookie_id: str,
        validated_cookie: str,
    ) -> None:
        if not self.db.update_cookie_account_info(
            cookie_id,
            cookie_value=validated_cookie,
        ):
            raise RuntimeError("人工恢复Cookie保存失败")

        manager = self.cookie_manager_provider()
        if manager is None:
            raise RuntimeError("CookieManager未就绪，无法重启账号")
        result = manager.update_cookie(
            cookie_id,
            validated_cookie,
            save_to_db=False,
        )
        if inspect.isawaitable(result):
            await result

        deadline = self.monotonic() + self.reconnect_timeout_seconds
        while True:
            if await self.is_connected(cookie_id):
                return
            if self.monotonic() >= deadline:
                raise RuntimeError("Cookie已保存，但账号未恢复连接")
            await self.sleep(self.reconnect_poll_seconds)

    async def close_browser(self, browser: RecoveryBrowserHandle) -> None:
        try:
            await browser.context.close()
        finally:
            await browser.playwright.stop()

    def _require_account(self, cookie_id: str) -> dict[str, Any]:
        account = self.db.get_cookie_details(cookie_id)
        if not account:
            raise RuntimeError("账号不存在")
        return account

    def _require_live(self, cookie_id: str):
        live = self.live_provider(cookie_id)
        if live is None:
            account = self._require_account(cookie_id)
            saved_cookie = account.get("value") or account.get("cookie_value") or ""
            live = self.live_factory(
                saved_cookie,
                cookie_id=cookie_id,
                user_id=account.get("user_id"),
                register_instance=False,
            )
        return live

    @staticmethod
    def _parse_cookie_string(cookie_value: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for pair in str(cookie_value or "").split(";"):
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name = name.strip()
            if name:
                parsed[name] = value.strip()
        return parsed

    @staticmethod
    def _validated_risk_url(risk_url: str) -> str:
        parsed = urlsplit(str(risk_url or ""))
        hostname = (parsed.hostname or "").lower()
        trusted = any(
            hostname == suffix[1:] or hostname.endswith(suffix)
            for suffix in _TRUSTED_RISK_HOST_SUFFIXES
        )
        if parsed.scheme != "https" or not trusted:
            raise RuntimeError("风控页面地址不受信任")
        return str(risk_url)
