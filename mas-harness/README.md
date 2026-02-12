# mas-harness

`mas-harness` 是 MAS 项目的评测执行与审计 TCB（Runner + Evidence + Oracles）：负责“跑任务、采证据、判成败、做接入与一致性验证”。

常用入口（从 repo 根目录执行；`Makefile` 已内置 `PYTHONPATH=mas-harness/src`）：

- 跑公开用例：`make run_public`
- 统一 runner（runnable / ingest）：`PYTHONPATH=mas-harness/src python -m mas_harness.cli.run_agent --help`
- 本地交互/冒烟：`PYTHONPATH=mas-harness/src python -m mas_harness.cli.agentctl --help`
- conformance suite：`PYTHONPATH=mas-harness/src python -m mas_harness.integration.conformance.suite --help`
- 审计 evidence bundle：`PYTHONPATH=mas-harness/src python -m mas_harness.tools.audit_bundle --help`

更多模块导航与“Phase0/2/3 合同字段在哪里维护”见：`mas-harness/docs/INDEX.md`。
