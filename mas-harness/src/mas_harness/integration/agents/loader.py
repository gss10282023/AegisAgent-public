from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from mas_harness.integration.agents.base import AgentAdapter, AgentAdapterError


def _load_module_from_path(path: Path) -> ModuleType:
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    module_name = f"mas_adapter_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AgentAdapterError(f"failed to load adapter module: {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_agent_adapter(adapter_path: Path) -> AgentAdapter:
    module = _load_module_from_path(adapter_path)

    adapter: Any
    if hasattr(module, "create_adapter"):
        adapter = getattr(module, "create_adapter")()
    elif hasattr(module, "ADAPTER"):
        adapter = getattr(module, "ADAPTER")
    elif hasattr(module, "Adapter"):
        adapter = getattr(module, "Adapter")()
    else:
        raise AgentAdapterError(
            f"adapter module must export create_adapter(), ADAPTER, or Adapter: {adapter_path}"
        )

    if not isinstance(adapter, AgentAdapter):
        raise AgentAdapterError(
            f"loaded adapter does not implement AgentAdapter protocol: {adapter_path}"
        )
    return adapter
