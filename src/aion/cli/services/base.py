"""服务管理抽象层 — 平台无关的 ServiceManager 基类与接口定义。"""

from __future__ import annotations

from abc import ABC, abstractmethod


class ServiceManager(ABC):
    """Service manager abstraction — platform-specific start/stop/find.

    Subclasses implement the concrete logic for launchd (macOS),
    systemd (Linux), or pidfile (fallback / Windows).
    """

    @abstractmethod
    async def start(self) -> bool:
        """Start the gateway service."""
        ...

    @abstractmethod
    async def stop(self, pids: set[int]) -> bool:
        """Stop gateway processes by PID set + clean up service definition."""
        ...

    @abstractmethod
    def find_pids(self) -> set[int]:
        """Discover running gateway PIDs from all available sources."""
        ...

    @abstractmethod
    def is_running(self) -> bool:
        """Check whether the service is currently running."""
        ...
