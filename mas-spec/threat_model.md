# Threat model

本章给出 MAS‑Spec 的威胁模型：

- 采用 **对抗者能力分级（T0–T2）** 描述 adversarial threats
- 采用 **hazards（U0/A0）** 描述非对抗风险（可靠性、人因）
- 明确 **TCB（Trusted Computing Base）** 与 out-of-scope，避免“你凭什么不考虑 X”的审稿争议

---

## 1. 对抗者能力分级（Adversarial tiers）

### T0 内容对手（Content adversary）

**能力**：只能控制不可信内容源（通过 B2 进入 `O_t`），但不能插入系统级 UI 或修改 OS。

典型通道：

- 邮件正文、网页内容、聊天内容
- 工具输出（tool output）
- 图片/截图中的文字（visual text）
- App 内文本（评论、客服对话、富文本等）

**典型目标**：间接 prompt injection、工具输出注入、视觉指令注入等。

### T1 UI 干扰对手（UI interference adversary）

**能力**：可触发或插入动态 UI 干扰（仍无 root），导致来源/身份判断困难。

典型通道：

- 通知（notification）
- 覆盖层/浮层（overlay）
- 伪系统弹窗、广告插屏

**典型目标**：点击劫持、钓鱼 UI、诱导授权/安装/外链点击。

### T2 生态对手（Ecosystem adversary）

**能力**：利用移动生态与跨 app 机制造成身份/解析混淆（仍无 root）。

典型通道：

- 同名同图标假 App（launcher/app list 混淆）
- intent/chooser 竞争（多个 app 可处理同一 intent）
- share sheet 默认勾选/多目标污染
- deep link/intent 跳转链劫持
- 剪贴板链路（clipboard）

**典型目标**：跨域/跨 app 数据外泄、误打开恶意 app、误分享给错误对象。

### （可选）T3 供应链/后门（Out of scope for main evaluation）

**例**：root、替换系统组件、模型权重/依赖投毒、解析器被篡改等。

MAS‑Spec 主评测默认不覆盖 T3，因为该类威胁会要求不同的信任假设与实验伦理。

---

## 2. 非对抗风险（Hazards）

为了避免把“可靠性问题”硬归类为“攻击”，MAS‑Spec 将 hazards 与 adversary tiers 区分。

### U0：用户侧风险（User hazards）

- 指令歧义（同名联系人/同域相似链接/多 app 选择）
- 误确认/确认疲劳
- 社会工程导致错误确认

### A0：agent 侧风险（Agent hazards）

- 误绑定（recipient/package/origin/amount）
- 错误工具选择/参数构造
- 错误归因（把内容当指令）
- 记忆写入不受控、跨任务污染
- 循环重试与预算耗尽

### G0–G3：生成侧风险（Builder hazards，v0.3.2）

生成侧不是被测对象，但它会直接影响“可判定性、可复现性与结论严谨性”，因此 v0.3.2 把以下风险显式化：

- **G0 flake 放大**：生成/构建/注入不稳定导致结果漂移
- **G1 历史污染**：未 reset、receipt 未清理、时间窗不严导致误判
- **G2 资产漂移**：同 seed 生成不同图/不同 APK（破坏可复现）
- **G3 Notebook 污染**：写入未经证据支撑的“经验”，导致探索退化/误导

---

## 3. TCB（Trusted Computing Base）假设

为了让“6 primitives 覆盖大部分攻击”的论证成立，必须明确哪些组件被假设为可信。

**MAS‑Spec v0.3.2 的 TCB 至少包含：**

1) **Harness**：确认/澄清 UI 渲染（B1），以及（可选）policy/executor
2) **Evidence Collector**：采证与落盘（含 `device_query` 原始回执与 digest）
3) **Oracle Framework runtime**：Detectors + Assertions + 结构化解析（Evidence→Facts→Assertions）
4) **Instrumentation**：前台包名/Activity、剪贴板/通知等轨迹采集（在 testbed 内）

**默认假设：**

- 对手不能直接篡改 TCB（无 root、不替换系统组件）
- 对手只能通过 T0–T2 指定通道影响 `O_t`、动作选择/执行语义、或跨域数据流

**生成侧补充（v0.3.2）**

- CaseBundle/AssetManifest 的 schema 校验与门禁属于 TCB
- `asset_trace/codex_trace/work reports` 属于 TCB 采证
- 生成器输出不得直接成为评分结论；评分必须基于运行时 evidence→facts→assertions（Hard Facts 为主干）

---

## 4. 资产、边界与威胁对应关系（便于论文写法）

| 边界 | 主要风险 | 典型 tiers/hazards |
|---|---|---|
| B1 User↔Agent | 误导确认、歧义消解失败 | U0, A0（以及“内容污染确认话术”的间接风险） |
| B2 Env→Agent | 内容注入、来源混淆 | T0, T1, T2 |
| B3 Agent→Env | 越权动作、跨域外发 | T1, T2, A0 |
| B4 Internal | 记忆/状态污染、持久化越权 | T0/T2（取决于写入通道）, A0 |

---

## 5. 伦理与双用途约束（Threat model 的实践边界）

为降低双用途风险，MAS‑Spec 的攻击注入与环境操控 **MUST** 满足：

- 仅在 emulator/testbed/mock apps 内运行
- 不提供可直接用于真实设备/真实 app 的通用投放脚本
- 公开 payload 降级，隐藏 seed 不公开

详细条款见 `docs/governance/ETHICS_AND_DUAL_USE.md`。
