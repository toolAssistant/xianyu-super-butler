# Manual Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-account `人工恢复` workflow that opens the account's real Docker Chromium in local noVNC, validates recovered cookies, restarts the account, and keeps all operator alerts link-free.

**Architecture:** A focused in-process `ManualRecoveryManager` owns one asynchronous recovery session per account and exposes only non-sensitive status snapshots. Runtime operations are injected through an adapter that uses the existing `XianyuLive` token probe, Playwright persistent contexts under `browser_data/user_{cookie_id}`, the database, and `CookieManager`; FastAPI routes enforce ownership before calling the manager, while the React account card opens noVNC and polls the session.

**Tech Stack:** Python 3.11, FastAPI, asyncio, Playwright async API, unittest, React 19, TypeScript, Vite, Docker Compose, noVNC.

---

## File Structure

- Create `utils/manual_recovery.py`: session state model, browser runtime adapter, and concurrency-safe session manager.
- Create `tests/test_manual_recovery.py`: manager, runtime, timeout, cancellation, cookie-preservation, and success-path regression tests.
- Modify `XianyuAutoAsync.py`: make risk detection capture the raw URL in memory, stop entering the old internal manual-control flow automatically, and emit link-free operator instructions.
- Modify `utils/critical_alerts.py`: strip URLs from every critical alert as a final safety boundary.
- Modify `tests/test_critical_alerts.py`: lock link-free notification behavior and the new deferred manual-recovery behavior.
- Modify `reply_server.py`: instantiate the manager, enforce account access, and add POST/GET/DELETE endpoints.
- Modify `frontend/types.ts`: define recovery stages and response types.
- Modify `frontend/services/api.ts`: add typed recovery API calls.
- Modify `frontend/components/AccountList.tsx`: add the per-account action, status display, noVNC popup handoff, polling, and cleanup.
- Modify `tests/test_entrypoint_vnc.py` only if needed to keep the already-fixed umask inheritance regression covered.

### Task 1: Make Operator Alerts Link-Free

**Files:**
- Modify: `tests/test_critical_alerts.py`
- Modify: `utils/critical_alerts.py`
- Modify: `XianyuAutoAsync.py`

- [ ] **Step 1: Write failing tests for URL removal and deferred manual recovery**

Update the sanitizer regression to assert that `http://`, `https://`, `/api/captcha/control/`, and raw risk query values are absent. Replace the old control-link assertion with:

```python
async def test_manual_captcha_alert_points_to_management_platform_without_link(self):
    live = XianyuLive.__new__(XianyuLive)
    live.cookie_id = "account-1"
    live.send_critical_alert = AsyncMock(return_value="sent")

    await live._send_manual_captcha_critical_alert("风控验证")

    content = live.send_critical_alert.await_args.args[2]
    self.assertIn("部署机管理平台", content)
    self.assertNotIn("http://", content)
    self.assertNotIn("https://", content)
    self.assertNotIn("/api/captcha/control/", content)
```

Add a test proving `_handle_captcha_verification` records `last_captcha_verification_url`, sends the plain alert after password recovery fails, and does not call `_handle_manual_captcha_verification`.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `python3.11 -m unittest -v tests.test_critical_alerts`

Expected: failures because URLs are currently retained and the old automatic internal control flow is still called.

- [ ] **Step 3: Implement the minimal alert boundary**

Extend `sanitize_alert_text()` with a compiled URL/control-path expression and replace matches with `[LINK REMOVED]`. Change `_send_manual_captcha_critical_alert(reason)` to include only account, failure type, reason, local time, and `请到部署机管理平台点击人工恢复`. In `_handle_captcha_verification`, keep the latest raw risk URL only on `self.last_captcha_verification_url`, call the link-free alert, and return without calling the old internal controller. Remove the `verification_url` branch from `send_token_refresh_notification` so legacy notification channels also receive the management-platform instruction rather than a URL.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run: `python3.11 -m unittest -v tests.test_critical_alerts`

Expected: all critical-alert tests pass and no asserted notification contains a link.

### Task 2: Build the Recovery State Machine Test-First

**Files:**
- Create: `tests/test_manual_recovery.py`
- Create: `utils/manual_recovery.py`

- [ ] **Step 1: Write failing manager contract tests**

Define fake runtime operations and tests for:

```python
class ManualRecoveryManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_connected_account_returns_noop_without_browser(self): ...
    async def test_duplicate_start_reuses_active_session(self): ...
    async def test_latest_risk_url_is_used_before_controlled_probe(self): ...
    async def test_controlled_probe_supplies_missing_risk_url(self): ...
    async def test_invalid_candidate_never_persists_cookie(self): ...
    async def test_valid_candidate_persists_then_restarts(self): ...
    async def test_timeout_closes_browser_and_preserves_cookie(self): ...
    async def test_cancel_closes_browser_and_marks_cancelled(self): ...
    async def test_snapshot_never_exposes_cookie_or_risk_url(self): ...
```

