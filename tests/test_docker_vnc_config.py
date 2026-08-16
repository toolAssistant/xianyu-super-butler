import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
VNC_OVERRIDE = ROOT / "docker-compose.vnc.yml"
BROWSER_DATA_DIR = str((ROOT / "browser_data").resolve())
PASSWORD_FILE = str((ROOT / "data" / "vnc_password").resolve())


def load_compose_config(*compose_files: Path) -> dict:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ.get("HOME", str(ROOT)),
        "VNC_HOST_PORT": "5900",
        "VNC_PASSWORD_FILE": PASSWORD_FILE,
        "WEB_PORT": "8080",
    }
    command = ["docker", "compose"]
    for compose_file in compose_files:
        command.extend(["-f", str(compose_file)])
    command.extend(["config", "--format", "json"])
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        env=env,
        text=True,
    )
    return json.loads(completed.stdout)


class DockerVNCConfigTests(unittest.TestCase):
    def test_default_compose_does_not_reserve_a_vnc_port(self):
        config = load_compose_config(ROOT / "docker-compose.yml")
        xianyu_app = config["services"]["xianyu-app"]

        self.assertFalse(
            any(port.get("target") == 5900 for port in xianyu_app.get("ports", []))
        )
        self.assertFalse(
            any(
                volume.get("target") == "/app/browser_data"
                for volume in xianyu_app.get("volumes", [])
            )
        )

    def _load_vnc_app(self) -> tuple[dict, dict]:
        self.assertTrue(VNC_OVERRIDE.is_file(), "VNC compose override must exist")
        config = load_compose_config(ROOT / "docker-compose.yml", VNC_OVERRIDE)
        return config, config["services"]["xianyu-app"]

    def test_vnc_override_publishes_vnc_on_loopback(self):
        _, xianyu_app = self._load_vnc_app()
        vnc_port = next(
            (
                port
                for port in xianyu_app.get("ports", [])
                if port.get("target") == 5900 and port.get("protocol") == "tcp"
            ),
            None,
        )

        self.assertIsNotNone(vnc_port, "xianyu-app must publish 5900/tcp")
        self.assertEqual(vnc_port.get("host_ip"), "127.0.0.1")
        self.assertEqual(vnc_port.get("published"), "5900")

    def test_vnc_override_uses_a_mounted_password_secret(self):
        _, xianyu_app = self._load_vnc_app()
        environment = xianyu_app.get("environment", {})

        self.assertEqual(environment.get("ENABLE_VNC"), "true")
        self.assertEqual(environment.get("VNC_SCREEN"), "1980x1024x24")
        self.assertEqual(environment.get("VNC_PORT"), "5900")
        self.assertEqual(
            environment.get("VNC_PASSWORD_FILE"),
            "/run/secrets/vnc_password",
        )
        self.assertNotIn("VNC_PASSWORD", environment)
        self.assertTrue(
            any(
                secret.get("target") == "vnc_password"
                for secret in xianyu_app.get("secrets", [])
            )
        )

    def test_vnc_override_mounts_browser_data_bind_volume(self):
        _, xianyu_app = self._load_vnc_app()
        browser_data = next(
            (
                volume
                for volume in xianyu_app.get("volumes", [])
                if volume.get("target") == "/app/browser_data"
            ),
            None,
        )

        self.assertIsNotNone(browser_data)
        self.assertEqual(browser_data.get("type"), "bind")
        self.assertEqual(browser_data.get("source"), BROWSER_DATA_DIR)

    def test_dockerfile_exposes_http_and_vnc_ports(self):
        expose_ports: set[str] = set()
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("EXPOSE "):
                expose_ports.update(
                    token.split("/", 1)[0] for token in stripped.split()[1:]
                )

        self.assertIn("8080", expose_ports)
        self.assertIn("5900", expose_ports)


if __name__ == "__main__":
    unittest.main()
