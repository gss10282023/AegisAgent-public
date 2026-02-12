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
        id="agentbeats_assess",
        name="AgentBeats Assessment Orchestrator (Step 6: MAS harness)",
        description=(
            "Receives AgentBeats assessment_request; validates remote ADB connectivity; "
            "runs MAS harness with agent_id=a2a_remote against the provided purple endpoint; "
            "returns results.json + evidence pack directory."
        ),
        tags=["agentbeats", "green", "phase1", "scaffold"],
        examples=[
            (
                '{"participants":{"purple":"http://127.0.0.1:9010/"},"config":'
                '{"seed":0,"num_tasks":1,"case_set":"mas-public","variants":["benign"],'
                '"adb_server":"host.docker.internal:5037","android_serial":"emulator-5554"}}'
            )
        ],
    )

    return AgentCard(
        name="MAS Green Agent (Phase 1)",
        description="Green agent scaffold for AgentBeats Phase 1 (Step 6: MAS harness runner).",
        url=card_url,
        version="0.1.1",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the MAS green A2A server (AgentBeats Phase 1)."
    )
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind the server")
    parser.add_argument("--port", type=int, default=9009, help="Port to bind the server")
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
