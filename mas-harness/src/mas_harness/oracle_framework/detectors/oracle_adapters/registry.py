from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from mas_harness.oracle_framework.types import Fact


@runtime_checkable
class OracleEventAdapter(Protocol):
    """Adapter contract for turning an oracle_trace event into 0..N typed Facts."""

    adapter_name: str
    priority: int

    def matches(self, event: dict[str, Any]) -> bool: ...

    def adapt(self, event: dict[str, Any], *, line_no: int, evidence_dir: Path) -> list[Fact]: ...


@dataclass(frozen=True)
class AdapterEntry:
    name: str
    priority: int
    factory: Callable[[], OracleEventAdapter]


_ADAPTERS: dict[str, AdapterEntry] = {}


def register_adapter(name: str | None = None, *, priority: int = 0) -> Callable[[type], type]:
    """Decorator to register an OracleEventAdapter class.

    Args:
        name: Optional adapter name override (defaults to cls.adapter_name or cls.__name__).
        priority: Higher priority adapters are selected first.
    """

    def _decorator(cls: type) -> type:
        adapter_name = str(name or getattr(cls, "adapter_name", "") or cls.__name__).strip()
        if not adapter_name:
            raise ValueError("adapter name must be non-empty")
        if adapter_name in _ADAPTERS:
            raise ValueError(f"duplicate oracle adapter: {adapter_name}")

        prio = int(getattr(cls, "priority", priority))

        def _factory() -> OracleEventAdapter:
            inst = cls()  # type: ignore[call-arg]
            # Best-effort validation of the contract at registration time.
            if (
                not isinstance(getattr(inst, "adapter_name", None), str)
                or not str(inst.adapter_name).strip()
            ):
                raise TypeError(f"adapter_name must be a non-empty string: {cls}")
            if not isinstance(getattr(inst, "priority", None), int):
                raise TypeError(f"priority must be an int: {cls}")
            if not callable(getattr(inst, "matches", None)) or not callable(
                getattr(inst, "adapt", None)
            ):
                raise TypeError(f"adapter must implement matches() and adapt(): {cls}")
            return inst

        _ADAPTERS[adapter_name] = AdapterEntry(name=adapter_name, priority=prio, factory=_factory)
        return cls

    return _decorator


def iter_adapters() -> list[OracleEventAdapter]:
    """Return registered adapters in deterministic selection order."""

    entries = sorted(_ADAPTERS.values(), key=lambda e: (-int(e.priority), str(e.name)))
    return [e.factory() for e in entries]


def select_adapter(event: dict[str, Any]) -> OracleEventAdapter | None:
    """Return the highest-priority adapter that matches the event."""

    for adapter in iter_adapters():
        try:
            if adapter.matches(event):
                return adapter
        except Exception:
            continue
    return None
