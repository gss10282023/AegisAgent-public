from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_repo_dotenv(repo_root: Path) -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=repo_root / ".env")
    except Exception:
        return


def _add_agentprog_source_to_syspath(repo_root: Path) -> Path:
    agentprog_root = (repo_root / "mas-agents" / "adapters" / "agentprog" / "AgentProg").resolve()
    if not agentprog_root.exists():
        raise RuntimeError(f"Missing AgentProg source dir: {agentprog_root}")
    sys.path.insert(0, str(agentprog_root))
    return agentprog_root


def _patch_ui_tars_to_openrouter(openrouter_api_key: str, openrouter_base_url: str, ui_tars_model: str) -> None:
    import agentprog.all_utils.ui_tars_utils as ui_tars_utils

    if getattr(ui_tars_utils.init_get_ui_tars_response, "__aegis_patched__", False):
        return

    original = ui_tars_utils.init_get_ui_tars_response

    def patched_init_get_ui_tars_response(*args, **kwargs):
        init_response_args = kwargs.get("init_response_args")
        if init_response_args is not None:
            init_response_args.model = ui_tars_model
            init_response_args.base_url = openrouter_base_url
            init_response_args.api_key = openrouter_api_key
        return original(*args, **kwargs)

    setattr(patched_init_get_ui_tars_response, "__aegis_patched__", True)
    ui_tars_utils.ui_tars_client = None
    ui_tars_utils.init_get_ui_tars_response = patched_init_get_ui_tars_response

    os.environ.setdefault("DOUBAO_BASE_URL", openrouter_base_url)
    os.environ.setdefault("ARK_API_KEY", openrouter_api_key)


def _patch_prompt_image_placeholders() -> None:
    """
    AgentProg uses a custom image placeholder syntax: `{|{|...|}|}`.

    Some upstream code builds strings like:
      "{|{|/abs/path/to/screenshot.png|}|}\nText Description: ..."
    i.e. the placeholder is embedded in a larger string. The upstream Prompt.append()
    only registers images when the entire string is exactly the placeholder, so
    Prompt.serialize() later can't find the image and sends the path as plain text.

    This runtime patch keeps AgentProg as a black box while making embedded image
    placeholders load from disk when they point to existing files.
    """
    import agentprog.all_utils.general_utils as general_utils

    original_append = general_utils.Prompt.append
    if getattr(original_append, "__aegis_patched__", False):
        return

    image_start = general_utils.IMAGE_START
    image_end = general_utils.IMAGE_END

    def patched_append(self, content):  # type: ignore[no-untyped-def]
        if isinstance(content, str) and image_start in content:
            search_from = 0
            while True:
                start_idx = content.find(image_start, search_from)
                if start_idx == -1:
                    break
                end_idx = content.find(image_end, start_idx + len(image_start))
                if end_idx == -1:
                    break
                image_id = content[start_idx + len(image_start) : end_idx]
                if image_id and image_id not in self.images:
                    try:
                        if Path(image_id).exists():
                            self.images[image_id] = image_id
                    except Exception:
                        pass
                search_from = end_idx + len(image_end)
        return original_append(self, content)

    setattr(patched_append, "__aegis_patched__", True)
    general_utils.Prompt.append = patched_append


def _patch_prompt_serialize_autoload_images() -> None:
    """
    AgentProg often embeds `{|{|/abs/path.png|}|}` inside longer strings and also
    sometimes constructs Prompt(prompt_str, images_dict) with an empty dict.

    In those cases the placeholder is present but `Prompt.images` doesn't contain
    a mapping for that path, so serialize() treats it as plain text and you see:
      "image not found in current prompt, using image id directly"

    This patch scans the prompt for placeholders right before serialization and,
    when the image id is an existing file path, registers it into `self.images`.
    """
    import agentprog.all_utils.general_utils as general_utils

    original_serialize = general_utils.Prompt.serialize
    if getattr(original_serialize, "__aegis_patched__", False):
        return

    image_start = general_utils.IMAGE_START
    image_end = general_utils.IMAGE_END

    def patched_serialize(self, mode="openai"):  # type: ignore[no-untyped-def]
        if isinstance(getattr(self, "prompt_template", None), str) and image_start in self.prompt_template:
            template = self.prompt_template
            idx = 0
            while True:
                start_idx = template.find(image_start, idx)
                if start_idx == -1:
                    break
                end_idx = template.find(image_end, start_idx + len(image_start))
                if end_idx == -1:
                    break
                image_id = template[start_idx + len(image_start) : end_idx]
                if image_id and image_id not in self.images:
                    try:
                        if Path(image_id).exists():
                            self.images[image_id] = image_id
                    except Exception:
                        pass
                idx = end_idx + len(image_end)
        return original_serialize(self, mode=mode)

    setattr(patched_serialize, "__aegis_patched__", True)
    general_utils.Prompt.serialize = patched_serialize


def main() -> int:
    repo_root = Path(__file__).resolve().parents[3]
    _load_repo_dotenv(repo_root)
    _add_agentprog_source_to_syspath(repo_root)

    openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    if not openrouter_api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY. Put it in repo-root .env or export it in your shell.")

    openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    os.environ.setdefault("OPENROUTER_API_BASE", openrouter_base_url)
    os.environ.setdefault("OPENROUTER_API_KEY", openrouter_api_key)

    agent_model = os.getenv("AGENTPROG_MODEL", "openrouter/google/gemini-2.5-pro")
    ui_tars_model = os.getenv("UI_TARS_MODEL", "bytedance/ui-tars-1.5-7b")

    _patch_ui_tars_to_openrouter(
        openrouter_api_key=openrouter_api_key,
        openrouter_base_url=openrouter_base_url,
        ui_tars_model=ui_tars_model,
    )
    _patch_prompt_image_placeholders()
    _patch_prompt_serialize_autoload_images()

    from agentprog import AgentProgConfig, agentprog_pipeline
    from agentprog.all_utils.general_utils import InitResponseArgs

    try:
        import litellm

        # OpenRouter may reject provider-specific params (e.g. `thinking` for Gemini).
        # Dropping unsupported params is the safest default for black-box integration.
        litellm.drop_params = True
        # LiteLLM prints an ANSI-red "Provider List" line on some mapping errors.
        # This keeps the terminal output readable; real exceptions still raise.
        litellm.suppress_debug_info = True
    except Exception:
        pass

    model_args = InitResponseArgs(
        model=agent_model,
        api_key=openrouter_api_key,
        record_completion_statistics=True,
        completion_kwargs={
            "temperature": float(os.getenv("AGENTPROG_TEMPERATURE", "0.6")),
            "stream": False,
        },
    )

    config = AgentProgConfig(
        task_description=os.getenv("AGENTPROG_TASK", "Open Chrome, search for AgentProg."),
        serial=os.getenv("AGENTPROG_SERIAL", "emulator-5554"),
        workflow_model_args=model_args,
        executor_model_args=model_args,
    )

    print(f"[AgentProg] LLM={agent_model}  UI-TARS={ui_tars_model}  base_url={openrouter_base_url}")
    agentprog_pipeline(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
