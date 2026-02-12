下面是我在 Phase 3 计划里**出现过的所有“分类/分桶/枚举/分层”**（只要我做了“把东西分成几类”的都算），按层级归类列出来。

---

## A. Registry 层的分类

1. **availability（三态可得性）**

* `runnable | audit_only | unavailable`
* 用途：全榜单覆盖时的硬分桶（报告也按这个分桶）

2. **open\_status（开源/可获取状态）**

* `open | closed | unknown`
* 用途：记录条目开放性（偏 metadata，但也是分类）

3. **tier（代表性集合分层）**

* `core | extended`
* 用途：只对 core 做强约束/批跑入口（避免全量拖死）

4. **execution\_mode\_supported（条目支持的运行模式）**

* `["planner_only", "agent_driven"]`（列表型分类）
* 用途：声明某个 agent 适配器支持哪些运行路径

5. **action\_trace\_level（条目宣称/期望的动作证据级别）**

* `L0 | L1 | L2 | L3 | none`（但 Phase 3 约束“工程/registry 不产出 L3”）
* 用途：约束 core 最低证据强度、以及 run 时的期望值

6. **obs\_modalities\_required（所需观测模态）**

* 例：`[screenshot, ui_elements, uiautomator_xml]`（列表型分类）
* 用途：声明该 agent/评测需要哪些观测输入

7. **trajectory\_format（audit\_only 轨迹格式指针）**

* 例：`androidworld_jsonl | droidrun_events_v1 | ...`
* 用途：auto-dispatch 到对应 ingestion 插件（格式分类）

---

## B. Adapter Manifest 层的分类

8. **action\_evidence.level（适配器声明的动作证据能力）**

* `L0 | L1 | L2 | none`
* 用途：run\_agent 决定要不要启动 L1/L2 采证/映射

9. **action\_evidence.source（动作证据来源分类）**

* `mas_executor | agent_events | comm_proxy | none`

10. **event\_stream.format（L1 事件文件格式分类）**

* 例：`agent_events_v1`（你计划里用的名字）
* 用途：L1 parser/mapping 选择分支

11. **default\_coord\_space（默认坐标口径分类）**

* （字段本身是一个枚举值，具体可支持哪些取决于你定义）
* 用途：当 agent 没说清坐标口径时的默认归一化入口

---

## C. Run Manifest / Episode Summary 层的分类

12. **execution\_mode（一次 run 的执行模式）**

* `planner_only | agent_driven`

13. **env\_profile（运行环境画像分类）**

* `mas_core | android_world_compat`

14. **run\_purpose（这次 run 的用途分类）**

* `benchmark | conformance | agentctl_fixed | agentctl_nl | ingest_only`

15. **eval\_mode（评测模式分类）**

* `vanilla | guarded`

16. **guard\_enforced（布尔分桶）**

* `true | false`

17. **guard\_unenforced\_reason（guard 未生效原因分类）**

* `null | guard_disabled | not_planner_only | not_L0 | unknown`

18. **action\_trace\_level（本次 run 实际动作证据级别）**

* `L0 | L1 | L2 | L3 | none`（约束：Phase 3 不产 L3）

19. **action\_trace\_source（本次 run 实际动作证据来源）**

* `mas_executor | agent_events | comm_proxy | system_capture | none`

20. **evidence\_trust\_level（证据可信来源分类）**

* `tcb_captured | agent_reported | unknown`

21. **oracle\_source（成功判定来源分类）**

* `device_query | trajectory_declared | none`

22. **oracle\_decision（oracle 输出四态分类）**

* `pass | fail | inconclusive | not_applicable`

23. **agent\_reported\_finished（布尔分类）**

* `true | false`

24. **task\_success（三态分类，派生值）**

* `true | false | "unknown"`

25. **ref\_check\_applicable（ref 绑定校验是否适用）**

* `true | false`

26. **auditability\_limited（审计能力受限）**

* `true | false`

27. **auditability\_limits（受限原因分类列表）**

* 例：`["no_screenshot","no_ui_tree","no_geometry"]`（列表型分类）

---

## D. Evidence / Trace 文件层的分类

### D1. device\_input\_trace.jsonl 相关

28. **source\_level（每条输入事件的证据级别）**

* `"L0" | "L1" | "L2"`（你计划里 device\_input\_trace 不写 L3）

29. **event\_type（输入事件类型枚举）**

* 例：`tap | swipe | type | back | home | open_app | open_url | wait | finished | ...`

30. **mapping\_warnings（映射告警分类列表）**

* 例：`["coord_unresolved", ...]`（你明确写死了至少一个：`coord_unresolved`）

31. **坐标严格度矩阵（按 level 分层的规则分类）**

* `L0 strict` vs `L1/L2 tolerant`（这是规则分层，也算分类）

32. **coord\_space（坐标口径分类）**

* 在 device\_input\_trace 里：最终必须 `"physical_px"`（固定一类）
* 在 normalized\_action / transform 里：还出现了输入口径分类（见下方坐标部分）

33. **step\_idx / ref\_step\_idx 的关联形态分类**

* L0：1:1（并且 ref\_step\_idx==step\_idx）
* L1/L2：允许 1:N，ref\_step\_idx 允许 null

### D2. comm\_proxy\_trace.jsonl 相关

34. **direction（通信方向/消息类型分类）**

* `request | response | message`

35. **non-action vs action 消息分类**

* action：进入 device\_input\_trace（L2）
* non-action：不进入 device\_input\_trace，但必须计数/记录（skipped\_non\_action\_count 等）

---

## E. 坐标/几何链路里的分类

36. **screen\_trace 四字段中的 orientation 分类**

* `portrait | landscape`（或你允许 0/90/180/270，但计划里是 portrait/landscape 这种分法）

37. **coord\_transform.from（输入坐标口径分类）**

* 计划里列过：
  * `screenshot_px`
  * `logical_px`
  * `normalized_physical | normalized_screenshot | normalized_logical`
  * `unknown`

38. **coord\_transform.to（输出口径分类）**

* `physical_px`（canonical）

39. **identity vs transform 两类换算路径**

* 输入已是 `physical_px` → **identity**（不换算、不写 transform）
* 其他口径 → **transform**（写参数 + warnings）

---

## F. 工具/运行参数层的分类

40. **comm\_proxy\_mode（是否开启 L2 recorder）**

* `off | record`

41. **strict\_action\_evidence（严格模式开关）**

* 有 / 无（布尔开关）
* 用途：期望 L1/L2 但最终没产出时，是否直接判失败

42. **dry\_run\_ingest\_events（运行路径分类：真跑 vs 仅映射）**

* 有该 flag：不连真机，只跑 mapping 管线
* 无该 flag：按正常 run 跑

---

## G. 报告/统计分桶层的分类

43. **按 availability 分桶**

* runnable / audit\_only / unavailable

44. **按 guard\_enforced 分桶（且 gated：只有 L0 + planner\_only 才可能 true）**

* guarded 生效桶 vs 不生效桶

45. **按 action\_trace\_level 分桶**

* L0 / L1 / L2 / none（计划里明确“不出现 L3”；若出现当 unknown/unsupported 另统计）

46. **按 action\_trace\_source 分桶**

* mas\_executor / agent\_events / comm\_proxy / none

47. **core vs extended 分桶**

* 用于 conformance 批跑与 regression 展示

---

如果你愿意，我还可以把这些“分类项”整理成一个**可以直接放进 schema 的枚举清单**（比如集中列出所有 enum 值），这样你写 JSONSchema/validator 会更顺手。
