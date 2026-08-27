"""Runtime context variables — set per agent run, consumed by tools and sub-systems."""

import contextvars
from pathlib import Path

current_workspace: contextvars.ContextVar[Path] = contextvars.ContextVar("workspace_dir")
current_agent_id: contextvars.ContextVar[str] = contextvars.ContextVar("agent_id", default="main")
