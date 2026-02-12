# agentbeats/

AgentBeats Phase 1 的工程落点（不大改现有 `mas-*` 结构）。

- `green/`：green agent（已到 Step 7：results.json 指标与评分；兼容 Step 3/5 的最小任务契约模式；并可通过 MAS harness 产出 results.json + evidence pack）
- `purple/`：baseline purple agent（已到 Step 5：droidrun A2A wrapper + 远程 ADB）
- `scenarios/`：本地/CI 用的 `scenario.toml`（Step 1 起就提供最小 smoke）

Step 2（宿主机 emulator + 远程 ADB server）相关：

- `emulator_host/`：宿主机启动 emulator / adb server / 等待 ready 的脚本
- `scenarios/phase1_remote_adb_smoke/compose.yaml`：green/purple 容器通过 `host.docker.internal:5037` 访问宿主机 adb server
- `tools/validate_step2_remote_adb.py`：连续重启两次 emulator 的验收脚本（容器需稳定可控）

Step 9（scenario.toml + 本地一键复现）相关：

- `scenarios/phase1_smoke/scenario.toml`：Phase 1 smoke 配置（green/purple 镜像 + MAS harness config）
- `scenarios/phase1_smoke/compose.yaml`：docker compose 端到端跑完并产出 `runs/*/results.json` + evidence pack
