# Open-AutoGLM env (isolated)

This folder is a standalone `uv` project to run `mas-agents/adapters/autoglm/example_autoglm.py`
without affecting the main repo `.venv`.

## Create / sync the env

From repo root:

```bash
uv sync --project mas-agents/adapters/autoglm/env
```

## Run the example

```bash
mas-agents/adapters/autoglm/env/.venv/bin/python mas-agents/adapters/autoglm/example_autoglm.py
```

Make sure you have `PHONE_AGENT_API_KEY` in the repo root `.env` (or exported in your shell).