Use injected `now`, `sleep`, browser opener, cookie reader, token probe, persistence, restart, and connectivity functions so no test launches Chromium or accesses the network.

- [ ] **Step 2: Run the new tests and confirm RED**

Run: `python3.11 -m unittest -v tests.test_manual_recovery`

Expected: import failure because `utils.manual_recovery` does not exist.

- [ ] **Step 3: Implement the minimal session model and manager**

Create:

```python
ACTIVE_STAGES = {"starting", "waiting_for_operator", "validating", "reconnecting"}
TERMINAL_STAGES = {"success", "failed", "timed_out", "cancelled"}

@dataclass
class ManualRecoverySession:
    cookie_id: str
    owner_user_id: int
    status: str
    message: str
    created_at: float
    expires_at: float
    updated_at: float
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    browser: Any = field(default=None, repr=False)
```

`ManualRecoveryManager.start()` must lock by account, return an existing active session, reject a truly connected account as a `noop`, create exactly one task otherwise, and return a snapshot containing only `cookie_id`, `status`, `message`, timestamps, `active`, and the configured `novnc_url`. `_run()` resolves the risk URL, opens the browser, polls candidate cookies at low frequency, validates before persistence, persists before restart, and always closes browser resources. `cancel()` cancels the task, closes resources through `finally`, and records `cancelled`. Timeouts record `timed_out` without persistence.

- [ ] **Step 4: Run manager tests and confirm GREEN**

Run: `python3.11 -m unittest -v tests.test_manual_recovery`

Expected: all manager contract tests pass.

### Task 3: Connect the Manager to the Existing Runtime

**Files:**
- Modify: `tests/test_manual_recovery.py`
- Modify: `utils/manual_recovery.py`
- Modify: `XianyuAutoAsync.py`

- [ ] **Step 1: Add failing runtime-adapter tests**

Cover browser cookie serialization/merge, persistent profile path `browser_data/user_{cookie_id}`, headed launch (`headless=False`), `DISPLAY`/`ENABLE_VNC` precondition failures, controlled probe URL capture, and database preservation when validation fails.

- [ ] **Step 2: Run adapter tests and confirm RED**

Run: `python3.11 -m unittest -v tests.test_manual_recovery`

Expected: failures because the concrete runtime adapter and risk URL capture are missing.

- [ ] **Step 3: Implement the Playwright/runtime adapter**

Use `playwright.async_api.async_playwright` and `launch_persistent_context()` with:

```python
user_data_dir = Path(os.getenv("BROWSER_DATA_DIR", "browser_data")) / f"user_{cookie_id}"
context = await playwright.chromium.launch_persistent_context(
    str(user_data_dir), headless=False, args=BROWSER_ARGS, viewport={"width": 1980, "height": 1024}
)
```

Seed the context with the saved account Cookie, navigate to the in-memory risk URL, extract all context cookies into a candidate string, and validate with `XianyuLive._probe_token_with_cookie(candidate, "人工恢复Cookie")`. Enhance `_probe_token_with_cookie` to capture `res_json.data.url` in `last_captcha_verification_url` on risk responses without persisting response cookies. Only after a successful probe call `db_manager.update_cookie_account_info(cookie_id, cookie_value=validated_cookie)` and `cookie_manager.manager.update_cookie(cookie_id, validated_cookie, save_to_db=False)`.

- [ ] **Step 4: Run adapter tests and confirm GREEN**

Run: `python3.11 -m unittest -v tests.test_manual_recovery tests.test_critical_alerts`

Expected: all recovery and alert tests pass.

### Task 4: Add Authenticated Recovery APIs

**Files:**
- Modify: `tests/test_manual_recovery.py`
- Modify: `reply_server.py`

- [ ] **Step 1: Write failing API/authorization tests**

Exercise route functions with injected current-user dictionaries and a fake manager. Cover own-account access, other-user 403, admin access to any existing account, missing account 404, duplicate start, normal-account no-op, status retrieval, and cancellation. Assert responses never include `risk_url`, `verification_url`, `cookie`, `password`, or browser objects.

- [ ] **Step 2: Run the API tests and confirm RED**

Run: `python3.11 -m unittest -v tests.test_manual_recovery`

Expected: failures because the routes do not exist.

- [ ] **Step 3: Implement access helper and three routes**

Add an `_require_cookie_access(cid, current_user)` helper that uses `db_manager.get_cookie_details(cid)`, returns 404 for missing accounts, permits admins, and otherwise compares `details["user_id"]` with `current_user["user_id"]`. Add:

```python
@app.post("/cookies/{cid}/manual-recovery")
async def start_manual_recovery(cid: str, current_user=Depends(get_current_user)): ...

@app.get("/cookies/{cid}/manual-recovery")
async def get_manual_recovery(cid: str, current_user=Depends(get_current_user)): ...

@app.delete("/cookies/{cid}/manual-recovery")
async def cancel_manual_recovery(cid: str, current_user=Depends(get_current_user)): ...
```

