from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Sequence

from mas_harness.evidence import stable_sha256
from mas_harness.evidence.evidence_pack import (
    ASSERTIONS_JSONL,
    EVIDENCE_PACK_V0_RUN_REQUIRED_FILES,
    FACTS_JSONL,
    resolve_episode_evidence_dir,
)
from mas_harness.oracle_framework.assertion_base import Assertion
from mas_harness.oracle_framework.assertions import (
    SA_BindingConsistentOrClarified,
    SA_CanaryNoUnauthorizedFlow,
    SA_ConsentRequiredAndMatched,
    SA_LoopBudgetBounded,
    SA_NoNewPackages,
    SA_NoSettingsDiff,
    SA_ScopeForegroundApps,
    SuccessOracleAssertion,
)
from mas_harness.oracle_framework.detector_base import Detector
from mas_harness.oracle_framework.detectors import (
    ActionEvidenceDetector,
    BindingStateDetector,
    CanaryConfigDetector,
    CanaryHitsDetector,
    ConsentTraceDetector,
    EnvProfileDetector,
    ForegroundSeqDetector,
    HighRiskEffectsDetector,
    OracleEventIndexDetector,
    OracleTypedFactsDetector,
    PackageDiffDetector,
    SettingsDiffDetector,
    StepStatsDetector,
)
from mas_harness.oracle_framework.policy_compile import (
    AssertionConfig,
    compile_baseline_safety_assertions,
)
from mas_harness.oracle_framework.schema_validators import (
    assert_assertion_result_v0,
    assert_fact_v0,
)
from mas_harness.oracle_framework.store import FactStore
from mas_harness.oracle_framework.types import (
    ALLOWED_ASSERTION_RESULTS_V0,
    ALLOWED_FACT_ORACLE_SOURCES_V0,
    ALLOWED_SEVERITIES_V0,
    AssertionResult,
    Fact,
)


def _json_dumps_canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _find_run_root(start: Path) -> Path | None:
    cur = Path(start).resolve()
    for _ in range(20):
        if all((cur / name).exists() for name in EVIDENCE_PACK_V0_RUN_REQUIRED_FILES):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


@dataclass(frozen=True)
class EpisodeEvidencePack:
    episode_dir: Path
    evidence_dir: Path
    run_root: Path | None
    run_manifest: dict[str, Any] | None
    env_capabilities: dict[str, Any] | None

    @classmethod
    def from_episode_dir(cls, episode_dir: Path | str) -> "EpisodeEvidencePack":
        episode_dir = Path(episode_dir)
        evidence_dir = resolve_episode_evidence_dir(episode_dir)
        run_root = _find_run_root(evidence_dir)
        run_manifest = _load_json_object(run_root / "run_manifest.json") if run_root else None
        env_capabilities = (
            _load_json_object(run_root / "env_capabilities.json") if run_root else None
        )
        return cls(
            episode_dir=episode_dir,
            evidence_dir=evidence_dir,
            run_root=run_root,
            run_manifest=run_manifest,
            env_capabilities=env_capabilities,
        )


_DEFAULT_DETECTORS: Sequence[type[Detector]] = (
    ForegroundSeqDetector,
    StepStatsDetector,
    ActionEvidenceDetector,
    EnvProfileDetector,
    OracleEventIndexDetector,
    OracleTypedFactsDetector,
    PackageDiffDetector,
    SettingsDiffDetector,
    HighRiskEffectsDetector,
    ConsentTraceDetector,
    CanaryConfigDetector,
    CanaryHitsDetector,
    BindingStateDetector,
)

_DETECTOR_REGISTRY: Mapping[str, type[Detector]] = {
    cls.detector_id: cls for cls in _DEFAULT_DETECTORS
}

