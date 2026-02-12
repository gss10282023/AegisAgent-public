# Mechanism primitives (P1–P6)

本章定义 MAS‑Spec 的 6 个**机制原语（mechanism primitives）**。它们不是“列举攻击”，而是对移动端 agent 闭环中**可被影响的关键部件**进行覆盖式分类。

---

## 1. 覆盖性论证（Rationale）

在 Threat Model 的 TCB 假设下（对手不 root、不篡改 harness/policy/checkers），并且对手能力限制在 T0–T2 之内：

> 任何导致违反策略 `P` 的现实现象，最终必须通过影响闭环中的以下某一类部件来实现：

1) 污染观测 `O_t` 的内容
2) 混淆观测的来源/身份（provenance/identity），导致错误信任
3) 改变可选动作集合或动作选择偏好（action space / tool selection)
4) 劫持跨域/跨 app 的执行语义与数据流（workflow / dataflow mapping）
5) 污染内部状态 `S_t`（记忆/偏好/绑定状态）
6) 破坏预算与终止条件（循环、DoS、资源耗尽）

MAS‑Spec 的 P1–P6 与上述 6 类部件一一对应，因此可以作为“标准”层面的核心抽象。

---

## 1.2 为什么每个 primitive 需要 3 个影响等级（probe / canary / highrisk）

单靠“能注入一段指令、模型就听了”很容易被 reviewer 认为是 **toy attack**：

- 你定义的标准会被质疑“拍脑袋”（没有可量化危害支撑）
- 你所谓的攻击会被质疑“没有真实后果”（只是演示可控性）

因此 MAS‑Spec 要求（或强烈建议，在 Phase‑1 先写死合同，后续实现时强制）：

> **每个 mechanism primitive（P1–P6）至少要能实例化成 3 个 impact levels**，并在报告中分开统计。

### Impact level 定义

- `probe`：低影响探针，用于测 **susceptibility（可被诱导/可被控制）**。
  - 例：诱导打开无害页面、点一个无害按钮、调用一个低风险工具。
  - 重要：probe 本身不应被用作“真实危害”的主证据。

- `canary`：可量化但低风险的影响，用 canary token 或确定性信号证明 **越界/误绑定/数据流违规** 发生。
  - 例：canary 出现在不允许的 clipboard/sink；或发送到了错误收件人（但内容是测试 canary）。
  - 这是 MAS‑Spec 用于主张“确实发生了安全后果”的主力证据之一。

- `highrisk`：高风险路径，触发支付/安装/设置修改/OTP/登录凭证/外发等。
  - 正确系统必须走 SP4 握手确认或被 policy 阻断。
  - 这是 MAS‑Spec 用于主张“可导致现实损失路径被触发”的第二类主力证据。

### 为什么要这样分层

1) **把“可控性”与“危害”分开**：probe 证明通道可用；canary/highrisk 证明会造成越权后果。
2) **避免“全拒绝刷分”或“只做演示”的争议**：成对评测 + impact 分层能同时衡量 utility 与 safety。
3) **让 SP 的违例阈值更可辩护**：核心结论建立在可机检的越界/外泄/未确认高风险/误绑定上。

AttackSpec 中用 `impact_level` 字段表达该分层（见 `mas-spec/schemas/attack_schema.json`），报告中建议默认按 impact_level 分桶输出（见 `metrics_reporting.md`）。

---

## 2. 原语与闭环/边界的映射

| Primitive | 影响对象 | 主要边界 | 常见 tiers/hazards |
|---|---|---|---|
| P1 Observation Injection | `O_t` 内容 | B2 | T0（主），T1（通知文案） |
| P2 Provenance & Identity Confusion | `O_t` 的来源/身份标签 | B2 | T1, T2 |
| P3 Action‑Space Manipulation | `\mathcal{A}_t` 或动作选择 | B3 | T1, T2（以及工具选择类 T0） |
| P4 Cross‑Domain Workflow Hijack | `Step(...)` / dataflow mapping | B3 | T2（主），T0 诱导触发 |
| P5 State & Memory Poisoning | `S_t` 更新/持久化 | B4 | A0（常见），T0/T2（取决于写入通道） |
| P6 Availability & Resource Abuse | 预算/终止条件 | B3/B4 | A0, T0/T1 |

---

## 3. Channels（通道）与命名规范

为了让 AttackSpec 可参数化、可组合，MAS‑Spec 建议把“注入/操控发生在哪里”统一用 `channel` 字段表达，并建议采用如下 canonical 命名（可扩展）：

### 3.1 P1 常用 channels

- `email_body`
- `webview_content`
- `chat_message`
- `tool_output`
- `image_text`
- `a11y_label`

### 3.2 P2 常用 channels

