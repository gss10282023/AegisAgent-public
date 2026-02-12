# MAS-Public

Public, reproducible cases.

Case layout (canonical):

```
mas-public/cases/<case_id>/
  task.yaml
  policy.yaml
  eval.yaml
  attack.yaml   # optional
  fixtures/     # optional (synthetic data)
```

Phase‑0 includes `smoke_001`.

Phase‑1 adds `schema_attack_001` as a **schema validation** case that includes an optional `attack.yaml` so that Task/Policy/Eval/Attack JSON Schemas are all exercised in CI.
