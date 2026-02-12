# Ethics & Dual‑Use Policy (Normative)

This repository contains code and specifications for evaluating **mobile agents** under adversarial and hazard conditions.

Because parts of this work can be **dual‑use**, this document defines **mandatory** constraints for what may be implemented and released.

## 1) Intended use

MAS is intended for:
- **Academic evaluation** and reproducible benchmarking
- **Controlled testing** in **emulators/testbeds/mock apps**
- **Defensive research** (measuring and reducing security risk)

MAS is **not** intended for:
- enabling real‑world attacks
- deploying exploit/attack tooling to real users, real devices, or real production apps

## 2) Hard constraints (MUST / MUST NOT)

### Injectors
- Injectors **MUST** only operate in:
  - Android emulator(s) configured for testing, OR
  - containerized/simulated testbeds, OR
  - mock applications and test content sources.
- Injectors **MUST NOT** provide a “drop‑in” capability to attack arbitrary real apps on real devices.
- Public releases **MUST NOT** include:
  - one‑click scripts for real device attack delivery,
  - generic overlay/popup deployment tooling,
  - payload libraries designed for stealthy real‑world abuse.

### Payload publication
- Public benchmark payloads **MUST** be **de‑risked**:
  - minimal templates
  - no real brand impersonation
  - no realistic phishing kits
- Hidden seeds and hidden case instances **MUST NOT** be published.

### Data handling
- Test fixtures **MUST** avoid real user data.
- Use **synthetic** or **canary** tokens for leakage detection.

## 3) Responsible disclosure

If you discover a vulnerability affecting a real system:
- **Do not** publicly disclose details that enable exploitation.
- Use a responsible disclosure process:
  1) Identify the vendor’s security contact / bug bounty program
  2) Privately report with reproduction steps
  3) Allow a reasonable remediation window before public discussion

A project‑level contact process can be documented in `SECURITY.md`.

## 4) Review gate

Any PR that adds or modifies an injector MUST include:
- a statement explaining why it cannot be used against real devices/apps
- a unit/integration test proving it requires testbed hooks

