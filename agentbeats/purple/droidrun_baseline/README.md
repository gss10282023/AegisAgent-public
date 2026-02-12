# agentbeats/purple/droidrun_baseline/

Phase 1 的 baseline **purple agent（A2A Server）**。

当前对应 `伯克利phase1.md` 的 Step 5：实现最小任务契约（Step 3），并用 **droidrun** 真正执行 `goal`。

实现要点：

- 不在容器内启动 emulator
- 通过 `adb_server/android_serial` 连接到宿主机 adb server（purple 进程内设置 `ADB_SERVER_SOCKET/ANDROID_SERIAL`）
- 超时/异常返回 `status=timeout|error`（不 crash）

## 安装依赖

```bash
uv pip install -r agentbeats/requirements.txt --python .venv/bin/python
```

运行 droidrun 需要你本地 `.venv` 已包含项目依赖（`droidrun`/`llama-index` 等）；如果你是新建 venv，请确保把项目依赖一起装上。

## 启动服务

```bash
.venv/bin/python agentbeats/purple/droidrun_baseline/src/server.py --host 127.0.0.1 --port 9010
```

Agent card：

- `http://127.0.0.1:9010/.well-known/agent-card.json`

## Docker（Step 8）

构建（平台要求 `linux/amd64`）：

```bash
docker build --platform linux/amd64 -f agentbeats/purple/droidrun_baseline/Dockerfile -t mas-purple-droidrun:phase1 .
```

运行（AgentBeats 运行镜像时会传入 `--host/--port/--card-url`）：

```bash
docker run --rm -p 9010:9010 mas-purple-droidrun:phase1 --host 0.0.0.0 --port 9010
```

Agent card：

- `http://127.0.0.1:9010/.well-known/agent-card.json`

## 运行 droidrun（Step 5）

必须环境变量（至少一个）：

- `OPENROUTER_API_KEY`（推荐）或 `OPENAI_API_KEY`

可选环境变量：

- `OPENROUTER_BASE_URL`（默认 `https://openrouter.ai/api/v1`）
- `DROIDRUN_MANAGER_MODEL` / `DROIDRUN_EXECUTOR_MODEL` / `DROIDRUN_CODEACT_MODEL`
- `AGENTBEATS_PURPLE_TRACE_DIR`（droidrun traces 输出目录；默认系统临时目录）

payload（green → purple）里至少要有：

- `goal`
- `adb_server`（例如 `host.docker.internal:5037`）
- `android_serial`（例如 `emulator-5554`）
- `timeouts.total_s` / `timeouts.max_steps`

兼容 Step 3 合同验收（不跑 droidrun）：

- `variant: {"mode":"dummy"}` → 直接返回 success
- `variant: {"simulate_delay_s":2}` → 仅 sleep（用于触发 green 超时用例）

## 一键验收脚本（Step 5）

如果你希望“一条命令”验证 **宿主机 emulator + A2A 调用 + droidrun**，用：

```bash
.venv/bin/python agentbeats/tools/validate_step5_droidrun_benign.py --avd-name "<your_avd_name>"
```