_DEFAULT_ASSERTIONS: Sequence[type[Assertion]] = (
    # SafetyAssertions v1 (audit-first)
    SA_ScopeForegroundApps,
    SA_LoopBudgetBounded,
    SA_ConsentRequiredAndMatched,
    SA_CanaryNoUnauthorizedFlow,
    SA_BindingConsistentOrClarified,
    SA_NoNewPackages,
    SA_NoSettingsDiff,
    # SuccessAssertions (wrapper over Oracle Zoo events)
    SuccessOracleAssertion,
)

_ASSERTION_REGISTRY: Mapping[str, type[Assertion]] = {
    cls.assertion_id: cls for cls in _DEFAULT_ASSERTIONS
}

_EVAL_CHECKER_ALIASES: Mapping[str, str] = {
    "C1": "SA_ScopeForegroundApps",
    "C2": "SA_ConsentRequiredAndMatched",
    "C3": "SA_CanaryNoUnauthorizedFlow",
    "C4": "SA_LoopBudgetBounded",
    "C5": "SA_BindingConsistentOrClarified",
}

_ASSERTION_CONFIG_ERROR_KEY = "__config_error__"
_ASSERTION_CONFIG_ERROR_DETAILS_KEY = "__config_error_details__"


def _normalize_evidence_refs(refs: Iterable[Any]) -> list[str]:
    out: set[str] = set()
    for r in refs:
        if r is None:
            continue
        s = str(r).strip()
        if not s:
            continue
        out.add(s)
    return sorted(out)


def _normalize_oracle_source(value: Any) -> str:
    s = str(value or "").strip()
    if s in ALLOWED_FACT_ORACLE_SOURCES_V0:
        return s
    return "none"


def _nonempty_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def _is_json_serializable(value: Any) -> bool:
    try:
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except Exception:
        return False
    return True


def _normalize_assertion_id(value: Any) -> str | None:
    name = _nonempty_str(value)
    if name is None:
        return None
    return _EVAL_CHECKER_ALIASES.get(name, name)


def _make_invalid_assertion_config(
    *,
    assertion_id: str,
    error: str,
    details: Mapping[str, Any] | None = None,
) -> AssertionConfig:
    payload: dict[str, Any] = {_ASSERTION_CONFIG_ERROR_KEY: str(error)}
    if details is not None:
        sanitized = dict(details)
        if not _is_json_serializable(sanitized):
            sanitized = {"details_repr": repr(details)[:500]}
        payload[_ASSERTION_CONFIG_ERROR_DETAILS_KEY] = sanitized
    return AssertionConfig(assertion_id=str(assertion_id), enabled=True, params=payload)


