# mas-harness 文档索引

`mas-harness` 负责把一个 case（task/policy/eval）在某个环境画像里执行成 episode，并产出可复现/可审计的 evidence bundle；同时提供 Oracles/Checkers、接入层（adapters/ingestion）、以及 conformance/agentctl 等开发者入口。

## 文档入口

- Phases：`mas-harness/docs/phases/`
  - Phase 0：`mas-harness/docs/phases/phase0.md`
  - Phase 2：`mas-harness/docs/phases/phase2.md`
  - Phase 3：`mas-harness/docs/phases/phase3.md`
- Architecture：`mas-harness/docs/architecture/`
  - Harness TCB：`mas-harness/docs/architecture/harness_tcb.md`
  - Evidence contract：`mas-harness/docs/architecture/evidence_contract.md`
  - Action trace 语义：`mas-harness/docs/architecture/action_trace.md`
- Operations：`mas-harness/docs/operations/runbook_local.md`

## 模块导航（按职责）

- **Evidence（采证据/合同）**
  - evidence writer（主入口）：`mas-harness/src/mas_harness/evidence/`
  - Evidence Pack 合同：`mas-harness/src/mas_harness/evidence/evidence_pack.py`
  - L1/L2 action evidence：`mas-harness/src/mas_harness/action_evidence/`
  - 动作归一化（action schema）：`mas-harness/src/mas_harness/evidence/action_normalizer.py`
- **Runtime（跑起来的执行链路）**
  - Android 执行与控制（L0）：`mas-harness/src/mas_harness/android/`
  - 运行期共享工具（reset/ref checks）：`mas-harness/src/mas_harness/runtime/`
- **Spec（schemas/case 校验）**
  - spec loader + 校验入口：`mas-harness/src/mas_harness/spec/`
- **Integration（接入/一致性验证）**
  - Agent adapters + registry：`mas-harness/src/mas_harness/integration/agents/`
  - Trajectory ingestion：`mas-harness/src/mas_harness/integration/ingestion/`
  - Conformance suite runner：`mas-harness/src/mas_harness/integration/conformance/`
  - CLI 入口（薄）：`mas-harness/src/mas_harness/cli/`
- **Oracles（判成败/可扩展 oracle zoo）**
  - 入口：`mas-harness/src/mas_harness/oracles/`
  - Oracle Zoo：`mas-harness/src/mas_harness/oracles/zoo/`
- **Reporting（汇总/统计口径）**
  - 汇总工具：`mas-harness/src/mas_harness/reporting/`
  - Phase3 bucketing 口径：`mas-harness/src/mas_harness/reporting/phase3_bucketing.py`
  - 通用汇总（summary.json 聚合）：`mas-harness/src/mas_harness/reporting/aggregate.py`
  - CLI：`mas-harness/src/mas_harness/cli/report.py`
- **Phases（阶段性支架）**
  - Phase0 运行治理产物：`mas-harness/src/mas_harness/phases/phase0_artifacts.py`
  - Phase3 冒烟用例生成：`mas-harness/src/mas_harness/phases/phase3_smoke_cases.py`
- **Examples（示例/玩具环境）**
  - Toy env/agent（无 Android 的冒烟跑通）：`mas-harness/src/mas_harness/examples/`

## Phase0/2/3 关键合同字段在哪里维护

- **Phase0**：`run_manifest.json` / `env_capabilities.json` 的生成与字段校验 —— `mas-harness/src/mas_harness/phases/phase0_artifacts.py`
- **Phase2**：hard-oracle/receipt/network/sqlite 等判定逻辑 —— `mas-harness/src/mas_harness/oracles/zoo/`
- **Phase3**：evidence bundle（obs/action/oracle trace 等） —— `mas-harness/src/mas_harness/evidence/` + `mas-harness/src/mas_harness/action_evidence/`

更细粒度的文件导航见：`mas-harness/FILEMAP.md`。
