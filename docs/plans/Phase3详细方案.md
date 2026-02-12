# Phase 3 详细方案（v3.2+：评测语义 A/B/C/D/E + 坐标/几何补丁整合版）

Runner + EnvAdapter + AndroidWorld Leaderboard 全覆盖接入（runnable / audit\_only / unavailable）

> 适用前提：你已完成 Phase 0/1/2（仓库治理、MAS‑Spec、Oracle Zoo + Evidence Pack v0 已落地）。
>
> 本文只覆盖 **Phase 3**，并把“全榜单接入”工程化成 **三态口径**，避免 Phase 3 把主线拖死。
>
> 说明：在不改变你既有 3a/3b/3c 主结构与落地顺序的前提下，本版本把两类“防返工补丁”统一写死：
>
> * **评测语义补丁（A/B/C/D/E）**：把 eval/guard/action‑trace/oracle/ref‑applicability 等“统计口径”固定回 manifest/summary + checker 输出，避免 Phase4/5/10 报告返工。
> * **坐标/几何补丁（Coord‑A/B/C）**：把 screen\_trace 四字段 + coord\_space → physical\_px 的归一化链路写死，避免“点不准/归因不清”。

---

## 3.0 Phase 3 的目标与口径

### 3.0.1 Phase 3 要解决什么

Phase 3 的核心是把“被测系统（mobile agent）”从少量 demo，扩展到 **AndroidWorld leaderboard 的全条目覆盖**，同时保证：

* 你可以对每个条目给出 **明确的可审计状态**（runnable / audit\_only / unavailable）。
* 对 runnable 的条目，你可以在 Runner 里启动、跑 conformance、产出标准 evidence bundle。
* 对 audit\_only 的条目，你可以 ingestion 其轨迹/日志到你的 evidence 格式，跑 checkers，且 **不会被误当作同强度证据**。
* 对 unavailable 的条目，你能解释“为什么不可得”，并在报告里显式区分（避免 reviewer 质疑“你说全覆盖但其实漏了”）。
* 你有一个专门的 CLI（agentctl）：能在 terminal 里切换已接入 agent，并用两种模式做“接入验证”：
  * 模式 1：固定 smoke 指令（写死在测试代码里，不随 agent 改变）
  * 模式 2：terminal 输入自然语言 goal，agent 执行（用于人工快速验证）

### 3.0.2 三态口径（强制写进 registry + 报告）

* **runnable**：
  * 你能拿到可运行工件（代码/容器/权重/API），可以在你的 Runner 启动。
  * 最低要求：跑通 **conformance suite**（不追求任务成功率，只追求“能跑、能产证据”）。
* **audit\_only**：
  * 你拿不到可运行工件，但能拿到 trajectories / logs / step‑by‑step records。
  * 你实现 ingestion：把对方轨迹转成你的 evidence 格式，然后跑 deterministic checkers。
  * 该类 run 的默认口径必须落盘为：
    * `eval_mode="vanilla"`
    * `guard_enforced=false`
    * `guard_unenforced_reason="guard_disabled"`（或无法判定则 `"unknown"`）
    * `evidence_trust_level="agent_reported"`
* **unavailable**：
  * 既拿不到可运行工件，也拿不到足够轨迹。
  * registry 必须记录原因与证据（例如 repo 404、需要私有 key、无发布 artifacts 等）。

> 兼容说明：你可以保留历史字段 `guard_enforcement="enforced|unenforced"`，但它应视为**派生/legacy 字段**，报告端不再把它当作唯一口径（见 3.0.7 / 3.3.6）。

### 3.0.3 运行模式（execution\_mode）

* `planner_only`：agent 只输出动作，动作由 MAS executor 执行。
* `agent_driven`：agent 自己执行动作，MAS 只能旁路采证/审计。

> Phase 3 的“全覆盖”不要求所有 runnable 条目都能立刻迁移到 planner\_only；但必须把 execution\_mode 写清楚。

### 3.0.4 动作证据链分级（action\_trace\_level, L0–L3）

> 目的：把“我们到底有没有动作级证据”从口头描述变成硬字段。

* **L0**：`planner_only` 且 MAS executor 真执行动作（最强）。
* **L1**：`agent_driven` 但 agent 可提供事件流/宏录制（可映射成 normalized actions）。
* **L2**：通信层 proxy/record（gRPC/HTTP/WebSocket action messages）。
* **L3**：系统级抓取（getevent 等，通常需要 root，语义映射困难）。

> L3 仅作为 legacy/枚举兼容保留，本 Phase3 工程与 registry 不产出 L3。

### 3.0.5 三个“防误用字段”（Phase 3 固化）

这些字段用于避免 audit\_only 被误当成同强度证据：

1. **env\_profile**（运行环境画像）

* `mas_core`：你自己的 baseline（例如 Pixel\_9/Android\_36 + 你的 reset/fixtures）。
* `android_world_compat`：严格对齐 AndroidWorld 官方环境/协议（AVD、转发通道、启动参数等）。

2. **evidence\_trust\_level**（证据可信来源）

* `tcb_captured`：由你的 harness/探针采集（更可信）。
* `agent_reported`：由第三方/agent 自报（audit\_only 常见）。
* `unknown`：无法判定来源。

3. **oracle\_source**（成功判定来源，至少对 success oracle 记录）

* `device_query`：你的 Oracle Zoo 在设备/宿主侧硬查询得到。
* `trajectory_declared`：轨迹自带“成功标记/回执”。
* `none`：无法判定成功（只做安全审计，不出 RSR/BSR）。

### 3.0.6 两道护栏（工程约束写死）

* **护栏 A（Guarded 有效性条件写死）**
  * 只有当 `eval_mode="guarded"` 且 `execution_mode="planner_only"` 且 `action_trace_level="L0"` 时，你才能标注 `guard_enforced=true`（并在报告里进入“Guarded 提升主结论”）。
  * 否则必须落盘 `guard_enforced=false`，并用 `guard_unenforced_reason` 解释原因（`guard_disabled|not_planner_only|not_L0|unknown`）。
* **护栏 B（index/坐标动作绑定 ref\_obs\_digest）**
  * 所有带坐标或 element‑index 的动作，normalized\_action 必须携带 `ref_obs_digest`（当 `ref_check_applicable=true` 时）。
  * executor 执行动作前校验：`ref_obs_digest == current_obs_digest`，不匹配 → 拒绝执行 → 归因 `agent_failed`。

### 3.0.7（新增，评测语义补丁 A/B/C/E）评测语义字段：必须回到 manifest/summary（避免歧义/误算）

> 这组字段是 Phase4/5/10 报告正确性的“地基”。字段很小，但能显著避免误用。

#### (A) eval\_mode + guard 状态三件套落盘

* `eval_mode: "vanilla" | "guarded"`
* `guard_enforced: true | false`
* `guard_unenforced_reason: null | "guard_disabled" | "not_planner_only" | "not_L0" | "unknown"`

兼容策略：

* 可以保留 `guard_enforcement` 字符串，但视为派生字段（report 时从上面三项推导），不再作为唯一口径。

默认规则建议（避免歧义）：

* 若 `eval_mode` 缺失：默认 `vanilla`
* 若 `eval_mode=vanilla`：`guard_enforced=false` 且 `guard_unenforced_reason="guard_disabled"`
* 若 `eval_mode=guarded` 但不满足护栏 A：`guard_enforced=false` 且 reason 具体化（not\_planner\_only / not\_L0）

#### (B) action\_trace\_source 与 action\_trace\_level 配对落盘

* `action_trace_level: "L0"|"L1"|"L2"|"L3"|"none"`
* `action_trace_source: "mas_executor"|"agent_events"|"comm_proxy"|"system_capture"|"none"`

> 说明：level 是强度分级，source 是采集来源。两者配对能显著降低“以为有 L0 其实是 agent 自报”的误用风险。

#### (C) 成功判定输出结构化（nl/ingest 不硬塞 true/false）

在 episode summary 固定三件事：

* `oracle_decision: "pass" | "fail" | "inconclusive" | "not_applicable"`
* `agent_reported_finished: true | false`
* `task_success: true | false | "unknown"`

派生规则写死：

* 若 `oracle_decision="pass"` → `task_success=true`
* 若 `oracle_decision="fail"` → `task_success=false`
* 若 `oracle_decision in {"inconclusive","not_applicable"}` → `task_success="unknown"`

#### (E) audit\_only ingestion：当 observation 缺失时，ref 绑定护栏不适用

新增：

* `ref_check_applicable: true | false`
* `auditability_limited: true | false`
* `auditability_limits: [..]`（可选，例如 `["no_screenshot","no_ui_tree","no_geometry"]`）

硬规则（写进 ingestion + checkers）：

* 当缺少可验证 observation（例如无 screenshot 且无 geometry）：
  * `obs_digest=null`
  * `ref_obs_digest=null`
  * `ref_check_applicable=false`
  * `auditability_limited=true`
* checkers 中凡依赖 `ref_obs_digest` 的一致性校验必须自动降级，并输出 `not_applicable`（避免 audit\_only 全被误判为 agent\_failed）。

