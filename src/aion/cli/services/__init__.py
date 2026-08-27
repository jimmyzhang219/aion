"""src/aion/cli/services/__init__.py"""

import shutil
import sys

from .base import ServiceManager


def create_service_manager() -> ServiceManager:
    """Return the platform-appropriate ServiceManager.

    Resolution order:
      1. macOS  -> LaunchdManager
      2. Linux  -> SystemdManager (if systemctl available)
      3. others -> PidfileManager
    """
    if sys.platform == "darwin":
        from .launchd import LaunchdManager  # type: ignore[import-not-found]

        return LaunchdManager()
    elif sys.platform == "linux" and shutil.which("systemctl"):
        from .systemd import SystemdManager  # type: ignore[import-not-found]

        return SystemdManager()
    else:
        from .pidfile import PidfileManager  # type: ignore[import-not-found]

        return PidfileManager()


__all__ = ["ServiceManager", "create_service_manager"]
