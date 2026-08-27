"""Linux 服务管理 — SystemdManager 通过 systemd --user 管理 Gateway 服务。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

from aion.core.constants import AION_HOME as AION_CONFIG_DIR
from aion.log import get_logger

from .base import ServiceManager

logger = get_logger(__name__)

PID_FILE = AION_CONFIG_DIR / "gateway.pid"
SERVICE_NAME = "aion-gateway.service"
SERVICE_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_PATH = SERVICE_DIR / SERVICE_NAME


def _is_process_running(pid: int) -> bool:
    try:
        if hasattr(os, "kill"):
            os.kill(pid, 0)
        return True
    except OSError:
        return False


class SystemdManager(ServiceManager):
    """Linux service manager via systemd --user."""

    PID_FILE: ClassVar[Path] = PID_FILE
    SERVICE_NAME: ClassVar[str] = SERVICE_NAME
    SERVICE_DIR: ClassVar[Path] = SERVICE_DIR
    SERVICE_PATH: ClassVar[Path] = SERVICE_PATH

    # ── Base interface ──────────────────────────────────────────

    async def start(self) -> bool:
        """Write systemd unit and enable/start the service."""
        SERVICE_DIR.mkdir(parents=True, exist_ok=True)
        SERVICE_PATH.write_text(self._generate_unit_content())
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(
            ["systemctl", "--user", "enable", SERVICE_NAME],
            capture_output=True,
        )
        result = subprocess.run(
            ["systemctl", "--user", "start", SERVICE_NAME],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    async def stop(self, pids: set[int]) -> bool:
        """Stop via systemctl, fallback to SIGTERM -> SIGKILL."""
        # Primary: systemctl stop
        subprocess.run(
            ["systemctl", "--user", "stop", SERVICE_NAME],
            capture_output=True,
        )

        # Secondary: kill remaining processes
        if pids:
            for attempt in range(3):
                running = sorted(p for p in pids if _is_process_running(p))
                if not running:
                    break

                for pid in running:
                    try:
                        if hasattr(signal, "SIGTERM"):
                            os.kill(pid, signal.SIGTERM)
                    except OSError:
                        pass

                time.sleep(0.5)

                still = [p for p in running if _is_process_running(p)]
                if still:
                    for pid in still:
                        try:
                            if hasattr(signal, "SIGKILL"):
                                os.kill(pid, signal.SIGKILL)
                        except OSError:
                            pass
                    time.sleep(0.5)

        PID_FILE.unlink(missing_ok=True)
        return not any(_is_process_running(p) for p in pids)

    def find_pids(self) -> set[int]:
        """Discover PIDs from systemctl + pidfile + pgrep."""
        pids: set[int] = set()

        # PID file
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                if _is_process_running(pid):
                    pids.add(pid)
            except (ValueError, OSError):
                pass

        # systemctl show --property MainPID
        try:
            result = subprocess.run(
                ["systemctl", "--user", "show", "--property", "MainPID", SERVICE_NAME],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                val = result.stdout.strip()
                if val.startswith("MainPID="):
                    pid_str = val.split("=", 1)[1].strip()
                    if pid_str.isdigit() and int(pid_str) > 0:
                        pids.add(int(pid_str))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # pgrep fallback
        try:
            result = subprocess.run(
                ["pgrep", "-f", "aion.*run"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    try:
                        pid = int(line.strip())
                        if _is_process_running(pid):
                            pids.add(pid)
                    except ValueError:
                        pass
        except FileNotFoundError:
            pass

        return pids

    def is_running(self) -> bool:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == "active"

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _generate_unit_content() -> str:
        return f"""\
[Unit]
Description=Aion Gateway Service

[Service]
Type=simple
ExecStart={sys.executable} -m aion run
Restart=on-failure
RestartSec=5
Environment=PATH={os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}

[Install]
WantedBy=default.target
"""
