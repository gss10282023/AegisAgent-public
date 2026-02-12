from __future__ import annotations

import argparse
import asyncio
import json
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import DataPart, Message, Part, Role, TextPart


def _event_parts(event: object) -> list[Part]:
    parts: list[Part] = []
    match event:
        case Message() as msg:
            parts.extend(msg.parts or [])
        case (task, _update):
            status_msg = getattr(getattr(task, "status", None), "message", None)
            if status_msg:
                parts.extend(status_msg.parts or [])
            for artifact in getattr(task, "artifacts", None) or []:
                parts.extend(artifact.parts or [])
        case _:
            pass
    return parts


def _extract_step3_artifact(event: object) -> dict:
    for part in _event_parts(event):
        if not isinstance(part.root, DataPart):
            continue
        if not isinstance(part.root.data, dict):
            continue
        data = part.root.data
        if {"purple_url", "task_request", "task_result"}.issubset(set(data.keys())):
            return data
    raise RuntimeError("Green response did not include `step3_purple_task` artifact data")


async def _send_assessment(
    *, green_url: str, purple_url: str, config: dict, timeout_s: float = 60
) -> dict:
    payload = {"participants": {"purple": purple_url}, "config": config}
    msg_text = json.dumps(payload)

    async with httpx.AsyncClient(timeout=timeout_s) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=green_url)
        agent_card = await resolver.get_agent_card()
        client = ClientFactory(
            ClientConfig(httpx_client=httpx_client, streaming=True)
        ).create(agent_card)

        msg = Message(
            kind="message",
            role=Role.user,
            parts=[Part(TextPart(text=msg_text))],
            message_id=f"step3-contract-{uuid4().hex}",
        )

        last_event: object | None = None
        async for event in client.send_message(msg):
            last_event = event

    if last_event is None:
        raise RuntimeError("No events received from green")
    return _extract_step3_artifact(last_event)


async def _run_once(
    *,
    label: str,
    green_url: str,
    purple_url: str,
    adb_server: str,
    android_serial: str,
    timeouts_total_s: float,
    variant: object,
    expect_status: str,
) -> None:
    config = {
        "case_id": f"step3_{label}",
        "variant": variant,
        "goal": "Step 3 contract validation",
        "adb_server": adb_server,
        "android_serial": android_serial,
        "timeouts": {"total_s": timeouts_total_s, "max_steps": 50},
    }

    artifact = await _send_assessment(
        green_url=green_url,
        purple_url=purple_url,
        config=config,
        timeout_s=max(10, timeouts_total_s + 10),
    )

    status = (
        (((artifact.get("task_result") or {}) if isinstance(artifact, dict) else {})).get("status")
    )
    summary = (
        (((artifact.get("task_result") or {}) if isinstance(artifact, dict) else {})).get("summary")
    )

    print(f"[step3:{label}] status={status!r} summary={summary!r}")
    if status != expect_status:
        raise RuntimeError(
            f"[step3:{label}] expected status={expect_status!r}, got={status!r} "
            f"(artifact={artifact})"
        )


async def _amain(args: argparse.Namespace) -> int:
    if args.mode in {"success", "both"}:
        await _run_once(
            label="success",
            green_url=args.green_url,
            purple_url=args.purple_url,
            adb_server=args.adb_server,
            android_serial=args.android_serial,
            timeouts_total_s=args.success_timeout_s,
            variant={"mode": "dummy"},
            expect_status="success",
        )

    if args.mode in {"timeout", "both"}:
        await _run_once(
            label="timeout",
            green_url=args.green_url,
            purple_url=args.purple_url,
            adb_server=args.adb_server,
            android_serial=args.android_serial,
            timeouts_total_s=args.timeout_s,
            variant={"simulate_delay_s": args.timeout_delay_s},
            expect_status="timeout",
        )

    print("[step3] OK: green↔purple task contract + timeout behavior verified")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Step 3 verifier: green calls purple using adb_server/android_serial contract."
    )
    parser.add_argument("--green-url", default="http://127.0.0.1:9009", help="Green agent base URL")
    parser.add_argument(
        "--purple-url",
        default="http://127.0.0.1:9010",
        help="Purple agent base URL",
    )
    parser.add_argument(
        "--adb-server",
        default="host.docker.internal:5037",
        help="ADB server host:port passed to purple",
    )
    parser.add_argument(
        "--android-serial",
        default="emulator-5554",
        help="Android serial passed to purple",
    )
    parser.add_argument(
        "--mode",
        choices=["success", "timeout", "both"],
        default="both",
        help="Which checks to run",
    )
    parser.add_argument(
        "--success-timeout-s",
        type=float,
        default=10,
        help="Timeout passed to purple for the success case",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=1,
        help="Timeout passed to purple for the timeout case",
    )
    parser.add_argument(
        "--timeout-delay-s",
        type=float,
        default=2,
        help="Artificial delay on purple side (via variant.simulate_delay_s)",
    )
    args = parser.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
