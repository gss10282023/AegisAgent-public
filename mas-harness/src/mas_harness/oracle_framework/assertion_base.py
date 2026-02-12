from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar, Sequence

from mas_harness.oracle_framework.store import FactStore
from mas_harness.oracle_framework.types import AssertionResult


class Assertion(ABC):
    """Assertion base class.

    An assertion evaluates required facts (+ case/policy context) into a
    PASS/FAIL/INCONCLUSIVE result for audit-first evaluation.
    """

    assertion_id: str = "assertion"
    required_fact_ids: Sequence[str] = ()
    capabilities_required: Sequence[str] = ()

    SUPPORTED_PARAMS: ClassVar[set[str] | None] = None

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = dict(params) if isinstance(params, Mapping) else {}
        self.validate_params()

    def validate_params(self) -> None:
        """Validate `self.params`.

        Default behavior is intentionally lightweight:
        - params must be a JSON-serializable object (dict)
        - optionally enforce SUPPORTED_PARAMS allowlist if set

        Subclasses may override for stricter validation.
        """

        if not isinstance(self.params, dict):
            raise ValueError("params must be a dict")

        supported = getattr(self, "SUPPORTED_PARAMS", None)
        if isinstance(supported, set):
            unknown = sorted({str(k) for k in self.params.keys()} - {str(k) for k in supported})
            if unknown:
                raise ValueError(f"unsupported params: {unknown}")

        try:
            json.dumps(
                self.params,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except Exception as e:
            raise ValueError(f"params must be JSON-serializable: {e}") from e

    @abstractmethod
    def evaluate(self, facts: FactStore, case_ctx: Any) -> AssertionResult:
        raise NotImplementedError
