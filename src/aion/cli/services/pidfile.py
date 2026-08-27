"""兜底服务管理 — PidfileManager 通过 PID 文件 + 直接进程管理控制 Gateway。

用于没有 launchd 或 systemd 的平台（如 Windows、容器环境）。"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

from aion.core.constants import AION_HOME as AION_CONFIG_DIR
from .base import ServiceManager

PID_FILE = AION_CONFIG_DIR / "gateway.pid"


def _is_process_running(pid: int) -> bool:
    try:
        if hasattr(os, "kill"):
            os.kill(pid, 0)
        return True
    except OSError:
        return False


class PidfileManager(ServiceManager):
    """Fallback service manager using PID file + direct process management.

    Used on platforms without launchd or systemd (e.g. Windows, containers).
    """

    PID_FILE: ClassVar[Path] = PID_FILE

    async def start(self) -> bool:
        """Start gateway as a background subprocess.

        Note: On Windows this starts a new console window.  For proper
        daemon/service behaviour use platform-specific solutions.
        """
        proc = subprocess.Popen(
            [sys.executable, "-m", "aion", "run"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(sys.platform != "win32"),
        )
        PID_FILE.write_text(str(proc.pid))
        time.sleep(1)
        return _is_process_running(proc.pid)

    async def stop(self, pids: set[int]) -> bool:
        """taskkill (graceful then force) on Windows; SIGTERM then SIGKILL on POSIX."""
        if not pids:
            return False

        for attempt in range(3):
            running = sorted(p for p in pids if _is_process_running(p))
            if not running:
                break

            for pid in running:
                try:
                    # Windows: taskkill (graceful). POSIX: SIGTERM.
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
                    else:
                        os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass

            time.sleep(0.5)

            still = [p for p in running if _is_process_running(p)]
            if still:
                for pid in still:
                    try:
                        if sys.platform == "win32":
                            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                        elif hasattr(signal, "SIGKILL"):
                            os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                time.sleep(0.5)

        PID_FILE.unlink(missing_ok=True)
        return not any(_is_process_running(p) for p in pids)

    def find_pids(self) -> set[int]:
        pids: set[int] = set()

        # PID file
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                if _is_process_running(pid):
                    pids.add(pid)
            except (ValueError, OSError):
                pass

        # pgrep (Unix); Windows falls through if no pidfile match
        if sys.platform != "win32":
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
