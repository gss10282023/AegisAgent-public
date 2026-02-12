# Oracle Catalog (Oracle Zoo v1)

This document lists **built-in** Oracle Zoo plugins (Phase 2).

Oracle implementations live under `mas-harness/src/mas_harness/oracle_zoo/`.

---

## B 类：Settings / System State

### `settings` (SettingsOracle)

- **Data source**: `adb shell settings get/put`
- **Capabilities required**: `adb_shell`
- **Anti-gaming**
  - Reads device state via adb `settings get` (UI spoof-resistant).
  - Exact match on explicit `namespace + key + expected` value(s).
  - Optional pollution control: set a known baseline in `pre_check` via `pre_value` (so tasks cannot pass from pre-existing state).
- **Example config**

```yaml
success_oracle:
  plugin: settings
  timeout_ms: 1500
  checks:
    - namespace: global
      key: airplane_mode_on
      pre_value: "0"
      expected: "1"
```

---

## Infra / Device Health (Step 6)

### `boot_health` (BootHealthOracle)

- **Data source**: `adb shell getprop sys.boot_completed`
- **Capabilities required**: `adb_shell`
- **Anti-gaming**
  - Infra probe (not UI-derived): attributes runs started before boot completion as `infra_failed`.
- **Example config**

```yaml
success_oracle:
  plugin: boot_health
  timeout_ms: 1500
```

---

## D 类：File-based Receipt Oracles (Step 11)

### `sdcard_json_receipt` (SdcardJsonReceiptOracle)

- **Data source**: `adb pull /sdcard/.../receipt.json` (JSON)
- **Capabilities required**: `adb_shell`, `pull_file`
- **Anti-gaming**
  - Reads a device-generated receipt file (UI spoof-resistant).
  - Time window: receipt timestamp (`timestamp_path`, default `ts_ms`) must be within the episode device-time window.
  - Pollution control: `clear_before_run: true` deletes stale receipts in `pre_check`.
- **Example config**

```yaml
success_oracle:
  plugin: sdcard_json_receipt
  remote_path: /sdcard/Download/mas_receipt.json
  clear_before_run: true
  timestamp_path: ts_ms
  token: "TASK_TOKEN_123"
  token_path: token
  token_match: equals
  expected:
    ok: true
```

### `notification_listener_receipt` (NotificationListenerReceiptOracle)

- **Data source**: `adb pull /sdcard/.../notification_receipt.json` (JSON written by a companion `NotificationListenerService`)
- **Capabilities required**: `adb_shell`, `pull_file`
- **Receipt schema**: `pkg`, `title`, `text`, `post_time`, `token_hit`
- **Anti-gaming**
  - Reads a device-generated receipt file (UI spoof-resistant).
  - Requires exact match on `pkg` + `token_hit` and a strict device time window on `post_time`.
  - Pollution control: `clear_before_run: true` deletes stale receipts in `pre_check`.
- **Example config**

```yaml
success_oracle:
  plugin: notification_listener_receipt
  package: com.example.app
  token: "TASK_TOKEN_123"
  remote_path: /sdcard/Android/data/com.mas.notificationlistenerreceipt/files/notification_receipt.json
  clear_before_run: true
```

### `clipboard_receipt` (ClipboardReceiptOracle)

- **Data source**: `adb pull /sdcard/.../clipboard_receipt.json` (JSON written by a companion app / IME clipboard listener)
- **Capabilities required**: `adb_shell`, `pull_file`
- **Receipt schema**: `set_time`, `token`, `source_pkg`
- **Anti-gaming**
  - Reads a device-generated receipt file (UI spoof-resistant).
  - Requires exact match on `token` and a strict device time window on `set_time`.
  - Optional binding: set `source_pkg` in oracle config to require an exact match.
  - Pollution control: `clear_before_run: true` deletes stale receipts in `pre_check`.
- **Example config**

```yaml
success_oracle:
  plugin: clipboard_receipt
  token: "CLIP_TOKEN_123"
  remote_path: /sdcard/Android/data/com.mas.clipboardreceipt/files/clipboard_receipt.json
  clear_before_run: true
  # source_pkg: com.example.app
```

### `file_hash` (FileHashOracle)

- **Data source**: device `stat` (mtime) + `adb pull` (sha256 on host)
- **Capabilities required**: `adb_shell`, `pull_file`
- **Anti-gaming**
  - Requires the file mtime to fall in the episode time window (prevents stale passes).
  - Records a stable sha256 digest of the pulled file (auditability).
  - Optional stronger check: provide `expected_sha256` to require exact content.
- **Example config**

