from __future__ import annotations

import argparse
import asyncio
import json

import httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.types import Message, Part, Role, TextPart


async def _send(*, green_url: str, purple_url: str, config_json: str) -> None:
    config = json.loads(config_json) if config_json else {}
    payload = {"participants": {"purple": purple_url}, "config": config}
    msg_text = json.dumps(payload)

    async with httpx.AsyncClient(timeout=30) as httpx_client:
        resolver = A2ACardResolver(httpx_client=httpx_client, base_url=green_url)
        agent_card = await resolver.get_agent_card()
        client = ClientFactory(
            ClientConfig(httpx_client=httpx_client, streaming=True)
        ).create(agent_card)

        msg = Message(
            kind="message",
            role=Role.user,
            parts=[Part(TextPart(text=msg_text))],
            message_id="step1-smoke",
        )

        async for event in client.send_message(msg):
            print(event)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a minimal AgentBeats assessment_request to green (Step 1)."
    )
    parser.add_argument("--green-url", default="http://127.0.0.1:9009", help="Green agent base URL")
    parser.add_argument(
        "--purple-url",
        default="http://127.0.0.1:9010",
        help="Purple agent base URL",
    )
    parser.add_argument(
        "--config-json",
        default='{"seed":0,"num_tasks":1}',
        help="JSON string for assessment_request.config",
    )
    args = parser.parse_args()

    asyncio.run(
        _send(
            green_url=args.green_url,
            purple_url=args.purple_url,
            config_json=args.config_json,
        )
    )


if __name__ == "__main__":
    main()
