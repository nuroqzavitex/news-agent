"""Registry các capability: đăng ký bằng decorator @capability(name, PayloadModel)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.contracts import Envelope


@dataclass
class CapabilityContext:
    """Phụ thuộc truyền vào capability. `deps` chứa session factory, service… do app gắn vào."""

    envelope: Envelope
    deps: dict[str, Any]


Handler = Callable[[Any, CapabilityContext], Awaitable[dict[str, Any]]]


@dataclass
class Capability:
    name: str
    payload_model: type[BaseModel]
    handler: Handler


class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    def register(self, name: str, payload_model: type[BaseModel]) -> Callable[[Handler], Handler]:
        def decorator(fn: Handler) -> Handler:
            if name in self._caps:
                raise ValueError(f"capability {name} đã được đăng ký")
            self._caps[name] = Capability(name, payload_model, fn)
            return fn

        return decorator

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def names(self) -> list[str]:
        return sorted(self._caps)


registry = CapabilityRegistry()
capability = registry.register


def load_builtin_capabilities() -> CapabilityRegistry:
    """Import các module capability để decorator chạy."""
    from app.agent.capabilities import news_search, news_summary, sentiment, trending  # noqa: F401

    return registry
