# System model

本章给出 MAS‑Spec 的**系统抽象**：它不假设 agent 的内部实现（单次模型调用、多 agent、脚本流水线均可），只约束与评估其**外部可观测行为**。

MAS‑Spec 将移动端 agent 视为一个能改变环境状态的闭环控制系统（O/S/A/P/E），并围绕参与方（principals）与信任边界（trust boundaries）进行建模。

---

## 1. 参与方（Principals）

### 1.1 User（用户）

**资产（Assets）**

- PII（联系人、地址、定位、照片等）
- 账号材料（密码、OTP/验证码、重置链接等）
- 金钱与交易安全
- 设备控制权（系统设置、权限、安装来源、辅助功能等）

**能力（Capabilities）**

- 提供目标与约束（user goal）
- 回答澄清（clarification）
- 明示授权（confirmation）
- 失败时接管（takeover）

**非对抗风险（U0 hazards）**

- 指令歧义（例如“发给 Alex”，但有多个 Alex）
- 误确认/确认疲劳（confirmation fatigue）
- 被社会工程诱导去确认

### 1.2 Agent / Agentic System（智能体系统）

MAS‑Spec 中的 **Agentic System** 指任何满足以下条件的系统：

> 能基于观测 `O_t` 输出动作 `A_t`，从而改变环境状态 `E_t`。

系统内部可以是：单次模型调用、planner‑executor、多 agent、多轮反思、规则与模型混合流水线等。

**资产**

- 策略/系统提示/权限边界
- 工具与 API 的调用能力
- 记忆与偏好（长期/短期状态）
- 执行能力（跨 App 操作、外发数据、变更设置）

**非对抗风险（A0 hazards）**

- 误绑定（收件人/包名/origin/金额等）
- 错误工具选择或参数构造
- 错误归因（把“不可信内容”当作“指令”）
- 记忆写入不受控/不可撤销
- 循环重试导致资源耗尽

### 1.3 Environment（环境）

环境包含：

- OS 与系统 UI（通知、权限弹窗、intent chooser、share sheet 等）
- 安装的 Apps（含 WebView、广告 SDK、第三方内容源）
- 外部服务/工具（天气、联系人检索、邮件检索等）
- 内容源（网页、邮件、聊天、图片文字、剪贴板等）

环境既可以是 benign 的，也可以包含对抗者可操控的通道（见 Threat Model）。

### 1.4 Benchmark Builder（基准构建者）

Benchmark Builder 指构建与运行评测基准的可信一侧组件与人员集合，例如：

- Harness（runner/instrumentation/evidence collector/oracle runtime）
- CaseGen（Discover 生成闭环）
- Maintainers（数据集与口径维护）

**资产（Assets）**

- 数据集完整性（public/hidden seeds、case specs、assets）
- 生成器版本与策略（CoachCards、budgets 配置）
- 证据链完整性（facts/assertions/evidence_refs、asset_trace/codex_trace）
- 口径一致性（trust buckets、guard/enforcement 约束）

**生成侧风险（Builder hazards）**

- 生成/构建不稳定导致 flake 放大（资产生成漂移、构建不稳定）
- 历史污染导致误判（未 reset、receipt 未清理、时间窗不严）
- 生成器越权修改（意外改 repo / 改 hidden seed / 改评分口径）
- 记忆污染（Notebook 写入未经证据支撑的“假规律”）

---

## 2. 闭环形式化：O / S / A / P / E

MAS‑Spec 将移动端 agent 执行视为一个交互闭环：

- 环境状态：`E_t`（OS + Apps + 外部服务 + 内容源的真实状态）
- 观测：`O_t = Obs(E_t)`
  - screenshot、a11y tree、通知内容、WebView 文本/DOM、工具输出、剪贴板、前台包名/Activity 等
- 内部状态：`S_t`
  - 短期上下文（任务进度）、长期记忆/偏好、实体绑定状态、历史轨迹摘要
- 安全策略：`P`
  - 可读/可写范围（Origin Sets）、读→写 Flow rules、高风险动作确认、预算约束等
