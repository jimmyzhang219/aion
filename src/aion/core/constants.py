"""Shared constants — single source of truth for default paths, ports, and provider names."""

import sys
from pathlib import Path

AION_HOME = Path.home() / ".aion"
DEFAULT_CONFIG_PATH = AION_HOME / "aion.json"
DEFAULT_WORKSPACES_DIR = AION_HOME / "workspaces"
DEFAULT_PORT = 19527
DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_PROVIDER = "deepseek"  # CLI interactive prompt default only; NOT a runtime fallback
# DEFAULT_MAX_OUTPUT_TOKENS = 8192  # 未使用
DEFAULT_MODEL_NAME = "deepseek-v4-flash"


class MemoryConstants:
    """Memory / vector store tunable parameters — single source of truth.

    All default values in MemorySearchTool, DocumentProcessor, AgentLoop,
    and VectorIndexer originate from here.  Modify with care — affects both
    runtime search quality and storage behaviour.
    """

    # ── Chunking ──────────────────────────────────────────────────
    CHUNK_SIZE: int = 400  # Characters per document chunk (~260 tokens CN)
    CHUNK_OVERLAP: int = 80  # Overlap between adjacent chunks

    # ── MemorySearchTool defaults ──────────────────────────────────
    MAX_RESULTS: int = 10  # Max search results returned to LLM
    MIN_SCORE: float = 0.2  # Minimum relevance threshold [0-1]
    N_RESULTS_PER_CHANNEL: int = 200  # Candidate pool per channel
    WEIGHT_DENSE: float = 0.6  # Dense channel fusion weight
    WEIGHT_BM25: float = 0.4  # BM25 channel fusion weight

    # ── Time Decay ────────────────────────────────────────────────
    DECAY_LAMBDA_SESSION: float = 0.3  # Session memory decay rate
    DECAY_LAMBDA_DAILY: float = 0.15  # Daily memory decay rate
    DECAY_LAMBDA_MEMORY: float = 0.0  # Permanent memory (no decay)

    # ── Legacy / compatibility ────────────────────────────────────
    DAILY_MEMORY_DAYS: int = 2  # Days of daily memory to scan (retained for compat)


# ── Bootstrap ritual file basenames ──────────────────────────────
# 供 tools/builtin/{delete,trash}.py 识别工作区/Agent 引导仪式文件。
# 与 agent/bootstrap.py 中同名常量保持同步。
RITUAL_BOOTSTRAP_BASENAMES_LOWER: frozenset[str] = frozenset(("workspace_bootstrap.md", "agent_bootstrap.md"))


def _is_ritual_markdown_basename(basename_lower: str) -> bool:
    """判断 basename（小写）是否为工作区/Agent 引导仪式文件。

    与 agent/bootstrap.py 中的同名函数保持同步。

    Args:
        basename_lower: 小写文件名

    Returns:
        True 表示属于 RITUAL_BOOTSTRAP_BASENAMES_LOWER
    """
    return basename_lower in RITUAL_BOOTSTRAP_BASENAMES_LOWER


def is_bootstrap_ritual_filename(filename: str) -> bool:
    """是否为工作区或 Agent 级引导文件。

    供 delete/trash 工具在删除前做安全检查，避免误删引导文件。

    Args:
        filename: 文件名（可含路径，仅取 basename 比较）

    Returns:
        True 表示是引导仪式文件
    """
    return filename.lower() in RITUAL_BOOTSTRAP_BASENAMES_LOWER


def get_trash_path() -> Path:
    """Platform-aware trash directory.

    Returns:
        Path to the system trash / fallback trash directory.
    """
    if sys.platform == "darwin":
        return Path.home() / ".Trash"
    elif sys.platform == "linux":
        return Path.home() / ".local" / "share" / "Trash" / "files"
    else:
        # Windows: no standard trash API accessible from Python; use a safe fallback
        return Path.home() / "AppData" / "Local" / "Temp" / ".aion_trash"