def parse_eval_checkers_enabled(eval_spec: Mapping[str, Any]) -> list[AssertionConfig]:
    """Parse eval.checkers_enabled into normalized AssertionConfig items.

    Supports legacy list[str] and mixed list[str|dict].
    """

    raw = eval_spec.get("checkers_enabled")
    if not isinstance(raw, list):
        return []

    out: list[AssertionConfig] = []
    for idx, item in enumerate(raw):
        if isinstance(item, str):
            assertion_id = _normalize_assertion_id(item)
            if assertion_id is None:
                continue
            out.append(AssertionConfig(assertion_id=assertion_id))
            continue

        if isinstance(item, Mapping):
            assertion_id = _normalize_assertion_id(item.get("assertion_id"))
            if assertion_id is None:
                out.append(
                    _make_invalid_assertion_config(
                        assertion_id=f"InvalidAssertionConfig/{idx}",
                        error="missing_assertion_id",
                        details={"item": dict(item)},
                    )
                )
                continue

            enabled_raw = item.get("enabled", True)
            if not isinstance(enabled_raw, bool):
                out.append(
                    _make_invalid_assertion_config(
                        assertion_id=assertion_id,
                        error="invalid_enabled_type",
                        details={"enabled": enabled_raw, "item": dict(item)},
                    )
                )
                continue

            params_raw = item.get("params", {})
            if params_raw is None:
                params_raw = {}
            if not isinstance(params_raw, dict):
                out.append(
                    _make_invalid_assertion_config(
                        assertion_id=assertion_id,
                        error="invalid_params_type",
                        details={"params": params_raw, "item": dict(item)},
                    )
                )
                continue
            if not _is_json_serializable(params_raw):
                out.append(
                    _make_invalid_assertion_config(
                        assertion_id=assertion_id,
                        error="params_not_json_serializable",
                        details={"item": dict(item)},
                    )
                )
                continue

            severity_override_raw = item.get("severity_override")
            severity_override = _nonempty_str(severity_override_raw)
            if severity_override_raw is not None and severity_override is None:
                out.append(
                    _make_invalid_assertion_config(
                        assertion_id=assertion_id,
                        error="invalid_severity_override",
                        details={"severity_override": severity_override_raw, "item": dict(item)},
                    )
                )
                continue
            if (
                severity_override is not None
                and severity_override.lower() not in ALLOWED_SEVERITIES_V0
            ):
                out.append(
                    _make_invalid_assertion_config(
                        assertion_id=assertion_id,
                        error="invalid_severity_override",
                        details={"severity_override": severity_override_raw, "item": dict(item)},
                    )
                )
                continue

            risk_override_raw = item.get("risk_weight_bucket_override")
            risk_override = _nonempty_str(risk_override_raw)
            if risk_override_raw is not None and risk_override is None:
                out.append(
                    _make_invalid_assertion_config(
                        assertion_id=assertion_id,
                        error="invalid_risk_weight_bucket_override",
                        details={
                            "risk_weight_bucket_override": risk_override_raw,
                            "item": dict(item),
                        },
                    )
                )
                continue

            out.append(
                AssertionConfig(
                    assertion_id=assertion_id,
                    enabled=bool(enabled_raw),
                    params=dict(params_raw),
                    severity_override=severity_override,
                    risk_weight_bucket_override=risk_override,
                )
            )
            continue

        out.append(
            _make_invalid_assertion_config(
                assertion_id=f"InvalidAssertionConfig/{idx}",
                error="invalid_checker_item_type",
                details={"item_repr": repr(item)[:500]},
            )
        )

    return out


def compile_enabled_assertions(
    policy_spec: Mapping[str, Any],
    eval_spec: Mapping[str, Any],
) -> tuple[list[AssertionConfig], Mapping[str, str]]:
    """Deterministically merge baseline + eval overrides into enabled AssertionConfigs."""

    baseline_cfgs = compile_baseline_safety_assertions(dict(policy_spec), eval_spec=eval_spec)
    extra_cfgs = parse_eval_checkers_enabled(eval_spec)

    merged: MutableMapping[str, AssertionConfig] = {}
    sources: dict[str, str] = {}

    for cfg in sorted(baseline_cfgs, key=lambda c: c.assertion_id):
        if cfg.enabled is not True:
            continue
        merged[cfg.assertion_id] = cfg
        sources[cfg.assertion_id] = "baseline"

    for cfg in extra_cfgs:
        assertion_id = _normalize_assertion_id(cfg.assertion_id) or str(cfg.assertion_id)
        if cfg.enabled is False:
            merged.pop(assertion_id, None)
            sources.pop(assertion_id, None)
            continue
        merged[assertion_id] = AssertionConfig(
            assertion_id=assertion_id,
            enabled=True,
            params=dict(cfg.params),
            severity_override=cfg.severity_override,
            risk_weight_bucket_override=cfg.risk_weight_bucket_override,
        )
        sources[assertion_id] = "eval_override"

    # Success oracle assertion is always enabled.
    if "SuccessOracleAssertion" not in merged:
        merged["SuccessOracleAssertion"] = AssertionConfig(assertion_id="SuccessOracleAssertion")
        sources["SuccessOracleAssertion"] = "baseline"

    # Never allow the safety baseline to be "disabled empty".
    has_safety = any(aid.startswith("SA_") for aid in merged.keys())
    if not has_safety:
        merged["SA_ScopeForegroundApps"] = AssertionConfig(assertion_id="SA_ScopeForegroundApps")
        sources["SA_ScopeForegroundApps"] = "baseline"

    out = [merged[aid] for aid in sorted(merged.keys())]
    return out, sources


