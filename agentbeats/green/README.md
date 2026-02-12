# agentbeats/green/

Phase 1 的 **green agent（A2A Server）**。

当前对应 `伯克利phase1.md` 的 Step 6：green 接收 `assessment_request` 后，把 MAS harness 串进执行链路（`agent_id=a2a_remote` 调用 purple），产出：

- `runs/.../episode_*/evidence/` evidence pack
- `runs/.../results.json`（并作为 A2A artifact 返回）

兼容模式：

- 如果 `config` 里包含 `goal` 字段，则走 Step 3/5 的最小任务契约（green→purple 直连），便于继续复用 `validate_step3_contract.py` / `validate_step5_droidrun_benign.py`。

## 安装依赖

用仓库自带 `.venv`（推荐）：

```bash
uv pip install -r agentbeats/requirements.txt --python .venv/bin/python
```

## 启动服务

```bash
.venv/bin/python agentbeats/green/src/server.py --host 127.0.0.1 --port 9009
```

Agent card：

- `http://127.0.0.1:9009/.well-known/agent-card.json`

## Docker（Step 8）

构建（平台要求 `linux/amd64`）：

```bash
docker build --platform linux/amd64 -f agentbeats/green/Dockerfile -t mas-green:phase1 .
```

运行（AgentBeats 运行镜像时会传入 `--host/--port/--card-url`）：

```bash
docker run --rm -p 9009:9009 mas-green:phase1 --host 0.0.0.0 --port 9009
```

Agent card：

- `http://127.0.0.1:9009/.well-known/agent-card.json`

## Step 2（宿主机 emulator + 远程 ADB server）验收

对应 `伯克利phase1.md` 的 Step 2：提前验证 **emulator + adb server 在宿主机**，green/purple 容器通过网络访问宿主机 **adb server:5037**，并且在 emulator 重启后仍稳定可控。

### 1) 宿主机启动 emulator + adb server

在宿主机先跑（需本机已安装 Android SDK platform-tools + emulator，并准备好一个 AVD）：

```bash
bash agentbeats/emulator_host/start_adb_server.sh
bash agentbeats/emulator_host/start_emulator.sh
bash agentbeats/emulator_host/wait_until_ready.sh

# 或者：
# make -C agentbeats/emulator_host host-emulator-up
```

常用可选环境变量：

- `AVD_NAME`（你的 AVD 名称；默认 `mas_avd`）
- `EMULATOR_CONSOLE_PORT`（默认 `5554` → serial `emulator-5554`）
- `ANDROID_SERIAL`（默认 `emulator-5554`）

### 2) 一键验收（会连续重启 emulator 两次）

```bash
# 方式 A：用环境变量（推荐）
export AVD_NAME="<your_avd_name>"
python3 agentbeats/tools/validate_step2_remote_adb.py

# 方式 B：用参数
# python3 agentbeats/tools/validate_step2_remote_adb.py --avd-name "<your_avd_name>"
```

## Step 3 验收（最小链路）

1) 启动 purple（另一个终端）：

```bash
.venv/bin/python agentbeats/purple/droidrun_baseline/src/server.py --host 127.0.0.1 --port 9010
```

2) 启动 green（另一个终端）：

```bash
.venv/bin/python agentbeats/green/src/server.py --host 127.0.0.1 --port 9009
```

3) 运行 Step 3 验收脚本（会做 success + timeout 两个用例）：

```bash
.venv/bin/python agentbeats/tools/validate_step3_contract.py \
  --green-url http://127.0.0.1:9009 \
  --purple-url http://127.0.0.1:9010
```

验收点（对应 `伯克利phase1.md` Step 3）：

- green 能发 dummy task 给 purple（字段包含 `adb_server/android_serial/timeouts`）
- purple 返回结构化 `status/summary`
- timeout 用例被 green 标记为 `status=timeout`

说明：

- `validate_step3_contract.py` 的 success 用例会用 `variant={"mode":"dummy"}`，避免依赖 droidrun/LLM key。

## Step 5 验收（真实 benign case + droidrun）

对应 `伯克利phase1.md` 的 Step 5：green 触发一次 A2A 调用，purple 用 droidrun 在 **真实 emulator** 上执行 benign goal，并返回结构化结果（含 droidrun trace 位置）。

前置：

- 本机已安装 Android SDK platform-tools + emulator，并准备好一个可启动的 AVD（先过 Step 2）
- 设置 `OPENROUTER_API_KEY`（推荐）或 `OPENAI_API_KEY`（会产生模型调用费用）

一键验收（会自动启动宿主机 adb server + emulator + green/purple server）：

```bash
export AVD_NAME="<your_avd_name>"
.venv/bin/python agentbeats/tools/validate_step5_droidrun_benign.py
```

输出：

- `runs/step5_benign_*/step5_artifact.json`（包含 purple 的 `status/summary/artifacts`）
- `runs/step5_benign_*/purple_droidrun_traces/`（droidrun traces）

## Step 6 验收（MAS harness 端到端 + results.json + evidence pack）

对应 `伯克利phase1.md` 的 Step 6：

- 先做 Step 2 的 ADB 健康检查（失败则 fail fast）
- 选 case（seed/num_tasks/variants/case_set/case_ids）
- 跑 MAS harness（`agent_id=a2a_remote`，向 purple 发起 A2A 调用）
- 产出 `runs/.../results.json` + evidence pack

前置同 Step 5（需可用的 emulator + ADB server + LLM key）。

一键验收：

```bash
export AVD_NAME="<your_avd_name>"
.venv/bin/python agentbeats/tools/validate_step6_harness_benign.py
```

输出：

- `runs/step6_harness_*/results.json`
- `runs/step6_harness_*/episode_0000/evidence/`

## Step 7 验收（指标与评分）

对应 `伯克利phase1.md` 的 Step 7：在 `results.json` 里输出至少 2–3 个维度（`task_success / attack_resistance / efficiency`，可选 `stability`），并且**每条 episode result** 至少包含：

- `score / max_score / success / time_used`
- + 1–2 个安全/效率字段（本实现输出 `attack_resistance / efficiency / steps_used / stability`）

校验（默认检查 `runs/*/results.json` 里最新的一个）：

```bash
.venv/bin/python agentbeats/tools/validate_step7_metrics.py
```
