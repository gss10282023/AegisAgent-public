# Lifecycle: Audit‑First → Discover → Enforce（v0.3.2）

本章把 MAS‑Spec 的基准构建生命周期写成可审计流程。

> **默认主线采用 Audit‑First。**  
> Discover/生成闭环是一等公民，但其产物不得污染 SUT 主评分口径（评分只来自运行时 facts/assertions）。

---

## Stage A：Audit‑First（默认主线）

- 跑 benign/attack pairs（Paired Protocol）
- 产运行时 Evidence Pack（Hard Facts 主干）
- detectors → facts
- assertions（success + safety）→ PASS/FAIL/INCONCLUSIVE
- 报告分桶：trust buckets + applicability/inconclusive

---

## Stage B：Discover（生成闭环，规范化）

目标：以“触发 safety assertion FAIL 且 benign 不崩”为导向，自动生成/变异 case，并最小化后沉淀到 public/hidden 生成器。

v0.3.2 写死三道门禁：

### B‑3.1 SpecGate（跑设备前）

1) CaseBundle schema 校验
2) Paired 协议校验（reset→benign→reset→attack）
3) Policy→BaselineSafetyAssertions 编译后不可为空
4) `capabilities_required` 与 env/agent 匹配
5) 可判定性预测（关键 assertions 预计 INCONCLUSIVE 高则改方案）

### B‑3.2 AssetGate（生成资产后）

- 文件存在/大小/格式/路径策略
- sha256 与 WorkReport 一致
- APK 元信息（包名/版本/签名等，如适用）
- 元数据完整（asset_id/inputs_digest/codex refs）

### B‑3.3 PatchGate（仅 apply_patch）

- lint + unit tests + regression 子集
- anti‑gaming checklist（至少一条）
- diff 报告 + evidence_refs
- 两轨制：experimental/core（影响主评分口径的变更必须更严 + 人审）

---

## Stage C：Enforce（可选增强）

- 仅对 enforceable 子集承诺 uplift（典型：`planner_only + L0`）
- 其余 agent 继续 audit‑only，不混算 uplift 主结论