---

## 3.1 Phase 3 新增的仓库文件结构

> 原则：不推翻你 Phase 0–2 的结构；Phase 3 只新增必要目录与最小修改点。

### 3.1.1 Repo 根目录新增

```
AegisAgent/
  mas-agents/                         # Phase3 新增：榜单快照/registry/适配器工件
    registry/
      leaderboard_snapshot.json       # 固定一次快照（带日期/来源）
      agent_registry.yaml             # 全榜单条目：runnable/audit_only/unavailable
      schemas/
        agent_registry.schema.json    # （可选）registry JSONSchema
      env_profiles/
        mas_core.yaml
        android_world_compat.yaml
    adapters/
      <agent_id>/
        adapter_manifest.json         # 适配器声明（mode/L级/obs需求/secrets/坐标默认口径等）
        adapter.py                    # 统一接口的 wrapper
        docker/                       # （可选）容器化文件
    ingest/
      androidworld/
        mapping_notes.md              # 轨迹字段映射说明（audit_only 必备）

  mas-conformance/                    # Phase3 新增：仅用于接入验证，不进入 benchmark
    cases/
      conf_001_open_settings/
        task.yaml
        policy.yaml
        eval.yaml

  mas-harness/                        # 现有
  mas-public/                         # 现有
  mas-hidden/                         # 现有
  mas-spec/                           # 现有
  mas-guard/                          # 现有
```

### 3.1.2 mas-harness（Python 包）新增/修改

> 下面以当前代码为基线（你已完成 Phase0–2 的代码在 `mas-harness/src/mas_harness/`）。

新增建议结构（不要求一次做完，但建议按此落盘）：

```
mas-harness/src/mas_harness/
  phase3/
    README.md                         # Phase3 运行说明（面向开发者）

  cli/
    run_agent.py                      # 统一入口：runnable 真跑 / audit_only ingest
    agentctl.py                       # 接入验证 CLI：切换 agent + fixed/nl 两模式
    snapshot_leaderboard.py           # 抓取 leaderboard → 写 snapshot.json
    validate_registry.py              # 校验 agent_registry.yaml
    ingest_trajectory.py              # audit_only ingestion 命令

  agents/
    base.py                           # AgentAdapter 接口定义
    registry.py                       # 读取/校验 agent_registry.yaml
    loader.py                         # 动态加载 mas-agents/adapters/<id>/adapter.py

  conformance/
    suite.py                          # 发现 conformance cases + 批量跑

  android/
    controller.py                     # 现有：observe 能力（Phase2 已用）
    env_adapter.py                    # Phase3 新增：AndroidEnvAdapter（reset/observe/execute）
    executor.py                       # Phase3 新增：执行动作 + device_input_trace

  ingestion/
    androidworld.py                   # Phase3 新增：AW trajectories → evidence bundle

  evidence.py                         # 小改：obs_digest/ref_obs_digest + device_input_trace + screen_trace(或等价字段)
  phase0_artifacts.py                 # 小改：run_manifest 新字段（含 eval_mode 等）
  evidence_pack.py                    # 小改：device_input_trace.jsonl 作为 optional/required（按你选择）

  # （可选但建议）为 agentctl 内置 smoke case 减少耦合
  phase3_smoke_cases.py               # 固定 smoke 指令的“代码内 TaskSpec 生成器”
```

---

## 3.2 Phase 3 的交付拆分（3a / 3b / 3c）

> 这一步是为了“全覆盖但不拖死主线”。

* **Phase 3a（硬门槛）**：全榜单覆盖的骨架（snapshot + registry + conformance + ingest 框架 + 接入验证 CLI 骨架）。
* **Phase 3b（硬门槛）**：一组“代表性 runnable agents”跑通 conformance + 部分 regression suite（用于后续 Phase6/7/10 不空转），并把接入验证 CLI 的“真跑链路”打通。
* **Phase 3c（软门槛/持续扩展）**：其余 runnable 继续接；audit\_only 持续 ingest；unavailable 持续补证据。

---

## 3.3 Phase 3a：全榜单覆盖骨架（硬门槛）

### 3.3.1 3a‑1 Leaderboard Snapshot（冻结榜单输入）

**要做什么**

* 提供一次性脚本，从 AndroidWorld leaderboard 抓取/解析 → 写入 `mas-agents/registry/leaderboard_snapshot.json`。
* snapshot 必须记录：抓取日期、来源 URL、解析规则版本、条目列表（id/name/link/open\_status 等）。

**新增文件**

* `mas-harness/src/mas_harness/cli/snapshot_leaderboard.py`
* `mas-agents/registry/leaderboard_snapshot.json`

**代码验收标准**

* [X]  运行：

```bash
python -m mas_harness.cli.snapshot_leaderboard \
  --out mas-agents/registry/leaderboard_snapshot.json
```

* [X]  产物检查：
  * 文件存在且可解析为 JSON
  * 顶层字段至少包含：`snapshot_date`, `source`, `entries[]`, `parser_version`
* [X]  单测（离线）：
  * `mas-harness/tests/fixtures/leaderboard_sample.html`（或 sample json）
  * `pytest -q mas-harness/tests/test_snapshot_leaderboard.py`

> 备注：测试必须离线跑（CI 不依赖网络）。

---

### 3.3.2 3a‑2 Agent Registry（全条目落盘 + 三态口径）

**要做什么**

* `agent_registry.yaml` 必须覆盖 snapshot 的全部条目。
* 每条至少包含（建议字段集，越少越好但必须可审计）：

```yaml
- agent_id: "xxx"
  agent_name: "..."
  open_status: open|closed|unknown
  availability: runnable|audit_only|unavailable
  env_profile: mas_core|android_world_compat
  execution_mode_supported: [planner_only, agent_driven]
  action_trace_level: L0|L1|L2|L3|none
  obs_modalities_required: [screenshot, ui_elements, uiautomator_xml]
  adapter: "mas-agents/adapters/xxx/adapter.py"      # runnable 时必须
  ingest:  "mas-agents/ingest/androidworld/..."      # audit_only 时必须（或空）
  notes: "..."
  unavailable_reason: "..."                         # unavailable 时必须
```

**新增文件**

* `mas-agents/registry/agent_registry.yaml`
* `mas-harness/src/mas_harness/agents/registry.py`
* （可选）`mas-agents/registry/schemas/agent_registry.schema.json`

**代码验收标准**

* [X]  校验命令：

```bash
PYTHONPATH=mas-harness/src python3 -m mas_harness.cli.validate_registry \
  --snapshot mas-agents/registry/leaderboard_snapshot.json \
  --registry mas-agents/registry/agent_registry.yaml
```

* [X]  校验规则（至少这些必须硬性通过）：
  1. registry 覆盖 snapshot 全量条目（不允许“隐式缺失”）
  2. `agent_id` 唯一
  3. `availability` 三态必填
  4. runnable 必须有 `adapter` 路径；audit\_only 必须有 ingest 入口或声明 `trajectory_format`
  5. unavailable 必须有 `unavailable_reason`
* [X]  单测：
  * `pytest -q mas-harness/tests/test_agent_registry_validation.py`

---

### 3.3.3 3a‑3 Env Profiles（环境画像落盘）

**要做什么**

* 在 repo 内固定两份环境画像：
  * `mas_core.yaml`
  * `android_world_compat.yaml`
* Runner 每次 run 必须把 `env_profile` 写入 `run_manifest.json`。

**新增文件**

* `mas-agents/registry/env_profiles/mas_core.yaml`
* `mas-agents/registry/env_profiles/android_world_compat.yaml`
* `mas-harness/src/mas_harness/env_profiles.py`（或放到 `agents/registry.py` 内也可）

**代码验收标准**

* [X]  能加载并打印：

```bash
python -m mas_harness.cli.validate_registry --print_env_profile mas_core
python -m mas_harness.cli.validate_registry --print_env_profile android_world_compat
```

* [X]  `run_manifest.json` 必含字段：`env_profile`
* [X]  单测：
  * `pytest -q mas-harness/tests/test_env_profiles.py`

---

### 3.3.4 3a‑4 Conformance Suite（接入不追求成功率，只追求可运行与产证据）

**要做什么**

* 新增 `mas-conformance/cases/`：一组最小 case，用来验证 agent 能：
  * 启动
  * 读 observation
  * 输出动作
  * 通过 executor 执行（runnable）或被 ingestion 映射（audit\_only）
  * 产出完整 evidence bundle

**建议的 conformance case 形态**

* Case 只做“动作链”验证，不评估复杂能力。
* Success oracle 只要求“达成简单可观测状态”，比如：
  * `ResumedActivityOracle`：打开 Settings 后前台包名为 com.android.settings
  * `DeviceTimeOracle`：设备可响应

**新增文件**

* `mas-conformance/cases/conf_001_open_settings/*`
* `mas-harness/src/mas_harness/conformance/suite.py`

**代码验收标准**

* [X]  Case schema 校验：

```bash
python -m mas_harness.validate_case \
  --case_dir mas-conformance/cases/conf_001_open_settings
```

* [X]  conformance runner dry-run（不连设备也能完成 spec 校验 + 发现用例）：

