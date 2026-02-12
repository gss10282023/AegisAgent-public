## 总体原则（v3.2，对齐 Detector+Assertion + Audit‑First）

* **Hard Facts 默认（替代 Hard Oracle 默认）**：
  canary/highrisk 的 **任务成功** 与 **安全合规** 判定，必须以 **硬证据事实（hard facts）** 为主（ContentProvider / receipt file / host artifact / settings/dumpsys / package diff / sqlite 等 `device_query` 证据）。允许 hybrid，但**hard facts 必须是主干**；截图/LLM 主观判定仅可用于复核与案例分析，不进入 core 评分口径。
* **Oracle Framework 是“库”（升级 Oracle Zoo 的表述）**：
  评测能力沉淀为两类可复用组件：
  * **Detector Zoo**：从 Evidence Pack 抽取确定性事实（facts）
  * **Assertion Zoo**：用 facts + Policy/Case 给出 PASS/FAIL/INCONCLUSIVE
    新用例原则上“配配置 + 少量 glue”，避免从零写 verifier / checker。
* **结构化解析优先于 regex（保留）**：
  `adb shell content query` / dumpsys / sqlite 输出必须结构化解析，并支持 time window + 多条件匹配；regex 仅允许临时兜底，且必须标注置信度与误判风险。
* **Anti‑gaming 写进 Detector/Assertion（小改）**：
  每个 detector/assertion 插件必须写至少一条防刷分设计（如 time window、去历史污染、双向证据、对照检查），并把关键证据 digest 与 evidence\_refs 落盘。
* **能力分层（capability tiers）写死（保留，但扩展到 detector/assertion）**：
  每个 detector/assertion/任务必须显式声明 `capabilities_required`（如 `root_required` / `pull_db_required` / `run_as_required` / `host_artifacts_required`），避免环境差异导致不可复现或“不可判定被误当成安全”。
* **执行模式明确（保留，但补一句 Audit‑First）**：
  Runner 必须支持并在每次 run 落盘声明：
  * `planner_only`：agent 只出动作，执行由 MAS 控制（最终推荐；可 enforce；可做 guard uplift）
  * `agent_driven`：agent 自己执行，MAS 旁路采证（用于快速接入/外部有效性）
    **默认主线采用 Audit‑First：即便是 agent\_driven/L1–L3，也必须尽可能基于 hard facts 完成审计判定。**
* **外部模型调用可审计可复现（保留）**：
  对 UI‑TARS7B（OpenRouter）/AutoGLM 等外部 LLM agent，必须落盘 provider/model/参数/调用摘要（digests、错误、延迟、token 统计等），支撑复现与归因。
* **证据链分“两条结论腿”，但共享同一条“事实腿”（升级表述）**：
  * **事实腿（Facts）**：由 detectors 产生，可复用、可追溯
  * **成功结论腿（Success Assertions）**：BSR/BF/RSR 的基础
  * **安全结论腿（Safety Assertions）**：VR/VR\_core 的基础
    两条结论腿必须同时稳定，且都必须能链接到 facts + evidence\_refs；否则 BSR/RSR/BF/VR 不可信。
* **INCONCLUSIVE/Applicability 是一级公民（新增，关键）**：
  每条 assertion 必须输出 `PASS/FAIL/INCONCLUSIVE`，并给出 `inconclusive_reason`（缺证据/缺能力/不适用）。报告中必须统计：
  * assertion\_applicable\_rate
  * assertion\_inconclusive\_rate
    防止把“不可判定”包装成“安全/成功”。
* **动作证据链分级（L0–L3）（保留）**：
  每个 agent adapter 必须声明 `action_trace_level`，Runner 必须生成等效动作证据链（`device_input_trace` 优先）。同时保持 `ref_obs_digest` 绑定以降低漂移噪声。
* **护栏 A：Guarded 模式有效性条件写死（保留，且明确只影响 uplift 口径）**：
  Guarded uplift 主结论必须满足 `execution_mode=planner_only` 且 `action_trace_level=L0`，否则标注 `guard_unenforced`，仅做旁路审计，不进入 uplift 主结论。
* **护栏 B：index/坐标动作必须绑定 `ref_obs_digest`（保留）**：
  executor 校验 ref 不匹配直接拒绝执行并归因 `agent_failed`，避免索引/帧漂移污染结论。
* **三态口径写死（runnable / audit\_only / unavailable）（保留）**：
  leaderboard 覆盖必须允许 audit\_only/unavailable 存在；三态进入 registry 与报告分桶，避免工程现实拖死主线，同时保证结论严谨。
* **三字段防误用（env\_profile / evidence\_trust\_level / oracle\_source）（保留，但语义更聚焦）**：
  * `env_profile`：区分 `mas_core` vs `android_world_compat`
  * `evidence_trust_level`：`tcb_captured` vs `agent_reported` vs `unknown`
  * `oracle_source`：`device_query` vs `trajectory_declared` vs `none`（注意：现在更准确叫“facts/assertions 的来源”，但字段名可保持不变以兼容）
    **报告主结论默认只用 `tcb_captured + device_query` 子集；其他子集独立展示（coverage / external validity）。**
* **Discover（CaseGen）不得绕过评分主线（新增，防止生成器污染评测）**：
  自动生成/变异 case 可以无限快，但：
  * 新增 detector/assertion 必须走单测+回归+anti‑gaming gate 才能进入 core score
  * 生成的 case 必须满足 oracle/detector constraints（可判定性优先）

### （v3.3 补充，仅影响 Phase5+；不改变你现有 Phase0–4）

* **资产生成进入证据链（Asset Trace First）**：凡是 case 依赖图片/APK/网页 payload/数据文件，必须走统一的资产工单（Codex Workbench），并落盘 `asset_trace.jsonl + codex_trace`；否则该 case 不允许晋级 public/hidden。
* **生成侧门禁三道闸**：`SpecGate → AssetGate → PatchGate`，任一失败必须归因与记录（避免 BF/flake 风暴）。
* **生成侧可持久化记忆**：CaseGen 必须有 append‑only 的 Notebook（episodes/lessons）与可重建 stats；支持“跑一段时间关掉再跑”无损恢复。
* **TokenGovernor / RetryGovernor**：把“推理强度/输出上限/上下文预算”和“Codex 返工次数”变成确定性预算信号，防止生成侧失控。

---

# Phase 0：项目治理与可复现基线（保持 v2，不变，但 v3.1 增补 0.7 外部模型调用治理）

**新增两条 oracle 治理**仍然保留：

* 0.5 oracle 复现条款（adb version、provider 路径、app 版本、oracle evidence 落盘）
* 0.6 snapshot/reset 策略先定型（AVD snapshot tag + image fingerprint）

**验收点**不变，但建议加一个“Oracle capability probe”：

* 在 CI/smoke 中输出 `env_capabilities.json`（是否 root、是否可 pull /data、是否可用 run-as、是否可写 /sdcard、是否可访问 host artifacts 路径）。

---

## Phase 0 目标

* 固定命名、范围、伦理边界、复现策略、仓库结构，避免返工。
* 把 **oracle/验证**与 **外部模型调用**也纳入复现治理（v3.1 强化）。

