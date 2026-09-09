from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("phase3_acceptance", ROOT / "scripts" / "phase3_acceptance.py")
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def config(path: Path, data_root: Path, **changes) -> Path:
    payload = {
        "schema": 1,
        "run_id": "runner-test-001",
        "isolated": True,
        "allow_destructive": True,
        "platform_profile": "windows-11-x64",
        "data_root": str(data_root),
        "seed": 20260908,
        "service_urls": {},
        "artifacts": {},
        "secret_env": {},
    }
    payload.update(changes)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_contains_every_documented_case_once():
    assert len(runner.ALL_CASES) == 30
    assert len(set(runner.ALL_CASES)) == 30
    assert runner.select_cases("sync-client", "s-08") == ("S-08",)
    with pytest.raises(runner.AcceptanceError, match="CASE_NOT_IN_SUITE"):
        runner.select_cases("sidecar", "S-08")


def test_config_rejects_personal_vault_plaintext_secrets_and_unconfirmed_roots(tmp_path):
    with pytest.raises(runner.AcceptanceError, match="PERSONAL_VAULT"):
        runner.load_config(config(tmp_path / "vault.json", runner.VAULT_ROOT))
    with pytest.raises(runner.AcceptanceError, match="PLAINTEXT_SECRET"):
        runner.load_config(config(tmp_path / "secret.json", tmp_path / "data", password="do-not-store-this"))
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep", encoding="utf-8")
    loaded = runner.load_config(config(tmp_path / "existing.json", existing))
    with pytest.raises(runner.AcceptanceError, match="NOT_EMPTY_OR_MARKED"):
        runner.prepare_isolated_root(loaded)
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_missing_driver_is_a_junit_failure_and_never_a_skip(tmp_path):
    data_root = tmp_path / "isolated"
    config_path = config(tmp_path / "config.json", data_root)
    report = tmp_path / "report"
    args = Namespace(
        suite="sidecar", case="A-01", config=str(config_path), report_dir=str(report),
        list_cases=False, json=False,
    )
    assert runner.execute(args, {}) == 1
    result = json.loads((report / "cases" / "A-01.json").read_text(encoding="utf-8"))
    summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
    junit = (report / "junit.xml").read_text(encoding="utf-8")
    assert result["status"] == "NOT_IMPLEMENTED"
    assert summary["status"] == "NOT_PASSED"
    assert summary["contains_credentials"] is False
    assert '<failure type="NOT_IMPLEMENTED">' in junit
    assert "skipped=\"0\"" in junit


def test_driver_result_must_supply_assertions_metrics_and_zero_exit(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text("", encoding="utf-8")
    original_root = runner.ROOT
    try:
        runner.ROOT = tmp_path
        case = {
            "driver": "driver.py",
            "required_metrics": ("peak_rss_bytes",),
        }
        result_root = tmp_path / "result"
        result_root.mkdir()
        result = runner.run_case("A-01", tmp_path / "config.json", {"data_root": str(tmp_path / "data"), "secret_env": {}}, result_root, {}, {"A-01": case})
        assert result["status"] == "FAILED"
        assert "RESULT_ASSERTIONS_MISSING" in result["runner_errors"]
        assert "RESULT_METRIC_MISSING:peak_rss_bytes" in result["runner_errors"]
    finally:
        runner.ROOT = original_root


def test_valid_driver_passes_and_its_log_is_redacted(tmp_path):
    driver = tmp_path / "driver.py"
    driver.write_text(
        """import argparse, json, os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--config');p.add_argument('--output');a=p.parse_args()
print(os.environ['TEST_ACCEPTANCE_SECRET'])
Path(a.output).write_text(json.dumps({
 'schema':1,'case_id':os.environ['OPENNEXUS_ACCEPTANCE_CASE_ID'],'status':'PASSED','reason':'',
 'assertions':[{'name':'independent oracle','status':'PASSED','evidence':'fixture'}],
 'metrics':{'peak_rss_bytes':123,'max_process_count':1,'denied_access_count':0},
 'files':[],'revisions':[]}), encoding='utf-8')
""",
        encoding="utf-8",
    )
    original_root = runner.ROOT
    try:
        runner.ROOT = tmp_path
        result_root = tmp_path / "result"
        result_root.mkdir()
        result = runner.run_case(
            "A-01",
            tmp_path / "config.json",
            {
                "data_root": str(tmp_path / "data"), "secret_env": {"fixture": "TEST_ACCEPTANCE_SECRET"},
                "platform_profile": "windows-11-x64", "artifacts": {},
            },
            result_root,
            {"TEST_ACCEPTANCE_SECRET": "planted-secret-value"},
            {"A-01": {"driver": "driver.py", "required_metrics": ("peak_rss_bytes",)}},
        )
        assert result["status"] == "PASSED"
        log = (result_root / "logs" / "A-01.log").read_text(encoding="utf-8")
        assert "planted-secret-value" not in log
        assert "[REDACTED]" in log
    finally:
        runner.ROOT = original_root
