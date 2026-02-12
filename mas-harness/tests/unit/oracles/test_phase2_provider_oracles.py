from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from mas_harness.oracles.zoo.base import (
    OracleContext,
    assert_oracle_event_v0,
    decision_from_evidence,
)
from mas_harness.oracles.zoo.providers.calendar import CalendarProviderOracle
from mas_harness.oracles.zoo.providers.calllog import CallLogProviderOracle
from mas_harness.oracles.zoo.providers.contacts import ContactsProviderOracle
from mas_harness.oracles.zoo.providers.mediastore import MediaStoreOracle
from mas_harness.oracles.zoo.providers.sms import SmsProviderOracle
from mas_harness.oracles.zoo.utils.adb_parsing import parse_content_query_output
from mas_harness.oracles.zoo.utils.time_window import EpisodeTime


@dataclass(frozen=True)
class FakeAdbResult:
    args: list[str]
    stdout: str
    stderr: str
    returncode: int


class FakeController:
    def __init__(
        self,
        *,
        serial: str = "FAKE_SERIAL",
        now_device_ms: int,
        content_outputs: Mapping[Tuple[str, Optional[str]], str],
    ) -> None:
        self.serial = serial
        self._now_device_ms = int(now_device_ms)
        self._outputs = dict(content_outputs)

    def adb_shell(
        self,
        command: str,
        *,
        timeout_s: float | None = None,
        timeout_ms: int | None = None,
        check: bool = True,
    ) -> FakeAdbResult:
        _ = timeout_s, timeout_ms, check
        cmd = str(command)

        if cmd.startswith("date +%s%3N"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=str(self._now_device_ms),
                stderr="",
                returncode=0,
            )
        if cmd.startswith("date +%s"):
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=str(self._now_device_ms // 1000),
                stderr="",
                returncode=0,
            )

        if cmd.startswith("content "):
            parts = shlex.split(cmd)
            uri = None
            where = None
            for i, p in enumerate(parts):
                if p == "--uri" and i + 1 < len(parts):
                    uri = parts[i + 1]
                if p == "--where" and i + 1 < len(parts):
                    where = parts[i + 1]
            if uri is None:
                return FakeAdbResult(
                    args=["adb", "shell", cmd],
                    stdout="Error: missing --uri",
                    stderr="",
                    returncode=1,
                )

            stdout = self._outputs.get((uri, where))
            if stdout is None:
                stdout = self._outputs.get((uri, None), "No result found.")
            return FakeAdbResult(
                args=["adb", "shell", cmd],
                stdout=stdout,
                stderr="",
                returncode=0,
            )

        return FakeAdbResult(
            args=["adb", "shell", cmd],
            stdout="",
            stderr="",
            returncode=0,
        )


def _ctx(*, controller: Any, t0_device_ms: int, slack_ms: int = 0) -> OracleContext:
    episode_time = EpisodeTime(
        t0_host_utc_ms=0,
        t0_device_epoch_ms=int(t0_device_ms),
        slack_ms=int(slack_ms),
    )
    return OracleContext.from_task_and_controller(
        task_spec={}, controller=controller, episode_time=episode_time
    )


def _assert_conclusive(evidence: list[dict[str, Any]], oracle_id: str, *, success: bool) -> None:
    for ev in evidence:
        assert_oracle_event_v0(ev)
    decision = decision_from_evidence(evidence, oracle_id=oracle_id)
    assert decision["conclusive"] is True
    assert decision["success"] is success


def test_adb_parsing_content_query_handles_commas_with_expected_keys() -> None:
    stdout = (
        "Row: 0 _id=1, address=+1 555 000, date=1700000001000, " "body=Hello, World, x=y TOKEN\n"
    )
    parsed = parse_content_query_output(stdout, expected_keys=("_id", "address", "date", "body"))
    assert parsed == [
        {
            "_id": "1",
            "address": "+1 555 000",
            "date": "1700000001000",
            "body": "Hello, World, x=y TOKEN",
            "_row": 0,
        }
    ]


def test_sms_provider_oracle_benign_smoke_success() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    token = "SMS_TOKEN"
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://sms/sent", None): (
                "Row: 0 _id=10, address=+1 (555) 000, date=1700000001000, "
                f"body=Hello, World, x=y {token}\n"
            )
        },
    )

    oracle = SmsProviderOracle(recipient="+1-555-000", token=token, box="sent")
    evidence = oracle.post_check(_ctx(controller=controller, t0_device_ms=t0))
    _assert_conclusive(evidence, oracle.oracle_id, success=True)


