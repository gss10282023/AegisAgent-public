import asyncio
import os
from pathlib import Path

from droidrun import DroidAgent, DroidrunConfig
from droidrun import AgentConfig, CodeActConfig, ManagerConfig, ExecutorConfig, ScripterConfig, AppCardConfig,LoggingConfig,DeviceConfig
from llama_index.llms.openai import OpenAI
from llama_index.llms.openai_like import OpenAILike
from dotenv import load_dotenv

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


async def main() -> None:
    load_dotenv()
    openrouter_api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not openrouter_api_key:
        raise RuntimeError(
            "Missing API key. Set `OPENROUTER_API_KEY` (recommended) or `OPENAI_API_KEY` in your environment or `.env`."
        )

    config = DroidrunConfig(
        agent=AgentConfig(
            max_steps=15,               # Max execution steps
            reasoning=True,             # Enable Manager/Executor workflow
            after_sleep_action=1.0,      # Wait after actions (seconds)
            wait_for_stable_ui=0.3,      # Wait for UI to stabilize (seconds)
            manager=ManagerConfig(vision=True),
            executor=ExecutorConfig(vision=True),
        ),
        logging=LoggingConfig(
            debug=True,                 # Enable debug logs
            save_trajectory="action",   # "none" | "step" | "action"
            trajectory_path="myrecords",# Directory for trajectory files
            trajectory_gifs=False,      # Save trajectory as animated GIFs
            rich_text=False,            # Rich text formatting in logs
        ),
        device=DeviceConfig(
            serial="emulator-5554",          # Device serial/IP (None = auto-detect)
            platform="android",   # "android" or "ios"
            use_tcp=False,        # TCP vs content provider communication
        )
    )

    # Create agent
    # LLMs are automatically loaded from config.llm_profiles
    agent = DroidAgent(
        goal="Open Settings and check battery level",
        config=config,
        llms={
        "manager": OpenAI(model="gpt-5.1", api_base=OPENROUTER_BASE_URL, api_key=openrouter_api_key),  # Planning
        "executor": OpenAILike(
            model="google/gemini-3-flash-preview",
            api_base=OPENROUTER_BASE_URL,
            api_key=openrouter_api_key,
            is_chat_model=True,
        ),  # Action selection
        "codeact": OpenAI(model="gpt-5-mini", api_base=OPENROUTER_BASE_URL, api_key=openrouter_api_key),  # Code generation
        "text_manipulator": OpenAI(model="gpt-5-mini", api_base=OPENROUTER_BASE_URL, api_key=openrouter_api_key),  # Text input
        "app_opener": OpenAI(model="gpt-5-mini", api_base=OPENROUTER_BASE_URL, api_key=openrouter_api_key),  # App launching
        "scripter": OpenAI(model="gpt-5-mini", api_base=OPENROUTER_BASE_URL, api_key=openrouter_api_key),  # Off-device scripts
        "structured_output": OpenAI(model="gpt-5-mini", api_base=OPENROUTER_BASE_URL, api_key=openrouter_api_key),  # Output extraction
    }
    )

    # Run agent
    result = await agent.run()

    # Check results (result is a ResultEvent object)
    print(f"Success: {result.success}")
    print(f"Reason: {result.reason}")
    print(f"Steps: {result.steps}")

if __name__ == "__main__":
    asyncio.run(main())
