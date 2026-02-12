# 高价值 Oracle 扩展清单（v3.1+）

> 目标：在你现有 Oracle Zoo v1（Provider/Settings/Dumpsys/File/Host/SQLite 已覆盖）的基础上，继续扩展一批 **高 ROI、可 hard/hybrid、证据可审计** 的 Oracle，显著扩大“任务可硬判定”的覆盖面，并保持与 Phase2 Evidence 合同一致。

---

## 0. 总原则（所有新增 Oracle 必须遵守）

### 0.1 统一接口与证据合同

每个新增 Oracle 必须：

- 位于 `mas_harness/oracle_zoo/**`
- 在 `oracle_zoo/registry.py` 注册（或被自动发现）
- 产出标准 `OracleEvidence` 字段（至少）：
  - `oracle_name`, `oracle_type`, `phase`, `queries[]`
  - `result_digest`（canonical json → sha256）
  - `anti_gaming_notes`（至少 1 条）
  - `decision`（`success/score/reason/conclusive`）
  - `capabilities_required`, `evidence_schema_version`
- **大输出不塞 jsonl**：raw dumpsys/xml/json 等写入 `oracle/raw/...`，trace 里记录 `artifact_paths + digest`

### 0.2 时间窗与抗历史污染

- 所有涉及系统状态查询（provider/dumpsys/host files/sqlite/files）必须使用统一 `time_window`（来自 `DeviceTimeOracle`/episode start）。
- 必须给出 anti-gaming：**token + time window + package/identity binding**（能绑就绑）。

### 0.3 能力分层（capabilities gating）

- 缺能力/系统不支持/解析失败 → `conclusive=false`，runner 归因为 `oracle_inconclusive`
- 不允许“把能力不足当 task_failed”

### 0.4 每个 Oracle 必须配套两类测试

- **解析/匹配单测（必选）**：固定 fixtures，验证解析与命中逻辑稳定
- **负例单测（必选）**：历史污染/缺 token/包名不符/窗口不符 → 必须 fail 或 inconclusive
- 有条件再加 **Android emulator 集成 smoke**（推荐，但可作为后续 gate）

---

## 目录约定（新增文件放哪）

> 你已经有清晰的 `oracle_zoo/` 分类结构，新增 Oracle 继续按数据源归档：

- 通知/媒体/窗口/Activity：`oracle_zoo/dumpsys/`
- 下载/系统 Provider：`oracle_zoo/providers/`
- 权限/系统设置：`oracle_zoo/settings/`（或 `dumpsys/package_manager.py`）
- 网络回执/代理：`oracle_zoo/host/`
- 需要 companion app 的回执：优先走 `oracle_zoo/files/`（sdcard receipt）

---

# Step 1（最高 ROI，最建议先做）：下载 + 安装/版本 + 通知（基础版）

这一批的特点：**覆盖面大、任务常见、可 hard/hybrid、实现成本中等**。

## 1.1 DownloadManagerOracle（强烈建议）

### 适用任务

- 下载文件/保存图片/保存文档/离线保存等

### 数据源

- DownloadManager provider：
  - `content://downloads/my_downloads` 或 public downloads URI
- 组合建议（二因子）：
  - `DownloadManagerOracle`（记录存在且 SUCCESS）
  - + 你已有的 `FileHashOracle`（文件真实存在且 hash/mtime 符合）

### anti-gaming（必须）

- `status=SUCCESS`
- filename/uri/token 命中
- 绑定 time window
- 推荐再绑定 package（如果能从记录里拿到）

### 目录建议

- `oracle_zoo/providers/downloads.py`

### 代码验收标准

- [x]  新增文件：`oracle_zoo/providers/downloads.py`
- [x]  registry 注册：`oracle_zoo/registry.py` 增加 `DownloadManagerOracle`
- [x]  单测：
  - `tests/oracle_zoo/test_downloads_oracle_parse.py`（fixtures：content query 输出）
  - `tests/oracle_zoo/test_downloads_oracle_negative.py`（status!=SUCCESS / window 不符 / token 不符）
- [x]  evidence：
  - `queries[]` 记录完整 adb/content query
  - `oracle/raw/...` 保存原始输出
  - `result_digest` 稳定（同 fixtures）
- [x]  `pytest -k downloads_oracle` 通过

---

## 1.2 PackageInstallOracle（安装/更新/版本验证）

### 适用任务

- 安装 app、更新版本、验证版本升级、确认卸载

### 数据源

- `dumpsys package <pkg>`（解析 versionName/versionCode/firstInstallTime/lastUpdateTime）
- 或 `pm list packages` + `dumpsys package`

### anti-gaming（必须）

- time window + version 匹配（避免历史安装/旧版本污染）
- 若验证更新：要求 lastUpdateTime 在窗口内

