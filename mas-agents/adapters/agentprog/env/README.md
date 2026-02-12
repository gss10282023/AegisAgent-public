# AgentProg env (isolated)

This folder is a standalone `uv` project to run AgentProg in an isolated virtual environment,
without affecting the main repo `.venv`.

## Create / sync the env

From repo root:

```bash
uv sync --project mas-agents/adapters/agentprog/env
```

## API key (.env)

Put this in repo-root `.env`:

```env
OPENROUTER_API_KEY=...
```

Optional (recommended by OpenRouter):

```env
OPENROUTER_HTTP_REFERER=...
OPENROUTER_APP_TITLE=...
```

## Run (OpenRouter for both Gemini + UI-TARS)

```bash
mas-agents/adapters/agentprog/env/.venv/bin/python mas-agents/adapters/agentprog/run_openrouter.py
```

