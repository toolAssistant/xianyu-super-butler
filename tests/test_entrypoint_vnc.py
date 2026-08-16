import os
import subprocess
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path


ENTRYPOINT = Path(__file__).resolve().parents[1] / "entrypoint.sh"


@dataclass
class EntrypointRunResult:
    completed: subprocess.CompletedProcess[str]
    calls: list[str]
    launched_pids: list[int]
    live_pids: list[int]
    remaining_pid_files: list[str]
    cleanup_survivor_pids: list[int]


class EntrypointVNCTests(unittest.TestCase):
    def _write_script(self, path: Path, content: str):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _write_mkdir_stub(self, bin_dir: Path):
        self._write_script(
            bin_dir / "mkdir",
            """#!/bin/sh
set -eu
if [ "$#" -eq 0 ]; then
  printf "%s\\n" "mkdir" >> "$CALL_LOG"
else
  printf "%s\\n" "mkdir $*" >> "$CALL_LOG"
fi
exit 0
""",
        )

    def _write_passthrough_wrapper(self, bin_dir: Path, command: str, target: str):
        self._write_script(
            bin_dir / command,
            f"""#!/bin/sh
set -eu
exec {target} "$@"
""",
        )

    def _write_service_stub(self, bin_dir: Path, command: str):
        self._write_script(
            bin_dir / command,
            f"""#!/bin/sh
set -eu
pid_file="$PID_DIR/{command}.pid"
if [ "$#" -eq 0 ]; then
  printf "%s\\n" "{command}" >> "$CALL_LOG"
else
  printf "%s\\n" "{command} $*" >> "$CALL_LOG"
fi
printf "%s\\n" "$$" > "$pid_file"
printf "%s\\n" "$$" >> "$PID_DIR/launched-pids"
cleanup() {{
  /bin/rm -f "$pid_file"
}}
trap 'cleanup; exit 0' TERM INT
trap 'cleanup' EXIT
while :; do
  /bin/sleep 1
done
""",
        )

    def _write_python_stub(self, bin_dir: Path):
        self._write_script(
            bin_dir / "python",
            """#!/bin/sh
set -eu
if [ "${ENABLE_VNC}" = "true" ]; then
  pids=""
  for service in Xvfb fluxbox x11vnc; do
    pid_file="$PID_DIR/$service.pid"
    if [ ! -s "$pid_file" ]; then
      printf "%s\\n" "required service not running: $service" >&2
      exit 1
    fi
    IFS= read -r pid < "$pid_file"
    if ! kill -0 "$pid" 2>/dev/null; then
      printf "%s\\n" "required service not running: $service" >&2
      exit 1
    fi
    pids="$pids $pid"
  done
fi
if [ "$#" -eq 0 ]; then
  printf "%s\\n" "python" >> "$CALL_LOG"
else
  printf "%s\\n" "python $*" >> "$CALL_LOG"
fi
if [ "${ENABLE_VNC}" = "true" ]; then
  for pid in $pids; do
    kill "$pid"
  done
  attempts=0
  while [ "$attempts" -lt 50 ]; do
    all_cleared=1
    for service in Xvfb fluxbox x11vnc; do
      pid_file="$PID_DIR/$service.pid"
      if [ -e "$pid_file" ]; then
        all_cleared=0
      fi
    done
    if [ "$all_cleared" -eq 1 ]; then
      break
    fi
    /bin/sleep 0.1
    attempts=$((attempts + 1))
  done
  for service in Xvfb fluxbox x11vnc; do
    pid_file="$PID_DIR/$service.pid"
    if [ -e "$pid_file" ]; then
      printf "%s\\n" "service cleanup incomplete: $service" >&2
      exit 1
    fi
  done
fi
exit 0
""",
        )

    def _read_pid_list(self, pid_dir: Path) -> list[int]:
        launched = pid_dir / "launched-pids"
        if not launched.exists():
            return []
        return [
            int(line)
            for line in launched.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _is_process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def _force_cleanup(self, pid_dir: Path) -> list[int]:
        launched_pids = self._read_pid_list(pid_dir)
        for pid in launched_pids:
            if self._is_process_alive(pid):
                os.kill(pid, 15)
        deadline = time.time() + 2
        while time.time() < deadline:
            live_pids = [pid for pid in launched_pids if self._is_process_alive(pid)]
            if not live_pids:
                break
            time.sleep(0.05)
        for pid in launched_pids:
            if self._is_process_alive(pid):
                os.kill(pid, 9)
        return [pid for pid in launched_pids if self._is_process_alive(pid)]

    def _assert_no_residual_processes(self, result: EntrypointRunResult):
        self.assertEqual(result.live_pids, [])
        self.assertEqual(result.remaining_pid_files, [])
        self.assertEqual(result.cleanup_survivor_pids, [])

    def _run_entrypoint(self, *, enable_vnc: str, available_commands: list[str]) -> EntrypointRunResult:
        timeout_seconds = 3
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bin_dir = temp_path / "bin"
            pid_dir = temp_path / "pids"
            home_dir = temp_path / "home"
            bin_dir.mkdir()
            pid_dir.mkdir()
            home_dir.mkdir()
            log_path = temp_path / "entrypoint.log"

            self._write_mkdir_stub(bin_dir)
            self._write_passthrough_wrapper(bin_dir, "rm", "/bin/rm")
            self._write_passthrough_wrapper(bin_dir, "sleep", "/bin/sleep")
            self._write_python_stub(bin_dir)
            for command in available_commands:
                if command not in {"mkdir", "python", "rm", "sleep"}:
                    self._write_service_stub(bin_dir, command)

            env = {
                "HOME": str(home_dir),
                "LANG": "C.UTF-8",
                "PATH": str(bin_dir),
                "ENABLE_VNC": enable_vnc,
                "DISPLAY": ":199",
                "VNC_SCREEN": "1980x1024x24",
                "VNC_PORT": "5900",
                "VNC_STARTUP_WAIT_SECONDS": "0.1",
                "CALL_LOG": str(log_path),
                "PID_DIR": str(pid_dir),
            }

            try:
                completed = subprocess.run(
                    ["/bin/sh", str(ENTRYPOINT)],
                    cwd=ENTRYPOINT.parent,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                completed = subprocess.CompletedProcess(
                    exc.cmd,
                    124,
                    exc.stdout or "",
                    f"timed out after {timeout_seconds} seconds",
                )

            calls = []
            if log_path.exists():
                calls = log_path.read_text(encoding="utf-8").splitlines()

            launched_pids = self._read_pid_list(pid_dir)
            live_pids = [pid for pid in launched_pids if self._is_process_alive(pid)]
            remaining_pid_files = sorted(path.name for path in pid_dir.glob("*.pid"))
            cleanup_survivor_pids = self._force_cleanup(pid_dir)

            return EntrypointRunResult(
                completed=completed,
                calls=[call for call in calls if not call.startswith("mkdir ")],
                launched_pids=launched_pids,
                live_pids=live_pids,
                remaining_pid_files=remaining_pid_files,
                cleanup_survivor_pids=cleanup_survivor_pids,
            )

    def test_disable_vnc_executes_only_python_application(self):
        result = self._run_entrypoint(
            enable_vnc="false",
            available_commands=["mkdir", "python"],
        )

        self._assert_no_residual_processes(result)
        self.assertEqual(result.completed.returncode, 0, result.completed.stderr)
        self.assertEqual(result.calls, ["python Start.py"])

    def test_enable_vnc_starts_display_stack_before_python(self):
        result = self._run_entrypoint(
            enable_vnc="true",
            available_commands=["mkdir", "python", "rm", "sleep", "Xvfb", "fluxbox", "x11vnc"],
        )

        self._assert_no_residual_processes(result)
        self.assertEqual(result.completed.returncode, 0, result.completed.stderr)
        self.assertEqual(
            result.calls,
            [
                "Xvfb :199 -screen 0 1980x1024x24 -ac +extension RANDR",
                "fluxbox",
                "x11vnc -display :199 -forever -shared -nopw -rfbport 5900 -listen 0.0.0.0",
                "python Start.py",
            ],
        )

    def test_enable_vnc_requires_x11vnc_and_skips_python_when_missing(self):
        result = self._run_entrypoint(
            enable_vnc="true",
            available_commands=["mkdir", "python", "rm", "sleep", "Xvfb", "fluxbox"],
        )

        self._assert_no_residual_processes(result)
        self.assertNotEqual(result.completed.returncode, 0)
        self.assertEqual(result.completed.stderr.strip(), "required command not found: x11vnc")
        self.assertNotIn("python Start.py", result.calls)


if __name__ == "__main__":
    unittest.main()