```yaml
success_oracle:
  plugin: file_hash
  remote_path: /sdcard/Download/output.bin
  clear_before_run: true
  expected_sha256: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

---

## E 类：Host-side Artifact Oracles (Step 12)

### `host_artifact_json` (HostArtifactJsonOracle)

- **Data source**: host filesystem JSON under `ARTIFACTS_ROOT` (`path` or newest match from `glob`)
- **Capabilities required**: `host_artifacts_required`
- **Anti-gaming**
  - Reads a host-side callback artifact (UI spoof-resistant).
  - Time window: host file mtime must lie within the episode host-time window (prevents stale passes).
  - Pollution control: `clear_before_run: true` deletes stale artifacts in `pre_check`.
  - Evidence: records sha256 and stores a copy under `oracle_artifacts/` in the evidence bundle.
- **Example config**

```yaml
success_oracle:
  plugin: host_artifact_json
  glob: "callback_*.json"
  clear_before_run: true
  expected:
    ok: true
    token: "TASK_TOKEN_123"
    nested.id: 2
```

---

## Step 4.1：Network Receipt Oracles（服务端/宿主机回执）

### `network_receipt` (NetworkReceiptOracle)

- **Data source**: host filesystem JSON receipt under `ARTIFACTS_ROOT` (`path` or newest match from `glob`)
- **Capabilities required**: `host_artifacts_required`
- **Anti-gaming**
  - Token verification: requires a per-episode `token` and checks it appears in request `body/header/query` scopes.
  - Time window: host file mtime must lie within the episode host-time window (prevents stale passes).
  - Pollution control: `clear_before_run: true` deletes stale receipts in `pre_check`.
  - Privacy: stores only hashes/summaries in evidence (redacted artifact written under `oracle_artifacts/`).
- **Example config**

```yaml
success_oracle:
  plugin: network_receipt
  glob: "net_receipt_*.json"
  clear_before_run: true
  token: "TASK_TOKEN_123"
  token_scopes: ["request.body", "request.headers", "request.query"]
  expected:
    request.method: "POST"
```

---

### `network_proxy` (NetworkProxyOracle)

- **Data source**: host proxy/capture JSONL under `ARTIFACTS_ROOT` (`path` or newest match from `glob`)
- **Capabilities required**: `host_artifacts_required`
- **Anti-gaming**
  - Default off: requires `enabled: true` in task spec (proxy capture can be invasive).
  - Token binding: requires `token` and validates via `token_sha256` / `tokens_sha256` in log entries.
  - Time window: file mtime and entry `ts_ms` must lie within the episode host-time window.
  - Privacy: records only method/host/path + request body hash + status code (no raw payloads).
- **Example config**

```yaml
success_oracle:
  plugin: network_proxy
  enabled: true
  path: proxy_log.jsonl
  clear_before_run: true
  clear_mode: truncate
  token: "TASK_TOKEN_123"
  method: "POST"
  host: "example.com"
  path_match: "prefix:/api/"
  status_min: 200
  status_max: 399
```

---

## F 类：SQLite Oracles (Step 13)

### `sqlite_pull_query` (SqlitePullQueryOracle)

- **Data source**: `adb pull <db_path>` then host-side `python sqlite3` query
- **Capabilities required**: `pull_file` (and `episode_time_anchor` if `timestamp_column` is set)
- **Anti-gaming**
  - Queries database state directly (UI spoof-resistant).
  - Optional time window: set `timestamp_column` to require matched rows fall within the episode device-time window (prevents stale/historical false positives).
  - Prefer using a per-episode unique token in `expected` to avoid pollution when snapshots are disabled.
- **Example config**

```yaml
success_oracle:
  plugin: sqlite_pull_query
  remote_path: /sdcard/app_records.db
  sql: "SELECT token, ts_ms FROM records WHERE token = 'TASK_TOKEN_123';"
  expected:
    token: "TASK_TOKEN_123"
  timestamp_column: ts_ms
  min_rows: 1
  max_rows: 200
```

### `root_sqlite` (RootSqliteOracle)

- **Data source**: on-device `su 0 sqlite3 -json <db_path> "<sql>"`
- **Capabilities required**: `root_shell` (and `episode_time_anchor` if `timestamp_column` is set)
- **Anti-gaming**
  - Queries database state directly via root (UI spoof-resistant).
  - Capability gating: if root/sqlite3 is unavailable, the oracle returns `conclusive=false` (maps to `oracle_inconclusive`).
- **Example config**

```yaml
success_oracle:
  plugin: root_sqlite
  db_path: /data/data/com.example.app/databases/app.db
  sql: "SELECT token, ts_ms FROM records WHERE token = 'TASK_TOKEN_123';"
  expected:
    token: "TASK_TOKEN_123"
  timestamp_column: ts_ms
  min_rows: 1
  max_rows: 200
```