---

## Phase 0 交付物（v3.1 完整列表）

### 0.1 仓库结构与命名（保持不变）

* `mas-spec/`
* `mas-harness/`
* `mas-public/`
* `mas-hidden/`
* `mas-guard/`

### 0.2 伦理与双用途边界声明（规范性条款）（保持不变）

* injector 仅 emulator/testbed/mock
* 不提供真实投放脚本
* 公开 payload 降级、隐藏 seed 不公开
* 负责任披露流程

### 0.3 复现策略（保持不变）

* 固定 emulator 镜像/hash
* 固定依赖 lockfile/docker tag
* 固定 global seed + case seed
* 一键脚本 `make run_public`, `make report`

### 0.4 最小 CI（保持不变）

* schema 校验、lint、单元测试、最小 smoke test（1 个 benign）

### ✅ 0.5 oracle 复现条款（保持 v3.0）

* hard oracle 所用 ADB 命令/查询方式版本固定（adb version、content provider 路径、app 包版本）
* oracle 输出必须落盘（oracle evidence）

### ✅ 0.6 snapshot/reset 策略先定型（保持 v3.0）

* 选定第一优先 reset 机制：AVD snapshot
* 记录 snapshot tag + emulator image fingerprint

### ✅ 0.7 外部模型调用治理（v3.1 新增，面向 UI‑TARS7B / AutoGLM）

**目的**：保证“你到底跑的是什么模型/什么参数/什么 provider”可审计，可复现。

**交付物**

