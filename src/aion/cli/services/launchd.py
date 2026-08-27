"""macOS 服务管理 — LaunchdManager 通过 launchctl + LaunchAgent plist 管理 Gateway 服务。"""

from __future__ import annotations

import os
import shutil
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
PLIST_LABEL = "com.user.aion.gateway"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _is_process_running(pid: int) -> bool:
    """Check if a process is alive via kill(pid, 0).

    NOTE: This duplicates aion.cli.process.is_process_running to avoid
    a circular import (process.py imports from launchd.py).
    """
    try:
        if hasattr(os, "kill"):
            os.kill(pid, 0)
        return True
    except OSError:
        return False


class LaunchdManager(ServiceManager):
    """macOS service manager via launchctl + LaunchAgent plist."""

    PID_FILE: ClassVar[Path] = PID_FILE
    PLIST_LABEL: ClassVar[str] = PLIST_LABEL
    PLIST_PATH: ClassVar[Path] = PLIST_PATH

    # ── Base interface ──────────────────────────────────────────

    async def start(self) -> bool:
        """Write plist and bootstrap via launchctl."""
        import json
        import plistlib

        config_path = AION_CONFIG_DIR / "aion.json"
        with open(config_path) as f:
            config = json.load(f)
        port = config.get("gateway", {}).get("port", 19527)
        logfile = str(AION_CONFIG_DIR / f"gateway-{port}.log")

        # 优先使用 pip --user 安装版 aion，再试 PATH，最后 fallback 到 python -m aion
        _local_bin = Path.home() / ".local" / "bin" / "aion"
        _which_aion = shutil.which("aion")
        if _local_bin.is_file():
            program_args = [str(_local_bin), "run"]
        elif _which_aion:
            program_args = [_which_aion, "run"]
        else:
            program_args = [sys.executable, "-m", "aion", "run"]

        plist = {
            "Label": PLIST_LABEL,
            "ProgramArguments": program_args,
            "RunAtLoad": True,
            "KeepAlive": False,
            "StandardOutPath": logfile,
            "StandardErrorPath": logfile,
            "WorkingDirectory": str(AION_CONFIG_DIR),
            "EnvironmentVariables": {
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            },
        }
        os.makedirs(os.path.dirname(PLIST_PATH), exist_ok=True)
        with open(PLIST_PATH, "wb") as f:
            plistlib.dump(plist, f)

        # Check if already bootstrapped
        is_loaded = (
            subprocess.run(
                ["launchctl", "list", PLIST_LABEL],
                capture_output=True,
                text=True,
            ).returncode
            == 0
        )

        if is_loaded:
            uid = os.getuid() if hasattr(os, "getuid") else 0
            subprocess.run(
                ["launchctl", "kickstart", f"gui/{uid}/{PLIST_LABEL}"],
                capture_output=True,
                text=True,
            )
            return True
        else:
            uid = os.getuid() if hasattr(os, "getuid") else 0
            result = subprocess.run(
                ["launchctl", "bootstrap", f"gui/{uid}", str(PLIST_PATH)],
                capture_output=True,
                text=True,
            )
            return result.returncode == 0

    async def stop(self, pids: set[int]) -> bool:
        """Bootout launchd services, then SIGTERM -> SIGKILL processes."""
        if not pids:
            return False

        # 1. Bootout all aion-related launchd service definitions
        bootout_ok = True
        for lb in self._find_launchagent_labels():
            ok = self._bootout_launchagent(lb)
            bootout_ok = bootout_ok and ok

        # 2. Loop: kill + verify (max 3 attempts, handle KeepAlive restart)
        for attempt in range(3):
            running = sorted(p for p in pids if _is_process_running(p))

            if not running:
                loaded = self._find_launchagent_labels()
                if not loaded:
                    break
                for lb in loaded:
                    self._bootout_launchagent(lb)
                time.sleep(1)
                continue

            # SIGTERM
            for pid in running:
                try:
                    if hasattr(signal, "SIGTERM"):
                        os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass

            # Wait
            for _ in range(100 if attempt == 0 else 50):
                still = [p for p in running if _is_process_running(p)]
                if not still:
                    break
                time.sleep(0.1)

            still_running = [p for p in running if _is_process_running(p)]
            if still_running:
                for pid in still_running:
                    try:
                        if hasattr(signal, "SIGKILL"):
                            os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                time.sleep(1)

            time.sleep(1)

        PID_FILE.unlink(missing_ok=True)
        return not any(_is_process_running(p) for p in pids)

    def find_pids(self) -> set[int]:
        """Discover running gateway PIDs from pidfile + launchctl + pgrep."""
        pids: set[int] = set()

        # PID file
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                if _is_process_running(pid):
                    pids.add(pid)
            except (ValueError, OSError):
                pass

        # launchctl list
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.strip().split("\n"):
            if PLIST_LABEL in line:
                parts = line.split()
                if parts and parts[0].isdigit():
                    pid = int(parts[0])
                    if pid != 0 and _is_process_running(pid):
                        pids.add(pid)

        # pgrep (Unix fallback)
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
        return bool(self.find_pids())

    # ── macOS-specific helpers ──────────────────────────────────

    def _find_launchagent_labels(self) -> list[str]:
        if sys.platform != "darwin":
            return []
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
        )
        labels: list[str] = []
        for line in result.stdout.strip().split("\n"):
            parts = line.strip().split()
            if len(parts) >= 3:
                label = parts[-1]
                if "aion" in label.lower() or "gateway" in label.lower():
                    labels.append(label)
        return labels

    def _is_launchagent_loaded(self, label: str) -> bool:
        if sys.platform != "darwin":
            return False
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _bootout_launchagent(self, label: str) -> bool:
        if sys.platform != "darwin":
            return False
        if not self._is_launchagent_loaded(label):
            return True
        uid = os.getuid() if hasattr(os, "getuid") else 0

        # Try by label first, fallback to plist path
        result = subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}/{label}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True

        if PLIST_PATH.exists():
            logger.warning("launchctl bootout by label '%s' failed, retrying with plist", label)
            result = subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(PLIST_PATH)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return True

        logger.warning(
            "launchctl bootout failed for label '%s' (retcode=%d): %s",
            label,
            result.returncode,
            result.stderr.strip(),
        )
        return False
