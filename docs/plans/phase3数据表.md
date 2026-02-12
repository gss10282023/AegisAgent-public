下面先把你最困惑的三件事讲清楚（L0/L1/L2、ingestion 插件化、audit\_only），然后给你一张“术语表/概念表”（把中英混用的名词统一起来，按你这份 Phase3 计划里出现的语境解释）。

---

## L0 / L1 / L2 到底在分什么

它们分的不是“能力强弱”，而是 **动作证据链（action evidence）的来源与可审计强度**：
你到底能用多强的证据证明“设备上发生了哪些输入动作”。

### L0（最强）——MAS executor 真正在设备上执行动作并记账

* 典型场景：`planner_only`
* 证据来源：你的 harness / executor（TCB 内）
* 你能证明：**你系统确实对设备执行了 tap/swipe/type…**（至少你发出并执行了输入指令）
* `device_input_trace.jsonl` 的来源：executor 执行时写入（最可控、最可审计）
* 计划里：3.4.3（L0 最低交付）

### L1（中等）——agent 自己“执行/回放”的事件流（宏录制/事件流）被你接入

* 典型场景：`agent_driven`
* 证据来源：**agent 导出的事件流/宏录制**（可能是实时 event stream，也可能是 run 后 dump 的 jsonl）
* 你能证明：**agent 报告/导出的输入事件序列是什么**（更像“它说它点了哪里/滑了哪里”）
* 关键点：它更贴近“输入事件”本身，但**通常仍不等价于你在设备侧确认“真的执行成功”**（除非事件流来自你可信的外部采集探针）
* `device_input_trace.jsonl` 的来源：你把 agent\_events 映射成统一的 device\_input\_trace（`source_level=L1`）
* 计划里：3.4.4

### L2（更弱）——通信层记录：证明“动作命令被下发/请求被发送”

* 典型场景：`agent_driven`，而且动作是通过 RPC/HTTP/WebSocket 下发给执行端
* 证据来源：**comm proxy/recorder**（你在 agent 与执行端之间做旁路记录）
* 你能证明：**某个动作命令被发出**（例如 POST /act {type:"tap", x,y…}），以及返回码/错误
* 关键点：它证明的是“下发了命令”，不保证设备真的执行成功；粒度也可能更像“指令”而不是“最终输入事件”
* `device_input_trace.jsonl` 的来源：你把 comm\_proxy\_trace 映射成统一的 device\_input\_trace（`source_level=L2`）
* 计划里：3.4.5

一句话记忆：

* **L0 = 我执行的（executor 内）**
* **L1 = 它做的事件（agent\_events）**
* **L2 = 它发的命令（comm\_proxy）**

---

## audit\_only 是什么

**audit\_only 是 registry 里的一种可用性状态（availability）**：
你拿不到可运行工件（代码/容器/权重/API），所以没法用 runner 真跑；但你拿得到对方的轨迹/日志/事件记录，于是你做的是：

* **ingest（导入/转换）**：把对方给你的轨迹或日志，转换成你系统统一的 evidence bundle 格式
* **audit（审计）**：跑你的 deterministic checkers（安全、合规、行为一致性等），但要避免被误当成“和 runnable 同强度”的结论

因此你计划里才需要：

* `evidence_trust_level="agent_reported"`（通常）
* `oracle_source="trajectory_declared" | "none"`（多数情况下不是 device\_query）
* `ref_check_applicable=false`（当没有可验证 observation 时）

---

## ingestion 插件化 是什么

它是把“各种各样的轨迹/日志格式”接入你系统的方式变成 **可插拔** 的：

* 你不再把 ingestion 写死成 “只能 ingest AndroidWorld JSONL” 或 “只能 ingest 某个 agent 的日志”
* 而是定义一个统一接口 `TrajectoryIngestor`，每种格式写一个插件（format plugin）：
  * `androidworld_jsonl`
  * `droidrun_events_v1`
  * `your_agent_log_v1`
  * …

然后由一个 registry 来选择用哪个插件：

* 显式 `--format xxx`
* 或者 `--agent_id` 查 registry 自动选择
* 或者 `probe()` 自动探测

**这对 audit\_only 特别重要**：audit\_only 的输入几乎永远是“别人给你的文件”，而不是你能控制的在线 run。

---

# Phase 3 概念表（术语表）

> 下面这张表把你计划里出现的中英混用名词统一解释：
> **“中文建议叫法 / 英文或代码名 / 一句话定义 / 你会在哪看到它（字段/文件/命令/章节）”**


