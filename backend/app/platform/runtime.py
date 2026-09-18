from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any


_current_runtime: ContextVar[dict[str, Any] | None] = ContextVar(
    "workspace_runtime", default=None
)


def set_runtime(runtime: dict[str, Any]) -> Token:
    return _current_runtime.set(runtime)


def reset_runtime(token: Token) -> None:
    _current_runtime.reset(token)


class RuntimeProxy:
    """Route existing app.state service access to the signed-in project runtime."""

    def __init__(self, key: str, fallback: dict[str, Any]):
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_fallback", fallback)

    def _target(self) -> Any:
        runtime = _current_runtime.get() or object.__getattribute__(self, "_fallback")
        return runtime[object.__getattribute__(self, "_key")]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._target(), name, value)

    def __getitem__(self, key: Any) -> Any:
        return self._target()[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        self._target()[key] = value

    def get(self, *args: Any, **kwargs: Any) -> Any:
        return self._target().get(*args, **kwargs)
