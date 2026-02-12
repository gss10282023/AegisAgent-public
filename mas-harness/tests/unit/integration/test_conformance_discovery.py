from pathlib import Path

from mas_harness.integration.conformance.suite import discover_cases


def test_conformance_cases_discover_and_validate() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    cases_dir = repo_root / "mas-conformance" / "cases"
    schemas_dir = repo_root / "mas-spec" / "schemas"

    cases = discover_cases(cases_dir=cases_dir, schemas_dir=schemas_dir)
    assert any(c.case_id == "conf_001_open_settings" for c in cases)
