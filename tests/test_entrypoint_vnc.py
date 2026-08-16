import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ENTRYPOINT = Path(__file__).resolve().parents[1] / "entrypoint.sh"


class EntrypointVNCTests(unittest.TestCase):
    def _write_stub(self, bin_dir: Path, command: str):
        stub = bin_dir / command
        lines = [
            "#!/bin/sh",
            "set -eu",
            'if [ "$#" -eq 0 ]; then',
            f'  printf "%s\\n" "{command}" >> "$CALL_LOG"',
            "else",
            f'  printf "%s\\n" "{command} $*" >> "$CALL_LOG"',
            "fi",
        ]
        lines.append("exit 0")
        stub.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stub.chmod(0o755)

    def _run_entrypoint(self, *, enable_vnc: str, available_commands: list[str]):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "entrypoint.log"

            self._write_stub(bin_dir, "mkdir")
            self._write_stub(bin_dir, "python")
            for command in available_commands:
                if command not in {"mkdir", "python"}:
                    self._write_stub(bin_dir, command)

            env = {
                **os.environ,
                "ENABLE_VNC": enable_vnc,
                "CALL_LOG": str(log_path),
                "PATH": str(bin_dir),
            }

            completed = subprocess.run(
                ["/bin/sh", str(ENTRYPOINT)],
                cwd=ENTRYPOINT.parent,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )

            calls = []
            if log_path.exists():
                calls = log_path.read_text(encoding="utf-8").splitlines()

            return completed, [call for call in calls if not call.startswith("mkdir ")]

    def test_disable_vnc_executes_only_python_application(self):
        completed, calls = self._run_entrypoint(
            enable_vnc="false",
            available_commands=["mkdir", "python"],
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(calls, ["python Start.py"])

    def test_enable_vnc_starts_display_stack_before_python(self):
        completed, calls = self._run_entrypoint(
            enable_vnc="true",
            available_commands=["mkdir", "python", "Xvfb", "fluxbox", "x11vnc"],
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            calls,
            [
                "Xvfb :199 -screen 0 1980x1024x24 -ac +extension RANDR",
                "fluxbox",
                "x11vnc -display :199 -forever -shared -nopw -rfbport 5900 -listen 0.0.0.0",
                "python Start.py",
            ],
        )

    def test_enable_vnc_requires_x11vnc_and_skips_python_when_missing(self):
        completed, calls = self._run_entrypoint(
            enable_vnc="true",
            available_commands=["mkdir", "python", "Xvfb", "fluxbox"],
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("required command not found: x11vnc", completed.stderr)
        self.assertNotIn("python Start.py", calls)


if __name__ == "__main__":
    unittest.main()
