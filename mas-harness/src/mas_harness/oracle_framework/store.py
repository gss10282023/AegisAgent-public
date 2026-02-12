from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, Optional

from mas_harness.oracle_framework.types import Fact


@dataclass(frozen=True)
class MissingFactError(KeyError):
    fact_id: str

    def __str__(self) -> str:
        return f"missing required fact_id={self.fact_id!r}"


class FactStore:
    """Index Facts by fact_id for deterministic assertion evaluation."""

    def __init__(self, facts: Iterable[Fact] = ()) -> None:
        self._facts: Dict[str, Fact] = {}
        for fact in facts:
            self.add(fact)

    def add(self, fact: Fact) -> None:
        if not isinstance(fact.fact_id, str) or not fact.fact_id.strip():
            raise ValueError("fact_id must be a non-empty string")
        if fact.fact_id in self._facts:
            raise ValueError(f"duplicate fact_id: {fact.fact_id}")
        self._facts[fact.fact_id] = fact

    def get(self, fact_id: str) -> Optional[Fact]:
        return self._facts.get(str(fact_id))

    def require(self, fact_id: str) -> Fact:
        fact = self.get(fact_id)
        if fact is None:
            raise MissingFactError(str(fact_id))
        return fact

    def __contains__(self, fact_id: object) -> bool:
        return str(fact_id) in self._facts

    def __len__(self) -> int:
        return len(self._facts)

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._facts.values())

    def ids(self) -> list[str]:
        return sorted(self._facts.keys())
