# Docker Browser VNC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the headed Chromium session used by Docker Playwright through a localhost-only VNC connection so Xianyu verification cookies remain in the recovery context.

**Architecture:** The application entrypoint conditionally starts Xvfb, Fluxbox, and x11vnc before launching Python. Compose publishes VNC only on host loopback and persists Playwright profile data; the existing per-account `show_browser` flag selects headed Chromium.

**Tech Stack:** POSIX shell, Docker Compose, Xvfb, Fluxbox, x11vnc, Python `unittest`, Playwright Chromium

---

### Task 1: Lock Entrypoint Behavior With Tests

**Files:**
- Create: `tests/test_entrypoint_vnc.py`
- Test: `entrypoint.sh`

- [ ] **Step 1: Write the failing disabled-mode test**

Create a temporary command directory containing stubs for `python`, `Xvfb`,
`fluxbox`, and `x11vnc`. Run `entrypoint.sh` with `ENABLE_VNC=false` and assert
that only `python Start.py` is called.

```python
def test_vnc_disabled_starts_only_application(self):
    result, calls = self.run_entrypoint(enable_vnc="false")
    self.assertEqual(result.returncode, 0)
    self.assertEqual(calls, ["python Start.py"])
```

- [ ] **Step 2: Write the failing enabled-mode test**

Run the same harness with `ENABLE_VNC=true`, `DISPLAY=:199`, and a zero startup
delay. Assert that the virtual display, window manager, VNC server, and Python
application all start with the expected arguments.

```python
def test_vnc_enabled_starts_local_display_stack(self):
    result, calls = self.run_entrypoint(enable_vnc="true")
    self.assertEqual(result.returncode, 0)
    self.assertIn("Xvfb :199 -screen 0 1980x1024x24 -ac +extension RANDR", calls)
    self.assertIn("fluxbox", calls)
    self.assertIn("x11vnc -display :199 -forever -shared -nopw -rfbport 5900 -listen 0.0.0.0", calls)
    self.assertEqual(calls[-1], "python Start.py")
```

- [ ] **Step 3: Write the failing dependency test**

Omit the `x11vnc` stub and assert that enabled mode exits non-zero without
starting Python.

```python
def test_vnc_enabled_fails_when_dependency_is_missing(self):
    result, calls = self.run_entrypoint(enable_vnc="true", omit={"x11vnc"})
    self.assertNotEqual(result.returncode, 0)
    self.assertNotIn("python Start.py", calls)
    self.assertIn("required command not found: x11vnc", result.stderr)
```

- [ ] **Step 4: Run the tests and verify they fail**

Run: `python3.11 -m unittest tests.test_entrypoint_vnc -v`

Expected: disabled mode passes; enabled and missing-dependency cases fail
because `entrypoint.sh` does not yet manage VNC.

### Task 2: Add Optional VNC Startup

**Files:**
- Modify: `entrypoint.sh:1-6`
- Test: `tests/test_entrypoint_vnc.py`

- [ ] **Step 1: Add command validation and display startup**

Implement a guarded VNC branch before `exec python Start.py`:

```sh
if [ "${ENABLE_VNC:-false}" = "true" ]; then
    for command in Xvfb fluxbox x11vnc; do
        command -v "$command" >/dev/null 2>&1 || {
            echo "required command not found: $command" >&2
            exit 1
        }
    done

    DISPLAY="${DISPLAY:-:99}"
    VNC_SCREEN="${VNC_SCREEN:-1980x1024x24}"
    VNC_PORT="${VNC_PORT:-5900}"
    export DISPLAY

    display_number="${DISPLAY#:}"
    display_number="${display_number%%.*}"
    rm -f "/tmp/.X${display_number}-lock" "/tmp/.X11-unix/X${display_number}"

    Xvfb "$DISPLAY" -screen 0 "$VNC_SCREEN" -ac +extension RANDR \
        >/tmp/xvfb.log 2>&1 &
    xvfb_pid=$!
    sleep "${VNC_STARTUP_WAIT_SECONDS:-1}"
    kill -0 "$xvfb_pid" 2>/dev/null || {
        echo "Xvfb failed to start on $DISPLAY" >&2
        exit 1
    }

    fluxbox >/tmp/fluxbox.log 2>&1 &
    fluxbox_pid=$!
    x11vnc -display "$DISPLAY" -forever -shared -nopw \
        -rfbport "$VNC_PORT" -listen 0.0.0.0 >/tmp/x11vnc.log 2>&1 &
    x11vnc_pid=$!

    sleep "${VNC_STARTUP_WAIT_SECONDS:-1}"
    for process in "$fluxbox_pid" "$x11vnc_pid"; do
        kill -0 "$process" 2>/dev/null || {
            echo "VNC display process failed to start" >&2
            exit 1
        }
    done
fi
```

