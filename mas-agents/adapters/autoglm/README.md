# AutoGLM（Open-AutoGLM）独立虚拟环境

`Open-AutoGLM` 的依赖（例如 `openai`/`Pillow` 等）可能会和本仓库根目录 `.venv` 的依赖产生版本冲突。
建议在 `mas-agents/adapters/autoglm` 目录下单独创建/使用一个虚拟环境来运行示例。

## 1) 一键创建并安装依赖

在仓库根目录执行：

```bash
bash mas-agents/adapters/autoglm/bootstrap_venv.sh
```

或直接用 `uv`（推荐）：

```bash
uv sync --project mas-agents/adapters/autoglm/env
```

## 2) 运行示例

```bash
# 方式 A：直接用 env 的 python（推荐）
mas-agents/adapters/autoglm/env/.venv/bin/python mas-agents/adapters/autoglm/example_autoglm.py

# 方式 B：用 make
make autoglm_example
```

## 3) 配置（环境变量 / .env）

示例读取 `PHONE_AGENT_*` 环境变量（也兼容 `AUTOGLM_*` 别名）。常用项：

```bash
# 走智谱 BigModel OpenAI 兼容接口（可按需替换）
export PHONE_AGENT_BASE_URL="https://open.bigmodel.cn/api/paas/v4"
export PHONE_AGENT_MODEL="autoglm-phone"
export PHONE_AGENT_API_KEY="YOUR_API_KEY"
```

也可以把 `PHONE_AGENT_API_KEY=...` 写到仓库根目录 `.env`（如果环境里安装了 `python-dotenv`，示例会自动加载）。

> 如果你使用本地/无需鉴权的部署，可以不设置 `PHONE_AGENT_API_KEY`（会回退为 `EMPTY`）。
