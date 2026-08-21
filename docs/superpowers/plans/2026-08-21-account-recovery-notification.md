# Account Recovery Notification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send one PushPlus “account recovered” notification after a previously notified captcha/manual-recovery failure is followed by confirmed Token initialization and WebSocket recovery.

**Architecture:** Reuse the persisted `critical_alert_state` timestamps for `captcha_manual_required`, `account_recovery_failed`, and a new `account_recovered` key. A focused `XianyuLive` helper compares the newest failure timestamp with the newest recovery timestamp, while a connection-ready helper invokes it only after `init()` succeeds and the state becomes `CONNECTED`.

**Tech Stack:** Python 3.11, asyncio, unittest, SQLite-backed `DBManager`, existing PushPlus `CriticalAlertService`.

---

## File Structure

- Modify `XianyuAutoAsync.py`: add pending-recovery comparison, recovery message construction, and the confirmed-connection hook.
- Modify `tests/test_critical_alerts.py`: add timestamp ordering, deduplication, message contract, and connection-ready tests.
- Verify `db_manager.py` and `utils/critical_alerts.py` without modification: existing state reads, PushPlus delivery, retries, sanitization, and state writes already satisfy the design.

### Task 1: Determine and Close Pending Recovery Alerts

**Files:**
- Modify: `tests/test_critical_alerts.py`
- Modify: `XianyuAutoAsync.py:593-606`

- [ ] **Step 1: Write failing recovery-decision tests**

Add these methods to `CriticalAlertBusinessIntegrationTests`:

```python
async def test_pending_manual_alert_sends_account_recovered_notification(self):
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "account-1"
    live.send_critical_alert = AsyncMock(return_value="sent")
    states = {
        "captcha_manual_required": {"last_sent_at": 2000},
        "account_recovery_failed": {"last_sent_at": 2100},
        "account_recovered": {"last_sent_at": 1000},
    }

    with patch(
        "XianyuAutoAsync.db_manager.get_critical_alert_state",
        side_effect=lambda _cookie_id, key: states.get(key),
    ):
        result = await live._send_account_recovery_alert_if_needed()

    self.assertEqual(result, "sent")
    live.send_critical_alert.assert_awaited_once()
    args = live.send_critical_alert.await_args.args
    kwargs = live.send_critical_alert.await_args.kwargs
    self.assertEqual(args[0], "account_recovered")
    self.assertEqual(args[1], "闲鱼账号已自动恢复")
    self.assertIn("Token 与消息连接均已恢复", args[2])
    self.assertIn("无需人工处理", args[2])
    self.assertEqual(kwargs["cooldown_seconds"], 0)

async def test_recovery_notification_is_skipped_without_pending_failure(self):
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "account-1"
    live.send_critical_alert = AsyncMock(return_value="sent")

    with patch(
        "XianyuAutoAsync.db_manager.get_critical_alert_state",
        return_value=None,
    ):
        result = await live._send_account_recovery_alert_if_needed()

    self.assertEqual(result, "not_required")
    live.send_critical_alert.assert_not_awaited()

async def test_recovery_notification_is_not_duplicated(self):
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "account-1"
    live.send_critical_alert = AsyncMock(return_value="sent")
    states = {
        "captcha_manual_required": {"last_sent_at": 2000},
        "account_recovery_failed": {"last_sent_at": 2100},
        "account_recovered": {"last_sent_at": 2100},
    }

    with patch(
        "XianyuAutoAsync.db_manager.get_critical_alert_state",
        side_effect=lambda _cookie_id, key: states.get(key),
    ):
        result = await live._send_account_recovery_alert_if_needed()

    self.assertEqual(result, "already_notified")
    live.send_critical_alert.assert_not_awaited()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3.11 -m unittest -v \
  tests.test_critical_alerts.CriticalAlertBusinessIntegrationTests.test_pending_manual_alert_sends_account_recovered_notification \
  tests.test_critical_alerts.CriticalAlertBusinessIntegrationTests.test_recovery_notification_is_skipped_without_pending_failure \
  tests.test_critical_alerts.CriticalAlertBusinessIntegrationTests.test_recovery_notification_is_not_duplicated
```

Expected: all three tests fail with `AttributeError` because `_send_account_recovery_alert_if_needed` does not exist.

- [ ] **Step 3: Implement the minimal recovery-decision helper**

Add after `_send_manual_captcha_critical_alert` in `XianyuLive`:

```python
async def _send_account_recovery_alert_if_needed(self) -> str:
    """Close a previously delivered manual-recovery alert after a confirmed reconnect."""
    try:
        failure_states = [
            db_manager.get_critical_alert_state(
                self.cookie_id,
                "captcha_manual_required",
            ),
            db_manager.get_critical_alert_state(
                self.cookie_id,
                "account_recovery_failed",
            ),
        ]
        last_failure_at = max(
            (float((state or {}).get("last_sent_at") or 0) for state in failure_states),
            default=0,
        )
        if last_failure_at <= 0:
            return "not_required"

        recovery_state = db_manager.get_critical_alert_state(
            self.cookie_id,
            "account_recovered",
        )
        last_recovered_at = float(
            (recovery_state or {}).get("last_sent_at") or 0
        )
        if last_recovered_at >= last_failure_at:
            return "already_notified"

        content = (
            f"账号ID：{self.cookie_id}\n"
            "恢复类型：风控/验证码自动恢复\n"
            "恢复结果：Token 与消息连接均已恢复\n"
            f"恢复时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            "账号已恢复正常，无需人工处理先前告警。"
        )
        return await self.send_critical_alert(
            "account_recovered",
            "闲鱼账号已自动恢复",
            content,
            cooldown_seconds=0,
        )
    except Exception as recovery_alert_error:
        logger.warning(
            f"【{self.cookie_id}】检查或发送账号恢复通知失败: "
            f"{self._safe_str(recovery_alert_error)}"
        )
        return "error"
```