- [ ] **Step 2: Run the focused tests**

Run: `python3.11 -m unittest tests.test_entrypoint_vnc -v`

Expected: all three tests pass.

- [ ] **Step 3: Check shell syntax**

Run: `sh -n entrypoint.sh`

Expected: exit code 0 with no output.

### Task 3: Wire Localhost-Only Compose Configuration

**Files:**
- Modify: `docker-compose.yml:11-22`
- Modify: `docker-compose.yml:23-66`
- Modify: `Dockerfile:145-146`

- [ ] **Step 1: Publish and persist the browser session**

Add the loopback-only VNC port and browser profile volume:

```yaml
ports:
  - "${WEB_PORT:-8080}:8080"
  - "127.0.0.1:${VNC_PORT:-5900}:5900"
volumes:
  - ./browser_data:/app/browser_data:rw
```

- [ ] **Step 2: Pass VNC configuration to the container**

Add these environment entries:

```yaml
- ENABLE_VNC=${ENABLE_VNC:-false}
- VNC_SCREEN=${VNC_SCREEN:-1980x1024x24}
- VNC_PORT=5900
```

The container port stays 5900 while `${VNC_PORT}` controls only the host port.

- [ ] **Step 3: Document the optional container port**

Change the Dockerfile declaration to `EXPOSE 8080 5900`.

- [ ] **Step 4: Validate Compose expansion**

Run: `ENABLE_VNC=true docker compose config`

Expected: VNC target port 5900 is published with host IP `127.0.0.1`, and
`ENABLE_VNC` resolves to `true`.

### Task 4: Deploy the Headed Account Session

**Files:**
- Modify locally, ignored by Git: `.env`
- Modify operational state: `data/xianyu_data.db`

- [ ] **Step 1: Enable VNC in local deployment configuration**

Set `ENABLE_VNC=true` and `VNC_PORT=5900` in `.env` without changing existing
secrets.

- [ ] **Step 2: Enable headed mode for the target account**

Run:

```sql
UPDATE cookies SET show_browser = 1 WHERE id = '2217118657958';
```

Expected: exactly one row changes and a follow-up query returns
`show_browser = 1`.

- [ ] **Step 3: Run repository checks**

Run:

```bash
python3.11 -m unittest tests.test_entrypoint_vnc -v
sh -n entrypoint.sh
docker compose config
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 4: Rebuild and recreate the application container**

Run: `docker compose up -d --build --force-recreate xianyu-app`

Expected: image builds and the container becomes healthy.

- [ ] **Step 5: Verify the visible browser stack**

Run:

```bash
docker exec xianyu-auto-reply sh -lc \
  'ps aux | grep -E "Xvfb|fluxbox|x11vnc|chromium" | grep -v grep'
nc -z 127.0.0.1 5900
```

Expected: all four processes are present, Chromium lacks `--headless`, and the
local VNC port accepts connections.

- [ ] **Step 6: Open the Docker browser for the operator**

Run: `open 'vnc://127.0.0.1:5900'`

Expected: macOS Screen Sharing displays the active Xianyu browser page.

### Task 5: Verify Recovery and Publish

**Files:**
- No additional source changes expected.

- [ ] **Step 1: Confirm recovery logs**

After the operator completes verification, inspect redacted logs for
`Token` refresh success and `connecting -> connected`.

- [ ] **Step 2: Confirm no new risk record remains active**

Query the latest `risk_control_logs` rows and ensure the current attempt is no
longer the only evidence; WebSocket connection state remains the authoritative
success criterion.

- [ ] **Step 3: Commit implementation with Lore trailers**

Commit only the implementation, tests, Docker configuration, and plan. Record
the loopback security constraint and exact verification performed.

- [ ] **Step 4: Push `main`**

Run: `git push origin main`

Expected: the remote accepts both the design and implementation commits.