def _compile_enabled_assertion_configs_from_case_ctx(
    case_ctx: Any,
) -> tuple[list[AssertionConfig], Mapping[str, str]]:
    policy: dict[str, Any] = {}
    ev: Mapping[str, Any] = {}

    if isinstance(case_ctx, Mapping):
        raw_policy = case_ctx.get("policy")
        if isinstance(raw_policy, Mapping):
            policy = dict(raw_policy)

        raw_eval = case_ctx.get("eval")
        if isinstance(raw_eval, Mapping):
            ev = raw_eval

    return compile_enabled_assertions(policy, ev)


def _finalize_fact(fact: Fact) -> Fact:
    payload = dict(fact.payload) if isinstance(fact.payload, Mapping) else {}
    evidence_refs = _normalize_evidence_refs(fact.evidence_refs)
    oracle_source = _normalize_oracle_source(fact.oracle_source)
    digest = stable_sha256(
        {
            "fact_id": fact.fact_id,
            "schema_version": fact.schema_version,
            "oracle_source": oracle_source,
            "evidence_refs": evidence_refs,
            "payload": payload,
        }
    )
    finalized = Fact(
        fact_id=fact.fact_id,
        schema_version=fact.schema_version,
        digest=digest,
        oracle_source=oracle_source,
        evidence_refs=evidence_refs,
        payload=payload,
    )
    assert_fact_v0(finalized.to_dict())
    return finalized


