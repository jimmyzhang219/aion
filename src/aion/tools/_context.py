"""Context variable for current agent loop — set/reset by AgentLoop.run()."""

from contextvars import ContextVar, Token

_current_agent_loop: ContextVar[object] = ContextVar("_current_agent_loop", default=None)


def get_agent_loop() -> object:
    return _current_agent_loop.get()


def set_agent_loop(loop: object) -> Token[object]:
    return _current_agent_loop.set(loop)


def reset_agent_loop(token: Token[object]) -> None:
    _current_agent_loop.reset(token)
