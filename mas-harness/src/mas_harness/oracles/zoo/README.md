# Oracle Zoo

This folder contains **Oracle Zoo v1+** (Phase 2).

Design goal: keep all task success oracles in a single, discoverable place.

Recommended structure (Step 0):

```
mas_harness/oracles/zoo/
  __init__.py
  README.md
  base.py
  registry.py
  providers/
    __init__.py
    sms.py
    contacts.py
    calendar.py
    calllog.py
    mediastore.py
  settings/
    __init__.py
    settings.py
    device_time.py
    boot_health.py
  dumpsys/
    __init__.py
    telephony.py
    notifications.py
    window.py
  files/
    __init__.py
    sdcard_receipt.py
    file_hash.py
  host/
    __init__.py
    host_artifact_json.py
    network_receipt.py
    network_proxy.py
  sqlite/
    __init__.py
    pull_query.py
    root_query.py
  utils/
    __init__.py
    adb_parsing.py
    time_window.py
    hashing.py
    capabilities.py
```

## Naming conventions

- File name: `snake_case.py`
- Oracle class: `CamelCaseOracle` (suffix `Oracle`)
- Plugin id: `snake_case` (typically equals `oracle_id`)
- All oracle implementations live under `mas_harness/oracles/zoo/**`.

## Adding a new oracle

1. Add a module under the right subfolder.
2. Implement an `Oracle` subclass.
3. Register it in `mas_harness.oracles.zoo.registry` (currently: add your module to
   `_BUILTIN_ORACLE_MODULES`, or otherwise ensure your module is imported at runtime).
4. Reference it from task specs via `success_oracle.plugin` (or legacy `success_oracle.type`).
