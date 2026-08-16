import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
BROWSER_DATA_DIR = str((ROOT / "browser_data").resolve())


class DockerVNCConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = {
            "PATH": os.environ["PATH"],
            "HOME": os.environ.get("HOME", str(ROOT)),
            "ENABLE_VNC": "false",
            "VNC_PORT": "5900",
            "VNC_SCREEN": "1980x1024x24",
            "VNC_PASSWORD": "",
            "WEB_PORT": "8080",
        }
        completed = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            env=env,
            text=True,
        )
        cls.compose_config = json.loads(completed.stdout)
        cls.xianyu_app = cls.compose_config["services"]["xianyu-app"]

    def test_xianyu_app_publishes_vnc_on_loopback(self):
        ports = self.xianyu_app.get("ports", [])
        vnc_port = next(
            (
                port
                for port in ports
                if port.get("target") == 5900 and port.get("protocol") == "tcp"
            ),
            None,
        )

        self.assertIsNotNone(vnc_port, "xianyu-app must publish 5900/tcp")
        self.assertEqual(vnc_port.get("host_ip"), "127.0.0.1")
        self.assertEqual(vnc_port.get("published"), "5900")

    def test_xianyu_app_exposes_required_vnc_environment(self):
        environment = self.xianyu_app.get("environment", {})
        self.assertEqual(environment.get("ENABLE_VNC"), "false")
        self.assertEqual(environment.get("VNC_SCREEN"), "1980x1024x24")
        self.assertEqual(environment.get("VNC_PORT"), "5900")
        self.assertEqual(environment.get("VNC_PASSWORD"), "")

    def test_xianyu_app_mounts_browser_data_bind_volume(self):
        volumes = self.xianyu_app.get("volumes", [])
        browser_data = next(
            (volume for volume in volumes if volume.get("target") == "/app/browser_data"),
            None,
        )

        self.assertIsNotNone(
            browser_data,
            "xianyu-app must bind-mount /app/browser_data",
        )
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
