"""Oracle Zoo registry.

Step 0 goal: adding a new oracle should not require changing runner code.
New oracles register themselves here (or are auto-discovered in the future).
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, Mapping, Optional

from mas_harness.oracles.zoo.base import Oracle

OracleFactory = Callable[[Mapping[str, Any]], Oracle]

_REGISTRY: Dict[str, OracleFactory] = {}
_BUILTIN_ORACLE_MODULES = [
    "mas_harness.oracles.zoo.no_oracle",
    "mas_harness.oracles.zoo.toy",
    "mas_harness.oracles.zoo.adb_shell",
    "mas_harness.oracles.zoo.hybrid",
    "mas_harness.oracles.zoo.providers.sms",
    "mas_harness.oracles.zoo.providers.contacts",
    "mas_harness.oracles.zoo.providers.calendar",
    "mas_harness.oracles.zoo.providers.calllog",
    "mas_harness.oracles.zoo.providers.mediastore",
    "mas_harness.oracles.zoo.providers.downloads",
    "mas_harness.oracles.zoo.settings.device_time",
    "mas_harness.oracles.zoo.settings.boot_health",
    "mas_harness.oracles.zoo.settings.settings",
    "mas_harness.oracles.zoo.settings.permissions",
    "mas_harness.oracles.zoo.settings.notification_permission",
    "mas_harness.oracles.zoo.dumpsys.telephony",
    "mas_harness.oracles.zoo.dumpsys.notifications",
    "mas_harness.oracles.zoo.dumpsys.media_session",
    "mas_harness.oracles.zoo.dumpsys.connectivity",
    "mas_harness.oracles.zoo.dumpsys.location",
    "mas_harness.oracles.zoo.dumpsys.bluetooth",
    "mas_harness.oracles.zoo.dumpsys.appops",
    "mas_harness.oracles.zoo.dumpsys.package_install",
    "mas_harness.oracles.zoo.dumpsys.activity",
    "mas_harness.oracles.zoo.dumpsys.window",
    "mas_harness.oracles.zoo.utils.ui_token_match",
    "mas_harness.oracles.zoo.utils.composite",
    "mas_harness.oracles.zoo.chooser",
    "mas_harness.oracles.zoo.files.sdcard_receipt",
    "mas_harness.oracles.zoo.files.notification_listener_receipt",
    "mas_harness.oracles.zoo.files.clipboard_receipt",
    "mas_harness.oracles.zoo.files.file_hash",
    "mas_harness.oracles.zoo.host.host_artifact_json",
    "mas_harness.oracles.zoo.host.network_receipt",
    "mas_harness.oracles.zoo.host.network_proxy",
    "mas_harness.oracles.zoo.sqlite.pull_query",
    "mas_harness.oracles.zoo.sqlite.root_query",
]
_BUILTINS_LOADED = False


def register_oracle(plugin_id: str) -> Callable[[OracleFactory], OracleFactory]:
    """Decorator to register an oracle factory function."""

    def _decorator(factory: OracleFactory) -> OracleFactory:
        if plugin_id in _REGISTRY:
            raise ValueError(f"duplicate oracle plugin id: {plugin_id}")
        _REGISTRY[plugin_id] = factory
        return factory

    return _decorator


def available_oracles() -> Dict[str, OracleFactory]:
    load_builtin_oracles()
    return dict(_REGISTRY)


def make_oracle(oracle_cfg: Optional[Mapping[str, Any]]) -> Oracle:
    """Factory for oracles (registry-based).

    Supports both legacy naming (success_oracle.type) and a clearer field
    (success_oracle.plugin).
    """

    load_builtin_oracles()

    if not oracle_cfg:
        # Default to a trivial hard oracle that always fails (forces task authors to
        # think about success definition). For smoke tests we always provide one.
        plugin = "toy_success_after_steps"
        cfg = {"steps": 10**9}
        return _REGISTRY[plugin](cfg)

    plugin = oracle_cfg.get("plugin") or oracle_cfg.get("type")
    if not isinstance(plugin, str):
        raise ValueError("oracle config must contain 'type' or 'plugin' string")

    factory = _REGISTRY.get(plugin)
    if factory is None:
        raise ValueError(f"unknown oracle plugin: {plugin}")
    return factory(dict(oracle_cfg))


def load_builtin_oracles() -> None:
    """Import built-in oracle modules so they can register their plugins."""

    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return

    for module_name in _BUILTIN_ORACLE_MODULES:
        importlib.import_module(module_name)
    _BUILTINS_LOADED = True


# Load built-ins on import (keep this list small and dependency-free).
load_builtin_oracles()
