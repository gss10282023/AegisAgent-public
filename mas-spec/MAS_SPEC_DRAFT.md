# MAS‑Spec v0.3.2（Audit‑First + Detector/Assertion + Discover‑Integrated）

Mobile Agent Security Executable Specification（可执行移动端智能体安全规范）

MAS‑Spec 是一份 **executable specification（可执行规范）**：

- 规范正文（可引用于论文 / standard / proposal）
- 与之配套的 **Spec language（YAML/JSON）+ JSON Schemas**（可机检）
- 可复现评估主干：**Evidence → Detectors → Facts → Assertions → Metrics**

## v0.3.2 写死的口径（与 `docs/plans/方案.md` 对齐）

1) **Hard Facts 默认**：canary/highrisk 的成功与安全合规，必须以 `device_query/receipt/host artifact/sqlite/dumpsys/settings/package diff` 等硬证据为主干。
2) **Oracle Framework 是“库”**：沉淀为 **Detector Zoo（facts）+ Assertion Zoo（PASS/FAIL/INCONCLUSIVE）**，避免从零写 verifier。
3) **INCONCLUSIVE/Applicability 一级公民**：每条 assertion 必须输出 `PASS/FAIL/INCONCLUSIVE` + `inconclusive_reason` + `applicable/applicability`。
4) **动作证据链分级 L0–L3 + ref_obs_digest 绑定**：坐标/索引类动作必须绑定 `ref_obs_digest`，不匹配则拒绝执行并归因。
5) **三字段防误用**：`env_profile / evidence_trust_level / oracle_source` 必须进入 run 与报告；主结论默认只统计 `tcb_captured + device_query` 子集。
6) **外部模型调用可审计可复现**：必须提供逐步 `agent_call_trace.jsonl`（provider/model/params/digests/latency/tokens/errors）。
7) **Discover 一等公民但不污染主评分**：生成侧证据进入 bundle（asset_trace/codex_trace/work reports），但 SUT 的 PASS/FAIL 仍只由运行时 facts/assertions 决定。

---

## 规范正文（v0.3.2 章节拆分）

1) [System model](system_model.md)
   - Principals（User/Agent/Environment/Benchmark Builder）
   - Trust Boundaries（B0–B6）
   - 闭环形式化 O/S/A/P/E + Discover 元闭环

2) [Threat model](threat_model.md)
   - 对抗者能力分级 T0/T1/T2
   - hazards（U0/A0 + G0–G3）
   - TCB 假设（强调 hard facts 主干）

3) [Mechanism primitives](primitives.md)
   - 6 primitives（P1–P6）+ channels + impact levels（probe/canary/highrisk）

4) [Policy model](policy_origin_sets.md)
   - Readable/Writable/Flow rules/High‑Risk/Budgets/Binding
   - Policy → Baseline Safety Assertions（硬要求）

5) [Interaction protocols](protocols.md)
   - Handshake confirmation + Clarification
   - **确认/澄清 UI 由 Harness 渲染**（TCB）

6) [Oracle framework](oracle_framework.md)
   - Evidence Pack（Hard Facts 入口）
   - Detector Zoo（facts）
   - Assertion Zoo（PASS/FAIL/INCONCLUSIVE）+ Applicability

7) [Security properties & violations](properties_violations.md)
   - SP1–SP8（规范性）
   - Violations（可审计后果）
   - 与 facts/assertions 的映射要求

8) [Metrics & reporting](metrics_reporting.md)
   - paired protocol + BSR/BF/RSR/VR
   - `assertion_applicable_rate / assertion_inconclusive_rate`
   - 信任分桶 + 主结论/外部有效性双视图

9) [Spec language](spec_language.md)
   - 4‑Spec（task/policy/eval/attack）→ CaseBundleSpec（分发单位）
   - AssetSpec/AssetManifest/WorkOrder/WorkReport（生成侧治理）

10) [Lifecycle](lifecycle.md)
   - Audit‑First → Discover → Enforce（可选）

---

## Spec language（JSON Schemas）

位于 `mas-spec/schemas/`：

- `task_schema.json`
- `attack_schema.json`
- `policy_schema.json`
- `eval_schema.json`
- （Discover 侧扩展）`asset_schema.json`、`asset_manifest_schema.json`、`work_order.schema.json`、`work_report.schema.json`

## 覆盖矩阵模板

位于 `mas-spec/coverage_matrix_template.csv`（P‑01…P‑15 占位映射；字段与 v0.3.2 口径对齐）。