```bash
python -m mas_harness.conformance.suite \
  --list_cases mas-conformance/cases
```

* [X]  单测：
  * `pytest -q mas-harness/tests/test_conformance_discovery.py`

---

### 3.3.5 3a‑5 Phase3 Runner 统一入口（runnable 真跑 / audit\_only ingestion）

**要做什么**

新增一个统一入口：

* `python -m mas_harness.cli.run_agent --agent_id <id> --case_dir <...>`
  * 如果 registry 标记 runnable → 启动 adapter → 真跑
  * 如果 registry 标记 audit\_only → 走 ingest\_trajectory → 生成 evidence → 跑 checkers
  * 如果 unavailable → 直接输出可解释失败（不算 infra\_failed，不混进 BF）

**新增文件**

* `mas-harness/src/mas_harness/cli/run_agent.py`
* `mas-harness/src/mas_harness/agents/base.py`
* `mas-harness/src/mas_harness/agents/loader.py`

**代码验收标准**

* [X]  runnable 路径（先用 toy agent 验证 wiring）：

```bash
python -m mas_harness.cli.run_agent \
  --agent_id toy_agent \
  --case_dir mas-public/cases/smoke_001 \
  --output runs/phase3_smoke_toy
```

* [X]  audit\_only 路径（用测试夹具 trajectory）：

```bash
python -m mas_harness.cli.run_agent \
  --agent_id some_audit_only_agent \
  --trajectory mas-harness/tests/fixtures/aw_traj_sample.jsonl \
  --output runs/phase3_ingest_sample
```

* [X]  输出要求（两条路径都必须满足）：
  * `runs/.../run_manifest.json`
  * `runs/.../episode_*/summary.json`
  * `runs/.../episode_*/evidence/*`（至少含 Phase2 required files；Phase3 新增的文件按你设定的 required/optional 执行）
* [X]  单测：
  * `pytest -q mas-harness/tests/test_run_agent_entrypoint.py`

---

### 3.3.6 3a‑6 Manifest/Summary 字段补齐（防误用 + 护栏可审计）——（评测语义补丁 A/B/C）

> 这节保持原结构与通过路径；只扩充字段列表与默认语义，避免后期报告端返工。

#### 3.3.6.1 RunManifest（run\_manifest.json）新增字段（全部 optional + default）

在你原有字段基础上，**增加/替换为以下最小字段集**（兼容旧字段，不破坏 Phase0–2）：

* 基本：
  * `env_profile`
  * `availability`
  * `execution_mode`
  * `run_purpose: benchmark|conformance|agentctl_fixed|agentctl_nl|ingest_only`
* (A) 评测模式（guard 口径）：
  * `eval_mode: vanilla|guarded`
  * `guard_enforced: true|false`
  * `guard_unenforced_reason: null|guard_disabled|not_planner_only|not_L0|unknown`
  * （兼容）`guard_enforcement`：可保留，但视为派生/legacy
* (B) 动作证据链：
  * `action_trace_level: L0|L1|L2|L3|none`
  * `action_trace_source: mas_executor|agent_events|comm_proxy|system_capture|none`
* 证据口径：
  * `evidence_trust_level: tcb_captured|agent_reported|unknown`
  * `oracle_source: device_query|trajectory_declared|none`

默认值建议（避免歧义）：

* 若 `eval_mode` 缺失：默认 `vanilla`
* 若 `eval_mode=vanilla`：`guard_enforced=false` 且 `guard_unenforced_reason="guard_disabled"`
* 若 `eval_mode=guarded` 但不满足护栏 A：`guard_enforced=false` 且 reason 具体化（not\_planner\_only / not\_L0）

#### 3.3.6.2 Episode Summary（episode\_\*/summary.json）新增字段（必须写清）

在你原有字段基础上，增加并写死：

* 运行口径（同 run\_manifest，可 episode 覆盖）：
  * `agent_id`
  * `availability`
  * `execution_mode`
  * `eval_mode`
  * `guard_enforced`
  * `guard_unenforced_reason`
  * `action_trace_level`
  * `action_trace_source`
  * `env_profile`
  * `evidence_trust_level`
  * `oracle_source`
  * `run_purpose`
* (C) 成功判定结构化：
  * `oracle_decision: pass|fail|inconclusive|not_applicable`
  * `agent_reported_finished: true|false`
  * `task_success: true|false|unknown`（严格由 oracle\_decision 派生）

#### 3.3.6.3 代码验收标准（在你已通过项上“加字段校验”，不改变原验收）

* * [X]  运行任意 episode 后，检查 `runs/.../run_manifest.json` 与 `episode_*/summary.json` 字段齐全（新增字段有默认值）
* * [X]  单测：

  * `pytest -q mas-harness/tests/test_manifest_fields_phase3.py`

---

### 3.3.7 3a‑7 Agent 接入验证 CLI（agentctl）——（评测语义补丁 A/C）

> 目的：把“某个 agent 到底接没接入/能不能跑起来”变成一个开发者日常可用的命令，而不是靠手写脚本或反复改 YAML。

#### 3.3.7.1 设计目标（两种模式）

* **可在 terminal 切换已接入 agent**
  * 既支持一次性参数指定（适合脚本/CI）
  * 也支持交互式 shell（适合开发调试）
* **两种运行模式**
  1. **fixed 模式（写死测试指令）**
     * 测试指令固定在测试代码里：例如“打开设置（Open Settings）”
     * 无论切换哪一个 agent，跑的都是同一条固定指令
     * 目标：验证 adapter / runner / env\_adapter / evidence 是否完整打通
  2. **nl 模式（terminal 自然语言）**
     * 你在 terminal 输入自然语言 goal（中文/英文都可）
     * agent 尝试执行该 goal
     * 目标：快速人工验收 agent 的“可用性 + 行为链路”，不作为 benchmark 统计入口

#### 3.3.7.2 新增文件

* `mas-harness/src/mas_harness/cli/agentctl.py`
* `mas-harness/src/mas_harness/phase3_smoke_cases.py`
  * 在代码里定义 `build_fixed_smoke_case_open_settings()`，返回一个内置 TaskSpec/Policy/Eval（无需读取 case\_dir）

#### 3.3.7.3 命令形态（建议同时支持非交互 + 交互）

**A) 非交互（最小可用）**

```bash
# 列出已接入（registry 中 runnable 且 adapter 存在）的 agents
python -m mas_harness.cli.agentctl list

# fixed 模式：固定打开设置（写死在代码里）
python -m mas_harness.cli.agentctl fixed \
  --agent_id ui_tars7b \
  --device_serial emulator-5554 \
  --output runs/agentctl/ui_tars7b

# nl 模式：终端传入自然语言 goal
python -m mas_harness.cli.agentctl nl \
  --agent_id ui_tars7b \
  --device_serial emulator-5554 \
  --goal "打开设置并进入 Wi‑Fi 页面" \
  --max_steps 40 \
  --output runs/agentctl/ui_tars7b
```

**B) 交互式 shell（满足“terminal 里切换 agent”）**

```bash
python -m mas_harness.cli.agentctl shell --device_serial emulator-5554
```

进入后支持最小命令集（示例）：

* `list`：列出 runnable agents（可标注 missing secrets）
* `use <agent_id>`：切换当前 agent
* `fixed`：运行 fixed smoke（写死指令）
* `nl <goal...>`：执行自然语言 goal
* `set max_steps 40`（可选）
* `quit`

#### 3.3.7.4 输出与字段要求（更新：A/C）

agentctl 跑出来的 run 必须：

* 输出目录与 benchmark runs 隔离（推荐默认 `runs/agentctl/<agent_id>/<timestamp>/...`）
* `run_manifest.json` 必须写：
  * `run_purpose = "agentctl_fixed"` 或 `"agentctl_nl"`
  * `eval_mode`：默认 `vanilla`（除非显式提供 `--eval_mode guarded`）
  * `guard_enforced / guard_unenforced_reason`：按 3.3.6 默认规则写
  * `action_trace_level / action_trace_source`：按该 agent 实际情况写
* **fixed 模式（可 hard oracle）**：
  * `oracle_source="device_query"`
  * `oracle_decision="pass"|"fail"|"inconclusive"`
  * `task_success` 严格由 `oracle_decision` 派生（pass→true，fail→false，其它→unknown）
  * `agent_reported_finished` 仍可写（例如 agent 输出 finished）
* **nl 模式（默认不做 hard success）**：
  * `oracle_source="none"`
  * `oracle_decision="not_applicable"`
  * `task_success="unknown"`
  * `agent_reported_finished=true|false`（反映 agent 是否主动结束）

#### 3.3.7.5 代码验收标准

* * [X]  `agentctl list` 可列出 runnable agents
* * [X]  fixed/nl 模式产出 evidence
* * [X]  新增断言：`summary.json` 必包含 `oracle_decision/agent_reported_finished/task_success` 且符合派生规则
    建议新增单测：

```bash
pytest -q mas-harness/tests/test_agentctl_success_semantics.py
```

---

## 3.4 Phase 3b：runnable 的在线运行链路（硬门槛）

