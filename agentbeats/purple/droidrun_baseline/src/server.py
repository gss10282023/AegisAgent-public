from __future__ import annotations

import argparse
import logging

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from executor import Executor


def _make_agent_card(*, card_url: str) -> AgentCard:
    skill = AgentSkill(
        id="mobile_agent",
        name="Mobile Agent (baseline droidrun)",
        description=(
            "A2A wrapper for baseline purple agent (Step 5). "
            "Runs droidrun against an Android device reachable via "
            "a remote adb server (ADB_SERVER_SOCKET)."
        ),
        tags=["agentbeats", "purple", "phase1", "baseline"],
        examples=[
            (
                '{"case_id":"dummy","variant":"v1","goal":"Open Settings",'
                '"adb_server":"host.docker.internal:5037","android_serial":"emulator-5554",'
                '"timeouts":{"total_s":30,"max_steps":50}}'
            )
        ],
    )

    return AgentCard(
        name="MAS Purple Baseline (droidrun)",
        description="Baseline purple agent for AgentBeats Phase 1 (Step 5: droidrun wrapper).",
        url=card_url,
        version="0.2.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the MAS baseline purple A2A server (AgentBeats Phase 1)."
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9010, help="Port to bind the server")
    parser.add_argument("--card-url", type=str, help="URL to advertise in the agent card")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")

    card_url = args.card_url or f"http://{args.host}:{args.port}/"
    agent_card = _make_agent_card(card_url=card_url)

    request_handler = DefaultRequestHandler(
        agent_executor=Executor(),
        task_store=InMemoryTaskStore(),
    )
    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )
    uvicorn.run(server.build(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
