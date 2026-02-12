"""Time window helpers for Oracle Zoo.

Phase 2 Step 5: prevent historical/polluted state from causing false positives by
forcing all time-sensitive matching to use a shared episode time window.

Key idea
--------
At episode start we record a time anchor (t0). When an oracle queries state, it
must only consider evidence in:

  [t0 - slack, now + slack]

Slack accounts for emulator time drift and execution latency.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


def host_utc_ms() -> int:
    return int(time.time() * 1000)


def _env_int(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def default_slack_ms() -> int:
    """Default slack to tolerate emulator drift/latency."""

    ms = _env_int("MAS_TIME_WINDOW_SLACK_MS")
    if ms is not None:
        return max(0, ms)
    seconds = _env_int("MAS_TIME_WINDOW_SLACK_S")
    if seconds is not None:
        return max(0, seconds * 1000)
    return 120_000  # 2 minutes


_EPOCH_S_RE = re.compile(r"^\d{9,12}$")
_EPOCH_MS_RE = re.compile(r"^\d{13,}$")
_FLOAT_SECONDS_RE = re.compile(r"^\d+\.\d+$")


def parse_epoch_time_ms(text: str) -> Optional[int]:
    """Parse epoch time in seconds/ms from shell output into epoch milliseconds."""

    s = str(text).strip()
    if not s:
        return None
    if _EPOCH_MS_RE.match(s):
        # Coerce higher-resolution timestamps down to ms.
        value = int(s)
        while value > 10**13:
            value //= 10
        return value
    if _EPOCH_S_RE.match(s):
        return int(s) * 1000
    if _FLOAT_SECONDS_RE.match(s):
        try:
            return int(float(s) * 1000.0)
        except Exception:
            return None
    return None


def _run_adb_shell_best_effort(
    controller: Any, *, cmd: str, timeout_ms: int
) -> Tuple[Optional[str], Dict[str, Any]]:
    meta: Dict[str, Any] = {"cmd": cmd, "timeout_ms": int(timeout_ms)}
    if not hasattr(controller, "adb_shell"):
        meta["error"] = "missing controller capability: adb_shell"
        return None, meta

    try:
        try:
            res = controller.adb_shell(cmd, timeout_ms=timeout_ms, check=False)
        except TypeError:
            res = controller.adb_shell(cmd, timeout_s=float(timeout_ms) / 1000.0, check=False)
    except TypeError:
        # Fallback for controllers that don't accept timeout/check kwargs.
        try:
            res = controller.adb_shell(cmd)
        except Exception as e:  # pragma: no cover
            meta["error"] = repr(e)
            return None, meta
    except Exception as e:  # pragma: no cover
        meta["error"] = repr(e)
        return None, meta

    if hasattr(res, "stdout") or hasattr(res, "returncode"):
        stdout = str(getattr(res, "stdout", "") or "")
        meta.update(
            {
                "args": getattr(res, "args", None),
                "returncode": getattr(res, "returncode", None),
                "stderr": getattr(res, "stderr", None),
                "stdout": stdout,
            }
        )
        ok = getattr(res, "returncode", 0) == 0
        meta["ok"] = bool(ok)
        return stdout, meta

    # Toy/fake controllers may return raw stdout strings.
    stdout = str(res)
    meta.update({"stdout": stdout, "ok": True})
    return stdout, meta


def probe_device_epoch_time_ms(
    controller: Any, *, timeout_ms: int = 1500
) -> Tuple[Optional[int], Dict[str, Any]]:
    """Best-effort probe of device epoch time (ms).

    Returns (epoch_ms, meta). epoch_ms is None if the probe failed.
    """

    # Preferred: epoch milliseconds if toybox/coreutils supports %3N.
    stdout, meta = _run_adb_shell_best_effort(controller, cmd="date +%s%3N", timeout_ms=timeout_ms)
    parsed = parse_epoch_time_ms(stdout or "")
    if parsed is not None and parsed > 0:
        meta["parsed_epoch_ms"] = parsed
        meta["source"] = "date_ms"
        return parsed, meta

    meta["parse_failed"] = True

    # Fallback: epoch seconds.
    stdout2, meta2 = _run_adb_shell_best_effort(controller, cmd="date +%s", timeout_ms=timeout_ms)
    parsed2 = parse_epoch_time_ms(stdout2 or "")
    meta2["source"] = "date_s"
    if parsed2 is not None and parsed2 > 0:
        meta2["parsed_epoch_ms"] = parsed2
        return parsed2, {"attempt1": meta, "attempt2": meta2}

    meta2["parse_failed"] = True
    return None, {"attempt1": meta, "attempt2": meta2}


def compute_window_ms(*, t0_ms: int, now_ms: int, slack_ms: int) -> Tuple[int, int]:
    slack_ms = max(0, int(slack_ms))
    start = int(t0_ms) - slack_ms
    end = int(now_ms) + slack_ms
    if end < start:
        start, end = end, start
    return start, end


@dataclass(frozen=True)
class EpisodeTime:
    """Episode time anchors + slack for time-windowed oracles."""

    t0_host_utc_ms: int
    t0_device_epoch_ms: Optional[int]
    slack_ms: int

    def host_window(self, *, now_host_utc_ms: Optional[int] = None) -> "TimeWindow":
        now_val = host_utc_ms() if now_host_utc_ms is None else int(now_host_utc_ms)
        start, end = compute_window_ms(
            t0_ms=self.t0_host_utc_ms, now_ms=now_val, slack_ms=self.slack_ms
        )
        return TimeWindow(
            t0_ms=self.t0_host_utc_ms,
            now_ms=now_val,
            slack_ms=self.slack_ms,
            start_ms=start,
            end_ms=end,
        )

    def device_window(
        self,
        *,
        controller: Any,
        now_device_epoch_ms: Optional[int] = None,
        timeout_ms: int = 1500,
    ) -> Tuple[Optional["TimeWindow"], Dict[str, Any]]:
        if self.t0_device_epoch_ms is None:
            return None, {"error": "missing t0_device_epoch_ms"}

        probe_meta: Dict[str, Any] = {"source": "provided"}
        now_val: Optional[int]
        if now_device_epoch_ms is None:
            now_val, probe_meta = probe_device_epoch_time_ms(controller, timeout_ms=timeout_ms)
        else:
            now_val = int(now_device_epoch_ms)

        if now_val is None:
            return None, {"error": "failed to probe now_device_epoch_ms", "probe": probe_meta}

        start, end = compute_window_ms(
            t0_ms=self.t0_device_epoch_ms, now_ms=now_val, slack_ms=self.slack_ms
        )
        return (
            TimeWindow(
                t0_ms=self.t0_device_epoch_ms,
                now_ms=now_val,
                slack_ms=self.slack_ms,
                start_ms=start,
                end_ms=end,
            ),
            {"now_device_epoch_ms": now_val, "probe": probe_meta},
        )


@dataclass(frozen=True)
class TimeWindow:
    t0_ms: int
    now_ms: int
    slack_ms: int
    start_ms: int
    end_ms: int

    def contains(self, epoch_ms: int) -> bool:
        v = int(epoch_ms)
        return self.start_ms <= v <= self.end_ms


def slack_ms_from_task_spec(task_spec: Any) -> int:
    if not isinstance(task_spec, dict):
        return default_slack_ms()

    # Common variants: top-level `time_window_slack_ms` or nested object.
    for key in ("time_window_slack_ms", "time_window_slackMilliseconds"):
        if key in task_spec:
            try:
                return max(0, int(task_spec[key]))
            except Exception:
                return default_slack_ms()

    tw = task_spec.get("time_window")
    if isinstance(tw, dict):
        if "slack_ms" in tw:
            try:
                return max(0, int(tw["slack_ms"]))
            except Exception:
                return default_slack_ms()
        if "slack_s" in tw:
            try:
                return max(0, int(tw["slack_s"]) * 1000)
            except Exception:
                return default_slack_ms()

    return default_slack_ms()


def capture_episode_time(
    *, controller: Any, task_spec: Any, timeout_ms: int = 1500
) -> Tuple[EpisodeTime, Dict[str, Any]]:
    """Capture episode time anchors (best-effort).

    Always returns a host anchor; device anchor may be None.
    """

    t0_host = host_utc_ms()
    slack_ms = slack_ms_from_task_spec(task_spec)

    device_ms, meta = probe_device_epoch_time_ms(controller, timeout_ms=timeout_ms)

    episode_time = EpisodeTime(
        t0_host_utc_ms=t0_host,
        t0_device_epoch_ms=device_ms,
        slack_ms=slack_ms,
    )

    event = {
        "event": "episode_time_anchor",
        "t0_host_utc_ms": t0_host,
        "t0_device_epoch_ms": device_ms,
        "slack_ms": slack_ms,
        "device_time_probe": meta,
    }
    return episode_time, event