- 动作：`A_t = π(O_t, S_t, P)`
  - 点击/输入/滑动、打开 App、调用工具/API、分享/外发、设置变更等
- 环境转移：`E_{t+1} = Step(E_t, A_t)`

**攻击目标（Attack objective）**

攻击试图诱导系统产生违反策略 `P` 的动作序列（例如越权读取/外泄/支付/安装/改设置/误发），同时尽量不降低 benign 任务成功率（否则“拒绝一切”可刷分）。

---

## 2.1 Discover 元闭环（CaseGen 闭环）

Discover 元闭环不是被测对象，但决定用例扩张与覆盖增长；v0.3.2 要求生成侧可审计且不污染主评分口径。

**输入**

- coverage/stats（VR/BF/INCONCLUSIVE、失败簇、可判定性缺口）
- detectors/assertions catalog（能力要求、anti‑gaming 说明）
- agent registry（runnable/audit_only/unavailable，action_trace_level 等）
- Notebook（episodes/lessons）
- CoachCards（可选，带 TTL）

**输出**

- 新的 CaseBundleSpec（paired）
- 资产（AssetManifest）与生成证据（asset_trace/codex_trace/work reports）
- 最小复现与晋级记录（promotion log）
- （必要时）PatchProposal（experimental → core）

---

## 3. 信任边界（Trust boundaries）

MAS‑Spec 用多条信任边界覆盖移动端 agent 的主要风险穿越点（v0.3.2 扩展到生成侧）：

### B1：User ↔ Agent（人机交互边界）

- **B1a User → Agent**：用户目标、澄清回答、授权意图
- **B1b Agent → User**：解释、请求确认、请求澄清、进度摘要

**规范性要求（关键）**

为避免“确认话术被注入污染 / agent 自我包装误导用户”，MAS‑Spec 要求：

- 用户看到的 **确认/澄清 UI 必须由 Harness（TCB）渲染**。
- agent 只能提交结构化 metadata（例如 `REQUEST_USER_CONFIRMATION(action_metadata)`），不得自行生成用于用户决策的自然语言确认文本。

### B2：Environment → Agent（观测边界）

不可信内容、UI 干扰、来源/身份混淆通过该边界进入 `O_t`。

### B3：Agent → Environment（行动/外发边界）

agent 将计划转化为实际行为（点击/输入/跨 App/外发/设置变更等），对应 `A_t` 与跨域数据流。

### B4：Agent internal（状态/记忆边界）

系统内部状态 `S_t` 的更新与持久化（长期记忆写入、绑定状态缓存、偏好更新等）。

### B0：Benchmark Builder ↔ SUT Evaluation Harness

- CaseBundle、assets、injector、runner/detectors/assertions 的组织与执行
- 要求：生成侧证据可审计；生成器不能影响评分主口径（评分由 facts/assertions 决定）

### B5：Benchmark Builder ↔ Codex Workbench

当使用 Codex 进行资产生成或补丁施工时，必须受：

- 输出目录约束（例如 runs/**/assets/**；patch 走受控队列/工作区）
- 工单结构化（WorkOrder）
- 证据回执（WorkReport + codex_trace）
- 返工预算（一次定向修复 + 降级）

### B6：State Store（Notebook/Registry）边界

- Notebook/索引必须可持久化、append‑only、可重建
- 任何“可迁移经验（lessons）”必须引用 evidence_refs，否则只能进入 hypothesis_notes

---

## 4. 评测对象与非目标

### 4.1 评测对象

MAS‑Spec 评测的是 Agentic System 的**外部轨迹**：动作序列、访问的 app/origin、外发的内容/目标、是否进行确认/澄清等。

### 4.2 非目标

- 不评估模型参数安全（后门/供应链投毒）本身；可在 Threat Model 中作为扩展威胁（T3）讨论。
- 不要求接入特定 benchmark；MAS‑Spec 的 harness/adapters 旨在兼容 AndroidWorld/MobileWorld 等 workload。