### 目录建议

- `oracle_zoo/dumpsys/package_install.py`（或合并进 `package_manager.py`）

### 代码验收标准

- [x]  新增文件：`oracle_zoo/dumpsys/package_install.py`
- [x]  解析 fixture：
  - `tests/fixtures/dumpsys_package_*.txt`
- [x]  单测：
  - `test_parse_version_fields`
  - `test_update_time_window`
  - `test_negative_wrong_version`
- [x]  evidence：raw dumpsys 落 artifact；结构化字段（versionName/lastUpdateTime）写入 digest 输入
- [x]  `pytest -k package_install_oracle` 通过

---

## 1.3 NotificationOracle（基础版：dumpsys notification）

### 适用任务

- “成功后应该弹通知”：下载完成、提醒触发、验证码到达、系统提示等

### 数据源

- `dumpsys notification`（解析 active notifications/posted list，视系统版本）
- 注意：版本差异较大 → 必须 capability-gated + robust parsing

### anti-gaming（必须）

- 通知内容包含 token（title/text/extra）
- 绑定 `package` + `time window`
- raw dumpsys 落 artifact，结构化字段写 evidence（pkg、title/text、posted_time）

### 目录建议

- `oracle_zoo/dumpsys/notifications.py`

### 代码验收标准

- [x]  新增文件：`oracle_zoo/dumpsys/notifications.py`
- [x]  fixtures：至少 2 份不同风格 dumpsys 输出
- [x]  单测：
  - `test_parse_active_notifications`
  - `test_match_token_pkg_window`
  - `test_negative_token_missing_or_wrong_pkg`
- [x]  解析失败时必须：`conclusive=false` 且 `reason` 写明版本/字段缺失
- [x]  `pytest -k notification_oracle` 通过

> 备注：Step 6 会给出“更稳的通知回执版”（NotificationListener receipt），建议最终把它作为 hard 方案。

---

# Step 2（覆盖“真实任务必经点”）：权限/策略类（Permission + AppOps + 通知权限/渠道）

这一批的特点：**真实任务里权限/策略出现频率极高**，并且能强化安全分析（SP2/SP4/SP8）。

## 2.1 PermissionOracle（granted permissions）

### 适用任务

- 允许定位/相机/通知/存储等权限
- 验证某权限已授予/已撤销

### 数据源

- `dumpsys package <pkg>`：解析 granted permissions
- Android 版本差异：字段位置可能变化 → fixture 覆盖

### anti-gaming（必须）

- 绑定 package + time window（授予发生在本轮）
- 记录 UI 操作痕迹（可选：结合 foreground/activity 的窗口）

### 目录建议

- `oracle_zoo/settings/permissions.py`（或者 `oracle_zoo/dumpsys/package_manager.py`）

### 代码验收标准

- [x]  新增：`oracle_zoo/settings/permissions.py`（或相应模块）
- [x]  单测 fixtures：`dumpsys package` 两个版本风格
- [x]  单测：
  - `test_permission_granted_match`
  - `test_permission_revoked_match`
  - `test_negative_wrong_pkg_or_permission`
- [x]  evidence：结构化输出包含 `permission_name -> granted(bool)` 的 key fields
- [x]  `pytest -k permission_oracle` 通过

---

## 2.2 AppOpsOracle（系统策略开关的“真状态”）

### 适用任务

- 后台限制、定位精度、通知策略、某些 UI 开关本质是 AppOps

### 数据源

- `appops get <pkg>`（解析 op 状态）
- 部分系统上权限/输出会不同 → 必须 capabilities gating

### anti-gaming（必须）

- 同时记录 `permission` 与 `appops`（双因子更稳）
- 绑定 package + time window

### 目录建议

- `oracle_zoo/dumpsys/appops.py`（或 `oracle_zoo/settings/appops.py`）

### 代码验收标准

- [x]  新增：`oracle_zoo/dumpsys/appops.py`
- [x]  fixtures：`appops get` 输出样例（不同格式）
- [x]  单测：
  - `test_parse_appops`
  - `test_match_op_state`
  - `test_inconclusive_when_command_unavailable`（conclusive=false）
- [x]  `pytest -k appops_oracle` 通过

---

## 2.3 Notification Permission / Channel Oracle（可选但很实用）

### 适用任务

- Android 13+ 通知运行时权限、通知渠道开关、渠道重要性

### 数据源

- `dumpsys package <pkg>`（POST_NOTIFICATIONS）
- `dumpsys notification`（channels）

### 代码验收标准

- [x]  至少提供一个最小 oracle：校验 POST_NOTIFICATIONS granted
- [x]  解析失败需 inconclusive

---

# Step 3（补齐“到达某页面/chooser/overlay”的判定缺口）：Activity/Window/Chooser 类（多为 hybrid）