def _write_facts_jsonl(path: Path, facts: Sequence[Fact]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for fact in facts:
            f.write(_json_dumps_canonical(fact.to_dict()))
            f.write("\n")
    tmp_path.replace(path)


def _load_facts_jsonl(path: Path) -> list[Fact]:
    if not path.exists():
        return []
    facts: list[Fact] = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except Exception as e:
            raise ValueError(f"invalid json in facts.jsonl: {path}:{i} ({e})") from e
        if not isinstance(obj, dict):
            raise ValueError(f"facts.jsonl line must be an object: {path}:{i}")
        facts.append(
            Fact(
                fact_id=str(obj.get("fact_id", "")),
                schema_version=str(obj.get("schema_version", "")),
                digest=str(obj.get("digest", "")),
                oracle_source=str(obj.get("oracle_source", "")),
                evidence_refs=list(obj.get("evidence_refs") or []),
                payload=dict(obj.get("payload") or {}),
            )
        )
    return facts


def _finalize_assertion_result(result: AssertionResult) -> AssertionResult:
    evidence_refs = _normalize_evidence_refs(result.evidence_refs)
    raw_payload = getattr(result, "payload", {}) or {}
    try:
        payload = dict(raw_payload)
    except Exception:
        payload = {"payload_repr": repr(raw_payload)[:500]}
    if not _is_json_serializable(payload):
        payload = {"payload_repr": repr(raw_payload)[:500]}

    severity = result.severity
    if severity is not None:
        severity = str(severity).strip().lower() or None

    risk_bucket = result.risk_weight_bucket
    if risk_bucket is not None:
        risk_bucket = str(risk_bucket).strip() or None

    res = str(result.result or "").strip().upper()
    if res not in ALLOWED_ASSERTION_RESULTS_V0:
        raise ValueError(f"invalid assertion result: {result.assertion_id}={result.result!r}")

    finalized = AssertionResult(
        assertion_id=str(result.assertion_id),
        result=res,
        payload=payload,
        severity=severity,
        risk_weight_bucket=risk_bucket,
        mapped_sp=result.mapped_sp,
        mapped_primitive=result.mapped_primitive,
        mapped_boundary=result.mapped_boundary,
        impact_level=result.impact_level,
        evidence_refs=evidence_refs,
        inconclusive_reason=result.inconclusive_reason,
        applicable=result.applicable,
    )
    assert_assertion_result_v0(finalized.to_dict())

    if finalized.result == "FAIL" and not any(":L" in r for r in evidence_refs):
        raise ValueError(
            "FAIL assertions must include at least one line-level evidence_ref: "
            f"{finalized.assertion_id}"
        )

    return finalized


def _write_assertions_jsonl(path: Path, results: Sequence[AssertionResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(_json_dumps_canonical(r.to_dict()))
            f.write("\n")
    tmp_path.replace(path)


def run_assertions(
    pack: Any,
    case_ctx: Any,
    enabled_assertions: Sequence[Any] | None = None,
    *,
    facts: Sequence[Fact] | None = None,
) -> list[AssertionResult]:
    """Run assertion zoo over an episode evidence bundle, writing assertions.jsonl.

    Args:
        pack: episode directory path or EpisodeEvidencePack.
        case_ctx: case/policy context (policy is required for most safety assertions).
        enabled_assertions: optional list of assertion_ids or Assertion instances.
        facts: optional precomputed Facts (skips loading facts.jsonl).

    Returns:
        List of validated AssertionResults written to assertions.jsonl.
    """

    if isinstance(pack, EpisodeEvidencePack):
        pack_obj = pack
    else:
        pack_obj = EpisodeEvidencePack.from_episode_dir(Path(pack))

    facts_list = (
        list(facts) if facts is not None else _load_facts_jsonl(pack_obj.evidence_dir / FACTS_JSONL)
    )
    store = FactStore(facts_list)

    if enabled_assertions is None:
        enabled_assertions, _sources = _compile_enabled_assertion_configs_from_case_ctx(case_ctx)

    def _config_evidence_refs() -> list[str]:
        refs: list[str] = []
        if isinstance(case_ctx, Mapping):
            for k in ("policy_path", "eval_path"):
                v = _nonempty_str(case_ctx.get(k))
                if v is not None:
                    refs.append(v)
        refs.extend(["policy.yaml", "eval.yaml"])
        return _normalize_evidence_refs(refs)

    def _inconclusive(
        *,
        assertion_id: str,
        reason: str,
        evidence_refs: Sequence[str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> AssertionResult:
        return AssertionResult(
            assertion_id=str(assertion_id),
            result="INCONCLUSIVE",
            severity="med",
            risk_weight_bucket=None,
            mapped_sp=None,
            mapped_primitive=None,
            mapped_boundary=None,
            impact_level=_nonempty_str(case_ctx.get("impact_level"))
            if isinstance(case_ctx, Mapping)
            else None,
            evidence_refs=list(evidence_refs or []),
            inconclusive_reason=str(reason),
            applicable=True,
            payload=dict(payload or {}),
        )

    results_by_id: dict[str, AssertionResult] = {}

    def _record(result: AssertionResult) -> None:
        results_by_id[str(result.assertion_id)] = result

    for idx, item in enumerate(enabled_assertions):
        cfg: AssertionConfig | None = None
        assertion: Assertion | None = None

        if isinstance(item, Assertion):
            assertion = item
        elif isinstance(item, AssertionConfig):
            cfg = item
        elif isinstance(item, str):
            assertion_id = _normalize_assertion_id(item)
            if assertion_id is None:
                continue
            cls = _ASSERTION_REGISTRY.get(assertion_id)
            if cls is None:
                _record(
                    _inconclusive(
                        assertion_id=assertion_id,
                        reason="unknown_assertion_id",
                        evidence_refs=_config_evidence_refs(),
                    )
                )
                continue
            try:
                assertion = cls(params=None)
            except Exception as e:
                _record(
                    _inconclusive(
                        assertion_id=assertion_id,
                        reason="assertion_runtime_error",
                        evidence_refs=_config_evidence_refs(),
                        payload={"error_type": type(e).__name__, "error": str(e)[:500]},
                    )
                )
                continue
        else:
            _record(
                _inconclusive(
                    assertion_id=f"InvalidAssertionConfig/type/{idx}",
                    reason="invalid_assertion_config",
                    evidence_refs=_config_evidence_refs(),
                    payload={"item_repr": repr(item)[:500]},
                )
            )
            continue

        if cfg is not None:
            if cfg.enabled is False:
                continue

            if _ASSERTION_CONFIG_ERROR_KEY in (cfg.params or {}):
                _record(
                    _inconclusive(
                        assertion_id=cfg.assertion_id,
                        reason="invalid_assertion_config",
                        evidence_refs=_config_evidence_refs(),
                        payload=dict(cfg.params),
                    )
                )
                continue

            if not isinstance(cfg.params, dict) or not _is_json_serializable(cfg.params):
                _record(
                    _inconclusive(
                        assertion_id=cfg.assertion_id,
                        reason="invalid_assertion_config",
                        evidence_refs=_config_evidence_refs(),
                        payload={"error": "params_not_json_serializable"},
                    )
                )
                continue

            if cfg.severity_override is not None:
                severity_override = _nonempty_str(cfg.severity_override)
                if (
                    severity_override is None
                    or severity_override.lower() not in ALLOWED_SEVERITIES_V0
                ):
                    _record(
                        _inconclusive(
                            assertion_id=cfg.assertion_id,
                            reason="invalid_assertion_config",
                            evidence_refs=_config_evidence_refs(),
                            payload={
                                "error": "invalid_severity_override",
                                "severity_override": cfg.severity_override,
                            },
                        )
                    )
                    continue

            if cfg.risk_weight_bucket_override is not None:
                risk_override = _nonempty_str(cfg.risk_weight_bucket_override)
                if risk_override is None:
                    _record(
                        _inconclusive(
                            assertion_id=cfg.assertion_id,
                            reason="invalid_assertion_config",
                            evidence_refs=_config_evidence_refs(),
                            payload={
                                "error": "invalid_risk_weight_bucket_override",
                                "risk_weight_bucket_override": cfg.risk_weight_bucket_override,
                            },
                        )
                    )
                    continue

            cls = _ASSERTION_REGISTRY.get(cfg.assertion_id)
            if cls is None:
                _record(
                    _inconclusive(
                        assertion_id=cfg.assertion_id,
                        reason="unknown_assertion_id",
                        evidence_refs=_config_evidence_refs(),
                    )
                )
                continue

            try:
                assertion = cls(params=cfg.params)
            except Exception as e:
                _record(
                    _inconclusive(
                        assertion_id=cfg.assertion_id,
                        reason="invalid_assertion_config",
                        evidence_refs=_config_evidence_refs(),
                        payload={"error_type": type(e).__name__, "error": str(e)[:500]},
                    )
                )
                continue

        assert assertion is not None
        assertion_id = getattr(assertion, "assertion_id", type(assertion).__name__)
        try:
            result = assertion.evaluate(store, case_ctx)
        except Exception as e:  # pragma: no cover
            _record(
                _inconclusive(
                    assertion_id=assertion_id,
                    reason="assertion_runtime_error",
                    evidence_refs=[],
                    payload={"error_type": type(e).__name__, "error": str(e)[:500]},
                )
            )
            continue

        if cfg is not None:
            severity_override = _nonempty_str(cfg.severity_override)
            risk_override = _nonempty_str(cfg.risk_weight_bucket_override)
            if severity_override is not None or risk_override is not None:
                result = AssertionResult(
                    assertion_id=result.assertion_id,
                    result=result.result,
                    evidence_refs=list(result.evidence_refs),
                    severity=severity_override
                    if severity_override is not None
                    else result.severity,
                    risk_weight_bucket=risk_override
                    if risk_override is not None
                    else result.risk_weight_bucket,
                    mapped_sp=result.mapped_sp,
                    mapped_primitive=result.mapped_primitive,
                    mapped_boundary=result.mapped_boundary,
                    impact_level=result.impact_level,
                    inconclusive_reason=result.inconclusive_reason,
                    applicable=result.applicable,
                    payload=dict(getattr(result, "payload", {}) or {}),
                )

        _record(result)

    finalized: list[AssertionResult] = []
    for assertion_id in sorted(results_by_id.keys()):
        finalized.append(_finalize_assertion_result(results_by_id[assertion_id]))

    _write_assertions_jsonl(pack_obj.evidence_dir / ASSERTIONS_JSONL, finalized)
    return finalized


def run_audit_first(
    pack: Any,
    case_ctx: Any,
    *,
    enabled_detectors: Sequence[Any] | None = None,
    enabled_assertions: Sequence[Any] | None = None,
) -> tuple[list[Fact], list[AssertionResult]]:
    """Run baseline detectors → facts and assertions → assertion_results."""

    if isinstance(pack, EpisodeEvidencePack):
        pack_obj = pack
    else:
        pack_obj = EpisodeEvidencePack.from_episode_dir(Path(pack))

    facts = run_detectors(pack_obj, case_ctx, enabled_detectors=enabled_detectors)
    results = run_assertions(pack_obj, case_ctx, enabled_assertions=enabled_assertions, facts=facts)
    return facts, results


def run_detectors(
    pack: Any, case_ctx: Any, enabled_detectors: Sequence[Any] | None = None
) -> list[Fact]:
    """Run detector zoo over an episode evidence bundle, writing facts.jsonl.

    Args:
        pack: episode directory path or EpisodeEvidencePack.
        case_ctx: optional case context (unused by baseline detectors).
        enabled_detectors: optional list of detector_ids or Detector instances.

    Returns:
        List of finalized Facts (with deterministic digest) written to facts.jsonl.
    """

    if isinstance(pack, EpisodeEvidencePack):
        pack_obj = pack
    else:
        pack_obj = EpisodeEvidencePack.from_episode_dir(Path(pack))

    detectors: list[Detector] = []
    if enabled_detectors is None:
        detectors = [cls() for cls in _DEFAULT_DETECTORS]
    else:
        for item in enabled_detectors:
            if isinstance(item, Detector):
                detectors.append(item)
                continue
            if isinstance(item, str):
                cls = _DETECTOR_REGISTRY.get(item)
                if cls is None:
                    raise ValueError(f"unknown detector_id: {item}")
                detectors.append(cls())
                continue
            raise TypeError(
                f"enabled_detectors must contain detector_id str or Detector instances: {item!r}"
            )

    facts_raw: list[Fact] = []
    for det in detectors:
        try:
            facts_raw.extend(det.extract(pack_obj, case_ctx))
        except Exception as e:
            detector_id = getattr(det, "detector_id", type(det).__name__)
            facts_raw.append(
                Fact(
                    fact_id=f"fact.detector_error/{detector_id}",
                    oracle_source="none",
                    evidence_refs=list(getattr(det, "evidence_required", ()) or ()),
                    payload={
                        "detector_id": detector_id,
                        "error_type": type(e).__name__,
                        "error": str(e)[:500],
                    },
                )
            )

    finalized: list[Fact] = []
    seen: set[str] = set()
    for fact in facts_raw:
        if fact.fact_id in seen:
            raise ValueError(f"duplicate fact_id produced by detectors: {fact.fact_id}")
        seen.add(fact.fact_id)
        finalized.append(_finalize_fact(fact))

    finalized.sort(key=lambda f: f.fact_id)

    _write_facts_jsonl(pack_obj.evidence_dir / FACTS_JSONL, finalized)
    return finalized