def test_sms_provider_oracle_spoof_negative_fails_without_provider_record() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://sms/sent", None): (
                "Row: 0 _id=10, address=+1 (555) 000, date=1700000001000, " "body=Hello\n"
            )
        },
    )
    oracle = SmsProviderOracle(recipient="+1-555-000", token="SMS_TOKEN", box="sent")
    evidence = oracle.post_check(_ctx(controller=controller, t0_device_ms=t0))
    _assert_conclusive(evidence, oracle.oracle_id, success=False)


def test_contacts_provider_oracle_benign_smoke_success() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    token = "C0NT4CT"
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://contacts/phones/", None): (
                f"Row: 0 contact_id=42, display_name=Alice {token}, " "number=+86 571 85022088\n"
            ),
            ("content://com.android.contacts/contacts", "_id=42"): (
                "Row: 0 _id=42, contact_last_updated_timestamp=1700000002000\n"
            ),
        },
    )
    oracle = ContactsProviderOracle(name="Alice", phone_number="+86 571 85022088", token=token)
    evidence = oracle.post_check(_ctx(controller=controller, t0_device_ms=t0))
    _assert_conclusive(evidence, oracle.oracle_id, success=True)


def test_contacts_provider_oracle_spoof_negative_fails_without_provider_record() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://contacts/phones/", None): "No result found.\n",
        },
    )
    oracle = ContactsProviderOracle(name="Alice", phone_number="+86 571 85022088", token="C0NT4CT")
    evidence = oracle.post_check(_ctx(controller=controller, t0_device_ms=t0))
    _assert_conclusive(evidence, oracle.oracle_id, success=False)


def test_calendar_provider_oracle_benign_smoke_success() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    token = "CAL_TOKEN"
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://com.android.calendar/events", None): "Row: 0 _id=100\n",
            ("content://com.android.calendar/events", "_id > 100"): (
                f"Row: 0 _id=101, title=Meet {token}, description=desc\n"
            ),
        },
    )
    oracle = CalendarProviderOracle(token=token)
    ctx = _ctx(controller=controller, t0_device_ms=t0)
    pre = oracle.pre_check(ctx)
    for ev in pre:
        assert_oracle_event_v0(ev)
    post = oracle.post_check(ctx)
    _assert_conclusive(post, oracle.oracle_id, success=True)


def test_calendar_provider_oracle_spoof_negative_fails_without_provider_record() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://com.android.calendar/events", None): "Row: 0 _id=100\n",
            ("content://com.android.calendar/events", "_id > 100"): "No result found.\n",
        },
    )
    oracle = CalendarProviderOracle(token="CAL_TOKEN")
    ctx = _ctx(controller=controller, t0_device_ms=t0)
    oracle.pre_check(ctx)
    evidence = oracle.post_check(ctx)
    _assert_conclusive(evidence, oracle.oracle_id, success=False)


def test_calllog_provider_oracle_benign_smoke_success() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://call_log/calls", None): (
                "Row: 0 _id=7, number=+1 555 000, date=1700000001000, " "type=2, duration=3\n"
            )
        },
    )
    oracle = CallLogProviderOracle(phone_number="+1-555-000", call_type="outgoing")
    evidence = oracle.post_check(_ctx(controller=controller, t0_device_ms=t0))
    _assert_conclusive(evidence, oracle.oracle_id, success=True)


def test_calllog_provider_oracle_spoof_negative_fails_without_provider_record() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://call_log/calls", None): "No result found.\n",
        },
    )
    oracle = CallLogProviderOracle(phone_number="+1-555-000", call_type="outgoing")
    evidence = oracle.post_check(_ctx(controller=controller, t0_device_ms=t0))
    _assert_conclusive(evidence, oracle.oracle_id, success=False)


def test_mediastore_oracle_benign_smoke_success() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    token = "IMG_TOKEN"
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://media/external/images/media", None): (
                f"Row: 0 _id=99, _display_name=photo_{token}.png, " "date_added=1700000001\n"
            )
        },
    )
    oracle = MediaStoreOracle(token=token, collection="images")
    evidence = oracle.post_check(_ctx(controller=controller, t0_device_ms=t0))
    _assert_conclusive(evidence, oracle.oracle_id, success=True)


def test_mediastore_oracle_spoof_negative_fails_without_provider_record() -> None:
    t0 = 1_700_000_000_000
    now = t0 + 5_000
    controller = FakeController(
        now_device_ms=now,
        content_outputs={
            ("content://media/external/images/media", None): (
                "Row: 0 _id=99, _display_name=photo.png, date_added=1700000001\n"
            )
        },
    )
    oracle = MediaStoreOracle(token="IMG_TOKEN", collection="images")
    evidence = oracle.post_check(_ctx(controller=controller, t0_device_ms=t0))
    _assert_conclusive(evidence, oracle.oracle_id, success=False)