- [ ] **Step 4: Run focused and module tests and verify GREEN**

Run:

```bash
python3.11 -m unittest -v tests.test_critical_alerts
```

Expected: every critical-alert test passes.

- [ ] **Step 5: Commit the decision helper**

```bash
git add XianyuAutoAsync.py tests/test_critical_alerts.py
git commit -m "Notify operators when account recovery completes"
```

### Task 2: Trigger Recovery Notification Only After Confirmed Connection

**Files:**
- Modify: `tests/test_critical_alerts.py`
- Modify: `XianyuAutoAsync.py:9302-9305`

- [ ] **Step 1: Write a failing connection-ready test**

Add to `CriticalAlertBusinessIntegrationTests`:

```python
async def test_connection_ready_marks_connected_before_recovery_notification(self):
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "account-1"
    live.connection_state = ConnectionState.CONNECTING
    live.connection_failures = 3
    live.last_successful_connection = 0
    observed_states = []

    async def notify_after_ready():
        observed_states.append(live.connection_state)
        return "sent"

    live._send_account_recovery_alert_if_needed = AsyncMock(
        side_effect=notify_after_ready
    )

    with patch("XianyuAutoAsync.time.time", return_value=1234):
        await live._mark_connection_ready()

    self.assertEqual(observed_states, [ConnectionState.CONNECTED])
    self.assertEqual(live.connection_failures, 0)
    self.assertEqual(live.last_successful_connection, 1234)
```

Also add `ConnectionState` to the existing import from `XianyuAutoAsync`.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3.11 -m unittest -v \
  tests.test_critical_alerts.CriticalAlertBusinessIntegrationTests.test_connection_ready_marks_connected_before_recovery_notification
```

Expected: FAIL with `AttributeError` because `_mark_connection_ready` does not exist.

- [ ] **Step 3: Implement and wire the connection-ready helper**

Add near `_set_connection_state`:

```python
async def _mark_connection_ready(self):
    """Record a fully initialized connection before closing pending alerts."""
    self._set_connection_state(
        ConnectionState.CONNECTED,
        "初始化完成，连接就绪",
    )
    self.connection_failures = 0
    self.last_successful_connection = time.time()
    await self._send_account_recovery_alert_if_needed()
```

Replace the three statements after `await self.init(websocket)` with:

```python
await self._mark_connection_ready()
```

- [ ] **Step 4: Run the critical-alert and cookie-safety tests**

Run:

```bash
python3.11 -m unittest -v \
  tests.test_critical_alerts \
  tests.test_cookie_refresh_safety
```

Expected: all tests pass. Recovery notification errors remain contained by `_send_account_recovery_alert_if_needed`, so connection setup is not interrupted.

- [ ] **Step 5: Commit the connection hook**

```bash
git add XianyuAutoAsync.py tests/test_critical_alerts.py
git commit -m "Close recovery alerts after connection initialization"
```

### Task 3: Regression Verification

**Files:**
- Verify: `XianyuAutoAsync.py`
- Verify: `tests/test_critical_alerts.py`
- Verify: `db_manager.py`
- Verify: `utils/critical_alerts.py`

- [ ] **Step 1: Run syntax and focused regression checks**

Run:

```bash
python3.11 -m py_compile XianyuAutoAsync.py utils/critical_alerts.py db_manager.py
python3.11 -m unittest -v \
  tests.test_critical_alerts \
  tests.test_cookie_refresh_safety \
  tests.test_manual_recovery
```

Expected: compilation succeeds and all selected tests pass.

- [ ] **Step 2: Run the full test suite**

Run:

```bash
python3.11 -m unittest discover -s tests -v
```

Expected: all discovered tests pass. Any pre-existing unrelated failure must be reported separately and must not be hidden by changing unrelated code.

- [ ] **Step 3: Review the final diff**

Run:

```bash
git diff --check HEAD~2..HEAD
git status --short
```

Confirm only the intended recovery-notification code and tests were committed; preserve the user's pre-existing worktree changes.

## Self-Review

- Spec coverage: both target failure keys, cross-restart timestamp ordering, one notification per incident, confirmed Token/WebSocket recovery, retry after failed PushPlus delivery, safe content, and normal-reconnect suppression are covered.
- Placeholder scan: no `TBD`, `TODO`, “implement later”, or unspecified test step remains.
- Type consistency: production code and tests use `_send_account_recovery_alert_if_needed`, `_mark_connection_ready`, `account_recovered`, `not_required`, and `already_notified` consistently.
- Scope check: no database migration, frontend change, retry-policy change, or additional notification channel is included.
