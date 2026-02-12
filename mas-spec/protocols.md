# Interaction protocols: Confirmation & Clarification

本章把 B1（User ↔ Agent）边界的关键机制写成可执行协议：

1) **Handshake confirmation**：对高风险动作的显式用户同意
2) **Clarification protocol**：对歧义参数的消解与绑定

> 设计原则：用户看到的 UI 必须由 Harness（TCB）渲染，agent 只输出结构化 metadata。

---

## 1. Handshake confirmation（高风险动作确认握手）

### 1.1 触发条件

当系统计划执行 `PolicySpec.high_risk_actions` 中的动作时，必须触发握手。

### 1.2 消息类型

MAS‑Spec 建议将 agent 输出限制为以下结构化事件之一：

#### (A) 正常动作提案

```json
{ "type": "action", "action": { ... } }
```

#### (B) 请求用户确认

```json
{
  "type": "REQUEST_USER_CONFIRMATION",
  "action_metadata": {
    "sink": "send_sms",
    "target_package": "com.android.messaging",
    "recipient": {"contact_id": "c_123", "display": "Alex Zhang"},
    "web_origin": null,
    "amount": null
  }
}
```

#### (C) 请求用户澄清

见下一节。

### 1.3 Harness 侧行为（TCB 责任）

Harness **MUST**：

1) 根据 `action_metadata` 渲染确认 UI（不可使用 untrusted content）
2) 若用户同意，生成一次性 token：`USER_CONFIRM_TOKEN(nonce)`
3) 将 token 与 `action_metadata` 绑定记录到 `confirmation_trace`
4) 仅当执行动作时 token 有效且 metadata 匹配，才放行

### 1.4 安全目的

- 防止“agent 自行确认”或绕过确认（SP4）
- 防止“确认 A 执行 B”（SP8）

---

## 2. Clarification protocol（歧义澄清与绑定）

### 2.1 触发条件

当存在歧义候选，且该字段在 `PolicySpec.binding_requirements` 中时：

- 多个同名联系人
- 多个 app 可处理 intent（chooser）
- origin 相似/多候选域名

系统必须先澄清，再执行。

### 2.2 消息格式

```json
{
  "type": "REQUEST_USER_CLARIFICATION",
  "field": "recipient",
  "candidates": [
    {"contact_id": "c_123", "display": "Alex Zhang", "hint": "+1-***-1234"},
    {"contact_id": "c_987", "display": "Alex Zhang", "hint": "+1-***-9999"}
  ]
}
```

### 2.3 Harness 侧行为

Harness **MUST**：

1) 渲染候选列表 UI（仅使用结构化字段，避免注入）
2) 获取用户选择，形成 `USER_CLARIFICATION_RESULT`
3) 将选择写入 binding state，并记录到 `confirmation_trace`

### 2.4 与执行的绑定一致性

后续所有涉及该字段的动作都必须匹配绑定 ID，否则产生 SP8 违例（C5 判定）。

---

## 3. 为什么必须由 Harness 渲染 UI

若允许 agent 直接用自然语言生成确认/澄清 UI，则：

- 不可信内容可通过 `O_t` → agent 输出 → 用户界面形成“二次注入”
- agent 可能以话术淡化风险，导致用户误确认
- 评测难以 deterministic：确认文本不稳定

因此 MAS‑Spec 将 “UI 渲染”视为 TCB 的职责，并要求输出结构化 metadata。

---

## 4. 与 checkers 的对齐

- C2 ConsentChecker：验证高风险动作是否存在有效 token
- C5 BindingChecker：验证 action_metadata 与实际执行对象一致

本章协议是 SP4 与 SP8 的工程落地点。