* [`agent_providers.md`](https://chatgpt.com/governance/agent_providers.md)
  * 允许的 provider 列表（例如 OpenRouter / 本地模型 / 其它）
  * model 名称规范（如 `ui-tars-7b` 的准确标识）
  * 默认推理参数（temperature/top\_p/max\_tokens/timeout/retry）
  * 速率限制与失败重试策略（保证跑大规模时不崩）
* `secrets/README.md`
  * API key 通过环境变量注入（禁止进 repo）
  * 运行示例与最小权限建议
* `run_manifest.json`（每次 run 必须落盘）
  * `agent_name`、`provider`、`model_id`、`base_url`
  * `execution_mode`（planner\_only/agent\_driven）
  * 推理参数、重试策略、超时、并发设置
  * git commit hash / docker tag / emulator fingerprint
  * （v3.1 新增）`env_profile`（`mas_core` / `android_world_compat`）
  * （v3.1 新增）`evidence_trust_level`（`tcb_captured` / `agent_reported` / `unknown`）
* （可选但强烈建议）`llm_cache/` 设计说明
  * 支持“回放模式”：对同一 `prompt_digest + image_digest` 复用响应，用于复现实验或离线复跑
  * 明确缓存是否启用、是否参与最终评测（避免争议）

---

## Phase 0 验收点（v3.1 完整列表）

* 新环境一次性跑通 smoke test
* 同 seed 两机 episode 结构一致
* 仓库明确写“仅测试环境”伦理边界
* hard oracle 的查询命令与输出证据可重现（同镜像同 seed）
* CI/smoke 能输出 `env_capabilities.json`
* 任意一次本地 run 能输出 `run_manifest.json`（即使是 dummy run 也要落盘结构）

---

# Phase 1：MAS‑Spec v0.x（你已完成 Phase1c，保持 v2，不变）

你已经把 impact\_level（probe/canary/highrisk）写进规范，这是后面 Oracle Zoo 的“任务分层契约”。

---

## Phase 1 目标

* 固化标准化合同：principals + boundaries、闭环 O/S/A/P/E、T0–T2+hazards、P1–P6、SP1–SP8、协议、policy 模型、证据与指标、impact\_level 规则。

---

## Phase 1 交付物（保持 v3.0）

* `system_model.md / threat_model.md / primitives.md / properties_violations.md / policy_origin_sets.md / protocols.md / metrics_reporting.md`
* schemas：`task_schema.json / attack_schema.json / policy_schema.json / eval_schema.json`
* 覆盖矩阵模板（建议升级为含 impact\_level 的维度）
  * `Boundary×Tier×Primitive×Property×ImpactLevel`

---

## Phase 1 验收点（保持 v3.0）

* 任意 case spec 能 schema 校验通过
* P‑01…P‑15 都能映射到（Boundary, Tier/Hazard, Primitive, SP, impact\_level）
* 规范明确 TCB 假设
* 明确：probe 不进 core score；canary/highrisk 为主结论证据

---

# ✅ Phase 2：Evidence Pack v0 + Oracle Zoo v1 + Reset/Snapshot Grounding v0（v3 核心更新，v3.1 进一步补齐 “Agent traces + 观测/infra”）

> v2 的 Phase2 你已经做到了“框架雏形”。v3 要求把它升级成：框架 + 一批可直接复用的 hard oracles（Oracle Zoo），并补齐 AW/MW 常用的观测与 infra 信号。
> **v3.1 在此基础上再加一条硬要求：必须补齐 agent 的调用/动作证据链**，否则 Phase3 全量接入 leaderboard agent 会不可审计、不可复现。

---

## Phase 2 目标（保持 v3.0 + v3.1 增补）

* 让你在 Phase6/7 设计新任务时，能“选一个 oracle 插件就能判定成功”，而不是每个任务手写验证逻辑。
* 让任务成功判定在 UI spoof/注入下依然可靠。
* （v3.1 增补）让任意接入的 agent（尤其是调用外部模型的）具备可审计的 **agent 调用证据链 + 动作归一化证据链**。

---

## 2.1 Oracle Zoo v1（新增：必须交付一批可复用的 oracle 插件）

### 2.1.1 Oracle 插件体系（你已有雏形，但要补两项）（保持 v3.0）

**交付物**

* `Oracle` 接口保留 pre/post 两阶段，并补充：
  * `capabilities_required`（例如 root/run-as/pull-file/host-artifacts）
  * `evidence_schema_version`
* `OracleEvidence` 标准字段（强制）
  * `oracle_name`
  * `oracle_type`（hard/soft/hybrid）
  * `phase`（pre/post）
  * `queries[]`（每条 query 包含：cmd/sql/path/url）
  * `result_digest`（hash + key fields）
  * `anti_gaming_notes`
  * `decision`（score + reason）
  * （v3.1 新增）`oracle_source`（`device_query` / `trajectory_declared` / `none`）

**验收点**

* 任意 task spec 指定一个 oracle plugin 后，runner 能自动写出 `oracle_trace.jsonl`/`oracle/` 目录证据，并在 summary 里给出 success/failure\_class。

---

### 2.1.2 Oracle Zoo v1：必须覆盖的“硬数据源”清单（保持 v3.0）

下面每一类都对应你朋友点名的 AW/MW 常用验证方式；你做完这些，后续扩任务会非常快。

#### A) ContentProvider Oracles（Android “硬数据库 API 面”）

**必须实现（v1 最小集合）**

* `SmsProviderOracle`：sent/inbox 查询、按时间窗口+recipient+body/token 多条件匹配
* `ContactsProviderOracle`：按 display\_name/phone number 查询 + 去重
* `CalendarProviderOracle`：按 title/time window 查询（或 events provider）
* `CallLogProviderOracle`：按 number/time window 查询
* `MediaStoreOracle`：图片/文件是否入库（配合下载/分享类任务）

**关键 anti‑gaming**

* 必须带 `device_epoch_time` 探针，匹配时使用时间窗口避免历史记录误判（你朋友提到的 AW 做法）。

**验收点**

* 对每个 Provider Oracle，都有：
  * 一个 benign “oracle smoke task” 能稳定判定成功
  * 一个 “UI spoof 对照” 用例：UI 显示成功但 provider 中无记录 → oracle 判定失败

#### B) Settings / System State Oracles（系统设置与基础状态）

**必须实现**

* `SettingsOracle`：`settings get/put` 读写验证（飞行模式、自动时间、亮度/字体缩放等至少一两个）
* `DeviceTimeOracle`：`adb shell date +%s` / `getprop`（用于其它 oracle 的 time window 基础）
* `BootHealthOracle`：`getprop sys.boot_completed`、网络/飞行模式、存储可写性（infra health）

**验收点**

* 在每个 episode bundle 里落盘 `device_trace.jsonl`（或 summary 里 device snapshot），能区分 `infra_failed`。

#### C) Dumpsys Oracles（system service “真实状态”）

* `TelephonyCallStateOracle`：`dumpsys telephony.registry` 解析 call state（IDLE/OFFHOOK/RINGING）
* 其它可选：`dumpsys notification`、`dumpsys window`（用于 overlay/window 级证据）

**验收点**

* dumpsys 输出要结构化解析（至少提取关键字段），不能只留 raw 文本。

#### D) File‑based Receipt Oracles（强烈建议：不需要 root 也能 hard）

这是 MobileWorld 特别实用的路线：让 testbed/mock app 把 ground truth 写到 `/sdcard/.../*.json`，oracle 直接读文件验证。

**必须实现**

* `SdcardJsonReceiptOracle`：读取固定路径 JSON（例如 sentEmail.json / sentMessage.json）并按 time window + fields 匹配
* `FileHashOracle`：文件存在 + hash/mtime window 验证（比 file\_exists 强，抗刷分）

**验收点**

* 至少 2 个任务使用 receipt oracle 做 hard success（比如“发送邮件/发送站内信/提交订单（mock）”）。

#### E) Host‑side Artifact Oracles（宿主机回执/回调文件）

MobileWorld 的 mall callback files 属于这一类：ground truth 在宿主机，不在设备。

**建议实现（v1 里至少做 1 个）**

* `HostArtifactJsonOracle`：读取 `ARTIFACTS_ROOT/.../*.json`，按 time window/fields 验证
* 同时实现 `clear_artifacts()` 作为 pre\_check 清理（避免历史污染）

**验收点**

* evidence bundle 必须包含 host artifact digest（hash + key fields），并记录路径/清理动作。

#### F) SQLite Oracles（两条路线：AW & MW）

这类 oracle 极强，但要明确环境能力。

**建议把它拆成 v1/v2：**

* v1（可选但推荐做其中一个）
  * `SqlitePullQueryOracle`（AW 风格）：pull db 到 host → python sqlite3 查询
* v2（后续增强）
  * `RootSqliteOracle`（MW 风格）：设备上 `su 0 sqlite3 ...` / `sqlite3 -json` 查询

**前置：Controller 必须补能力**

* `pull_file()` / `push_file()`
* `run_as(pkg, cmd)`（如果用得到）
* `root_shell(cmd)`（如果环境允许）

**验收点**

* 至少 1 个 AW/MW 风格任务能用 SQLite oracle 判定成功，并且 oracle 证据可审计。

---

## 2.2 Evidence Pack 扩展（v3：补齐 AW/MW 常见观测/元数据；v3.1：新增 Agent traces）

你朋友提到的观测增强非常值：它不仅帮助执行稳定，也能增强 SP2/SP8 证据。
v3.1 还要求：为 Phase3 全量接入 leaderboard agent，必须把 agent 的调用与动作归一化过程也落盘。

---

### 2.2.1 screen\_trace.jsonl（或合并进 device\_trace）（保持 v3.0）

**交付物**

* 物理/逻辑分辨率、density、orientation、可用 frame boundary
* 每次 episode start 必须记录一次（必要时每步记录）

---

### 2.2.2 ui\_dump/（保持 v3.0）

**交付物**

* `uiautomator_dump.xml`（每步或关键步）
* 仍保留你已有的 a11y tree（AW 风格），两者并存（兼容更多环境）

---

### 2.2.3 ui\_elements.jsonl（强烈建议）（保持 v3.0）

**交付物**

* 把 raw a11y forest/树解析成统一的 element list：
  * bbox、clickable、resource-id、package\_name、text/content-desc、enabled、focused

**价值（保持 v3.0）**

* 极大增强：
  * SP2（身份/来源）
  * SP8（绑定一致性）
  * P2 overlay/chooser 识别（多 window）

---

### 2.2.4 device\_trace.jsonl（v3.0 已隐含要求，v3.1 强制写清）

**交付物**

* boot\_completed / 网络 / 飞行模式 / 存储可写性 / 时间同步状态（至少包含 device\_epoch\_time probe）
* 这些字段用于：
  * oracle time window（反历史误判）
  * infra\_failed 归因（评测稳定性）

---

### 2.2.5 agent\_call\_trace.jsonl（v3.1 新增，强制）

**交付物（每条记录至少包含）**

* `step_idx`
* `agent_name`（ui-tars7b / autoglm / droidrun / ...）
* `provider`、`model_id`、`base_url`（如果是外部模型）
* `input_digest`（prompt hash + screenshot hash 等；默认不存敏感原文）
* `response_digest`（response hash；可配置是否存脱敏后的全文）
* `latency_ms`、`tokens_in/out`（可得则写）
* `error`（timeout / parse\_error / http\_error / rate\_limit / …）

---

### 2.2.6 agent\_action\_trace.jsonl（v3.1 新增，强制）

**交付物（每条记录至少包含）**

* `step_idx`
* `raw_action`（agent 原始输出动作）
* `normalized_action`（映射到 MAS Action schema 的结构化动作）
* `normalization_warnings`（坐标超界/元素缺失/不支持动作等）
* （v3.1 新增）`ref_obs_digest`（见 Phase3.7/3.8，护栏 B）

---

### 2.2.7 run\_manifest.json（v3.1 新增，强制）

**交付物（每次 run 一份）**

* `execution_mode`（planner\_only / agent\_driven）
* `agent_name` + agent 版本信息（commit/tag）
* 外部模型调用配置（provider/model/params/retry/timeout）
* emulator fingerprint + snapshot tag
* repo git commit hash / docker image tag
* （v3.1 新增）`action_trace_level`（L0/L1/L2/L3）
* （v3.1 新增）`guard_enforcement`（enforced/unenforced）
* （v3.1 新增）`env_profile`（mas\_core/android\_world\_compat）
* （v3.1 新增）`evidence_trust_level`（tcb\_captured/agent\_reported/unknown\`）

---

### 2.2 验收点（v3.0 + v3.1 合并，完整）

* 任意 episode bundle 能从 evidence 中重建：
  * 每步前台包名/Activity
  * 截图
  * uiautomator xml（至少关键步）
  * ui\_elements 列表（至少关键步）
* 任意 run 必须落盘：
  * `oracle_trace.jsonl` 或 `oracle/` 目录（pre/post）
  * `device_trace.jsonl`
  * `screen_trace.jsonl`（或合并后等效字段）
  * `agent_call_trace.jsonl`（对外部模型 agent 强制）
  * `agent_action_trace.jsonl`
  * `run_manifest.json`

---

## 2.3 Reset/Snapshot Grounding v0（保持 v2，但建议加“oracle污染清理”）（保持 v3.0）

**交付物**

* `load_snapshot(tag)` + 记录 emulator fingerprint
* 每个 oracle 插件如果会被历史数据污染，必须提供 `pre_check` 清理或用 snapshot 保证初始一致。

**验收点**

* “同镜像同 seed”重跑，oracle 的 pre/post 证据一致（允许少量无关字段差异，但关键字段一致）。

---

# ✅ Phase 3：Runner + EnvAdapter v0（v3：oracle‑rich benign 基座任务集；v3.1：全量覆盖 AndroidWorld Leaderboard Mobile Agent）

v2 已经把 “oracle-first 生命周期”写对了，v3 要求把 Phase3 的 benign 基座任务集扩到能覆盖 Oracle Zoo。
**v3.1 进一步要求：Phase3 的目标是“对 leaderboard 全量覆盖”，但不把它写成单一硬门槛：验收拆成 Phase3a/3b/3c 三层**，避免被不可得/不可复现条目拖死主线，同时仍能在论文里严谨地主张“全覆盖口径 + 可审计入口”。

---

## Phase 3 目标（v3.0 + v3.1 合并，完整）

* 让你拥有一套“只跑 benign 就能验证 Oracle Zoo 是否可用”的任务集（oracle regression suite）。
* 以后你写任何新安全用例，都能先选一个现成 oracle，避免卡在成功判定。
* （v3.1 新增）把 AndroidWorld leaderboard 上的 mobile agent 全量覆盖到 Runner，形成**可审计、可复现、可对比**的被测系统集合（runnable + audit‑only 两条腿 + unavailable 说明）。

---

## Phase 3 交付物（v3.1 完整列表）

### 3.0 Phase3 的三层验收（v3.1 新增，强制口径）

> **Phase3a/3b 为硬门槛（不拖死主线），Phase3c 为持续扩展（不断逼近“全量 runnable”）**。

* **Phase3a（硬门槛）：全榜单入口与可审计口径**
  * `leaderboard_snapshot` 固定化
  * `agent_registry` 覆盖全部条目并三态标注
  * `conformance_suite` 能跑（用于“能启动/能产证据”）
  * `audit_only` ingestion 框架可用
  * 必须落盘：`env_profile`、`evidence_trust_level`、`oracle_source`（至少在 summary/oracle\_trace）
* **Phase3b（硬门槛）：代表性 runnable agents 真跑起来支撑后续 Phase6/7/10**
  * 选择一组“代表性 runnable 子集”（例如 top‑K、或 open 且可跑的那些）
  * 跑通 conformance
  * 至少跑通 regression suite 的一部分（≥N 个 hard‑oracle benign）
  * 这些 run 的主口径必须是：`evidence_trust_level=tcb_captured` 且 `oracle_source=device_query`
* **Phase3c（软门槛/持续扩展）：其余 runnable/audit\_only/unavailable 持续补齐**
  * runnable 条目持续接入并进入批跑
  * audit\_only 持续 ingest 更多轨迹
  * unavailable 持续补“不可得证据/原因”
  * 报告里持续扩大“覆盖面统计”，但不影响主线结论的可交付性

---

### 3.1 Runner 生命周期固定（保持 v3.0）

`reset/setup → oracle pre_check → run → oracle post_check → record → score`

并且在 `summary.json` 中固定写出：

* `task_success`（score + reason）
* `failure_class`（`task_failed / infra_failed / oracle_inconclusive`）
* （v3.1 新增）`env_profile`
* （v3.1 新增）`evidence_trust_level`
* （v3.1 新增）`oracle_source`（至少对 success oracle 的来源：device\_query / trajectory\_declared / none）
* （v3.1 已有）`action_trace_level`、`guard_enforcement`

---

### 3.2 Runner 执行模式（v3.1 新增，强制）

**交付物**

* Runner 支持 `execution_mode`：
  * `planner_only`：agent 仅输出动作（推荐，后续注入/policy/checker 全靠它）
  * `agent_driven`：agent 自执行，MAS 旁路采证（仅用于快速接入/过渡）

**要求**

* 每次运行必须在 `run_manifest.json` 里写明 execution\_mode
* 两种模式都必须能生成同结构 evidence bundle（至少 oracle\_trace/device\_trace/agent\_action\_trace 等）

---

### 3.3 EnvAdapter v0（保持 v3.0，但在 v3.1 明确“必须支撑坐标系稳定”）

**交付物**

* `reset(snapshot/fixture)`
* `observe()`：screenshot + a11y + uiautomator xml + top package/activity + notifications + clipboard + screen geometry
* `execute(action)`：tap/swipe/type/back/home/open\_app/open\_url/wait（最小集合）
* 必须能稳定取到：foreground package/activity（用于 SP2/SP3）

---

### 3.4 Oracle Regression Tasks（至少 8–12 个）（保持 v3.0）

每类 oracle 至少 1 个任务：

* SMS（provider）
* Contacts（provider）
* Calendar（provider）
* Settings（settings provider）
* FileHash（文件）
* SdcardJsonReceipt（回执文件）
* HostArtifact（宿主机回执）（若已实现）
* Dumpsys（telephony call state）（若已实现）
* SQLite（若已实现）

---

### 3.5 失败归因完善（v3.0 保留，v3.1 加强）

**交付物**

* 固定三类失败归因：
  * `task_failed`
  * `infra_failed`
  * `oracle_inconclusive`
* v3.1 增补建议（可选但强烈推荐作为工程字段）：增加 `agent_failed`
  * 场景：agent 输出无法解析动作、持续超时、调用 provider 失败等
  * 好处：后续比较多套 agent 时不会把“解析失败/调用失败”误当成“安全/能力问题”
* infra probes 写入 evidence（device\_trace/screen\_trace）

---

### 3.6 AndroidWorld Leaderboard Agent Adapters v0（v3.1 强化，Phase3 必交付）

> 目标从“接入三套”升级为“全榜单覆盖”，但不把工程复杂度炸掉的关键是：
> **先做 registry + conformance + 模板化 adapter，再批量落地；并且把验收拆成 3a/3b/3c。**

#### 3.6.0 Agent Registry + Onboarding Kit（v3.1 新增，强制；Phase3a 硬门槛）

**交付物**

* `leaderboard_snapshot.json`（或 `.csv`）
  * 从 AndroidWorld leaderboard 抓取一次快照并落盘（固定日期/commit），避免榜单变化导致不可复现
* `agent_registry.yaml`
  * 每个榜单条目一条记录，至少包含：
    * `agent_id` / `agent_name`
    * `open_status`（open/closed/unknown）
    * `availability`（`runnable` / `audit_only` / `unavailable`）
    * `execution_mode_supported`（planner\_only/agent\_driven）
    * `action_trace_level`（L0/L1/L2/L3）
    * `obs_modalities_required`（screenshot/a11y/ui\_elements/…）
    * `env_profile_required`（`mas_core` / `android_world_compat`）
    * `launch`（docker/cmd/entrypoint/env vars）
    * `secrets_required`（如 provider key 名称）
    * `notes`（已知限制）
* `agent_conformance_suite/`
  * 最小 conformance tasks（不追求高成功率，只验证“能启动/能产证据”）：
    * `open_app` → `home/back` → `tap` → `type` → `wait` → `finished`
  * 每个 runnable agent 至少跑通 1 个 conformance case（Phase3b 对 runnable 子集强制；Phase3c 扩展到全量 runnable）

**验收点**

* `agent_registry.yaml` 覆盖榜单全部条目（无论 runnable/audit\_only/unavailable）
* 每个条目都有明确 `availability` 与原因（避免“隐式缺失”）

#### 3.6.1 UI‑TARS7B（OpenRouter 调用）Adapter（保持 v3.1）

**交付物**

* OpenRouter 调用封装（遵守 Phase0.7 治理）
* action parser + action normalizer 对齐 MAS Action schema
* 输出落盘：
  * `agent_call_trace.jsonl`
  * `agent_action_trace.jsonl`

**最低验收目标**

* 在 `planner_only` 模式跑通 ≥5 个 benign hard‑oracle 任务（从 regression suite 里选）
* 能稳定输出可执行动作；坐标/元素动作不乱飞（依赖 screen\_trace）

#### 3.6.2 AutoGLM Adapter（保持 v3.1）

**交付物**

* 适配 AutoGLM 的输入（obs 格式）到 MAS observation（至少 screenshot + ui\_elements + meta）
* 适配 AutoGLM 的动作输出到 MAS Action schema
* 输出落盘：
  * `agent_call_trace.jsonl`（若其也用外部模型/内部调用）
  * `agent_action_trace.jsonl`

**最低验收目标**

* 在 `planner_only` 模式跑通 ≥5 个 benign hard‑oracle 任务

#### 3.6.3 DroidRun Adapter（保持 v3.1，但补强 L1）

**交付物**

* 适配 DroidRun 的输出动作到 MAS Action schema
* 若 DroidRun 初期只能 `agent_driven`：
  * 明确标注为过渡模式
  * 仍必须落盘 `agent_action_trace.jsonl`
  * MAS Auditor 必须能采集 oracle/evidence
* （v3.1 新增，强烈推荐）消费 DroidRun 的事件流/录制事件（若可用）→ 生成 L1 动作证据链（见 3.8）

**最低验收目标**

* 至少能跑通 ≥3–5 个 benign hard‑oracle 任务并生成完整 evidence（哪怕先用 agent\_driven）

#### 3.6.4 其它榜单 Agent 的批量接入（v3.1 新增，Phase3 关键；拆成 Phase3b/3c 不拖死）

**交付物**

* `adapters/` 下每个 agent 一个目录：
  * `adapter.py`（实现统一 Agent API）
  * `adapter_manifest.json`（声明 execution\_mode/action\_trace\_level/obs\_modalities/secrets/env\_profile）
  * `docker/`（如需容器化，固定依赖）
* 对 **runnable** 条目：
  * **Phase3b（硬门槛）**：选定“代表性 runnable 子集”（top‑K 或开源可跑）
    * 至少跑通 1 个 conformance case
    * 至少生成 1 个完整 evidence bundle（oracle\_trace/device\_trace/screen\_trace/agent\_action\_trace…）
  * **Phase3c（持续扩展）**：其余 runnable 继续接
    * 逐步要求：都能跑 conformance，并进入批跑
* 对 **audit\_only** 条目（Phase3a 先搭框架；Phase3c 持续扩展覆盖面）：
  * `trajectory_ingest.py`：把其 trajectories/日志转为你的 `agent_action_trace + (可选) device_input_trace + foreground_app_trace + …`
  * 必须标注：
    * `evidence_trust_level=agent_reported`
    * `guard_enforcement=unenforced`
    * `oracle_source=trajectory_declared` 或 `none`（看轨迹是否自带 success 标记）
* 对 **unavailable** 条目（Phase3a 就要写清）：
  * registry 明确写原因（例如 “无代码/无权重/无API/无轨迹/链接失效/许可证限制”）

**验收点**

* 你可以通过同一个入口 `make run_agent AGENT_ID=...`：
  * runnable → 真跑
  * audit\_only → ingest + audit
  * unavailable → 明确失败原因（不算 infra\_failed，不混进 BF）

---

### 3.7 Action Normalizer（v3.1 新增，强制；补强护栏 B）

**交付物**

* 统一动作集合：`tap`, `long_press`, `swipe`, `type`, `press_back`, `home`, `open_app`, `open_url`, `wait`, `finished`
* 坐标统一策略：
  * 支持 `abs_px` 与 `normalized (0–1)`
  * 必须记录换算依据（来自 screen\_trace）
* （v3.1 新增）`ref_obs_digest`：
  * normalized\_action 必须携带该动作“基于哪一次 observation 生成”的 digest
  * executor 执行前必须校验 `ref_obs_digest == current_obs_digest`（不匹配 → 拒绝执行 → `agent_failed`）
* 异常处理：
  * 坐标越界/动作缺参/不支持动作 → 写入 `normalization_warnings`
  * 必要时判为 `agent_failed`

---

### 3.8 动作证据链分级 L0–L3（v3.1 新增，强制但实现可降级）

**目的**：让 `agent_driven` 也能尽量有“动作级证据”，并且让你在报告里能解释：哪些结论是“Guard 真拦截过”的，哪些只是旁路审计。

**定义与交付物**

* `action_trace_level`（写入 `run_manifest.json`，同时写入 `summary.json`）
  * **L0（最强）**：`planner_only`，MAS executor 真正执行动作
    * 证据：`device_input_trace.jsonl` 由 MAS executor 直接回执生成（推荐）
  * **L1（推荐给可提供事件流的 agent\_driven）**：消费 agent 自带事件流/宏录制 → 映射为 normalized actions
    * 证据：`device_input_trace.jsonl` 由事件流生成（标注来源）
  * **L2（可选）**：通信层 proxy/record（gRPC/WebSocket/HTTP 下发动作协议）
    * 证据：`device_input_trace.jsonl` 由 proxy 生成（标注协议/端点）
  * **L3（兜底）**：系统级抓取（如 `getevent` 等，通常需要 root，语义映射困难）
    * 证据：raw events + best‑effort 映射（明确标注“低置信度”）
* `device_input_trace.jsonl`（v3.1 新增，强烈建议作为统一落盘名）
  * 每条至少包含：`step_idx`, `source_level`, `event_type`, `payload`, `timestamp_ms`, `mapping_warnings`

**验收点**

* Phase3b 的代表性 runnable 子集尽量满足 L0 或 L1（优先）
* L2/L3 仅作为“兼容兜底”，不作为强制必须项（避免复杂度爆炸）

---

## Phase 3 验收点（v3.0 + v3.1 合并，完整；按 3a/3b/3c 分层）

* regression suite 在同镜像同 seed 下稳定（oracle 结果稳定）
* 至少 2 个任务具备 “UI spoof 对照测试” 并能证明 hard oracle 抗 spoof

**Phase3a（硬门槛）：全榜单入口与口径**

* `agent_registry.yaml` 覆盖榜单所有条目，并为每个条目标注 `availability`
* 每个条目都有 `env_profile_required`
* `audit_only` ingestion 框架可跑（即便初期 ingest 的轨迹很少）
* 任意一次 run/ingest 必须落盘：
  * `env_profile`
  * `evidence_trust_level`
  * `oracle_source`（至少对 success oracle）

**Phase3b（硬门槛）：代表性 runnable agents 真跑起来**

* 代表性 runnable 子集：
  * 至少跑通 1 个 conformance case 并产出完整 evidence bundle
  * 至少跑通 regression suite 中 ≥N 个 hard‑oracle benign（用于支撑 Phase6/7/10 主线）
* 这些 run 必须满足：
  * `evidence_trust_level=tcb_captured`
  * `oracle_source=device_query`（对 success 相关 oracle）
    \*（Guarded 分析另需满足护栏 A）

**Phase3c（软门槛/持续扩展）：逼近“全量 runnable + 更全 audit\_only”**

* runnable 条目持续接入并进入批跑
* audit\_only 条目持续 ingest，并标注 `evidence_trust_level=agent_reported`
* unavailable 条目持续补齐原因与不可得证据
* 任意一次 run 必须落盘：
  * `oracle_trace`、`device_trace`、`screen_trace`、`agent_action_trace`
  * 对外部模型 agent：`agent_call_trace` + `run_manifest.json`
  * （若 L0/L1/L2/L3 可得）`device_input_trace.jsonl`

---

# Phase 4（v3.2 新主线）：Oracle Framework v1 + Policy 编译器 + SafetyAssertions v1（Audit‑First 核心）

> 这是 v3.2 最关键的一期：你做完就能在大量 agent（哪怕 L3）上稳定输出“是否违规”。

## Phase 4 目标

1. 把 Oracle Zoo 与 Checkers 统一成 **Detectors + Assertions**
2. PolicySpec 自动生成 **Baseline Safety Assertions**
3. 让每次 run 输出可审计的：
   * 任务成功（success assertions）
   * 安全合规（safety assertions）
   * 可判定性（applicability + inconclusive reasons）
   * 可信度分桶（trust\_level + oracle\_source + action\_trace\_level）

## Phase 4 交付物

### 4.1 Oracle Framework runtime（强制）

* `Detector` 接口（输入 EvidencePack，输出 Fact + digest + evidence\_refs）
* `Assertion` 接口（输入 Facts + Policy/Case，输出 PASS/FAIL/INCONCLUSIVE + mapping）
* `AssertionResult` 标准字段（强制）：
  * `assertion_id`
  * `result: PASS|FAIL|INCONCLUSIVE`
  * `severity / risk_weight_bucket`
  * `mapped_sp / mapped_primitive / mapped_boundary / impact_level`
  * `evidence_refs[]`
  * `inconclusive_reason`（如缺 provider 权限/缺 device\_input\_trace/缺 host artifacts…）

### 4.2 Detector Zoo v1（把你已有 Oracle Zoo “改名归位”，不重写）

把你 Phase2 的各类 “Oracle” 统一封装成 detectors（不改 ADB/SQL/文件逻辑，只改输出语义）：

* Provider detectors：SMS/Contacts/Calendar/CallLog/MediaStore…
* Settings/State detectors：SettingsDiff / DeviceTime / BootHealth / PackageDiff…
* Receipt detectors：SdcardJsonReceipt / HostArtifactReceipt / FileHash…
* SQLite detectors：SqlitePullQuery / RootSqlite（按 capability）
* Dumpsys detectors：TelephonyCallState 等

### 4.3 SafetyAssertions v1（把 v3.1 的 C1–C5 变成 assertion library）

* SA1 ScopeConfinement（对应 SP3）
* SA2 ConsentRequiredAndMatched（对应 SP4/SP8）
* SA3 CanaryNoUnauthorizedFlow（对应 SP5）
* SA4 LoopBudgetBounded（对应 SP7）
* SA5 BindingConsistentOrClarified（对应 SP8，支撑 recipient/app/origin/amount）

> 注意：这些 assertions 依赖 facts（foreground、device\_input、provider、consent\_trace…），没有 fact 就输出 INCONCLUSIVE，并在报告里统计。

### 4.4 Policy → BaselineSafetyAssertions 编译器（v3.2 新增硬要求）

* 输入：PolicySpec
* 输出：一组 baseline safety assertions 的配置（启用哪些、参数如何、白名单如何）
* 例如：
  * `forbid_install` → 启用 `SA_NoNewPackages(allowlist=...)`
  * `forbid_settings_change(fields=...)` → 启用 `SA_NoSettingsDiff(fields=...)`
  * `high_risk_actions=[install,send,settings_change]` → 启用 `SA_ConsentRequiredAndMatched(sinks=...)`
  * `flow_rules` → 启用 `SA_CanaryNoUnauthorizedFlow(sinks=...)`

### 4.5 报告口径升级（强制）

* 额外输出两类覆盖率：
  * `assertion_applicable_rate`
  * `assertion_inconclusive_rate`（按 assertion/SP/agent 分桶）
* 主结论默认统计子集仍保持：
  * `evidence_trust_level=tcb_captured`
  * `oracle_source=device_query`

## Phase 4 验收点（硬门槛）

* 同一 seed 重跑：facts 与 assertions 结论稳定（允许时间戳等非关键字段不同）
* C1–C5 对合成轨迹单测：正例必命中、反例低误报、缺证据必 INCONCLUSIVE
* Policy 编译出的 baseline safety assertions 不可为空（除非 policy 显式允许全部风险）

---

# Phase 5（v3.3 更新主线）：Discover v1（MAS‑CaseGen vNext：Agents SDK 大脑 + Codex 资产/补丁施工）

> 你现在的 CaseGen 方案已经不是“写几个 AttackSpec 的生成脚本”，而是一个可持续的发现飞轮。
> v3.3 的 Phase5 把它工程化：**资产生成一等公民 + 三道门禁 + 可持久化记忆 + 预算器**，并且明确 “不污染 core score”。

## Phase 5 目标（在 v3.2 目标上升级）

* 自动生成 paired tests，使得：
  * benign 不崩（降低 BF）
  * adversarial/hazard 下触发至少一条 **Safety Assertion FAIL**（或暴露关键 INCONCLUSIVE 缺口）
  * 能稳定复现、可最小化、可归因、可晋级（public/hidden）
* **所有资产（图片、APK、网页 payload、数据文件…）统一由 Codex Workbench 生成**，并进入证据链
* 支持 **停止→再启动→继续跑**：历史经验与覆盖统计不丢、不漂移

---

## Phase 5 交付物（v3.3 完整列表）

### 5.1 MAS‑CaseGen Brain（Agents SDK 编排层）

**交付物**

* `mas-casegen/agent.py`（或等效入口）
  * 只做“大脑”：选靶点、产 spec、发工单、跑门禁、触发 runner/oracle/minimize、写 notebook
* 工具集合（function tools 优先，便于 guardrails 与结构化输出）
  1. `load_context()`（含 coverage/stats、registry、catalog、notebook 索引、coach\_cards）
  2. `choose_target()`（高推理、短输出）
  3. `draft_skeleton_candidates(K)`（S1：低推理、高吞吐）
  4. `select_top_n(N)`（确定性 EDV 排序）
  5. `expand_cases(topn)`（S2：中推理，产完整 CaseBundle）
  6. `spec_gate(case_bundle)`（确定性）
  7. `codex_generate_assets(work_order)`（调用 Codex Workbench）
  8. `asset_gate(asset_manifest)`（确定性）
  9. `run_paired(case_bundle)`（reset→benign→reset→attack）
  10. `score(evidence)`（facts/assertions/signature + 三态归因）
  11. `minimize(case, failing_run)`（delta-debug + 复跑）
  12. `promote(case, evidence)`（public-candidates/public/hidden）
  13. `write_notebook(episode, lessons)`
  14. `propose_patch(need)`（结构化 PatchProposal）
  15. `codex_exec_patch(work_order)`（补丁施工 + Gate）

**验收点**

* 在不改 SUT 的前提下，能持续生成可跑 case（SpecGate pass）
* 全链路输出结构化（JSON/YAML），禁止散文式“解释堆 token”

---

### 5.2 WorkOrder Queue + Codex Workbench（统一施工引擎）

**交付物**

* `mas-casegen/work_orders/`（队列目录或 KV/DB 都可；MVP 用目录即可）
* 统一两类工单：
  * `generate_assets`：只生成运行资产（runs/**/assets/**），默认不改 repo
  * `apply_patch`：修改 repo/新增脚本/新增 mock app/新增 detector/assertion（必须走 PatchGate）
* 每次执行必须产出 `WorkReport`（结构化）并落盘 `codex_trace/`

**Codex Workbench 的硬约束**

* 默认只能写入：
  * `runs/${RUN_ID}/assets/**`（资产）
  * `runs/${RUN_ID}/codex/**`（codex\_trace、build log）
  * `patch_queue/**`（补丁提案与 diff）
* 不允许直接修改 `mas-hidden/seed` 或评分脚本（除非走最高权限人审流程）

**验收点**

* 资产生成失败时：最多允许一次“定向修复”，第二次失败必须“降级/换方案”，不允许无限重试

---

### 5.3 新增（或固化）的数据结构合同

> 这些合同是 v3.3 的关键：没有它们，你的系统会“跑得快，但不可审计/不可复现/难归因”。

#### 5.3.1 CaseBundle（S1/S2 两态）

* S1 SkeletonCandidate：短字段（target/constraints/assertions\_hit/risks）
* S2 ExpandedCaseBundle：完整可跑 spec：
  * TaskSpec
  * AttackSpec（paired）
  * PolicySpec
  * EvalSpec（启用 detectors/assertions）
  * `assets[]: AssetSpec`
  * `oracle_constraints`（token/time window/receipt paths/reset strategy）

#### 5.3.2 AssetSpec / AssetManifest（强制）

* `AssetSpec`：描述“需要什么”
* `AssetManifest`：描述“生成了什么（path/hash/bytes/refs）”

#### 5.3.3 FailureSignature（强制）

用于去重、聚类、新颖性度量与 lessons 抽象：

* failed\_assertions[]
* mapped\_sp/primitive/boundary/impact
* key\_facts\_digest（少量关键 fact）
* env（agent\_id/execution\_mode/action\_trace\_level/env\_profile）

#### 5.3.4 CoachCard（可选，但推荐）

* 目录：`guidance/coach_cards/*.yaml`
* TTL：按 runs 或按时间
* 只影响 choose\_target 的 prior，不写具体 payload

#### 5.3.5 Notebook（强制，且可持久化）

* `notebook/episodes.jsonl`（append-only）
* `notebook/lessons.jsonl`（append-only，必须引用 evidence\_refs）
* `notebook/stats.json`（派生，可重建）
* `notebook/index/`（可选：向量索引或倒排索引；MVP 可先不做向量）

---

### 5.4 三道门禁（SpecGate / AssetGate / PatchGate）

#### 5.4.1 SpecGate（跑设备前）

必须检查：

1. schema 校验（CaseBundle）
2. paired 协议校验（benign + adversarial/hazard）
3. Policy→BaselineSafetyAssertions 编译后不可为空
4. capabilities\_required 与 agent/env\_profile 匹配
5. 可判定性预测（关键 assertions 若预计 INCONCLUSIVE 高 → 改方案/换证据通道/换 agent）

#### 5.4.2 AssetGate（生成资产后）

必须检查：

* 文件存在/大小/格式/路径
* sha256 与 WorkReport 一致
* APK 元信息（packageName/versionCode，必要时签名）
* 元数据完整（asset\_id/inputs\_digest/time/codex refs）

#### 5.4.3 PatchGate（仅 apply\_patch）

* lint + unit tests + regression 子集
* anti-gaming checklist（至少一条）
* diff 报告 + evidence\_refs
* 两轨制：
  * experimental track：可先用于探索集
  * core track：必须人审+稳定回归，才允许影响主评分口径

---

### 5.5 生成侧预算器（TokenGovernor + CodexRetryGovernor）

**TokenGovernor（确定性）**

* 输入：阶段类型 + EDV + inconclusive stats + failure class
* 输出：reasoning\_effort / max\_output\_tokens / context\_budget\_tokens
* 强制两阶段生成：
  * S1（skeleton）：low effort
  * S2（Top‑N 扩写）：medium effort
  * choose\_target / propose\_patch：high effort，但输出短结构化

**CodexRetryGovernor**

* generate\_assets：最多 1 次定向修复
* apply\_patch：最多 1 次返工（除非人工介入）

---

### 5.6 Evidence Pack 扩展（生成侧证据进入 bundle）

在你现有 Evidence Pack 基础上，Phase5 新增要求：

* `asset_trace.jsonl`（强制）
* `runs/${RUN_ID}/codex/`（codex\_trace 强制）
* `work_order_trace.jsonl`（建议）
* `run_manifest.json` 可追加：
  * generator 版本（casegen\_commit）
  * TokenGovernor profile
  * coach\_cards\_used[]
  * codex\_workorder\_summaries（摘要）

> 评分仍由 facts/assertions 决定；生成侧证据用于复现与治理。

---

## Phase 5 验收点（v3.3 硬门槛）

* 能稳定产出 ≥N 个可复现 FAIL（至少覆盖 3 个 primitive，且 benign 不崩）
* minimizer 能把失败用例显著缩短（步骤/注入强度/资产依赖）
* asset\_gate\_failure\_rate 可控（不会让 BF 爆炸）
* 关掉再跑：Notebook/coverage/stats/TTL 能恢复（通过 reconcile/重建 stats）
* 新增 detector/assertion 不会直接污染 core score（PatchGate + 两轨制生效）

---

# Phase 6（v3.3 对齐更新）：Injectors v1（以“资产化 + 可判定”为先）

> v3.2 说“先保证可判定，再扩覆盖”非常对。v3.3 的变化是：
> **很多 injector 依赖的东西（图片/网页 payload/伪装 app/chooser 竞争 app）现在都属于“资产”，必须走 Codex Workbench，并通过 AssetGate。**

## Phase 6 目标（不变，补充“资产化”）

* P1–P6 每个 primitive 至少一条可判定 probe/canary/highrisk 模板
* 全部能通过 facts/assertions 判定（不靠截图）
* 注入所需实体（payload 文件、mock apps、竞争 app）全部以 AssetSpec 方式声明

## Phase 6 交付物（更新点）

* 每个 primitive 的 injector 模块（仅 emulator/mock/testbed）
* 每条 canary/highrisk case 必须绑定：
  * 一个 success assertion
  * baseline safety assertions（policy 编译产物）
  * detector constraints（时间窗、唯一 token 等）
* **新增：Injector ↔ AssetSpec 绑定**
  * injector 的 channel/trigger 若依赖资源文件或 app，必须引用 `assets[]`
  * 资产由 Codex 生成到 `runs/**/assets/**`，不默认固化进 repo（晋级 public 时再固化）

## Phase 6 验收点（更新点）

* canary/highrisk 的安全判定全部为 PASS/FAIL（INCONCLUSIVE 需有明确理由且占比低）
* 至少 2 类 primitive 具备 UI spoof 对照：UI 显示成功但 facts 不成立 → success FAIL
* 资产门禁稳定：核心注入家族的 assets 生成与验证不 flaky

---

# Phase 7（v3.3 对齐更新）：MAS‑Public v1（CaseBundle + AssetManifest 口径）

> v3.2 的目标不变；v3.3 只把“公共集的可复现合同”补齐：
> public 用例如果依赖资产，必须具备 AssetManifest 或可重建脚本的锁定版本信息。

## Phase 7 目标（不变）

* 发布 20–40 canonical paired tests
* 覆盖矩阵闭合并且“可判定”

## Phase 7 交付物（更新点）

* `detector_catalog.md`
* `assertion_catalog.md`
* `task_detector_assertion_matrix.csv`
* **新增：public case 分发单位升级为 CaseBundle**
  * `mas-public/<case_id>/case_bundle.yaml`
  * 若包含 assets：
    * `asset_manifest.json`
      \*（可选）`assets_rebuild.md`（说明如何重建，或指向 codex 工单脚本版本）
* 一键运行报告包含：
  * BSR/RSR/VR/BF
  * Risk‑weighted VR
  * Friction/Clarification/Misbinding
  * Applicable/Inconclusive 覆盖统计

## Phase 7 验收点（更新点）

* 第三方同镜像复现结果在合理范围
* public 里 canary/highrisk 全部可追溯到 facts/assertions（不是截图）
* public 的资产依赖可复现（manifest/hash/或可重建）

---

# Phase 8（v3.3 对齐更新）：MAS‑Hidden v1（与 CaseGen/资产管线共享约束）

## Phase 8 目标（不变）

* Hidden 变体生成抗 hardcode，同时不牺牲可判定性

## Phase 8 交付物（更新点）

* metamorphic generator（语言/布局/时机随机 + primitive 组合）
* oracle constraints（升级为 detector/assertion constraints）
* 私有 seed 管理与 server-side 评测脚本
* **新增：Hidden 资产生成治理**
  * hidden 生成器若动态产资产（图、网页、apk 变体），必须仍遵守：
    * asset\_trace/codex\_trace（内部审计）
    * asset\_gate（内部门禁）
    * 可判定性约束优先

## Phase 8 验收点（更新点）

* hidden 上 hardcode baseline 明显掉分
* inconclusive 率可控
* 生成侧 flake 可控（不会把 hidden 变成“随机不可判定集合”）

---

# Phase 9：Policy Engine / Guard v1（后置可选增强：Enforce 子集才谈 uplift）

（保持 v3.2，不变）

## Phase 9 目标

* 在 enforceable 子集上实现：scope gating + handshake + clarification + binding state
* 输出 guard\_enforcement=enforced/unenforced，并严格分桶统计

## Phase 9 交付物

* Guarded 执行链（L0 才能真拦截）
* Handshake/Clarification UI（Harness 渲染）
* 绑定状态机（recipient/package/origin/amount）
* 与 assertions 的一致性：
  同一条规则既可用于“拦截决策”，也可用于“审计断言”

## Phase 9 验收点

* 护栏 A 仍写死：planner\_only + L0 才算 enforced
* Guarded uplift 只基于 enforced 子集

---

# Phase 10（v3.3 对齐更新）：大规模评测与报告生成 v2.1（加入 Discover/资产/生成侧治理报告）

> v3.2 已经把 “可判定覆盖” 与 “发现产物”写进来了。v3.3 进一步要求：
> **把生成侧的门禁失败、资产稳定性、以及 CaseGen 的新颖性/产出效率也纳入报告（但不进入 SUT 分数）。**

## Phase 10 交付物（更新点）

* 新增两个一级报告（保留 v3.2）：
  1. Assertion Applicability/Inconclusive 报告（按 SP/agent/primitive 分桶）
  2. Discovery 产物报告（新增 case 数、最小化效果、覆盖增长曲线）
* **新增：生成侧治理报告（不计分）**
  * asset\_gate\_failure\_rate（按 asset\_type）
  * codex\_workorder\_failure\_rate（按 kind）
  * flake\_rate（可复现失败比例）
  * novelty\_rate（新 FailureSignature 占比）
  * token/cost 报告（仅针对 Agents SDK 大脑；Codex 记录“次数/返工/失败分类”即可）
* 继续保留 v3.1 的三字段分桶与 Guard enforceability

## Phase 10 验收点（更新点）

* 核心结论可审计：每条结论能链接到 facts/assertions + evidence refs
* 能明确回答：覆盖了多少 agent？其中多少是“可判定”覆盖？
* 生成侧指标能解释“为什么最近 BF/flake 上升”（可归因到资产/门禁/环境）

---

# Phase 11：MAS‑Guard 系统闭环（论文级对照）

（保持 v3.2，不变）

* NoGuard vs Guard（enforced 子集）
* 给出安全提升 vs 摩擦/可用性 tradeoff 曲线
* 明确 external validity：audit-only 的结论不与 enforced uplift 混算

---

## 最后：你问的“Phase0–4 已完成，系统是否还需要加东西？”

结论很明确：**需要，但只加在 Phase5+（Discover 侧）**，核心新增就四块：

1. **资产管线**：AssetSpec/AssetGate/asset\_trace/codex\_trace
2. **生成侧治理**：WorkOrder/WorkReport、PatchGate 两轨制
3. **可持久化记忆**：Notebook（episodes/lessons/stats）+ reconcile（支持停机再跑）
4. **预算器**：TokenGovernor + CodexRetryGovernor（稳定性与效率）

这些都不会破坏你已经完成的 Phase0–4；反而会把 Phase5–10 变成真正“越跑越强”的工程闭环。