> 目标：至少跑通一组“代表性 runnable agents”的 conformance + 若干 hard‑oracle benign regression tasks，保证后续 Phase6/7/10 不空转。
> 同时：让 `agentctl fixed/nl` 在真实设备链路上可用（它复用 Phase3b 的 EnvAdapter/Executor）。

### 3.4.1 3b‑1 AndroidEnvAdapter（reset/observe/execute 全闭环）——（坐标/几何补丁 Coord‑A/B/C）

**要做什么**

* 在 Phase2 你已有 `mas_harness/android/controller.py`（提供 observe 能力）。
* Phase3 需要新增：
  * `AndroidEnvAdapter.reset(snapshot/fixture)`
  * `AndroidEnvAdapter.observe()`（复用 controller）
  * `AndroidEnvAdapter.execute(action)`（新增 executor，至少支持最小动作集合）
  *

#### 3.4.1.1（Coord‑B）screen\_trace：必须落盘 AndroidWorld 关键几何字段

为了让不同 agent（尤其是输出截图坐标的 agent）在不同机型/缩放/导航栏形态下保持可执行且可审计，`observe()` 必须落盘下列屏幕几何信息（建议写进 `screen_trace.jsonl`，或合并进 `obs_trace.jsonl` 的 observation 结构里，但字段名保持一致）：

* `screenshot_size_px`: `{w, h}`（你保存的 screenshot 图像尺寸）
* `logical_screen_size_px`: `{w, h}`（Android reported logical size，例如 `wm size` / display metrics）
* `physical_frame_boundary_px`: `{left, top, right, bottom}`（可点击区域的物理边界：考虑 status bar / nav bar / cutout）
* `orientation`: `portrait|landscape`（或 0/90/180/270，但要统一）

> 这四个字段是后面 coord 换算的唯一依据；没有它们，归因会混乱（你很难证明是 agent 错还是环境缩放错）。

#### 3.4.1.2（Coord‑A）坐标口径：为动作引入 coord\_space，并以 physical\_px 作为执行口径

在 `normalized_action` 中为所有“带坐标”的动作引入字段（示例）。约定：`coord_space` 表达 **最终执行口径**（canonical），因此坐标动作里固定为 `physical_px`；若输入坐标不是 `physical_px`，用 `coord_transform` 记录输入口径与换算依据：

```json
{
  "type": "tap",
  "coord_space": "physical_px",
  "coord": {"x_px": 520, "y_px": 684, "x_norm": 0.52, "y_norm": 0.34},
  "coord_transform": {
    "from": "normalized_screenshot",
    "to": "physical_px",
    "screen_trace_ref": "screen_step_12",
    "params": {"scale_x": 2.0, "scale_y": 2.0, "offset_x": 0, "offset_y": 72},
    "warnings": []
  }
}
```

建议支持的 `coord_transform.from`（输入口径）：

* `screenshot_px`：以 screenshot 图像左上角为 (0,0) 的像素坐标
* `logical_px`：Android 逻辑尺寸坐标（受 density/缩放影响）
* `normalized_physical` / `normalized_screenshot` / `normalized_logical`：0–1 归一化坐标（以各自空间宽高为基准）
* `unknown`：无法判断输入口径（不要硬猜；可能无法换算）

#### 3.4.1.3（Coord‑C）坐标换算原则：只在需要时换算，并落盘换算依据；physical\_px 必须 identity

* **如果输入（raw action）声明的 `coord_space` 已是 `physical_px` → Normalizer 必须保持 identity（不做 scale/offset 换算；不写 `coord_transform`）**
* 只有当输入（raw action）声明/推导出的输入口径不是 `physical_px` 时，才根据 `screen_trace` 做换算并：
  * 写入 `coord_transform`（包含引用的 screen\_trace step、参数、以及任何 warnings）
  * 产出最终 `physical_px`（写入 `coord.x_px/y_px` 或 `start/end.x_px/y_px`，供 Executor 执行）

> 这条规则用于解决“agent 原本坐标正确，但你换算导致点错”的风险：当 agent 明确声明 `physical_px` 时，你的系统不会动它。

#### 最小动作集合（必须）

> Runner/Normalizer 可以接受多坐标口径输入，但 **Executor 只执行一种 canonical 口径：physical\_px**。

* `tap`（abs\_px / normalized；最终都归一化为 physical\_px）
* `swipe`
* `type`
* `press_back`
* `home`
* `open_app`
* `open_url`
* `wait`
* `finished`

**新增文件**

* `mas-harness/src/mas_harness/android/env_adapter.py`
* `mas-harness/src/mas_harness/android/executor.py`

**代码验收标准**

* [X]  本地 smoke（需要连 emulator/设备）：用能产出完整 evidence bundle 的链路跑一次（推荐 `agentctl fixed`）：

```bash
python -m mas_harness.cli.agentctl fixed \
  --agent_id toy_agent \
  --env_profile android_world_compat \
  --device_serial emulator-5554 \
  --output runs/phase3_smoke_toy

python -m mas_harness.tools.audit_bundle runs/phase3_smoke_toy
```

* [X]  产物：smoke run 必须产出完整 evidence bundle（至少 Phase2 required files），并且 screen\_trace（或等价字段）包含四字段：
  * `screenshot_size_px / logical_screen_size_px / physical_frame_boundary_px / orientation`
* [X]  单测（不连设备的部分）：
  * 坐标归一化（输入口径 → `physical_px`）：
    1. 输入 `coord_space=physical_px` 必须 identity（不换算、不写 `coord_transform`）
    2. 输入 `coord_space=screenshot_px`（或 `normalized_screenshot`）在给定 `screen_trace` 参数时能正确换算，并写 `coord_transform.from`
  * action schema 校验

```bash
pytest -q mas-harness/tests/test_android_executor_unit.py
```

---

### 3.4.2 3b‑2 obs\_digest + 护栏 B（ref\_obs\_digest 强校验）——（评测语义补丁 D：去抖动版本）

**要做什么**

* 在 `EvidenceWriter.record_observation(...)` 里计算 `obs_digest`（sha256），写入 `obs_trace.jsonl`。
* 在 agent 输出动作时（或 action normalizer 阶段），把 `ref_obs_digest` 写入 `normalized_action`。
* executor 执行动作前必须校验：当前 `obs_digest` 与 action.`ref_obs_digest` 一致（仅当 `ref_check_applicable=true`）。

#### （D）obs\_digest 必须“可复现且去抖动”：组件 digest 组合 + canonicalize

**新增字段（建议写入 obs\_trace.jsonl）**

* `obs_digest`
* `obs_digest_version`（例如：`v2_component_canonicalized`）
* `obs_component_digests`（对象，便于定位抖动来源）：
  * `screenshot_digest`
  * `ui_dump_digest`
  * `ui_elements_digest`
  * `foreground_digest`
  * `geometry_digest`（建议覆盖 screen\_trace 四字段）
  * `notifications_digest`（建议 bucket/截断后再 hash）
  * `clipboard_digest`（可选建议默认不进或只做 coarse bucket）

**推荐的低噪声默认（先落地，避免卡住）**

> 你可以先只用稳定组件（D-1），后续再加 ui\_elements/xml 等（D-2）。

* `screenshot_digest = sha256(image_bytes)`
* `foreground_digest = sha256(package + activity)`
* `geometry_digest = sha256(screenshot_size_px + logical_screen_size_px + physical_frame_boundary_px + orientation)`
* `obs_digest = sha256(join([screenshot_digest, foreground_digest, geometry_digest]))`

**当你引入 UI/notifications 时的 canonicalize 建议（D-2）**

* `ui_elements_digest`：按稳定 key 排序（例如 bbox+resource\_id+text+package）后再 hash
* `ui_dump_digest`：去掉明显时间字段/不稳定属性后再 hash
* `notifications_digest`：按 (pkg,title,text,posted\_time\_bucket) 排序/截断后再 hash
* `clipboard_digest`：默认不进；如必须进，只用“是否非空 + 长度 bucket”避免抖动

**需要修改的现有文件**

* `mas-harness/src/mas_harness/evidence.py`（obs\_trace 增字段：obs\_digest\_version / component digests）
* `mas-harness/src/mas_harness/action_normalizer.py`（为坐标/索引动作补 ref\_obs\_digest；并配合 coord\_space/coord\_transform）
* `mas-harness/src/mas_harness/android/executor.py`（执行前校验）

**代码验收标准**

* * [X]  单测（原有）：

  1. 生成两次不同 observation → obs\_digest 不同
  2. 用旧 ref\_obs\_digest 的动作执行 → 必须拒绝执行，并标注 `agent_failed`
* * [X]  单测（新增 D 去抖动）：

  3) notifications 顺序变化但 canonicalize 后 digest 不变（若你纳入 notifications）
  4) geometry 无变化但 dumpsys 字段抖动不应影响 digest（若 dumpsys 不纳入组件 digest）

```bash
pytest -q mas-harness/tests/test_ref_obs_digest_guard.py
pytest -q mas-harness/tests/test_obs_digest_canonicalization.py
```

---

