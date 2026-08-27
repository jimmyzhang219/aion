"""Internal shared constants — no dependency on other bootstrap submodules."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
CONTEXT_FILE_ORDER: dict[str, int] = {
    "workspace.md": 5,
    "config.md": 10,
    "user.md": 40,
    "memory.md": 45,
    "workspace_bootstrap.md": 55,
    "agent_bootstrap.md": 56,
}

SYSTEM_PROMPT_CACHE_BOUNDARY = "\n<!-- AION_CACHE_BOUNDARY -->\n"

BOOTSTRAP_FILE_ORDER: list[str] = [k for k, _ in sorted(CONTEXT_FILE_ORDER.items(), key=lambda kv: kv[1])]

DEFAULT_BOOTSTRAP_MAX_CHARS = 12_000
DEFAULT_BOOTSTRAP_TOTAL_MAX_CHARS = 60_000
MIN_BOOTSTRAP_FILE_BUDGET_CHARS = 64
BOOTSTRAP_HEAD_RATIO = 0.7
BOOTSTRAP_TAIL_RATIO = 0.2
