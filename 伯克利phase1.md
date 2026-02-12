## 背景与目标（保持不变）

你当前代码已经满足“可跑通的 mobile-agent benchmark”的关键条件：

* `mas-public/` 里有 cases（任务与变体）
* `mas-agents/adapters/` 里已成功接入 droidrun（还有 autoglm/minitap 等）
* `mas-harness/` 里有 runner、oracle/evidence、Android runtime 支持
  → 评测必须跑 Android emulator，但核心 harness 已存在。

**Phase 1 目标**：把它改造成可在平台端到端运行的 green agent，并提供 baseline purple agent（先用你已跑通的 droidrun），满足提交要求：公开仓库、README、baseline purple、可端到端运行的 Docker、平台注册、≤3 分钟 demo。([加州大学伯克利分校RDI](https://rdi.berkeley.edu/agentx-agentbeats.html "https://rdi.berkeley.edu/agentx-agentbeats.html"))

---

## 安卓模拟器放哪儿跑（新结论）

### 新建议（替代你原先的 “emulator 在 docker service 里”）

**emulator + adb server 运行在宿主机（本地机器 / GitHub Actions runner）**；green/purple 容器只带 `adb` 客户端并通过网络访问宿主机 adb server。

为什么可行、且更贴合比赛形态：

* AgentBeats 的 assessment runner 本质是 GitHub Actions workflow：workflow 可以先准备环境再跑容器化 agents。([AgentBeats](https://docs.agentbeats.dev/tutorial/ "https://docs.agentbeats.dev/tutorial/"))
* 提交要求强调 green agent 要 Docker 化并能端到端跑，不等于“所有依赖都必须塞进同一个容器”；只要 workflow 能自动把依赖环境（这里是 emulator）准备好，就仍是“无需人工干预”。([加州大学伯克利分校RDI](https://rdi.berkeley.edu/agentx-agentbeats.html "https://rdi.berkeley.edu/agentx-agentbeats.html"))

> 实操上，**把 emulator 从 Docker 拿出来**，往往比在 Docker 内折腾 KVM/特权/嵌套虚拟化稳定得多。

---

# Phase 1 改造计划（按实施顺序重排，已改为“宿主机 emulator”）

> 每一步仍包含：做什么、代码落点建议、验收标准。
> 你可以照旧把每一步做成一个 PR。

---

## Step 0 — 对齐 Phase 1 交付物与仓库结构（保持不变）

### 你要做的改造

1. README 写清 Phase 1 提交清单（Submission Checklist）：
   * Abstract
   * Public GitHub repo + README
   * Baseline purple agent(s)
   * Green Docker image：端到端运行无需人工干预
   * AgentBeats 注册（green + baseline purple）
   * ≤3 分钟 demo video ([加州大学伯克利分校RDI](https://rdi.berkeley.edu/agentx-agentbeats.html "https://rdi.berkeley.edu/agentx-agentbeats.html"))
2. 新增顶层目录：`agentbeats/`（尽量不动 `mas-*`）：
   * `agentbeats/green/`
   * `agentbeats/purple/droidrun_baseline/`
   * `agentbeats/scenarios/phase1_smoke/`
   * **新增：`agentbeats/emulator_host/`**（放宿主机启动/健康检查脚本）

### 验收标准

* 根目录 README 出现 `AgentBeats Phase 1 Quickstart`
* `agentbeats/` 目录结构落地（含 `emulator_host/`）

---

## Step 1 — green / purple 都先包成 A2A Server 外壳（保持不变）

### 你要做的改造

基于模板把 green 和 purple 都做成 A2A server：

* green 接收 `assessment_request`（participants + config），最终输出 JSON results artifact ([GitHub](https://github.com/agentbeats/tutorial "https://github.com/agentbeats/tutorial"))
* purple 先做 dummy 也行（后面再换成 droidrun）

### 代码落点建议

* `agentbeats/green/src/server.py`
* `agentbeats/green/src/execute.py`
* `agentbeats/purple/droidrun_baseline/src/server.py`
* `agentbeats/purple/droidrun_baseline/src/agent.py`

### 验收标准

* green/purple 都能启动并暴露 agent card
* 发最小 payload 给 green：能记录 participants 与 config

---

## Step 2 — 最关键风险点提前验证：宿主机 emulator + 远程 ADB server → 容器可稳定控制

> 这是你要替换掉原方案中 “emulator container/service” 的部分。

### 目标架构（新）

* 宿主机（你的电脑或 GitHub Actions runner）：
  * 启动 Android emulator（headless 或 GUI 都可）
  * 启动 **adb server**，并让它**监听非 localhost**，这样容器才能访问
    ADB 的 server 默认用 5037 端口。([Android Developers](https://developer.android.com/tools/adb "https://developer.android.com/tools/adb"))
* green/purple 容器：
  * 不启动 adb server（或即使启动也无所谓，但建议统一走宿主机 server）
  * `adb` 客户端通过 `-H/-P` 或 `ADB_SERVER_SOCKET` 连接宿主机 adb server。([Android Git Repositories](https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md "https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md"))

### 你要做的改造

#### 2.1 在宿主机准备 `emulator_host/` 脚本

在 `agentbeats/emulator_host/` 放三类脚本（shell/python 都行）：

* `start_emulator.sh`
  * 启动 emulator（建议固定端口/AVD，确保 serial 稳定，如 `emulator-5554`）
* `start_adb_server.sh`
  * 用 `adb -a` 让 server 监听所有网卡，而不是只监听 localhost（否则容器访问不到）。([Android Git Repositories](https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md "https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md"))
* `wait_until_ready.sh`
  * 等待设备真正启动完成：`adb shell getprop sys.boot_completed` 返回 `1`
* （可选）`reset_device.sh`
  * 做你 benchmark 需要的 reset（清数据、回到桌面、重置账号/状态等）

> 重要安全提醒：`adb -a` 会把 5037 暴露到网络接口。你应确保只在本机/CI 内网可访问，不要暴露到公网（本地可以用防火墙限制或只在可信网络运行）。

#### 2.2 让 green/purple 容器能访问宿主机

* macOS / Windows Docker Desktop：容器里通常可直接用 `host.docker.internal` 访问宿主机。
* Linux：需要在 compose 里给每个 service 加 `extra_hosts: ["host.docker.internal:host-gateway"]`（或 docker run 的 `--add-host=host.docker.internal:host-gateway`）。

#### 2.3 统一容器内 ADB 的“目标”

推荐你把 “ADB 连接信息”拆成两部分（比一个 `adb_target` 更稳）：

* `adb_server`：`host.docker.internal:5037`
* `android_serial`：`emulator-5554`（或你固定的 serial）

容器内 adb 调用方式二选一：

* **方式 A（推荐）：每次命令都显式指向远程 server**
  * `adb -H host.docker.internal -P 5037 devices`
  * `adb -H host.docker.internal -P 5037 -s emulator-5554 shell ...`
    ([Android Git Repositories](https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md "https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md"))
* **方式 B：设置环境变量**
  * `ADB_SERVER_SOCKET=tcp:host.docker.internal:5037`（让 adb client 走远程 server）([Stack Overflow](https://stackoverflow.com/questions/71094306/adb-server-socket "https://stackoverflow.com/questions/71094306/adb-server-socket"))
  * `ANDROID_SERIAL=emulator-5554`（默认设备）([Android Git Repositories](https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md "https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md"))

### 验收标准（强制）

在 **green 容器**里：

1. `adb ... devices` 能列出 emulator，并且状态为 `device`（online）
2. `adb ... shell getprop sys.boot_completed` 返回 `1`

在 **purple 容器**里也同样两条都满足。

**稳定性（必须做）：**

* 在宿主机把 emulator 重启两次（或 kill 后再启动两次）
* 每次重启后，green/purple 容器都能重新看到设备并执行一次 `adb shell ...` 成功

---

## Step 3 — 定义 green↔purple 最小“任务契约”（更新 adb 字段）

把你 green 给 purple 的请求 schema 更新为：

**green → purple（任务请求）至少包含：**

* `case_id / variant`
* `goal`
* `adb_server`（例如 `host.docker.internal:5037`）
* `android_serial`（例如 `emulator-5554`）
* `timeouts`（总时长/最大步数）

**purple → green（执行结果）至少包含：**

* `status`: `success|fail|timeout|error`
* `summary`
* `artifacts`（可选）

### 验收标准

* green 能发一个 dummy task 给 purple，purple 返回结构化 `status/summary`
* 超时能被 green 正确识别并标记

---

## Step 4 — 实现 `a2a_remote`：让 MAS runner 能评测“外部 purple A2A agent”（小改）

### 你要做的改造

新增 adapter：`mas-agents/adapters/a2a_remote/adapter.py`

职责同你原方案，但这里要把 “设备连接方式”改为：

* 不再假设 `adb connect emulator:5555`
* 改为在 adapter 里：
  * 读取 `adb_server/android_serial`
  * 统一用 `adb -H ... -P ... -s ...` 或设置 env `ADB_SERVER_SOCKET/ANDROID_SERIAL`

并在 registry 注册 `agent_id: a2a_remote`。

### 验收标准

* 不改 `mas-public/cases/**` 的情况下，用 CLI 跑通 1 个 case（purple 可先 dummy）
* purple 超时/故障：runner 不崩，case 标记失败并继续后续 case

---

## Step 5 — baseline purple：把 droidrun 封装成 A2A purple agent（改 adb 接入方式）

### 你要做的改造

把你现有 `mas-agents/adapters/droidrun/adapter.py` 可跑通逻辑抽出来，让 purple A2A server 调用：

* 接收 `adb_server`、`android_serial`、`goal`、`timeouts`
* **不再在 purple 容器里启动 emulator**
* droidrun 侧的 ADB 调用统一走：
  * 方式 ：在 purple 进程环境里设置 `ADB_SERVER_SOCKET/ANDROID_SERIAL`（如果 droidrun 走系统 adb）([Stack Overflow](https://stackoverflow.com/questions/71094306/adb-server-socket "https://stackoverflow.com/questions/71094306/adb-server-socket"))

> 这样你之后换 autoglm/minitap，只替换 purple 内部实现，green/runner 不动。

### 验收标准

* green 触发一次 A2A 调用，purple 能在 emulator 上完成至少 1 个 benign case
* 异常时返回 `status=error` + message（不直接 crash）

---

## Step 6 — 把 MAS harness 串进 green 的 `execute()`：端到端评测 + results.json + evidence pack（保持）

### 你要做的改造

green 收到 `assessment_request` 后：

1. 解析 config（case 选择、seed、数量、变体）
2. **先做 Step 2 的 ADB 健康检查**（否则直接 fail fast，给出明确错误）
3. 创建输出目录
4. 对每个 case 调 MAS runner（agent\_id 固定 `a2a_remote`，并把 purple endpoint + `adb_server/android_serial` 注入）
5. runner 生成 evidence/oracle/summary
6. 聚合成 `results.json` artifact 返回

说明：评测结果必须产出合法 JSON artifact（结构可自定义）。([GitHub](https://github.com/agentbeats/tutorial "https://github.com/agentbeats/tutorial"))

### 验收标准

* green 完整跑完 1 个 benign case：
  * evidence pack 目录存在
  * results artifact 是合法 JSON
* 同 config 同 seed 重跑两次：结果一致/基本一致

---

## Step 7 — 指标与评分（保持）

在 `results.json` 里至少输出 2–3 维：

* `task_success`
* `attack_resistance`
* `efficiency`（time\_used / steps 等）
* （可选）`stability`

### 验收标准

* 每条 result 至少包含：`score/max_score/success/time_used` + 1\~2 个安全/效率字段
* 评分尽量基于 green 可验证证据（oracle/evidence），purple artifacts 仅做补充

---

## Step 8 — Docker 化到 AgentBeats 标准（保持，但镜像里只带 adb client）

你仍然要为 green 与 baseline purple 写 Dockerfile，并满足：

* ENTRYPOINT 支持 `--host --port --card-url`（平台如何运行镜像在 tutorial repo 有明确要求）([GitHub](https://github.com/agentbeats/tutorial "https://github.com/agentbeats/tutorial"))
* build `linux/amd64`（GitHub Actions 用这个架构）([GitHub](https://github.com/agentbeats/tutorial "https://github.com/agentbeats/tutorial"))

**变化点**：镜像里不再需要 emulator；只需要 `platform-tools`（adb）和你 harness/agent 依赖。

### 验收标准

* `docker build --platform linux/amd64` 两个镜像都成功
* `docker run -p ...` 后能访问 agent card

---

## Step 9 — scenario.toml + 本地一键复现（调整：增加“宿主机 emulator 前置步骤”）

你原来是 `docker compose up --abort-on-container-exit` 一条命令跑完。现在 emulator 在宿主机，所以建议改成：

### 你要做的改造

1. `agentbeats/scenarios/phase1_smoke/scenario.toml`：照旧（green/purple 镜像 + config）
2. 在 `agentbeats/emulator_host/` 提供 `make host-emulator-up`（或 `./start_emulator.sh && ./start_adb_server.sh && ./wait_until_ready.sh`）
3. README 的 Quickstart 改成两步：

* 先启动宿主机 emulator + adb server
* 再跑 `docker compose up`

> 这仍然是“端到端无需人工干预”，因为你可以把两步封进一个 `make smoke`；平台侧则由 GitHub Actions workflow 执行这两个阶段。([AgentBeats](https://docs.agentbeats.dev/tutorial/ "https://docs.agentbeats.dev/tutorial/"))

### 验收标准

* 本地能复现：启动宿主机脚本后，`docker compose up --abort-on-container-exit` 跑完并产出：
  * green results JSON
  * evidence pack

---

## Step 10 — AgentBeats 注册 + 提交材料（保持）

按 Phase 1 提交要求准备：

* Abstract
* Public repo + README
* baseline purple（A2A）
* green Docker image
* AgentBeats 注册
* ≤3 分钟 demo ([加州大学伯克利分校RDI](https://rdi.berkeley.edu/agentx-agentbeats.html "https://rdi.berkeley.edu/agentx-agentbeats.html"))

**demo 建议脚本**（更贴合你现在架构）：

1. 展示宿主机启动 emulator（headless 也行）+ adb server
2. 启动 green/purple 容器
3. 触发一次 assessment
4. 展示 results.json + evidence

---

# 你现有代码中尽量不动的部分（仍然保留）

* `mas-public/cases/**`
* `mas-harness/**`
* `mas-agents/adapters/droidrun/**`（仅做“入口层”的 adb 目标改造）
* 其他 adapters（autoglm/minitap）后续可替换 purple 内部实现

---

# 最小可交付（MVP）里程碑（按顺序）

如果只想先过线：

1. Step 1（A2A 外壳跑起来）
2. **Step 2（宿主机 emulator + 远程 adb server → 容器可稳定控制）**
3. Step 4（a2a\_remote adapter 能跑一个 case）
4. Step 5（droidrun purple 能被 A2A 调用并跑完）
5. Step 6（green 端到端评测 + JSON results）

---

## 你现在遇到的“docker 里 emulator 卡/起不来”的直接对应解法小结

* emulator 不进 Docker → 直接跑在宿主机（本地/runner）
* 容器不连 emulator 端口 → 直接连宿主机 **adb server:5037**（需要 `adb -a`）([Android Git Repositories](https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md "https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md"))
* 容器侧统一用 `adb -H/-P` 或 `ADB_SERVER_SOCKET` 指向宿主机 adb server ([Android Git Repositories](https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md "https://android.googlesource.com/platform/packages/modules/adb/%2B/refs/heads/master/docs/user/adb.1.md"))