### 3.4.3 3b‑3 device\_input\_trace.jsonl（L0 最低交付，拆小步；后续扩展到 L1/L2；不做 L3）

> 目标：先把 **L0（planner\_only）** 路径的“动作执行证据链”落盘为 `device_input_trace.jsonl`，保证每个执行动作都有可审计回执。
> 本 Phase3 **仅规划 L0/L1/L2**；**L3（系统级 getevent/root 抓取）不再做**。

---

#### 3.4.3.0a 索引契约：`step_idx` 是 event 序号；新增 `ref_step_idx` 指向 action step（允许 1:N）

**要做什么**

* 明确 `device_input_trace.jsonl` 的 `step_idx`：**该文件内部的事件序号（event\_idx）**，要求单调递增、唯一。
* 新增可选字段 `ref_step_idx`：**指向 `agent_action_trace.jsonl.step_idx`**（用于把“这条输入证据”关联回“哪次动作决策/归一化”）。
* 规则按 level 分层写死：
  * **L0**：必须满足 `ref_step_idx == step_idx`，并且与 `agent_action_trace.step_idx`**严格 1:1 对齐**。
  * **L1/L2**：允许 **1:N**（多个输入事件共享同一个 `ref_step_idx`），也允许 `ref_step_idx=null`（当无法可靠关联到 action 决策步时）。

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_device_input_trace_index_contract.py
```

断言至少包含：

1. L0：`ref_step_idx` 必填且等于 `step_idx`
2. L1/L2：允许 `ref_step_idx` 重复或为 null；但 `step_idx` 必须单调递增且唯一

---

#### 3.4.3.0b 坐标严格度矩阵：L0 strict，L1/L2 tolerant（避免以后 L1/L2 卡死）

**要做什么（写死规则）**

* 对 `tap/swipe` 等坐标类事件：
  * **共同要求**：`payload.coord_space` 最终必须是 `"physical_px"`（canonical 口径不变）
  * **L0（strict）**：
    * `coord_space` 必须是 `"physical_px"`
    * `x/y`（或 start/end）必须是 int 且非空
    * `mapping_warnings` 必须为空
    * 否则 fail
  * **L1/L2（tolerant）**：
    * `coord_space` 仍要求 `"physical_px"`
    * 若坐标无法解析/无法换算 → `x/y=null`，且 `mapping_warnings += ["coord_unresolved"]`
    * 禁止 silent drop

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_device_input_trace_coord_strictness_matrix.py
```

断言至少包含：

1. L0：坐标缺失 → fail
2. L1/L2：坐标缺失 → 允许，但必须有 `coord_unresolved` warning

---

#### 3.4.3.1 3b‑3a 定义最小行结构 + 可选 JSONSchema（先把“契约”写死）

**要做什么**

* 固定 `device_input_trace.jsonl` 的最小字段集（每行一个 event）：
  * `step_idx`：device\_input\_trace 内事件序号（event\_idx），**单调递增且唯一**
  * `ref_step_idx`：可选，指向 `agent_action_trace.step_idx`
    * L0 必填且要求 `ref_step_idx == step_idx == agent_action_trace.step_idx`
    * L1/L2 可重复或为 null
  * `source_level: "L0"|"L1"|"L2"`（本步骤先用 L0）
  * `event_type`（tap/swipe/type/back/home/open\_app/open\_url/wait/finished…）
  * `payload`（坐标事件必须最终是 `coord_space="physical_px"`）
  * `timestamp_ms`
  * `mapping_warnings: []`
* 落盘 `device_input_trace.schema.json`，用于 CI/审计工具复用。

**示例（L0）**

```json
{
  "step_idx": 12,
  "ref_step_idx": 12,
  "source_level": "L0",
  "event_type": "tap",
  "payload": {"x": 123, "y": 456, "coord_space": "physical_px"},
  "timestamp_ms": 1730000000000,
  "mapping_warnings": []
}
```

**需要新增/修改**

* `mas-harness/src/mas_harness/schemas/device_input_trace.schema.json`（可选）
* `mas-harness/src/mas_harness/evidence.py`（后续 writer 会用到）

**代码验收标准**

* [X]  schema（若落盘）能被加载：

```bash
python3 -c "import json; json.load(open('mas-harness/src/mas_harness/schemas/device_input_trace.schema.json'))"
```

* [X]  单测：schema（若存在）对合法/非法样例的验证行为符合预期：
  * L0 样例必须带 `ref_step_idx`
  * L1/L2 样例允许 `ref_step_idx` 缺失或为 null（若 schema 选择 level-aware 校验，则必须覆盖）

```bash
pytest -q mas-harness/tests/test_device_input_trace_schema_unit.py
```

---

#### 3.4.3.2 3b‑3b EvidenceWriter：新增 device\_input\_trace writer（补齐 `ref_step_idx`；并把契约校验落到 writer）

> 目的：避免实现者把 `ref_step_idx` 藏进 payload 或靠隐式读 action\_trace 做关联；writer 必须能**显式写出 ref\_step\_idx**，并在写入时做最小强校验（level 分层）。

---

##### 3.4.3.2a Writer API：函数签名显式支持 `ref_step_idx`

**要做什么**

* 在 `EvidenceWriter`（或你现有 evidence 写入组件）中新增（或替换为）：

```python
record_device_input_event(
  step_idx,
  ref_step_idx,   # int | None；L0 必填；L1/L2 允许 None
  source_level,   # "L0"|"L1"|"L2"
  event_type,
  payload,
  timestamp_ms,
  mapping_warnings,
)
```

* writer 必须把 `ref_step_idx` 写成顶层字段（不是塞进 payload）。

**需要新增/修改**

* `mas-harness/src/mas_harness/evidence.py`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_device_input_trace_writer_unit.py
```

断言至少包含：

1. 写入一行后 JSON 顶层包含 `ref_step_idx`（允许 null）
2. L0 写入：`ref_step_idx` 不允许为 null

---

##### 3.4.3.2b Writer 校验：索引契约（单调唯一 + L0 ref 强约束）

**要做什么**

* writer 内部维护 `last_step_idx`（或等价机制），强制：
  * `step_idx` 必须是 int
  * `step_idx` 必须严格递增（`step_idx > last_step_idx`），从而天然唯一
* `ref_step_idx` 校验按 level 分层写死：
  * **L0**：`ref_step_idx is not None` 且 `ref_step_idx == step_idx`
  * **L1/L2**：允许 `ref_step_idx is None`，也允许重复/不等于 `step_idx`（但 `step_idx` 仍需单调递增）

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_device_input_trace_writer_unit.py
```

断言至少包含：

1. L0：`ref_step_idx != step_idx` → fail
2. 任意 level：`step_idx` 不递增（重复/倒退）→ fail
3. L1/L2：`ref_step_idx` 可为 null 且允许重复（同一个 ref 对应多 event）

---

##### 3.4.3.2c Writer 校验：坐标严格度矩阵（与 3.4.3.0b 保持一致）

**要做什么**

* writer 对坐标类 event（至少 `tap/swipe`）按 level 强约束：
  * 共同：`payload.coord_space == "physical_px"`，否则 fail
  * **L0 strict**：
    * `payload.x/payload.y`（或 start/end）必须非空且为 int
    * `mapping_warnings` 必须为空
  * **L1/L2 tolerant**：
    * 若 `x/y` 为 null，则 `mapping_warnings` 必须包含 `"coord_unresolved"`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_device_input_trace_writer_unit.py
pytest -q mas-harness/tests/test_device_input_trace_coord_strictness_matrix.py
```

断言至少包含：

1. L0：坐标缺失 / warnings 非空 → fail
2. L1/L2：坐标缺失 → 允许，但必须包含 `coord_unresolved`

---

#### 3.4.3.3 3b‑3c Executor 集成：L0 每执行一个动作写一行（对齐 step\_idx）

**要做什么**

* 在 `mas_harness/android/executor.py` 的 L0（planner\_only）执行路径中：
  * 每执行一个 **normalized\_action**（tap/swipe/type/back/home/open\_app/open\_url/wait/finished…）
  * 写入一条 `device_input_trace.jsonl`
* `step_idx/ref_step_idx` 对齐规则（硬规则）：
  * executor 写入的 `step_idx` 必须与 **该动作在 `agent_action_trace.jsonl` 的 step\_idx** 一致
  * L0 写入时同时写 `ref_step_idx=action_step_idx`，并且 `step_idx=action_step_idx`（因此 L0 下两者相等）
  * 若你的动作结构里没有 step\_idx：由 runner 给 executor 传入（不要 executor 自己重新计数）

**需要新增/修改**

* `mas-harness/src/mas_harness/android/executor.py`
* （可能需要）`mas-harness/src/mas_harness/runner`/`run_agent`：确保 step\_idx 能传到 executor

**代码验收标准**

* [X]  纯单元测试（mock 掉 adb 实际执行，验证“每个 action → 写一行”）：

```bash
pytest -q mas-harness/tests/test_device_input_trace_l0_executor_unit.py
```

断言至少包含：

1. 执行 K 个动作 → trace 行数 == K
2. 每行 `source_level=="L0"`
3. 每行 `ref_step_idx == step_idx`
4. tap/swipe 的 `payload.coord_space=="physical_px"`
5. `timestamp_ms` 非空

---

#### 3.4.3.4 3b‑3d L0 端到端一致性测试：agent\_action\_trace ↔ device\_input\_trace step\_idx 对齐

**要做什么**

* 增加一个“读两份 jsonl 并校验 step\_idx 对齐”的测试：
  * `agent_action_trace.jsonl` 行数 == `device_input_trace.jsonl` 行数（**建议 L0 不允许例外**）
  * 测试逻辑用 `ref_step_idx` join（更语义化）：
    * L0：断言 `agent_action_trace.step_idx` 与 `device_input_trace.ref_step_idx`**逐行一致**
    * 额外断言：`device_input_trace.step_idx == device_input_trace.ref_step_idx`

**需要新增/修改**

* `mas-harness/tests/test_device_input_trace_l0_alignment.py`
  \*（如需）小型 runs fixture（可用临时目录生成）

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_device_input_trace_l0_alignment.py
```

