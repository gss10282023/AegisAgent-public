"""Oracles (task success) interface + factory.

The stable entrypoint is `from mas_harness.oracles import make_oracle`.
Implementations live under the Oracle Zoo.
"""

from __future__ import annotations

from mas_harness.oracles.factory import (
    AdbShellExpectRegexOracle,
    CompositeOracle,
    HybridOracle,
    Oracle,
    OracleContext,
    OracleEvent,
    OracleEvidence,
    ToySuccessAfterStepsOracle,
    make_oracle,
)

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
