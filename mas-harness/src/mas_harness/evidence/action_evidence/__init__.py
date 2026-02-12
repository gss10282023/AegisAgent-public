"""Phase-3 action evidence helpers (L1/L2).

This package is intentionally small and contract-driven: parsers validate the
expected input formats early so later mapping steps can stay deterministic.
"""

from __future__ import annotations

__all__ = [
    "agent_events_v1",
    "base",
    "comm_proxy_trace",
    "device_input_trace_writer",
    "device_input_trace_validator",
    "materialize",
    "l2_http_recorder",
    "l2_mapping",
    "l1_agent_events",
    "l1_mapping",
]