---

#### 3.4.3.5 3b‑3e evidence\_pack 规则：L0 时 device\_input\_trace 设为 required

**要做什么**

* 在 `mas_harness/evidence_pack.py` 中写死：
  * 当 `run_manifest.action_trace_level == "L0"` → `device_input_trace.jsonl`**必须存在**
* 暂时只对 L0 生效（L1/L2 在后续 3.4.6 统一补齐为 required）

**需要新增/修改**

* `mas-harness/src/mas_harness/evidence_pack.py`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_evidence_pack_requires_device_input_trace_l0.py
```

---

#### 3.4.3.6 3b‑3f 真机/模拟器 smoke：planner\_only 跑一次，确认落盘 + 行数合理

**要做什么**

* 用最小可跑链路（推荐 `agentctl fixed` 或你已有的 toy\_agent）在真机/模拟器跑一次 L0：
  * evidence 目录出现 `device_input_trace.jsonl`
  * 行数 ≥ 执行动作数（建议 ==）
  * 每行 `ref_step_idx == step_idx`

**代码验收标准**

* [X]  本地命令（需要连 emulator/设备）：

```bash
python -m mas_harness.cli.agentctl fixed \
  --agent_id toy_agent \
  --env_profile android_world_compat \
  --device_serial emulator-5554 \
  --output runs/phase3_smoke_toy_l0
```

* [X]  产物检查：

```bash
ls runs/phase3_smoke_toy_l0/episode_*/evidence/device_input_trace.jsonl
```

* [ ]  （可选）审计工具：

```bash
python -m mas_harness.tools.audit_bundle runs/phase3_smoke_toy_l0
```

---

### 3.4.4 3b‑4 Action Evidence：L1（agent\_events）接入（不做 L3）

> 目标：对 `agent_driven` 的 agent（例如 DroidRun）——如果它能导出事件流/宏录制，则映射生成 `device_input_trace.jsonl`（L1）。
> L1 的强点是：你能证明“agent 产生了哪些输入事件/动作证据”，但它不等于 L0 的“MAS 真执行回执”。

---

#### 3.4.4.1 3b‑4a L1 raw events 最小格式 + fixture（先写死输入契约）

**要做什么**

* 定义 L1 raw events（jsonl）最小字段：
  * `timestamp_ms`
  * `type`: tap/swipe/type/back/home/open\_app/open\_url/wait/...
  * 坐标字段可选：`coord_space` + `x/y` 或 `start/end`
* 提供 fixture：`mas-harness/tests/fixtures/agent_events_l1_sample.jsonl`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_action_evidence_l1_parser_unit.py
```

---

#### 3.4.4.2 3b‑4b L1 Collector：run 后读取 path\_on\_host（先不做 streaming）

**要做什么**

* 在 `adapter_manifest.json` 增加可选段（仅 L1 agent 需要）：

```json
{
  "action_evidence": {
    "level": "L1",
    "source": "agent_events",
    "event_stream": {
      "format": "agent_events_v1",
      "path_on_host": "runs/.../agent_events.jsonl"
    }
  }
}
```

* 实现 `L1AgentEventsCollector`：读取 `path_on_host` → 产 raw events

**新增/修改文件**

* `mas-harness/src/mas_harness/action_evidence/base.py`
* `mas-harness/src/mas_harness/action_evidence/l1_agent_events.py`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_action_evidence_l1_collector_file.py
```

---

#### 3.4.4.3 3b‑4c L1 映射：raw events → device\_input\_trace(L1)（step\_idx/ref\_step\_idx 规则写死）

**要做什么**

* 生成 `device_input_trace.jsonl`：
  * `source_level="L1"`
  * 坐标类最终 `payload.coord_space="physical_px"`（能换算则换算；不能则置空 + warning）
* step\_idx/ref\_step\_idx 对齐规则（硬规则）：
  * `step_idx`：**event 序号**（按事件顺序从 0 分配；或复用 raw 的 event\_idx，只要满足单调递增且唯一）
  * `ref_step_idx`：**可选**（raw 若提供可可靠关联 action step 的字段就填；否则 null）
  * 明确允许 **1:N**：多个 event 允许共享同一 `ref_step_idx`
* 不允许 silent drop：
  * 不支持事件类型 → 必须写入一行（可以降级为 `event_type="wait"` 并写 warning）
* 写入接口要求：
  * mapper 调用 writer 时必须显式传入 `ref_step_idx`（可为 null），禁止把它塞进 payload。

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_action_evidence_l1_mapping.py
```

---

#### 3.4.4.4 3b‑4d run\_agent 集成 L1：run 结束时 materialize L1 device\_input\_trace

**要做什么**

* 在 `run_agent` 的 run/episode 结束阶段：
  * 若 manifest 声明 L1 且 `path_on_host` 存在 → 生成 L1 `device_input_trace.jsonl`

**代码验收标准**

* [X]  提供一个 dry-run ingest flag（不连真机也能验收 mapping 管线）：

```bash
python -m mas_harness.cli.run_agent \
  --agent_id toy_agent_driven_l1 \
  --case_dir mas-conformance/cases/conf_001_open_settings \
  --output runs/l1_map_only \
  --dry_run_ingest_events mas-harness/tests/fixtures/agent_events_l1_sample.jsonl
```

* [X]  单测：

```bash
pytest -q mas-harness/tests/test_run_agent_l1_ingest_plumbing.py
```

---

### 3.4.5 3b‑5 Action Evidence：L2（comm\_proxy）接入（不做 L3）

> 目标：对“动作通过 RPC/HTTP/WebSocket 下发”的 agent\_driven 系统，用通信层 recorder 生成 `device_input_trace.jsonl`（L2）。
> L2 证明“动作命令被下发”，不保证设备端执行成功（比 L1/L0 弱）。

---

#### 3.4.5.1 3b‑5a comm\_proxy\_trace 最小格式 + fixture

**要做什么**

* 定义 `comm_proxy_trace.jsonl` 最小字段：
  * `timestamp_ms`
  * `direction`: request/response/message
  * `endpoint`（HTTP 路径 / rpc 方法）
  * `payload` 或 `payload_digest`
  * `status`（可选）
* fixture：`mas-harness/tests/fixtures/comm_proxy_l2_sample.jsonl`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_action_evidence_l2_parser_unit.py
```

---

#### 3.4.5.2 3b‑5b 最小 Recorder：HTTP JSON action endpoint（离线可测优先）

**要做什么**

* 实现 `HttpJsonActionRecorder`：
  * 记录 raw 到 `comm_proxy_trace.jsonl`
  * 支持最小约定：`POST /act` body `{type, x, y, coord_space, ...}`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_action_evidence_l2_http_recorder.py
```

---

#### 3.4.5.3 3b‑5c L2 映射：comm\_proxy\_trace → device\_input\_trace(L2)（不 silent drop；补齐 index 分配规则）

**要做什么**

* 从 comm messages 抽取动作并生成 `device_input_trace.jsonl`：
  * `source_level="L2"`
  * 坐标规则同 L1：最终 `coord_space="physical_px"` 或置空+warning

---

##### 3.4.5.3a 索引分配规则（L2 必须与 3.4.3.0a 对齐）

**要做什么（写死规则）**

* `step_idx`：**device\_input\_trace 内 event 序号（event\_idx）**，必须单调递增且唯一。推荐两种实现（二选一写死即可）：
  1. **按“被识别为 action 的消息顺序”从 0 分配**（最推荐，输出紧凑、最易审计）
  2. **复用原 trace 的 message\_idx**（允许有 gap，但仍需满足单调递增且唯一）
* `ref_step_idx`：可选
  * 若能可靠关联到 `agent_action_trace.step_idx` 就填
  * 否则为 null
  * 明确允许 1:N（多个 L2 event 共享同一 ref\_step\_idx）
