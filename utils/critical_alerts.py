import asyncio
import json
import re
import time
from typing import Any, Awaitable, Callable, Dict, Optional

import aiohttp
from loguru import logger


PUSHPLUS_ENDPOINT = "https://www.pushplus.plus/send"
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"cookie2|sgcookie|_m_h5_tk(?:_enc)?|xsrf-token|_tb_token_|"
    r"token|password|x5secdata|x5sectag|x5sec"
    r")\s*[=:]\s*([^\s;&]+)"
)
_LINK_RE = re.compile(
    r"(?i)(?:(?:https?://|www\.)[^\s<>\"']+|/api/captcha/control/[^\s<>\"']*)"
)


def sanitize_alert_text(value: object) -> str:
    """Remove credentials, anti-bot values, and links from operator alerts."""
    text = str(value or "")
    redacted = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    return _LINK_RE.sub("[LINK REMOVED]", redacted)


def parse_channel_config(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {"token": value.strip()} if value.strip() else {}
    return {}


async def send_pushplus_notification(
    config: Dict[str, Any],
    title: str,
    content: str,
) -> bool:
    token = str(config.get("token") or config.get("config") or "").strip()
    if not token:
        logger.error("关键告警的 PushPlus 配置缺少 token")
        return False

    payload = {
        "token": token,
        "title": sanitize_alert_text(title),
        "content": sanitize_alert_text(content),
        "template": config.get("template") or "txt",
    }
    for key in ("topic", "channel", "webhook", "callbackUrl", "to", "timestamp"):
        value = config.get(key)
        if value not in (None, ""):
            payload[key] = value

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(PUSHPLUS_ENDPOINT, json=payload) as response:
                response_text = await response.text()
                if response.status != 200:
                    logger.error("关键告警 PushPlus 请求失败: HTTP {}", response.status)
                    return False

                try:
                    response_json = json.loads(response_text)
                except json.JSONDecodeError:
                    logger.error("关键告警 PushPlus 返回了无法解析的响应")
                    return False

                code = response_json.get("code")
                if code in (200, 0, None):
                    return True

                logger.error("关键告警 PushPlus 业务响应失败: code={}", code)
                return False
    except Exception as exc:
        logger.error("关键告警 PushPlus 发送异常: {}", sanitize_alert_text(exc))
        return False


class CriticalAlertService:
    def __init__(
        self,
        db,
        sender: Callable[[Dict[str, Any], str, str], Awaitable[bool]] = send_pushplus_notification,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_attempts: int = 3,
    ):
        self.db = db
        self.sender = sender
        self.now = now
        self.sleep = sleep
        self.max_attempts = max(1, int(max_attempts))

    async def send(
        self,
        cookie_id: str,
        alert_key: str,
        title: str,
        content: str,
        cooldown_seconds: int = 1800,
    ) -> str:
        current_time = self.now()
        state = self.db.get_critical_alert_state(cookie_id, alert_key)
        if state:
            last_sent_at = float(state.get("last_sent_at") or 0)
            if current_time - last_sent_at < max(0, int(cooldown_seconds)):
                return "suppressed"

        channels = self.db.get_enabled_pushplus_channels_for_cookie(cookie_id)
        if not channels:
            logger.error("账号 {} 没有可用的 PushPlus 关键告警渠道", cookie_id)
            return "no_channel"

        safe_title = sanitize_alert_text(title)
        safe_content = sanitize_alert_text(content)
        sent_count = 0

        for channel in channels:
            config = parse_channel_config(channel.get("config"))
            channel_sent = False
            for attempt in range(self.max_attempts):
                if await self.sender(config, safe_title, safe_content):
                    channel_sent = True
                    sent_count += 1
                    break
                if attempt + 1 < self.max_attempts:
                    await self.sleep(min(4, 2 ** attempt))

            if not channel_sent:
                logger.error(
                    "关键告警渠道发送失败: account={}, channel_id={}",
                    cookie_id,
                    channel.get("id"),
                )

        if sent_count == 0:
            return "failed"

        if not self.db.mark_critical_alert_sent(cookie_id, alert_key, current_time):
            logger.error(
                "关键告警已发送但冷却状态保存失败: account={}, alert_key={}",
                cookie_id,
                alert_key,
            )
        return "sent"
