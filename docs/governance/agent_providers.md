# Agent Providers (Phase 0.7)

This document defines **how MAS integrates external model providers** in a way that is:

1) **auditable** (we can prove what model/provider/params produced an action), and
2) **reproducible** (we can re-run the same configuration later).

> Scope: This file focuses on governance + run metadata. It does **not** define
> agent adapter implementations (those live under `mas-harness/`).

---

## 1. Allowed providers

MAS is designed to support multiple providers. In Phase 0 we only **define** the
allowed list and the metadata contract; Phase 3 will add actual adapters.

### 1.1 OpenRouter

* **provider_id**: `openrouter`
* Authentication: `OPENROUTER_API_KEY` (env var)
* Base URL: `https://openrouter.ai/api/v1` (override via `MAS_AGENT_BASE_URL`)

### 1.2 Local / self-hosted

* **provider_id**: `local`
* No network required.
* For self-hosted OpenAI-compatible endpoints, set:
  * `MAS_AGENT_PROVIDER=local`
  * `MAS_AGENT_BASE_URL=http://127.0.0.1:...`

---

## 2. Model naming conventions

To avoid ambiguity across providers, the `model_id` field in `run_manifest.json`
MUST be the provider-facing model identifier.

Examples (illustrative):

* UI-TARS 7B on OpenRouter: `MAS_AGENT_MODEL_ID=<openrouter_model_slug>`
* AutoGLM: `MAS_AGENT_MODEL_ID=<autoglm_model_slug_or_version>`

---

## 3. Default inference parameters (recorded in run_manifest)

The harness records the following parameters if available:

* `temperature` (default: `0.2`)
* `top_p` (default: `1.0`)
* `max_tokens` (default: `1024`)
* `timeout_s` (default: `60`)

These are controlled via environment variables (Phase 0 contract):

* `MAS_AGENT_TEMPERATURE`
* `MAS_AGENT_TOP_P`
* `MAS_AGENT_MAX_TOKENS`
* `MAS_AGENT_TIMEOUT_S`

---

## 4. Rate limiting & retries (recorded in run_manifest)

When using network providers, the runner MUST record its retry behavior:

* `retry.max_retries` (default: `2`)
* `retry.backoff_s` (default: `2`)

Controlled via:

* `MAS_AGENT_MAX_RETRIES`
* `MAS_AGENT_RETRY_BACKOFF_S`

---

## 5. Execution mode (recorded in run_manifest)

MAS supports two execution modes:

* `planner_only` (recommended): agent outputs actions; MAS executes and audits.
* `agent_driven` (transition): agent executes; MAS audits side-channel evidence.

Controlled via:

* `MAS_EXECUTION_MODE` (default: `planner_only`)

---

## 6. Required run artifacts

Every run MUST write:

* `run_manifest.json`
* `env_capabilities.json`

These are created even in the toy Phase-0 smoke runs.