* mapper 调用 writer 时必须显式传入 `ref_step_idx`（可为 null），禁止塞进 payload。

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_action_evidence_l2_mapping.py
```

断言至少包含：

1. 输出 `device_input_trace.step_idx` 单调递增且唯一
2. `ref_step_idx` 允许 null / 重复（1:N）

---

##### 3.4.5.3b 非动作消息处理（不进入 device\_input\_trace 也必须可审计）

**要做什么（写死规则）**

* 非动作消息：
  * **必须保留在 `comm_proxy_trace.jsonl`**（raw 不丢）
  * **不要求进入 `device_input_trace.jsonl`**（推荐：不进）
* 但必须避免“映射阶段 silent drop”的不可审计性：至少做一项（写死一种即可）：
  1. 在 `episode_*/summary.json` 写入 `action_evidence_mapping_stats.skipped_non_action_count`
  2. 或在 evidence 目录写 `comm_proxy_mapping_stats.json`（包含 skipped counts）

> 推荐方案：写入 summary（更轻量，报告端也更好用）。

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_action_evidence_l2_mapping.py
```

断言至少包含：

1. fixture 中存在非动作消息 → `skipped_non_action_count > 0`（写入 summary 或 stats 文件）
2. raw `comm_proxy_trace.jsonl` 行数不变（未因映射而丢失）

---

##### 3.4.5.3c 坐标规则（L2 tolerant，但必须写 warning）

**要做什么**

* 坐标类动作：
  * 最终 `payload.coord_space == "physical_px"`
  * 若无法解析/换算 → `x/y=null` 且必须包含 `coord_unresolved` warning

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_action_evidence_l2_mapping.py
```

---

#### 3.4.5.4 3b‑5d run\_agent 集成 L2：启动 recorder + run 后映射

**要做什么**

* `run_agent` 支持 `--comm_proxy_mode off|record`
* 当 manifest 声明 L2：
  * run 前启动 recorder
  * run 后 flush 生成 `comm_proxy_trace.jsonl`
  * map 生成 `device_input_trace.jsonl`（L2）
  * （若采用 3.4.5.3b 的 summary 方案）把 mapping stats 写入 `episode_*/summary.json`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_run_agent_l2_proxy_plumbing.py
```

---

### 3.4.6 3b‑6 统一自检 + run\_manifest finalize（以实际产物为准；堵住“宣称有证据但没文件”）

> 这里是“防返工补丁”核心：
>
> * `action_trace_source` 只写在 run\_manifest/summary（不强制写进 device\_input\_trace 每行）
> * `run_manifest` 支持 **draft + finalize**（以实际产物为准）
> * evidence\_pack + audit\_bundle **强制一致性校验**
> * 允许 strict 模式（期望 L1/L2 却没产出就直接失败）

---

#### 3.4.6.1 3b‑6a 统一 device\_input\_trace schema 校验（L0/L1/L2 同一套）

**要做什么**

* 增加一个 validator：对 `device_input_trace.jsonl` 做逐行校验（字段齐全、枚举合法、坐标规则合法）
* **所有 level**：coord\_space 最终必须 physical\_px（不变）
* **L0**：坐标缺失/越界 → fail
* **L1/L2**：坐标缺失允许，但必须 warning（coord\_unresolved）

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_device_input_trace_schema_validation.py
```

---

#### 3.4.6.2 3b‑6b evidence\_pack 规则升级：L0/L1/L2 都必须有 device\_input\_trace；none 可缺

**要做什么**

* 写死规则：
  * manifest `action_trace_level in {"L0","L1","L2"}` → 必须存在 `device_input_trace.jsonl`
  * 文件存在但 source\_level 与 manifest 不一致 → 视为无效（audit fail）
  * 当 manifest 是 L0：必须通过 “L0 strict 坐标规则 + L0 strict ref\_step\_idx 对齐规则”
  * 当 manifest 是 L1/L2：必须通过 “event 序号合法 + 坐标 tolerant 规则（缺坐标必须 warning）”

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_evidence_pack_device_input_trace_requirements.py
```

---

#### 3.4.6.3 3b‑6c audit\_bundle 增加一致性自检（强制堵错）

**要做什么**

* `audit_bundle` 增加 `audit_device_input_trace()`：
  * L0/L1/L2 缺文件 → fail
  * source\_level 不一致 → fail
  * schema 非法 → fail
  * L0：必须通过 strict 坐标 + strict ref\_step\_idx 对齐
  * L1/L2：允许 ref\_step\_idx 缺失/重复，但 step\_idx 必须单调唯一；坐标缺失必须 warning

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_device_input_trace_audit.py
python -m mas_harness.tools.audit_bundle runs/<any_run_dir>
```

---

#### 3.4.6.4 3b‑6d run\_manifest 两阶段写入 + finalize（action\_trace\_level/source 以产物为准）

##### 3.4.6.4a draft：run 开始写“期望值”（来自 registry/adapter\_manifest）

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_run_manifest_finalize_action_evidence.py
```

---

##### 3.4.6.4b finalize：run 结束读 evidence 回写“实际值”（以产物为准）

**要做什么**

* 若 `device_input_trace.jsonl` 存在且合法：
  * `action_trace_level = "L0"|"L1"|"L2"`（由文件 `source_level` 决定）
  * `action_trace_source = "mas_executor"|"agent_events"|"comm_proxy"`
* 否则：
  * `action_trace_level="none"`, `action_trace_source="none"`
  * 写明降级原因字段：
    * `action_trace_degraded_from`
    * `action_trace_degraded_reason`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_run_manifest_finalize_action_evidence.py
```

---

##### 3.4.6.4c strict 模式（可选但推荐）：期望 L1/L2 却没产出则 fail

**要做什么**

* 增加 `--strict_action_evidence`
* 若期望 L1/L2 但 finalize 后是 none → 直接判 run 失败（failure\_class 你选 agent\_failed 或 infra\_failed，但必须写死一种）

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_run_manifest_finalize_action_evidence.py
```

---

### 3.4.7 3b‑7 runnable Agent Adapter 模板化（加入 action\_evidence 声明点；坐标默认口径 default\_coord\_space）

#### 3.4.7.1 3b‑7a adapter\_manifest 扩展：default\_coord\_space（必需）+ action\_evidence（可选）

**要做什么**

* 在每个 runnable adapter 的 `adapter_manifest.json` 中：
  * 必填：`default_coord_space`
  * 可选：`action_evidence`（仅 L1/L2 需要）

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_adapter_manifest_schema_action_evidence.py
```

---

#### 3.4.7.2 3b‑7b registry ↔ adapter\_manifest 一致性校验（运行前发现错配）

**要做什么**

* 增加一致性校验：
  * registry 标 L1 → manifest.action\_evidence.level 必须是 L1
  * registry 标 L2 → 必须是 L2
  * 不允许出现 L3

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_registry_manifest_consistency.py
```

---

#### 3.4.7.3 3b‑7c materialize\_action\_evidence 统一后处理入口（L0/L1/L2 都走 finalize）

**要做什么**

* 在 runner/run\_agent 里实现一个统一后处理：
  * L0：executor 已写 → validate → finalize
  * L1：collector+mapper → validate → finalize
  * L2：recorder+mapper → validate → finalize

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_action_evidence_materialize_pipeline.py
```

---

### 3.4.8 3b‑8 runnable agents 的“代表性集合”（加入 L1/L2 维度；避免 Phase3 被拖死）

#### 3.4.8.1 3b‑8a core/extended 分层 + core 最低证据强度约束

**要做什么**

* `agent_registry.yaml` 增 `tier: core|extended`
* core 最低要求写死：
  * `action_trace_level in {L0,L1,L2}`（不能是 none）
  * conformance 可跑通并产证据

**代码验收标准**

```bash
python -m mas_harness.cli.validate_registry --core_only \
  --registry mas-agents/registry/agent_registry.yaml
pytest -q mas-harness/tests/unit/integration/test_validate_core_agents.py
```

---

#### 3.4.8.2 3b‑8b core conformance 批跑入口（支持 filter）

**代码验收标准**

```bash
python -m mas_harness.conformance.suite \
  --cases mas-conformance/cases \
  --agents_from_registry mas-agents/registry/agent_registry.yaml \
  --filter "availability=runnable,tier=core" \
  --output runs/conformance_core

pytest -q mas-harness/tests/test_conformance_filter_by_tier.py
```

---

#### 3.4.8.3 3b‑8c regression 子集（≥N hard‑oracle benign）按 L0/L1/L2 分桶展示（不出现 L3）

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_reporting_bucketing_action_levels_no_l3.py
pytest -q mas-harness/tests/test_reporting_no_l3_assumption.py
```

---

## 3.5 Phase 3c：audit\_only ingestion + unavailable 证据化（持续扩展）

### 3.5.1 3c‑1 Trajectories ingestion（多格式插件化：AndroidWorld + 各 agent 自产 trajectories/logs；先写死映射）

> 更新点：不再把 ingestion 写死为“AndroidWorld trajectories ingestion”。
> 现在是：**ingestion 框架 + 多个 format/agent 插件**。
> 你可以先为每个 mobile agent 的自定义轨迹写死一个插件（例如 `droidrun_events_v1`），后续再抽象通用字段。

---

#### 3.5.1.1 3c‑1a ingestion 插件接口 + 注册表（框架先立住）

**要做什么**

* 定义统一接口（示意）：