| 分类          | 中文建议叫法             | 英文/代码名（计划中出现）             | 一句话定义（你需要记住的点）                                              | 在哪里出现（字段/文件/命令/章节）                                                      |
| ------------- | ------------------------ | ------------------------------------- | ------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| 可用性状态    | 可运行                   | runnable                              | 有可运行工件，runner 能真跑，能产证据                                     | `agent_registry.yaml: availability=runnable`；`run_agent`真跑路径                      |
| 可用性状态    | 仅审计导入               | audit\_only                           | 不能真跑，但能拿到轨迹/日志文件；通过 ingestion 转 evidence 后跑 checkers | `agent_registry.yaml: availability=audit_only`；`ingest_trajectory`；3.5.x             |
| 可用性状态    | 不可得                   | unavailable                           | 既不能真跑，也没有足够轨迹；必须记录原因与证据                            | `agent_registry.yaml: availability=unavailable`+`unavailable_reason`；报告输出 reasons |
| 运行模式      | 规划器输出动作、由你执行 | planner\_only                         | agent 只“计划动作”，真正点击/输入由 MAS executor 执行                   | `run_manifest.execution_mode=planner_only`；L0 主要在此出现                            |
| 运行模式      | agent 自己执行动作       | agent\_driven                         | agent 自己驱动设备；你只能旁路采证（L1/L2）                               | `run_manifest.execution_mode=agent_driven`；3.4.4/3.4.5                                |
| 动作证据等级  | L0（最强）               | action\_trace\_level="L0"             | 你的 executor 执行动作并记录 device\_input\_trace（TCB 内）               | `run_manifest.action_trace_level`；`device_input_trace.jsonl: source_level=L0`；3.4.3  |
| 动作证据等级  | L1（事件流）             | "L1" + source="agent\_events"         | agent 导出的事件流/宏录制被你映射成 device\_input\_trace                  | `adapter_manifest.action_evidence.source=agent_events`；3.4.4                          |
| 动作证据等级  | L2（通信记录）           | "L2" + source="comm\_proxy"           | 通过通信层 proxy 记录“命令被下发”，再映射成 device\_input\_trace        | `comm_proxy_trace.jsonl`；3.4.5                                                        |
| 动作证据等级  | L3（legacy，不做）       | "L3"                                  | 系统级抓取（getevent/root），本 Phase3 不产出，仅保留枚举兼容             | 文档枚举；计划已写“不做 L3”                                                          |
| 动作证据来源  | 执行器来源               | action\_trace\_source="mas\_executor" | L0 的来源：你的 executor                                                  | `run_manifest.action_trace_source`（finalize）                                         |
| 动作证据来源  | 事件流来源               | "agent\_events"                       | L1 的来源：agent events stream / dump                                     | `run_manifest.action_trace_source`；`adapter_manifest.action_evidence`                 |
| 动作证据来源  | 通信来源                 | "comm\_proxy"                         | L2 的来源：comm proxy                                                     | `run_manifest.action_trace_source`；`--comm_proxy_mode`                                |
| 关键文件      | 设备输入轨迹             | device\_input\_trace.jsonl            | **统一动作证据文件名**；每行一个“设备输入事件”                          | 3.4.3/3.4.4/3.4.5；evidence 目录                                                       |
| 关键文件      | 动作决策轨迹             | agent\_action\_trace.jsonl            | 记录 agent 的 raw\_action/normalized\_action 及规范化警告（更像“决策”） | ingestion 输出要求里提到；也常在 runner 输出                                           |
| 关键文件      | 通信原始记录             | comm\_proxy\_trace.jsonl              | L2 的 raw 记录（请求/响应/message）                                       | 3.4.5；`HttpJsonActionRecorder`                                                        |
| 关键文件      | 事件原始备份             | agent\_event\_trace.jsonl             | 可选：L1 的 raw 备份（不强制）                                            | 3.4.4（可选 writer）                                                                   |
| 关键字段      | 事件序号                 | step\_idx                             | 在 device\_input\_trace 中表示**event 序号**；必须单调递增且唯一          | `device_input_trace.jsonl.step_idx`；3.4.3.0a                                          |
| 关键字段      | 关联动作步               | ref\_step\_idx                        | 把某条输入证据关联回 action step（允许 1:N 或 null）                      | `device_input_trace.jsonl.ref_step_idx`；L0 要求与 step\_idx 相等                      |
| 关键字段      | 证据级别                 | source\_level                         | 每行 device\_input\_trace 的证据级别（L0/L1/L2）                          | `device_input_trace.jsonl.source_level`                                                |
| 关键字段      | 坐标口径                 | coord\_space                          | 坐标在哪个空间定义；你的 canonical 要求是 physical\_px                    | `payload.coord_space`；Coord 补丁；3.4.3.0b                                            |
| 坐标概念      | 物理像素坐标             | physical\_px                          | **最终执行口径**；device\_input\_trace 要求落到 physical\_px              | `payload.coord_space="physical_px"`；executor 只执行它                                 |
| 关键字段      | 映射警告                 | mapping\_warnings                     | 不允许 silent drop：缺字段/无法换算/不支持类型都要写 warning              | `device_input_trace.jsonl.mapping_warnings`；L1/L2 tolerant 时关键                     |
| 观测概念      | 观测轨迹                 | obs\_trace.jsonl                      | 记录每步 observation（截图、UI 等）+ obs\_digest 等                       | 3.4.2；EvidenceWriter.record\_observation                                              |
| 观测概念      | 观测摘要哈希             | obs\_digest                           | 给 observation 做可复现摘要，用于 ref\_obs\_digest 绑定护栏               | `obs_trace.jsonl.obs_digest`；3.4.2                                                    |
| 观测概念      | 动作引用观测             | ref\_obs\_digest                      | 动作声明“它是基于哪个 obs 做的”；executor 执行前校验                    | `normalized_action.ref_obs_digest`；护栏 B                                             |
| 可用性/审计   | 可引用检查是否适用       | ref\_check\_applicable                | 没有可验证 observation 时必须 false，相关 checker 输出 not\_applicable    | ingestion 规则（E）；summary 字段                                                      |
| 可用性/审计   | 审计受限标记             | auditability\_limited                 | 告诉报告端：这条 run 证据不足，不能当同强度结论                           | ingestion 规则（E）；summary 字段                                                      |
| 评测语义      | 评测模式                 | eval\_mode                            | vanilla/guarded（guarded 只有 L0+planner\_only 才可能 enforced）          | `run_manifest.eval_mode`；3.0.7(A)                                                     |
| 评测语义      | guard 是否真正生效       | guard\_enforced                       | 只有满足护栏 A 才能 true；否则必须 false 并写 reason                      | `run_manifest.guard_enforced`                                                          |
| 评测语义      | guard 未生效原因         | guard\_unenforced\_reason             | guard\_disabled / not\_planner\_only / not\_L0 / unknown                  | `run_manifest.guard_unenforced_reason`                                                 |
| 评测语义      | 成功判定来源             | oracle\_source                        | device\_query / trajectory\_declared / none                               | summary/run\_manifest；audit\_only 常不是 device\_query                                |
| 评测语义      | oracle 结论              | oracle\_decision                      | pass/fail/inconclusive/not\_applicable（再派生 task\_success）            | `episode_*/summary.json`                                                               |
| 评测语义      | 任务成功派生值           | task\_success                         | true/false/unknown（严格由 oracle\_decision 派生）                        | summary 字段；计划里强调不能硬塞                                                       |
| 证据可信度    | 证据可信等级             | evidence\_trust\_level                | tcb\_captured / agent\_reported / unknown                                 | audit\_only 常为 agent\_reported                                                       |
| 工程结构      | 轨迹导入                 | ingestion                             | 把外部轨迹/日志转换成 evidence bundle 的过程                              | 3.5.1（plugin 化）                                                                     |
| 工程结构      | 插件化导入               | ingestion pluginization               | 用插件接口支持多种输入格式（format\_id）                                  | `TrajectoryIngestor`+ registry；3.5.1.1/3.5.1.2                                        |
| 工程结构      | 导入插件                 | format plugin / ingestor              | 每个格式一个实现：probe+ingest                                            | `mas_harness/ingestion/formats/*.py`（建议）                                           |
| 核心组件      | 环境适配层               | EnvAdapter / AndroidEnvAdapter        | reset/observe/execute 的统一封装                                          | 3.4.1；`mas_harness/runtime/android/env_adapter.py`                                    |
| 核心组件      | 执行器                   | Executor                              | 把 normalized\_action 变成真实设备输入，并写 L0 device\_input\_trace      | 3.4.1 + 3.4.3                                                                          |
| 核心组件      | 证据写入器               | EvidenceWriter                        | 统一落盘 obs\_trace、device\_input\_trace 等                              | `mas_harness/evidence/evidence.py`；3.4.3.2                                            |
| 工具          | 证据包规则               | evidence\_pack                        | 定义哪些文件 required/optional（L0 时 device\_input\_trace 必须有）       | 3.4.3.5 + 3.4.6                                                                        |
| 工具          | 证据审计工具             | audit\_bundle                         | run 后自检：manifest↔evidence 一致性、schema、缺文件等                   | 3.4.6；`python3 -m mas_harness.tools.audit_bundle <run_dir>`                           |
| CLI           | 统一入口                 | run\_agent                            | runnable 真跑 / audit\_only ingest / unavailable 解释失败                 | 3.3.5；`mas_harness/cli/run_agent.py`                                                  |
| CLI           | 接入验证命令             | agentctl                              | 用 fixed/nl 快速验收 agent 是否“跑得起来”                               | 3.3.7；`mas_harness/cli/agentctl.py`                                                   |
| 测试集合      | 接入用例集               | conformance suite                     | 不追求成功率，只追求能跑、能产证据                                        | 3.3.4；`mas_harness/integration/conformance/suite.py`                                  |
| registry 分层 | 核心集                   | tier=core                             | 核心 runnable 子集：必须有 L0/L1/L2 之一并能跑 conformance                | 3.4.8；`python -m mas_harness.cli.validate_registry --core_only --registry ...`        |

---

## 你接下来最容易用的“判断题”

你可以用这几个问题快速判断自己现在属于哪条路径：

1. **我能不能把 agent 跑起来？**

* 能 → runnable
* 不能，但有轨迹/日志 → audit\_only
* 都没有 → unavailable

2. **动作到底是谁在设备上执行？**

* 你 executor 执行 → L0
* agent 自己执行，并且给你事件流 → L1
* agent 自己执行，只能在通信层看到命令 → L2

3. **我现在要做的是“在线跑”还是“离线导入”？**

* 在线跑（连接设备） → Phase 3b（3.4.x）
* 离线导入（拿文件） → Phase 3c（3.5.x），也就是 ingestion