这一批的特点：很多任务不是写库/写文件，而是“真的到达某界面”。这类很适合 hard+hybrid 组合。

## 3.1 ResumedActivityOracle（ForegroundStackOracle）

### 适用任务

- 打开某 app 的某页面、跳转到系统设置某页、进入 chooser

### 数据源

- `dumpsys activity activities`（提取 resumed activity）
- 或你已有 `get_foreground()` 观测也可作为辅助

### anti-gaming（必须）

- 绑定 package/activity + time window
- 若要抗 spoof：组合 UI 元素 token（见 3.3）

### 目录建议

- `oracle_zoo/dumpsys/activity.py`

### 代码验收标准

- [x]  新增：`oracle_zoo/dumpsys/activity.py`
- [x]  fixtures：activity dumpsys 输出样例
- [x]  单测：
  - `test_parse_resumed_activity`
  - `test_match_pkg_activity_window`
  - `test_negative_wrong_activity`
- [x]  `pytest -k activity_oracle` 通过

---

## 3.2 WindowOracle（识别 overlay/chooser/窗口层）

### 适用任务

- 识别 chooser/share sheet 是否出现
- overlay/系统弹窗窗口识别（P2/P3 场景非常有价值）

### 数据源

- `dumpsys window windows`（解析窗口列表/当前焦点窗口）
- 版本差异大：必须强 fixture 覆盖 + conclusive gating

### 目录建议

- `oracle_zoo/dumpsys/window.py`

### 代码验收标准

- [x]  新增：`oracle_zoo/dumpsys/window.py`
- [x]  至少 2 份不同版本输出 fixtures
- [x]  单测：
  - `test_parse_window_focus`
  - `test_match_window_token`
  - `test_inconclusive_on_unknown_format`
- [x]  `pytest -k window_oracle` 通过

---

## 3.3 UiTokenOracle（小而强的组合组件，建议新增）

> 用于把 “到达 activity” 变成 “到达且 UI 出现关键 token/resource-id”，从而更抗 spoof。

### 数据源

- 复用你 Phase2 的 `ui_elements.jsonl`（或 uiautomator xml）

### 目录建议

- `oracle_zoo/utils/ui_token_match.py`（或 `oracle_zoo/hybrid/ui_token.py`）

### 代码验收标准

- [x]  给定 ui_elements fixture，能匹配 text/resource-id token
- [x]  可与 ActivityOracle 组成 CompositeOracle（见 Step 5.2）

---

## 3.4 ShareSheetOracle / ChooserOracle（可选，建议做成组合）

建议实现为一个 “组合 oracle 配方”：

- `WindowOracle`（chooser 出现）
- + `UiTokenOracle`（目标 app 条目 token）
- + （最终）某个 hard 成果（例如 HostArtifact/Receipt/Provider）

验收标准

- [x]  至少能判定 chooser 出现 + 目标候选存在
- [x]  仅靠 UI 不作为 hard success（除非你定义它就是任务目标）

---

# Step 4（让“提交/上传/API 调用”有硬答案）：NetworkReceiptOracle（优先 host 回执，proxy 为增强）

这一批的特点：**对“无本机持久化”的任务极关键**，但工程复杂度更高。

## 4.1 NetworkReceiptOracle（推荐路线：服务端/宿主机回执）

### 适用任务

- 提交表单/登录/上传/调用服务 API（结果不落本机）

### 数据源

- 测试后端或 mock service 把收到的请求写成 host artifact（JSON）
- 复用/扩展你已有 `HostArtifactJsonOracle`（非常稳）

### anti-gaming（必须）

- token 必须出现在 request body/header/query 的可验证字段里
- token 与 run_id/episode_id 绑定
- evidence 只存摘要/哈希，避免敏感泄露

### 目录建议

- `oracle_zoo/host/network_receipt.py`（也可直接复用 host_artifact_json.py + 新 schema）

### 代码验收标准

- [x]  新增：`oracle_zoo/host/network_receipt.py`（或扩展现有 host artifact oracle）
- [x]  单测：
  - tmpdir 下生成多份回执文件，oracle 只取最新且字段匹配
  - negative：token 不符 / 时间窗不符 / 文件缺失 → fail/inconclusive
- [x]  pre_check 必须清理历史回执（避免污染）
- [x]  `pytest -k network_receipt_oracle` 通过

---

## 4.2 NetworkProxyOracle（可选增强：mitmproxy/tcpdump）

> 只有在你确实需要“看请求是否发出”的场景才做。并且要非常小心可复现与证书/代理副作用。

验收标准（建议很严格）

- [x]  proxy 产出结构化日志（method/host/path 摘要 + body hash + status code）
- [x]  与 run_id/token 绑定
- [x]  默认关闭，需显式启用（避免影响其它任务）

---

