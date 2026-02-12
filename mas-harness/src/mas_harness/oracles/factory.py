"""Compatibility entrypoint for task success oracles/verifiers.

Phase 2 Step 0 goal: keep **all** oracle implementations under a single,
discoverable "Oracle Zoo" location.

This module remains a stable import path for runners and legacy code:
`from mas_harness.oracles import make_oracle`.
"""

from __future__ import annotations

from mas_harness.oracles.zoo import adb_shell as _adb_shell
from mas_harness.oracles.zoo import base as _base
from mas_harness.oracles.zoo import hybrid as _hybrid
from mas_harness.oracles.zoo import toy as _toy
from mas_harness.oracles.zoo.registry import make_oracle
from mas_harness.oracles.zoo.utils import composite as _composite

AdbShellExpectRegexOracle = _adb_shell.AdbShellExpectRegexOracle
CompositeOracle = _composite.CompositeOracle
HybridOracle = _hybrid.HybridOracle
Oracle = _base.Oracle
OracleContext = _base.OracleContext
OracleEvidence = _base.OracleEvidence
OracleEvent = _base.OracleEvent
ToySuccessAfterStepsOracle = _toy.ToySuccessAfterStepsOracle

__all__ = [
    "AdbShellExpectRegexOracle",
    "CompositeOracle",
    "HybridOracle",
    "Oracle",
    "OracleContext",
    "OracleEvidence",
    "OracleEvent",
    "ToySuccessAfterStepsOracle",
    "make_oracle",
]
