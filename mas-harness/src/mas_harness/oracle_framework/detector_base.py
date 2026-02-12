from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

from mas_harness.oracle_framework.types import Fact


class Detector(ABC):
    """Detector base class.

    A detector extracts stable, traceable facts from an EvidencePack (episode bundle).
    """

    detector_id: str = "detector"
    capabilities_required: Sequence[str] = ()
    evidence_required: Sequence[str] = ()
    produces_fact_ids: Sequence[str] = ()

    @abstractmethod
    def extract(self, pack: Any, case_ctx: Any) -> list[Fact]:
        raise NotImplementedError