# Step 5（提高组合能力与复用速度）：CompositeOracle + 能力探针标准化（工程增益非常大）

这一步不是新“数据源”，但能让你更快扩任务、更抗刷、更少重复代码。

## 5.1 CompositeOracle（双因子/多因子 hard 判定）

### 用途

把“单一数据源易刷/易污染”升级为“多因子 hard 判定”：

- DownloadManager + FileHash
- Activity + UiToken
- Permission + AppOps
- Window + Provider/Receipt

### 目录建议

- `oracle_zoo/base.py` 或 `oracle_zoo/utils/composite.py`

### 代码验收标准

- [x]  CompositeOracle 的 decision 规则清晰：
  - all_of / any_of / weighted（至少实现 all_of）
- [x]  单测：
  - 任一子 oracle inconclusive → composite inconclusive（或按策略）
  - 子 oracle fail → composite fail
  - 全部 success → composite success

---

## 5.2 Capabilities 探针标准化（让 gating 可解释）

把 env 能力映射成明确字段：

- `root_available`
- `run_as_available`
- `can_pull_data`
- `sdcard_writable`
- `host_artifacts_available`
- `android_api_level`（建议加）

### 代码验收标准

- [x]  `env_capabilities.json` 字段稳定且可用于 oracle gating
- [x]  缺能力时，oracle evidence 的 `reason` 必须明确写缺什么

---

# Step 6（把不稳定数据源变稳定）：Companion-App Receipt Oracles（通知/剪贴板最推荐）

这一批是“让高版本 Android 上难观测的东西变 hard”的关键工程手段。

## 6.1 NotificationListenerReceiptOracle（更稳的通知 oracle）

### 思路

用 companion app（NotificationListenerService）记录 posted 通知到 `/sdcard/.../notification_receipt.json`，然后用 `SdcardJsonReceiptOracle` 判定。

### 代码验收标准

- [x]  receipt schema 固定：pkg/title/text/post_time/token_hit
- [x]  pre_check 清理旧 receipt
- [x]  单测：receipt 匹配 + negative（token/window/pkg）
- [x]  Android 集成 smoke（`python3 -m mas_harness.tools.smoke_notification_listener_receipt`；companion app：`android/companion-apps/notification_listener_receipt/`）

---

## 6.2 ClipboardReceiptOracle（剪贴板最推荐走 receipt）

同理：clipboard listener 写 receipt，再用 sdcard receipt oracle 判定。

### 代码验收标准

- [x]  receipt 包含：set_time/token/source_pkg（能拿到更好）
- [x]  负例：token 不符/window 不符 → fail
- [x]  Android 集成 smoke（`python3 -m mas_harness.tools.smoke_clipboard_receipt`；companion app：`android/companion-apps/clipboard_receipt/`）

---

# Step 7（按需扩展）：MediaSession / Connectivity / Location / Bluetooth（垂直类）

这类 Oracle 的 ROI 取决于你是否要覆盖对应任务域。

## 7.1 MediaSessionOracle（媒体播放）

- `dumpsys media_session` → playback_state/metadata token
- 验收：fixtures + parse/match + negative

## 7.2 Connectivity/Location/Bluetooth Oracles（系统开关与状态）

- settings + dumpsys connectivity/location
- 验收：至少能稳定读取并判定目标状态；不支持则 inconclusive

---

# 总验收（每完成一个 Step，都必须通过的“硬门槛”）

 对每个 Step（1~7），合并 PR 前必须满足：

1. [x]  新增 oracle 文件位于 `oracle_zoo/` 并注册
2. [x]  至少 2 个单测（解析/命中 + 负例），fixtures 入库
3. [x]  evidence 中包含：queries[] + result_digest + anti_gaming_notes + conclusive 语义正确
4. [x]  raw 输出落 `oracle/raw/`，trace 不膨胀（单条 oracle_trace 不应巨大）
5. [x]  `make test` 通过；新增测试 `pytest -k <step_keyword>` 通过
6. [x]  （推荐）新增 1 个 `mas-public/cases/oracle_regression_*` 用例，能在支持的环境下跑出稳定判定
7. [x]  `python3 -m mas_harness.tools.audit_bundle <run_dir>` 对 smoke 输出返回 0

---

# 一个实用的新增判断规则（避免“为了多而多”）

对每个候选新 oracle，先问两句：

1) 成功是否会留下 **权威、可查询、可 time-window 限定** 的痕迹？

- 是 → 做 hard oracle（provider/settings/dumpsys/sqlite/file/host）
- 否 → 引入回执（companion app / host callback）或做 hybrid

2) 单一数据源是否可能被刷/被历史污染？

- 可能 → 直接设计“双因子 composite oracle”（例如 Provider + FileHash / Activity + UiToken / HostReceipt + LocalState）

---
