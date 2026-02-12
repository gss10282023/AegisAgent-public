# Reproducibility Contract

This document defines **what MUST be pinned and recorded** so MAS results are reproducible.

Phase‑0 includes a toy smoke case that runs without Android. Later phases will add Android emulator integration.

## 1) Android emulator pinning (required once Android is used)

When MAS-Harness runs on Android, record the following in every run:

### 1.1 Host + tooling
- Host OS and kernel version
- Android SDK tools versions
  - `sdkmanager --version`
  - `avdmanager --version`
  - `adb version`

### 1.2 Emulator & system image
For the AVD used in experiments, record:
- Emulator binary version (`emulator -version`)
- AVD name
- Android API level (e.g., 34)
- System image package name (e.g., `system-images;android-34;google_apis;x86_64`)
- Build fingerprint (from `adb shell getprop ro.build.fingerprint`)
- **Hash** of the system image directory

#### How to compute a system image hash (example)

```bash
# Locate the system image directory under $ANDROID_SDK_ROOT/system-images/...
IMG_DIR="$ANDROID_SDK_ROOT/system-images/android-34/google_apis/x86_64"

# Compute a stable hash
find "$IMG_DIR" -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum
```

### 1.3 AVD configuration
Record:
- the `config.ini` of the AVD
- CPU arch, RAM, disk size
- display resolution / DPI
- input method settings

### 1.4 Snapshot/reset strategy (Phase 2 contract)

To reduce flakiness and make task setup reproducible, MAS-Harness uses **AVD snapshots**
as the first-priority reset mechanism (inspired by MobileWorld).

For every Android task run, record:

- `snapshot_tag` (the snapshot name loaded)
- the output of the snapshot load command
  - `adb emu avd snapshot load <snapshot_tag>`
- the emulator build fingerprint (see 1.2)

The snapshot load itself MUST be part of the evidence bundle (e.g., as a `reset`
event in `device_trace.jsonl`, or in the oracle trace).

## 2) Python dependencies

### 2.1 Locked dependencies
Use pinned versions:
- `requirements.txt`
- `requirements-dev.txt`

Optionally, also build a Docker image and record its digest.

### 2.2 Python version
Record:
- `python --version`
- `pip --version`

## 3) Randomness control

Every run MUST record:
- **global seed** (runner)
- **case seed** (per test case)

The runner MUST accept:
- `--seed <global>`
- case specs MAY include `case_seed` overrides

## 4) One‑click commands

- `make smoke` — run the toy smoke case
- `make run_public` — run all public cases
- `make report` — aggregate results into `runs/report.json`

## 5) Evidence bundles

Each episode MUST produce an evidence bundle with a stable structure:
- action trace
- foreground app trace
- confirmation/clarification trace
- (optional) screenshots / a11y dumps

## 6) Oracle reproducibility (Phase 2 contract)

Task success in MAS is defined by a **task success oracle/verifier**, not by "what the UI
looks like".

To keep BSR/RSR/BF meaningful under adversarial UI conditions, oracle queries MUST be:

- **Hard by default** (state-based): adb/content provider queries, files/db checks, backend
  callbacks, etc.
- **Auditable**: every oracle query and a digest of its result MUST be recorded in the
  evidence bundle (`oracle_trace.jsonl`).
- **Version-pinned**: when a hard oracle depends on adb behavior, content provider paths,
  or app versions, those MUST be recorded.

At minimum, record:

- `adb version`
- the exact oracle query (adb command, sqlite query, etc.)
- a stable `result_digest` (sha256 over canonical JSON)

The runner MUST be able to distinguish:

- `task_failed` (oracle is conclusive and indicates failure)
- `oracle_inconclusive` / `infra_failed` (oracle cannot determine success due to environment)

See `mas-harness/src/mas_harness/evidence.py`.
