# MAS (Draft) — Mobile Agent Security

This repository is a **phase-0/phase-1 scaffold** for the MAS project:

- **MAS-Spec (Draft)**: an *executable security specification* for mobile agents.
- **MAS-Harness**: evaluation harness (runner, adapters, injectors, policy, checkers, auditor).
- **MAS-Public**: public, reproducible benchmark cases.
- **MAS-Hidden**: hidden/parameterized generator + private seeds (not published).
- **MAS-Guard** (optional): a defense middleware used to demonstrate measurable risk reduction.

> Scope note: MAS evaluates *externally observable behavior* of an agentic system (observations → actions → environment transitions). The spec does **not** assume anything about the agent’s internal architecture.

## Repository layout

```
docs/          # project-level plans/governance/oracles
mas-spec/      # spec documents + JSON schemas
mas-harness/   # runner + policy + checkers + evidence recorder
mas-public/    # public benchmark cases
mas-hidden/    # hidden generator interface (seeds not included)
mas-guard/     # optional guard middleware (placeholder)
```

Project docs index: [docs/INDEX.md](docs/INDEX.md).

## Safety & dual‑use policy

This project is designed for **controlled evaluation** only.

- Injectors MUST only work in **emulators/testbeds/mock apps**.
- We do **not** provide generic scripts to deploy attacks on real devices/apps.

See: [docs/governance/ETHICS_AND_DUAL_USE.md](docs/governance/ETHICS_AND_DUAL_USE.md).

## Quickstart (phase‑0 smoke)

This phase‑0 scaffold includes a **toy smoke case** that does *not* require Android.
It validates:
- schema validation
- harness wiring
- evidence bundle structure

### 1) Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

### 2) Run baseline validation + smoke

```bash
make validate_schemas
make smoke
```

Outputs are written to `runs/`.

### 3) Run public cases (smoke + schema validation case)

```bash
make run_public
make report
```

---

## AgentBeats Phase 1 Quickstart (Berkeley Step 9)

Phase 1 runs require an **Android emulator**. Per `伯克利phase1.md` Step 2, this repo
runs the **emulator + adb server on the host**, while green/purple run in Docker and
connect to the host adb server over the network.

### 0) Host prerequisites (emulator)

- Android SDK `platform-tools` (for `adb`)
- Android SDK `emulator`
- An AVD you can boot (set `AVD_NAME`)

> Security note: the host script uses `adb -a` which exposes port `5037` on network
> interfaces. Ensure it’s only reachable from your machine / trusted CI network.

### 1) Start host emulator + adb server

```bash
export AVD_NAME="mas_avd"  # replace with your AVD name
make -C agentbeats/emulator_host host-emulator-up
```

### 2) Run the Phase 1 smoke scenario (docker compose)

```bash
docker compose --env-file .env -f agentbeats/scenarios/phase1_smoke/compose.yaml up --abort-on-container-exit --exit-code-from phase1_smoke --build
```

If you don’t use a `.env`, remove `--env-file .env` and export your keys in the shell instead.

Linux only (if `host.docker.internal` is not available):

```bash
docker compose --env-file .env -f agentbeats/scenarios/phase1_smoke/compose.yaml -f agentbeats/scenarios/phase1_smoke/compose.linux.yaml up --abort-on-container-exit --exit-code-from phase1_smoke --build
```

One command (wraps both steps above):

```bash
make phase1_smoke
```

Outputs (on success):

- `runs/step6_eval_*/results.json`
- `runs/step6_eval_*/episode_0000/evidence/`
- `runs/phase1_smoke_*/results_artifact.json` (A2A artifact copy)

## Reproducibility

Phase‑0 provides the **reproducibility contract** (what must be pinned & recorded).
Android emulator pinning is documented in [docs/governance/REPRODUCIBILITY.md](docs/governance/REPRODUCIBILITY.md).

## CI

GitHub Actions runs:
- schema validation
- lint
- unit tests
- smoke run

See `.github/workflows/ci.yml`.

---

## Phase‑0 limitations

- No Android emulator integration yet (will be introduced in later phases).
- Injectors are placeholders.
- MAS-Guard is a placeholder.
