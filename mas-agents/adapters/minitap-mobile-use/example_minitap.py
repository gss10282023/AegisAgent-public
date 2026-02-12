import asyncio
from pathlib import Path

from minitap.mobile_use.sdk import Agent
from minitap.mobile_use.sdk.types import AgentProfile
from minitap.mobile_use.sdk.builders import Builders

async def main():
    llm_config_path = Path(__file__).resolve().with_name("llm-config.override.jsonc")

    # Create an agent profile
    default_profile = AgentProfile(
        name="default",
        from_file=str(llm_config_path),
    )

    # Configure the agent
    agent_config = Builders.AgentConfig.with_default_profile(default_profile).build()
    agent = Agent(config=agent_config)

    try:
        # Initialize the agent (connect to the first available device)
        await agent.init()

        # Define a simple task goal
        result = await agent.run_task(
            goal="Open the calculator app, calculate 123 * 456, and tell me the result",
            name="calculator_demo"
        )

        # Print the result
        print(f"Result: {result}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Always clean up when finished
        await agent.clean()

if __name__ == "__main__":
    asyncio.run(main())
