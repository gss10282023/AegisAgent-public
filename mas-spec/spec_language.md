# Spec language: 4‑Spec → CaseBundleSpec（v0.3.2）

本章定义 MAS‑Spec 的“可分发合同”（CaseBundleSpec）与相关 schemas。

> v0.3.2 的关键原则：**评分只依赖运行时 Evidence→Facts→Assertions**。  
> Spec language 负责声明任务/攻击/策略/评测配置，以及（可选）生成侧资产与治理字段。

---

## 1. CaseBundleSpec（分发单位）

CaseBundleSpec 是一个逻辑概念：**同一个 case 的四份 spec**（以及可选 assets/治理字段）共同构成一个“可运行、可审计”的分发单位。

最小必含：

- `TaskSpec`（`task.yaml/json`）
- `PolicySpec`（`policy.yaml/json`）
- `EvalSpec`（`eval.yaml/json`）
- `AttackSpec`（`attack.yaml/json`，对 adversarial/hazard case 必填；benign case 可为空/缺省）

推荐包含（不破坏兼容）：

- `assets[]`（AssetSpec）
- `oracle_constraints`（time window/唯一 token/receipt paths/reset strategy）
- `capabilities_required`（case 级能力需求）
- `gen_meta`（generator_version/seed/coach_card_ids/token_governor_profile…）

---

## 2. TaskSpec（任务合同）

现有 schema：`mas-spec/schemas/task_schema.json`。

v0.3.2 推荐扩展字段（保持向后兼容）：

- `impact_level`：probe | canary | highrisk（口径/分桶）
- `success_assertions[]`：引用 assertion_id 或模板化规则
- `capabilities_required[]`：若成功判定依赖硬证据（例如 provider/sqlite/receipt）

---

## 3. AttackSpec（攻击/干扰合同）

现有 schema：`mas-spec/schemas/attack_schema.json`。

v0.3.2 写死：

- `impact_level`：probe/canary/highrisk（报告中必须分桶；主结论默认基于 canary/highrisk）
- `primitive/boundary/channel`：用于归因与覆盖统计
- `tier/hazard_class`：对抗 tiers（T0–T2）与 hazards（U0/A0）

---

## 4. PolicySpec（策略合同）

现有 schema：`mas-spec/schemas/policy_schema.json`。

PolicySpec 必须可执行，并且能自动编译 Baseline Safety Assertions：

- Readable/Writable sets
- Flow rules（读→写映射约束）
- High‑risk actions（必须 handshake confirmation）
- Budgets（SUT budgets；生成侧 budgets 仅用于治理，不进 SUT 分数）
- Binding requirements（SP8 硬契约：contact_id/target_package/web_origin/amount/sink_type…）

---

## 5. EvalSpec（评测配置合同）

现有 schema：`mas-spec/schemas/eval_schema.json`。

v0.3.2 推荐明确：

- `baseline_safety_assertions_mode=compiled_from_policy`（默认）
- `detectors_enabled` / `assertions_enabled`
- `reporting_buckets`（SP/primitive/boundary/impact + trust buckets）

---

## 6. 生成侧合同（Discover/CaseGen，不改变 SUT 主评分）

当 CaseGen 产生 assets 或走 Codex Workbench 时，需要额外合同（schemas 位于 `mas-spec/schemas/`）：

- `AssetSpec`：描述“需要什么资产/注入实体”
- `AssetManifest`：描述“生成了什么（path/hash/bytes/refs）”
- `WorkOrder`：统一工单（generate_assets/apply_patch）
- `WorkReport`：结构化回执（文件清单 + sha256 + 验证结果 + 失败原因）

这些字段进入 Evidence Pack（`asset_trace/codex_trace/work_reports`），用于复现/归因/治理；**不改变** SUT 的 PASS/FAIL 口径。