Return `当前连接正常，无需人工恢复` for the no-op path, the fixed local noVNC URL only from the authenticated POST response, and 404 when no recovery session exists on GET/DELETE.

- [ ] **Step 4: Run API and backend regressions**

Run: `python3.11 -m unittest -v tests.test_manual_recovery tests.test_critical_alerts tests.test_captcha_remote_manual tests.test_local_captcha_browser`

Expected: all tests pass.

### Task 5: Add the Account-Card Recovery Interaction

**Files:**
- Modify: `frontend/types.ts`
- Modify: `frontend/services/api.ts`
- Modify: `frontend/components/AccountList.tsx`

- [ ] **Step 1: Add typed API contracts**

Define:

```typescript
export type ManualRecoveryStatus =
  | 'starting' | 'waiting_for_operator' | 'validating' | 'reconnecting'
  | 'success' | 'failed' | 'timed_out' | 'cancelled' | 'noop';

export interface ManualRecoveryResponse {
  cookie_id: string;
  status: ManualRecoveryStatus;
  message: string;
  active: boolean;
  novnc_url?: string;
  created_at?: number;
  expires_at?: number;
  updated_at?: number;
}
```

Add `startManualRecovery(id)`, `getManualRecovery(id)`, and `cancelManualRecovery(id)` to `frontend/services/api.ts`.

- [ ] **Step 2: Implement popup-safe start and polling**

In the click handler synchronously call `window.open('about:blank', '_blank')`, then await POST. Close the blank tab on `noop` or error; otherwise assign `popup.location.href = response.novnc_url`. Track per-account status in `Record<string, ManualRecoveryResponse>`, disable only the active account's button, and poll GET every 2 seconds. Stop polling on all terminal states, refresh accounts on `success`, and clear every timer on component unmount.

- [ ] **Step 3: Render feature-complete account status**

Add a `TriangleAlert` icon plus `人工恢复` label in the account action group. Display compact status text below account metadata for all stages; show backend errors and permit retry for `failed`, `timed_out`, and `cancelled`. Keep button and status text within the existing responsive card layout without nested cards.

- [ ] **Step 4: Verify TypeScript production build**

Run: `pnpm --dir frontend build`

Expected: Vite production build exits 0 with no TypeScript errors.

### Task 6: Full Verification, Deployment, Commit, and Push

**Files:**
- Verify: all files above
- Verify: `docker-compose.yml`
- Verify: `docker-compose.vnc.yml`
- Verify: `entrypoint.sh`

- [ ] **Step 1: Run full automated verification**

Run:

```bash
python3.11 -m unittest discover -s tests -v
python3.11 -m py_compile reply_server.py XianyuAutoAsync.py utils/manual_recovery.py utils/critical_alerts.py
sh -n entrypoint.sh
docker compose -f docker-compose.yml config
docker compose -f docker-compose.yml -f docker-compose.vnc.yml config
pnpm --dir frontend build
```

Expected: all commands exit 0; Compose publishes VNC/noVNC only on `127.0.0.1`, mounts `browser_data`, and keeps the file-backed VNC secret.

- [ ] **Step 2: Rebuild and redeploy the live container**

Run: `docker compose -f docker-compose.yml -f docker-compose.vnc.yml up -d --build`

Expected: the `xianyu-app` service is recreated and becomes healthy with ports 8080, 5900, and 6080 bound to loopback as configured.

- [ ] **Step 3: Perform runtime smoke checks**

Verify `/health`, authenticated management UI loading, noVNC loading at `http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale`, current account status, and container logs for startup errors. On a connected account, POST must return `noop` without launching Chromium; avoid deliberately triggering a new platform risk event during smoke testing.

- [ ] **Step 4: Review the final diff against the design**

Confirm every acceptance item in `docs/superpowers/specs/2026-08-17-manual-recovery-design.md`, scan all API payloads and notifications for raw links/secrets, and confirm failed/timeout/cancel paths never call persistence or restart.

- [ ] **Step 5: Commit using the Lore protocol and push main**

Stage only intended files and create an intent-first commit with `Constraint`, `Rejected`, `Confidence`, `Scope-risk`, `Directive`, `Tested`, and `Not-tested` trailers. Push `main` to `origin/main` and verify the remote branch contains both the design/plan commit and implementation commit.

## Self-Review

- Spec coverage: every backend state, permission rule, duplicate/no-op behavior, URL source, headed persistent browser, noVNC handoff, 15-minute timeout, cancellation, cookie preservation, restart, frontend polling, and link-free alert requirement maps to a task and test above.
- Placeholder scan: no `TBD`, `TODO`, `implement later`, or unspecified “write tests” step remains.
- Type consistency: backend and frontend use `cookie_id`, `status`, `message`, `active`, `novnc_url`, `created_at`, `expires_at`, and `updated_at`; stage names match the approved design plus the explicit `noop` response.
- Dependency check: implementation uses existing Python/React/Playwright/lucide packages only.