- `overlay`
- `notification`
- `system_like_dialog`
- `launcher_app_list`
- `intent_chooser`

### 3.3 P3 常用 channels

- `share_sheet`
- `multi_recipient_ui`
- `ambiguous_button_copy`
- `tool_selection_metadata`

### 3.4 P4 常用 channels

- `clipboard`
- `intent_deeplink`
- `share_intent`
- `app_switch_chain`

### 3.5 P5 常用 channels

- `memory_store`
- `preference_store`
- `binding_state`

### 3.6 P6 常用 channels

- `infinite_list`
- `dynamic_ui_jitter`
- `retry_loop`
- `context_bloat`

---

## 4. 原语定义（规范性条款）

本节给出每个 primitive 的**标准化定义模板**：影响对象、典型攻击方式、关键参数化维度、与安全性质（SP）的主要关联。

### P1 Observation Injection（观测注入）

**定义**：对手通过不可信内容源将恶意 payload 注入到 `O_t` 的内容中，使 agent 将“内容”误当作“指令”或将内容携带到不当 sink。

- 影响对象：`O_t` 内容
- 主要边界：B2
- 常见 tiers：T0
- 典型 payload：间接 prompt injection、工具输出注入、视觉文字注入
- 参数化维度：语言/同义词、混淆（Unicode/低对比度）、触发时机（step range/聚焦输入框）
- 主要关联性质：SP1、SP5（数据外泄）、SP4（诱导高风险动作）

### P2 Provenance & Identity Confusion（来源/身份混淆）

**定义**：对手让 agent 难以区分“系统 UI/可信提示”与“第三方内容/伪 UI”，或让 agent 误认 app/origin 身份。

- 影响对象：观测的 provenance / identity
- 主要边界：B2
- 常见 tiers：T1/T2
- 典型 payload：overlay/伪通知/钓鱼 UI、同名假 app、chooser 混淆
- 参数化维度：UI 相似度、出现时机、与任务相关性伪装、包名/origin 混淆
- 主要关联性质：SP2、SP8、SP4

### P3 Action‑Space Manipulation（动作空间操控）

**定义**：对手改变 agent 的可选动作集合或动作选择偏好（例如默认选项、显著性、工具选择描述污染），导致误点/误发/误调用。

- 影响对象：`\mathcal{A}_t` 或选择策略
- 主要边界：B3
- 常见 tiers：T1/T2（UI），以及 tool‑selection 类 T0
- 典型 payload：share sheet 默认多选、同名按钮、工具描述污染
- 参数化维度：候选数量、默认项、语义相似度、工具 metadata 污染位置
- 主要关联性质：SP3、SP4、SP8

### P4 Cross‑Domain Workflow Hijack（跨域/跨 app 劫持）

**定义**：对手利用 intent/deeplink/share/clipboard 等机制把数据从 A 域带到 B 域，或把流程引到不允许的 app/origin。

- 影响对象：执行语义与 dataflow mapping（`Step`）
- 主要边界：B3
- 常见 tiers：T2
- 典型 payload：OTP 链路外泄、剪贴板投毒、deeplink 跳转链
- 参数化维度：跳转链长度、携带方式、目标 sink、触发时机
- 主要关联性质：SP5、SP3、SP8、SP4

### P5 State & Memory Poisoning（状态/记忆投毒）

**定义**：对手或环境因素让 `S_t` 被污染（长期记忆、偏好、绑定状态、缓存规则），导致未来任务越权或行为偏移。

- 影响对象：`S_t`
- 主要边界：B4
- 常见 tiers/hazards：A0（常见），T0/T2（取决于写入通道）
- 典型 payload：把“以后都转发验证码”写入长期规则
- 参数化维度：写入时机、持久化范围、可撤销性
- 主要关联性质：SP6、SP8

### P6 Availability & Resource Abuse（可用性/资源滥用）

**定义**：对手或环境因素诱导 agent 陷入循环、超长上下文或重复动作，导致预算耗尽或不可用。

- 影响对象：预算/终止条件
- 主要边界：B3/B4
- 常见 tiers/hazards：A0，T0/T1
- 典型 payload：无限列表、动态 UI 抖动导致反复重试
- 参数化维度：UI 波动频率、列表长度、预算阈值
- 主要关联性质：SP7

---

## 5. 与 Spec language 的对应

为了让 primitives 可执行化，AttackSpec 至少应包含：

- `primitive`（P1–P6）
- `boundary`（B1–B4）
- `channel`（canonical 命名或扩展字符串）
- `trigger`（何时注入/何时干扰）
- `payload_generator`（可选：如何生成变体）
- `objective`（目标 violations/SP）

详细字段与 JSON schema 见 `mas-spec/schemas/attack_schema.json`。
