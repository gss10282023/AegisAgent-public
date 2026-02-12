import os
import sys
from pathlib import Path

try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass

try:
    from phone_agent import PhoneAgent
    from phone_agent.agent import AgentConfig
    from phone_agent.model import ModelConfig
except ModuleNotFoundError:
    here = Path(__file__).resolve().parent
    open_autoglm_dir = here / "Open-AutoGLM-main"
    if (open_autoglm_dir / "phone_agent").is_dir():
        sys.path.insert(0, str(open_autoglm_dir))

    from phone_agent import PhoneAgent
    from phone_agent.agent import AgentConfig
    from phone_agent.model import ModelConfig


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


# Configure model
model_config = ModelConfig(
    base_url=(
        os.getenv("PHONE_AGENT_BASE_URL")
        or os.getenv("AUTOGLM_BASE_URL")
        or "https://open.bigmodel.cn/api/paas/v4"
    ),
    model_name=os.getenv("PHONE_AGENT_MODEL") or os.getenv("AUTOGLM_MODEL_NAME") or "autoglm-phone",
    api_key=(
        os.getenv("PHONE_AGENT_API_KEY")
        or os.getenv("AUTOGLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or "EMPTY"
    ),
)

config = AgentConfig(
    max_steps=int(os.getenv("PHONE_AGENT_MAX_STEPS") or os.getenv("AUTOGLM_MAX_STEPS") or "20"),  # 每个任务最大步数
    verbose=_env_bool("PHONE_AGENT_VERBOSE", _env_bool("AUTOGLM_VERBOSE", True)),  # 打印调试信息(包括思考过程和执行动作)
    lang=os.getenv("PHONE_AGENT_LANG") or os.getenv("AUTOGLM_LANG") or "en",  # 语言选择：cn(中文)或 en(英文)
    device_id=os.getenv("PHONE_AGENT_DEVICE_ID") or os.getenv("AUTOGLM_DEVICE_ID") or None,
)

# 创建 Agent
agent = PhoneAgent(model_config=model_config, agent_config=config)

# 执行任务
task = os.getenv("PHONE_AGENT_TASK") or os.getenv("AUTOGLM_TASK") or "Open Chrome, search for Open-AutoGLM"
result = agent.run(task)
print(result)