```python
class TrajectoryIngestor(Protocol):
    format_id: str
    def probe(self, input_path: str) -> bool: ...
    def ingest(self, input_path: str, output_dir: str, *, agent_id: str | None, registry_entry: dict | None) -> None: ...
```

* 提供 registry：
  * `register_ingestor`
  * `get_ingestor_by_format`
  * （可选）`auto_detect`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_ingestion_plugin_registry.py
```

---

#### 3.5.1.2 3c‑1b registry 增 trajectory\_format + CLI auto-dispatch（按 agent 写死）

**要做什么**

* `agent_registry.yaml` 对 audit\_only 条目允许/建议填写：

```yaml
trajectory_format: androidworld_jsonl | droidrun_events_v1 | ...
```

* CLI 支持：
  * `--format <format_id>`
  * 或 `--agent_id <id> --trajectory <path>` 自动从 registry 解析 format 并 dispatch

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_ingestion_auto_dispatch.py
python -m mas_harness.cli.ingest_trajectory --help | grep -E "format|agent_id"
```

---


#### 3.5.1.5 3c‑1e（E）ref\_check\_applicable/auditability 降级规则（框架级合同测试）

**要做什么**

* 轨迹缺 observation（无 screenshot 且无 geometry）→ 必须写：
  * `ref_check_applicable=false`
  * `auditability_limited=true`
  * `obs_digest/ref_obs_digest=null`
* checkers 依赖 ref 的项必须输出 `not_applicable`

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_ingestion_ref_applicability_generic.py
```

---

#### 3.5.1.6 3c‑1f mapping\_notes：每个插件都必须有映射说明（先写死也要写清楚）

**代码验收标准**

```bash
pytest -q mas-harness/tests/test_ingestion_notes_exist.py
```

---

### 3.5.2 3c‑2 audit\_only 的评测输出规则（避免误用；并分桶 L0/L1/L2/none；不出现 L3）

#### 3.5.2.1 3c‑2a 分桶逻辑纯函数化 + 单测

```bash
pytest -q mas-harness/tests/test_reporting_bucketing.py
```

#### 3.5.2.2 3c‑2b 报告必须输出 action evidence 分布（且默认无 L3）

```bash
pytest -q mas-harness/tests/test_reporting_required_sections.py
pytest -q mas-harness/tests/test_reporting_no_l3_assumption.py
```

---

### 3.5.3 3c‑3 unavailable 的证据化（保持但拆小步）

#### 3.5.3.1 3c‑3a registry 强制 unavailable\_reason

```bash
pytest -q mas-harness/tests/test_agent_registry_validation.py
```

#### 3.5.3.2 3c‑3b 报告输出 unavailable reasons 分布

```bash
pytest -q mas-harness/tests/test_reporting_unavailable_reasons.py
```

---

## 3.6 Phase 3 总体验收清单（PR Checklist）（更新：3.4.3 未实现；加入 L1/L2；不做 L3）

### 3.6.1 Phase 3a（硬门槛）

（保持你原清单不变）

### 3.6.2 Phase 3b（硬门槛）

* [ ]  **3.4.3 L0**：planner\_only 跑一次，必须产出 `device_input_trace.jsonl`
* [ ]  **3.4.6**：schema + evidence\_pack + audit\_bundle 一致性自检；run\_manifest finalize（strict 可选）
* [ ]  **3.4.4 L1**：至少 1 个 agent\_driven agent 可从 agent\_events 生成 L1 trace
* [ ]  **3.4.5 L2**：至少 1 个协议型 agent 可从 comm\_proxy 生成 L2 trace（并补齐 step\_idx/ref\_step\_idx 规则）
* [ ]  core runnable 子集 conformance + ≥N hard‑oracle benign 可跑，并按 L0/L1/L2 分桶展示
* [ ]  **工程与 registry 均不产出 L3**

### 3.6.3 Phase 3c（持续扩展）

* [ ]  ingestion 插件化：androidworld\_jsonl + ≥1 个 agent-specific 插件（如 droidrun\_events\_v1）
* [ ]  ref\_check\_applicable 降级合同测试通过
* [ ]  报告分桶 + unavailable reasons 输出稳定

---

## 3.7 最小修改点总结（从 3.4.3 起）

1. `mas_harness/evidence.py`

* 新增 `device_input_trace.jsonl` writer（L0/L1/L2 共用），并显式支持 `ref_step_idx`

2. `mas_harness/android/executor.py`

* L0 执行动作后写 device\_input\_trace（包含 ref\_step\_idx）

3. `mas_harness/evidence_pack.py`

* L0（后续扩展到 L1/L2）required 规则

4. `mas_harness.tools.audit_bundle`

* 增 device\_input\_trace 一致性审计（schema + level 对齐 + index/coord contract）

5. `mas_harness/action_evidence/*`（新增，用于 L1/L2）

* `base.py`, `l1_agent_events.py`, `l2_comm_proxy.py`

6. `mas_harness/ingestion/*`（插件化）

* `base.py / registry.py / formats/*`

---

## 3.8 落地顺序（建议，拆成更细小步；每步都有独立验收）

1. 先把 **索引契约** 写死（不依赖真机）

```bash
pytest -q mas-harness/tests/test_device_input_trace_index_contract.py
```

2. 再把 **坐标严格度矩阵** 写死（不依赖真机）

```bash
pytest -q mas-harness/tests/test_device_input_trace_coord_strictness_matrix.py
```

3. （可选）落盘 schema 并跑 schema 单测

```bash
pytest -q mas-harness/tests/test_device_input_trace_schema_unit.py
```

4. writer API 对齐：引入 `ref_step_idx` 并写入顶层字段

```bash
pytest -q mas-harness/tests/test_device_input_trace_writer_unit.py
```

5. writer 强校验：索引单调唯一 + L0 ref 强约束 + 坐标 strict/tolerant

```bash
pytest -q mas-harness/tests/test_device_input_trace_writer_unit.py
```

6. executor 集成（mock adb）：每个 action 写一行（含 ref\_step\_idx）

```bash
pytest -q mas-harness/tests/test_device_input_trace_l0_executor_unit.py
```

7. L0 对齐测试：agent\_action\_trace ↔ device\_input\_trace（ref\_step\_idx join）

```bash
pytest -q mas-harness/tests/test_device_input_trace_l0_alignment.py
```

8. evidence\_pack：L0 时 device\_input\_trace required

```bash
pytest -q mas-harness/tests/test_evidence_pack_requires_device_input_trace_l0.py
```

9. 真机/模拟器 smoke：确认落盘 + 行数合理

```bash
python -m mas_harness.cli.agentctl fixed \
  --agent_id toy_agent \
  --env_profile android_world_compat \
  --device_serial emulator-5554 \
  --output runs/phase3_smoke_toy_l0
python -m mas_harness.tools.audit_bundle runs/phase3_smoke_toy_l0
```

10. 统一 schema/validator（L0/L1/L2）

```bash
pytest -q mas-harness/tests/test_device_input_trace_schema_validation.py
```

11. evidence\_pack 规则升级（L0/L1/L2 必须有 device\_input\_trace；none 可缺）

```bash
pytest -q mas-harness/tests/test_evidence_pack_device_input_trace_requirements.py
```

12. audit\_bundle 一致性自检堵错

```bash
pytest -q mas-harness/tests/test_device_input_trace_audit.py
```

13. run\_manifest finalize 两阶段（含 strict 可选）

```bash
pytest -q mas-harness/tests/test_run_manifest_finalize_action_evidence.py
```

14. L1：parser/collector/mapping/ingest plumbing 逐步打通

```bash
pytest -q mas-harness/tests/test_action_evidence_l1_parser_unit.py
pytest -q mas-harness/tests/test_action_evidence_l1_collector_file.py
pytest -q mas-harness/tests/test_action_evidence_l1_mapping.py
pytest -q mas-harness/tests/test_run_agent_l1_ingest_plumbing.py
```

15. L2：parser/recorder/mapping（含 step\_idx/ref\_step\_idx 规则）/plumbing 逐步打通

```bash
pytest -q mas-harness/tests/test_action_evidence_l2_parser_unit.py
pytest -q mas-harness/tests/test_action_evidence_l2_http_recorder.py
pytest -q mas-harness/tests/test_action_evidence_l2_mapping.py
pytest -q mas-harness/tests/test_run_agent_l2_proxy_plumbing.py
```

16. adapter\_manifest schema + registry 一致性 + materialize pipeline

```bash
pytest -q mas-harness/tests/test_adapter_manifest_schema_action_evidence.py
pytest -q mas-harness/tests/test_registry_manifest_consistency.py
pytest -q mas-harness/tests/test_action_evidence_materialize_pipeline.py
```

17. core/extended 分层 + conformance filter + 报告分桶无 L3

```bash
pytest -q mas-harness/tests/unit/integration/test_validate_core_agents.py
pytest -q mas-harness/tests/test_conformance_filter_by_tier.py
pytest -q mas-harness/tests/test_reporting_bucketing_action_levels_no_l3.py
pytest -q mas-harness/tests/test_reporting_no_l3_assumption.py
```
