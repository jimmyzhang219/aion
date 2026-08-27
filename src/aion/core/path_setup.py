"""PATH 环境变量补齐 — 为 launchd/systemd 等受限环境追加已知工具目录。

用法:
    from aion.core.path_setup import ensure_system_path
    ensure_system_path()

在 Gateway / CLI 启动时调用一次即可，所有子进程自动受益。
"""

import os
import platform
from pathlib import Path

_AION_PATH_BOOTSTRAPPED = "AION_PATH_BOOTSTRAPPED"


def ensure_system_path() -> None:
    """追加已知工具目录到 PATH。

    只做一次（由 AION_PATH_BOOTSTRAPPED 标记控制）。
    不读取 shell profile，只追加平台已知的包管理器安装目录。
    已在 PATH 中的目录不会重复添加，不改变已有路径的优先级。
    """
    if os.environ.get(_AION_PATH_BOOTSTRAPPED):
        return

    current_path = os.environ.get("PATH", "")
    candidates = _candidate_dirs()
    if not candidates:
        os.environ[_AION_PATH_BOOTSTRAPPED] = "1"
        return

    merged = _merge_path(existing=current_path, append=candidates)
    if merged and merged != current_path:
        os.environ["PATH"] = merged
    os.environ[_AION_PATH_BOOTSTRAPPED] = "1"


def _candidate_dirs() -> list[str]:
    """按平台返回应追加的已知工具目录（仅已验证存在的目录）。"""
    system = platform.system()

    if system == "Darwin":
        return _collect_candidate_dirs(
            [
                "/opt/homebrew/bin",  # Apple Silicon Homebrew
                "/usr/local/bin",  # Intel Homebrew / 手动安装
                str(Path.home() / ".local" / "bin"),
            ]
        )

    if system == "Linux":
        return _collect_candidate_dirs(
            [
                str(Path.home() / ".linuxbrew" / "bin"),
                "/home/linuxbrew/.linuxbrew/bin",
                str(Path.home() / ".local" / "bin"),
            ]
        )

    # Windows: 系统 PATH 通常已完整，不追加
    return []


def _collect_candidate_dirs(candidates: list[str]) -> list[str]:
    """过滤出实际存在的目录。"""
    return [d for d in candidates if os.path.isdir(d)]


def _merge_path(*, existing: str, append: list[str]) -> str:
    """合并 PATH：保留现有顺序 + 追加候选目录（去重）。

    Args:
        existing: 当前 PATH 字符串
        append: 待追加的候选目录列表

    Returns:
        合并后的 PATH 字符串
    """
    parts = [p for p in existing.split(os.pathsep) if p]
    seen = set(parts)
    for d in append:
        if d not in seen:
            seen.add(d)
            parts.append(d)
    return os.pathsep.join(parts)
