"""aion CLI 进程管理 — 共享工具层（原 macOS 逻辑已迁移到 services/launchd.py）"""

from __future__ import annotations

import os
from pathlib import Path

from ..log import get_logger
from .services.launchd import PID_FILE, PLIST_LABEL, PLIST_PATH, LaunchdManager

__all__ = [
    "PID_FILE",
    "PLIST_LABEL",
    "PLIST_PATH",
    "is_process_running",
    "find_all_gateway_pids",
    "stop_gateway_processes",
    "_find_launchagent_labels",
    "_is_launchagent_loaded",
    "_bootout_launchagent",
]

logger = get_logger(__name__)


def is_process_running(pid: int) -> bool:
    """跨平台：检查进程是否存活（kill(pid, 0)）。"""
    try:
        if hasattr(os, "kill"):
            os.kill(pid, 0)
        return True
    except OSError:
        return False


# ── 向下兼容委托 ──────────────────────────────────
# 以下函数保留供 status.py 等调用方迁移过渡用；
# 新代码请直接使用 create_service_manager()。


def find_all_gateway_pids() -> set[int]:
    """委托到 create_service_manager().find_pids()。"""
    from aion.cli.services import create_service_manager

    return create_service_manager().find_pids()


def stop_gateway_processes(pids: set[int]) -> bool:
    """委托到 create_service_manager().stop()（同步包装）。"""
    import asyncio

    from aion.cli.services import create_service_manager

    return asyncio.run(create_service_manager().stop(pids))


def _find_launchagent_labels() -> list[str]:
    return LaunchdManager()._find_launchagent_labels()


def _is_launchagent_loaded(label: str) -> bool:
    return LaunchdManager()._is_launchagent_loaded(label)


def _bootout_launchagent(label: str, plist_path: Path) -> bool:
    return LaunchdManager()._bootout_launchagent(label)
