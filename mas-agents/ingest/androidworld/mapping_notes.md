# mapping_notes（androidworld_jsonl）

> Phase3 3c‑1f：每个 audit_only ingestion 插件都必须提供轨迹字段 → MAS evidence bundle 的映射说明。
>
> 该文件落盘位置遵循 `docs/plans/Phase3详细方案.md §3.1.1/§3.1.2`：`mas-agents/ingest/<plugin>/mapping_notes.md`。

## 1) 覆盖的轨迹格式（format_id）

- `androidworld_jsonl`
- 对应 mapping_notes 文件：`mas-agents/ingest/androidworld/mapping_notes.md`

## 2) 当前落地实现（先写死，但行为必须可追溯）

目前仓库内可运行/可复现的 audit_only ingestion（用于 conformance 与本地验收）实现位于：

- `mas-harness/src/mas_harness/cli/run_agent.py`：`_ingest_trajectory(...)`

该实现把输入 JSONL 轨迹“最小化地”映射为 Evidence Pack v0 结构（`episode_0000/evidence/*`），用于：

- 产出结构完整的 evidence bundle（即使 observation 不完整也能落盘）
- 触发/验证 Phase3 的 downgrade 规则（`ref_check_applicable=false` 等）

## 3) 输入格式（JSONL，每行一个 step）

### 3.1 每行 JSON 对象（dict）

必需：无（允许字段缺失；会使用默认值补齐，见 §5）。

推荐字段：

- `task_id` 或 `case_id`：字符串，用于确定 case_id
- `step`（或 `step_idx`）：整数，步骤序号
- `observation`：对象（dict）
- `action`：对象（dict）

最小示例（仓库 fixture 同款）：

```jsonl
{"task_id":"aw_traj_sample_001","step":0,"observation":{"ui_text":"home"},"action":{"type":"wait","note":"sample trajectory step 0"}}
{"task_id":"aw_traj_sample_001","step":1,"observation":{"ui_text":"settings"},"action":{"type":"finished","note":"sample trajectory end"}}
```

### 3.2 observation（允许为空）

当前实现读取/使用的 observation 子字段：

- `ui_text`：用于生成占位 a11y_tree 与 ui_hash
- `foreground_package`：前台包名（缺省为 `"unknown"`）
- `foreground_activity`：前台 Activity（缺省为 `null`）
- `screen_info`：屏幕信息（缺省为固定值，见 §5.2）
- `a11y_tree`：无障碍树（缺省会用 `ui_text` 生成一个最小树）
- `ui_hash`：UI 哈希（缺省会用 `ui_text` 计算）

### 3.3 action（允许为空）

- 任意 JSON 对象；会写入 evidence 的 `action_trace.jsonl` 与 `agent_action_trace.jsonl`
- 仅当最后一步 `action.type in {"finished","stop"}` 时，summary 的 `task_success_details.success=true`（`run_purpose="ingest_only"` 时 `oracle_decision="not_applicable"`，`task_success="unknown"`）

## 4) 输出（Evidence Pack v0 结构）

输出根目录由 CLI `--output <dir>` 指定；audit_only ingestion 固定写入：

- `<output>/episode_0000/evidence/`（Evidence Pack v0 目录）
- `<output>/episode_0000/summary.json`（episode summary）
- `<output>/run_manifest.json`、`<output>/env_capabilities.json`（run 级文件）

其中每个 step 至少会写入以下 trace：

- `obs_trace.jsonl`：observation 事件（含 screenshot/ui_dump 指针 + digest）
- `screen_trace.jsonl`：screen/geometry 信息（来自 `screen_info` 或默认值）
- `foreground_trace.jsonl`：前台 app 信息（来自 `foreground_package/activity` 或默认值）
- `agent_call_trace.jsonl`：合成的“agent_call”占位事件（用于结构完整性）
- `agent_action_trace.jsonl`：原始 action + normalized_action（含 auditability/ref 相关字段）
- `action_trace.jsonl`：action + 固定 result（`{"ok": true, "source": "trajectory"}`）

## 5) 字段映射（输入 → evidence）

### 5.1 case_id 与 step 序号

- `task_id`（优先）或 `case_id` → EvidenceWriter 的 `case_id`
- `step`（优先）或 `step_idx` → 各 trace 的 `step/step_idx`
- 若 `step/step_idx` 均缺失 → 使用该行在文件中的行号（从 0 开始）

### 5.2 observation → EvidenceWriter.record_observation(...)

输入 `observation` 经过最小补齐后写入：

- `foreground_package` → `observation_payload.foreground_package`（缺省 `"unknown"`）
- `foreground_activity` → `observation_payload.foreground_activity`（缺省 `null`）
- `screen_info` → `observation_payload.screen_info`
  - 缺省固定为：`{"width_px":1080,"height_px":1920,"density_dpi":440,"surface_orientation":0}`
- `a11y_tree` → `observation_payload.a11y_tree`
  - 缺省为一个最小树（root + label，label.text = ui_text）
- `ui_hash` → `observation_payload.ui_hash`
  - 缺省为 `sha256(ui_text)`

注意：当前实现不读取任何“真实截图字节”，因此不会提供 `screenshot_png`。

### 5.3 action → record_agent_action/record_action

- `action`（dict）原样写入 `agent_action_trace.jsonl` 的 `raw_action/action` 字段
- 同时经过 normalizer 生成 `normalized_action`（包含 `step_idx`、`auditability_limited` 等）
- `action_trace.jsonl` 的 `result` 固定为：`{"ok": true, "source": "trajectory"}`

## 6) audit_only 降级规则（ref_check_applicable / auditability）

由于当前实现不提供截图字节：

- `auditability_limited=true`
- `auditability_limits` 至少包含：`["no_screenshot"]`
- `obs_digest=null`，从而：
  - `ref_obs_digest=null`
  - `ref_check_applicable=false`

这用于满足 Phase3 3c‑1e 的框架级合同：当 observation 无法支撑 ref 绑定校验时，相关 checker 必须降级输出 `not_applicable`，避免误判。

## 7) 已知限制 / 后续扩展点

- 该 mapping 是“最小可验收”的 audit_only ingestion：目标是保证 evidence bundle 结构完整 + downgrade 行为明确。
- 若后续接入真实 AndroidWorld 轨迹（截图、真实 UI tree、坐标体系等），需要：
  - 更新 ingestion 实现（提供 `screenshot_png` / 更完整的 `screen_info` / UI 树）
  - 同步更新本 mapping_notes，确保字段映射可追溯且版本化
