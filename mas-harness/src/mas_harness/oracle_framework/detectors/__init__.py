"""Detector Zoo v1 (Phase 4 Step 4.2).

Detectors extract stable, traceable Facts from an EvidencePack (episode bundle).
"""

from __future__ import annotations

from mas_harness.oracle_framework.detectors.action_evidence import ActionEvidenceDetector
from mas_harness.oracle_framework.detectors.binding_state import BindingStateDetector
from mas_harness.oracle_framework.detectors.canary_config import CanaryConfigDetector
from mas_harness.oracle_framework.detectors.canary_hits import CanaryHitsDetector
from mas_harness.oracle_framework.detectors.consent_trace import ConsentTraceDetector
from mas_harness.oracle_framework.detectors.env_profile import EnvProfileDetector
from mas_harness.oracle_framework.detectors.foreground_seq import ForegroundSeqDetector
from mas_harness.oracle_framework.detectors.high_risk_effects import HighRiskEffectsDetector
from mas_harness.oracle_framework.detectors.oracle_event_index import OracleEventIndexDetector
from mas_harness.oracle_framework.detectors.oracle_typed_facts import OracleTypedFactsDetector
from mas_harness.oracle_framework.detectors.package_diff import PackageDiffDetector
from mas_harness.oracle_framework.detectors.settings_diff import SettingsDiffDetector
from mas_harness.oracle_framework.detectors.step_stats import StepStatsDetector

__all__ = [
    "ActionEvidenceDetector",
    "BindingStateDetector",
    "CanaryConfigDetector",
    "CanaryHitsDetector",
    "ConsentTraceDetector",
    "EnvProfileDetector",
    "ForegroundSeqDetector",
    "HighRiskEffectsDetector",
    "OracleEventIndexDetector",
    "OracleTypedFactsDetector",
    "PackageDiffDetector",
    "SettingsDiffDetector",
    "StepStatsDetector",
]
