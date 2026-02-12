from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4


def _fail(msg: str) -> NoReturn:
    print(f"[phase1_smoke] ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(2)


def _repo_root() -> Path:
    # agentbeats/scenarios/phase1_smoke/<this_file.py>
    return Path(__file__).resolve().parents[3]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _event_parts(event: object) -> list[Any]:
    from a2a.types import Message, Part

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


def _print_progress(event: object) -> None:
    from a2a.types import TextPart

    for part in _event_parts(event):
        if isinstance(part.root, TextPart):
            text = str(part.root.text or "").strip()
            if text:
                print(f"[phase1_smoke] {text}", flush=True)


def _extract_results(event: object) -> dict[str, Any]:
    from a2a.types import DataPart

    for part in _event_parts(event):
        if isinstance(part.root, DataPart) and isinstance(part.root.data, dict):
            data = part.root.data
            if data.get("schema_version") == "agentbeats.phase1.results.v1":
                return data
    raise RuntimeError("Green response did not include a results.json DataPart")


def _read_scenario_config(*, scenario_path: Path) -> dict[str, Any]:
    if not scenario_path.exists():
        _fail(f"missing scenario.toml: {scenario_path}")

    try:
        import tomllib  # py311+
    except Exception as e:  # pragma: no cover
        raise RuntimeError("Python 3.11+ required (tomllib not available)") from e

    data = tomllib.loads(scenario_path.read_text(encoding="utf-8"))
    config = data.get("config")
    if not isinstance(config, dict):
        _fail("scenario.toml missing [config]")
    return config


async def _wait_for_agent_card(*, base_url: str, timeout_s: float) -> None:
    import httpx

    url = base_url.rstrip("/") + "/.well-known/agent-card.json"
    deadline = time.time() + float(timeout_s)
    last_err: str | None = None
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    return
                last_err = f"HTTP {res.status_code}: {res.text[:200]}"
            except Exception as exc:  # pragma: no cover
                last_err = str(exc)
            await asyncio.sleep(0.25)
    raise TimeoutError(f"timed out waiting for agent card: {url} ({last_err})")


async def _send_assessment(
    *,
    green_url: str,
    purple_url: str,
    config: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from a2a.types import Message, Part, Role, TextPart

    payload = {"participants": {"purple": purple_url}, "config": config}
    msg_text = json.dumps(payload, ensure_ascii=False)

    async with httpx.AsyncClient(timeout=max(5.0, float(timeout_s))) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=green_url)
        agent_card = await resolver.get_agent_card()
        client = ClientFactory(ClientConfig(httpx_client=httpx_client, streaming=True)).create(
            agent_card
        )

        msg = Message(
            kind="message",
            role=Role.user,
            parts=[Part(TextPart(text=msg_text))],
            message_id=f"phase1-smoke-{_now_ms()}-{uuid4().hex[:8]}",
        )

        last_event: object | None = None
        async for event in client.send_message(msg):
            _print_progress(event)
            last_event = event

    if last_event is None:
        raise RuntimeError("No events received from green")
    return _extract_results(last_event)


def _validate_outputs(*, results: dict[str, Any]) -> tuple[Path, list[Path]]:
    run_dir_raw = str(results.get("run_dir") or "").strip()
    if not run_dir_raw:
        raise RuntimeError("results.run_dir is missing/empty")

    run_dir = Path(run_dir_raw).expanduser()
    if not run_dir.exists():
        raise RuntimeError(f"run_dir does not exist: {run_dir}")

    results_path = run_dir / "results.json"
    if not results_path.exists():
        raise RuntimeError(f"missing results.json: {results_path}")
    results_obj = json.loads(results_path.read_text(encoding="utf-8"))

    counts = results_obj.get("counts") if isinstance(results_obj, dict) else None
    episodes_n = int(counts.get("episodes") or 0) if isinstance(counts, dict) else 0
    if episodes_n <= 0:
        raise RuntimeError(f"expected >=1 episode, got: {episodes_n}")

    evidence_dirs: list[Path] = []
    for idx in range(episodes_n):
        evidence_dir = run_dir / f"episode_{idx:04d}" / "evidence"
        if not evidence_dir.is_dir():
            raise RuntimeError(f"missing evidence dir: {evidence_dir}")
        evidence_dirs.append(evidence_dir)

    return results_path, evidence_dirs


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Berkeley Phase 1 Step 9: docker-compose smoke scenario. "
            "Assumes host-run emulator + host-run adb server is already up."
        )
    )
    ap.add_argument(
        "--scenario",
        type=str,
        default=str(Path(__file__).with_name("scenario.toml")),
        help="Path to scenario.toml",
    )
    ap.add_argument(
        "--green-url",
        type=str,
        default=os.getenv("GREEN_URL", "http://green:9009"),
        help="Green base URL reachable from this container",
    )
    ap.add_argument(
        "--purple-url",
        type=str,
        default=os.getenv("PURPLE_URL", "http://purple:9010"),
        help="Purple base URL reachable from green",
    )
    ap.add_argument(
        "--wait-timeout-s",
        type=float,
        default=float(os.getenv("AGENT_CARD_TIMEOUT_S", "60")),
        help="Timeout waiting for agent cards (seconds)",
    )
    ap.add_argument(
        "--timeout-s",
        type=float,
        default=float(os.getenv("ASSESSMENT_TIMEOUT_S", "900")),
        help="Timeout for the assessment call (seconds)",
    )
    args = ap.parse_args()

    repo_root = _repo_root()
    scenario_path = Path(args.scenario).expanduser()
    if not scenario_path.is_absolute():
        scenario_path = (repo_root / scenario_path).resolve()

    config = _read_scenario_config(scenario_path=scenario_path)

    print(f"[phase1_smoke] green_url={args.green_url} purple_url={args.purple_url}", flush=True)
    print(f"[phase1_smoke] scenario={scenario_path}", flush=True)
    print(f"[phase1_smoke] config={json.dumps(config, ensure_ascii=False)}", flush=True)

    asyncio.run(_wait_for_agent_card(base_url=args.purple_url, timeout_s=args.wait_timeout_s))
    asyncio.run(_wait_for_agent_card(base_url=args.green_url, timeout_s=args.wait_timeout_s))

    try:
        results = asyncio.run(
            _send_assessment(
                green_url=args.green_url,
                purple_url=args.purple_url,
                config=config,
                timeout_s=float(args.timeout_s),
            )
        )
    except Exception as exc:
        _fail(f"assessment failed: {type(exc).__name__}: {exc}")

    results_path, evidence_dirs = _validate_outputs(results=results)

    # Write a small pointer file for convenience (host bind-mount: ../../../runs).
    out_dir = Path("runs") / f"phase1_smoke_{_now_ms()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results_artifact.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("[phase1_smoke] OK: results.json + evidence pack generated", flush=True)
    print(f"[phase1_smoke] results_path={results_path}", flush=True)
    for idx, p in enumerate(evidence_dirs):
        print(f"[phase1_smoke] evidence_dir[{idx}]={p}", flush=True)
    print(f"[phase1_smoke] artifact_path={out_dir / 'results_artifact.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
