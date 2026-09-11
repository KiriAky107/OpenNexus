"""OpenNexus 第三阶段生产验收 ID 的默认拒绝型执行器。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
VAULT_ROOT = (ROOT / "backend" / "data" / "vault").resolve()
SCHEMA = 1
CASE_SUITES = {
    "sidecar": tuple(f"A-{number:02d}" for number in range(1, 5)),
    "credentials": tuple(f"B-{number:02d}" for number in range(1, 5)),
    "sandbox": tuple(f"C-{number:02d}" for number in range(1, 5)),
    "extensions": tuple(f"D-{number:02d}" for number in range(1, 5)),
    "sync-client": ("S-01", "S-02", "S-03", "S-08"),
    "sync-service": ("S-04", "S-05", "S-06", "S-07", "S-09"),
    "e2e": tuple(f"E-{number:02d}" for number in range(1, 6)),
}
ALL_CASES = tuple(case for cases in CASE_SUITES.values() for case in cases)
# 只有在此注册了仓库自有驱动的案例才能执行；组件或单元测试命令不计入生产验收。
CASE_DRIVERS: dict[str, dict[str, Any]] = {
    "A-02": {
        "driver": "scripts/acceptance_cases/a02_sidecar.py",
        "timeout_seconds": 900,
        "required_metrics": (),
    },
    "A-03": {
        "driver": "scripts/acceptance_cases/a03_transport.py",
        "timeout_seconds": 900,
        "required_metrics": (),
    },
    "B-01": {
        "driver": "scripts/acceptance_cases/b01_credentials.py",
        "timeout_seconds": 900,
        "required_metrics": (),
    },
    "B-02": {
        "driver": "scripts/acceptance_cases/b02_credentials.py",
        "timeout_seconds": 900,
        "required_metrics": (),
    },
    "B-03": {
        "driver": "scripts/acceptance_cases/b03_credentials.py",
        "timeout_seconds": 900,
        "required_metrics": (
            "lock_deadline_ms",
            "native_lock_events",
            "manual_lock_events",
            "restored_records",
            "local_edit_successes",
        ),
        "platform_profiles": ("windows-11-x64",),
    },
    "B-04": {
        "driver": "scripts/acceptance_cases/b04_credentials.py",
        "timeout_seconds": 1800,
        "required_metrics": (
            "migration_boundaries",
            "hard_terminations",
            "recovered_records",
            "duplicate_records",
            "cleanup_boundaries",
            "managed_artifacts_remaining",
            "external_key_changes",
            "old_writer_rejections",
            "native_confirmation_boundary",
        ),
        "platform_profiles": ("windows-11-x64",),
    },
    "C-03": {
        "driver": "scripts/acceptance_cases/c03_permission_binding.py",
        "timeout_seconds": 900,
        "required_metrics": (
            "bound_claim_fields",
            "python_bypass_rejections",
            "restart_rejections",
        ),
        "platform_profiles": ("windows-11-x64",),
    },
    "C-02": {
        "driver": "scripts/acceptance_cases/c02_sandbox.py",
        "timeout_seconds": 900,
        "required_metrics": (
            "shell_argument_rounds",
            "environment_injection_rounds",
            "child_escape_rounds",
            "link_race_rounds",
            "dns_rebinding_rounds",
            "redirect_rounds",
            "authorized_file_reads",
            "authorized_tool_calls",
            "authorized_https_calls",
        ),
        "platform_profiles": ("windows-11-x64",),
    },
    "C-04": {
        "driver": "scripts/acceptance_cases/c04_resources.py",
        "timeout_seconds": 900,
        "required_metrics": (
            "memory_limit_bytes",
            "scratch_limit_bytes",
            "process_limit",
            "tool_deadline_seconds",
            "cleanup_deadline_ms",
            "broker_requests_per_second",
            "resource_failures_verified",
        ),
        "platform_profiles": ("windows-11-x64",),
    },
    "D-01": {
        "driver": "scripts/acceptance_cases/d01_extensions.py",
        "timeout_seconds": 900,
        "required_metrics": (),
    },
    "D-02": {
        "driver": "scripts/acceptance_cases/d02_extension_migration.py",
        "timeout_seconds": 900,
        "required_metrics": (
            "legacy_groups",
            "migration_rounds",
            "imported_records",
            "duplicate_records",
            "external_directory_changes",
            "inherited_permissions",
            "legacy_write_rejections",
        ),
        "platform_profiles": ("windows-11-x64",),
    },
    "D-03": {
        "driver": "scripts/acceptance_cases/d03_extension_transactions.py",
        "timeout_seconds": 900,
        "required_metrics": (
            "power_cut_rounds",
            "disk_full_rounds",
            "configuration_failure_rounds",
            "dependency_preflight_rejections",
            "permission_expansions",
        ),
        "platform_profiles": ("windows-11-x64",),
    },
    "D-04": {
        "driver": "scripts/acceptance_cases/d04_extension_lifecycle.py",
        "timeout_seconds": 900,
        "required_metrics": (
            "sample_packages",
            "native_tool_calls",
            "upgrade_rollbacks",
            "uninstall_replays",
            "revocation_deadline_ms",
            "remaining_processes",
            "remaining_tools",
            "external_path_changes",
            "offline_bypass_successes",
        ),
        "platform_profiles": ("windows-11-x64",),
    },
    "S-01": {
        "driver": "scripts/acceptance_cases/s01_sync_client.py",
        "timeout_seconds": 900,
        "required_metrics": (),
    },
    "S-02": {
        "driver": "scripts/acceptance_cases/s02_sync_client.py",
        "timeout_seconds": 900,
        "required_metrics": (),
    },
    "S-03": {
        "driver": "scripts/acceptance_cases/s03_sync_client.py",
        "timeout_seconds": 900,
        "required_metrics": (),
    },
    "S-08": {
        "driver": "scripts/acceptance_cases/s08_sync_client.py",
        "timeout_seconds": 900,
        "required_metrics": (),
    },
    "S-04": {
        "driver": "scripts/acceptance_cases/s04_sync_service.py",
        "timeout_seconds": 900,
        "required_metrics": ("successful_commits", "conflict_responses", "worker_count"),
        "required_artifacts": ("postgres_initdb", "minio_server"),
    },
    "S-05": {
        "driver": "scripts/acceptance_cases/s05_sync_uploads.py",
        "timeout_seconds": 900,
        "required_metrics": (
            "offset_races",
            "response_loss_recoveries",
            "cleanup_races",
            "max_cleanup_latency_ms",
            "worker_count",
        ),
        "required_artifacts": ("postgres_initdb", "minio_server"),
    },
    "S-06": {
        "driver": "scripts/acceptance_cases/s06_sync_security.py",
        "timeout_seconds": 900,
        "required_metrics": (
            "hash_rejections",
            "revoked_rejections",
            "expired_token_rejections",
            "upload_link_rejections",
            "rate_limited_responses",
            "ready_failure_max_ms",
            "ready_recovery_max_ms",
            "worker_count",
        ),
        "required_artifacts": ("postgres_initdb", "minio_server"),
    },
    "S-07": {
        "driver": "scripts/acceptance_cases/s07_sync_backup.py",
        "timeout_seconds": 3600,
        "required_metrics": (
            "file_count",
            "object_count",
            "object_bytes",
            "verified_objects",
            "rto_ms",
            "backup_age_seconds",
            "initialization_runs",
            "migration_failures",
        ),
        "required_artifacts": ("postgres_initdb", "minio_server"),
    },
    "S-09": {
        "driver": "scripts/acceptance_cases/s09_sync_performance.py",
        "timeout_seconds": 3000,
        "required_metrics": (
            "load_requests",
            "api_p95_ms",
            "unexpected_5xx_rate",
            "validated_commits",
            "service_peak_rss_bytes",
            "upload_verified",
            "initial_sync_ms",
            "initial_verified_files",
            "convergence_p95_ms",
            "worker_count",
            "client_count",
        ),
        "required_artifacts": ("postgres_initdb", "minio_server"),
    },
}
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{2,127}")
RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}")
SENSITIVE_KEY = re.compile(r"(?:password|passwd|secret|token|api[_-]?key|credential)", re.I)
MAX_LOG_BYTES = 10 * 1024 * 1024


class AcceptanceError(ValueError):
    """一个稳定的、用户可操作的运行器配置错误。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_plain_secret(value: Any, path: tuple[str, ...] = ()) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            nested = path + (str(key),)
            if SENSITIVE_KEY.search(str(key)) and str(key) != "secret_env" and (not path or path[-1] != "secret_env"):
                return True
            if _contains_plain_secret(item, nested):
                return True
    elif isinstance(value, list):
        return any(_contains_plain_secret(item, path) for item in value)
    return False


def _resolve_data_root(config: dict[str, Any], environ: dict[str, str]) -> Path:
    direct = config.get("data_root")
    reference = config.get("data_root_env")
    if bool(direct) == bool(reference):
        raise AcceptanceError("CONFIG_DATA_ROOT_REQUIRED: set exactly one of data_root or data_root_env")
    if reference:
        if not isinstance(reference, str) or not ENV_NAME.fullmatch(reference):
            raise AcceptanceError("CONFIG_DATA_ROOT_ENV_INVALID")
        direct = environ.get(reference)
        if not direct:
            raise AcceptanceError(f"CONFIG_ENV_MISSING: {reference}")
    path = Path(str(direct)).expanduser()
    if not path.is_absolute():
        raise AcceptanceError("CONFIG_DATA_ROOT_NOT_ABSOLUTE")
    resolved = path.resolve(strict=False)
    if resolved == ROOT or _inside(ROOT, resolved):
        raise AcceptanceError("CONFIG_DATA_ROOT_CONTAINS_REPOSITORY")
    if _inside(resolved, VAULT_ROOT) or _inside(VAULT_ROOT, resolved):
        raise AcceptanceError("CONFIG_PERSONAL_VAULT_FORBIDDEN")
    if _inside(resolved, (ROOT / ".git").resolve()):
        raise AcceptanceError("CONFIG_GIT_DIRECTORY_FORBIDDEN")
    if path.exists() and path.is_symlink():
        raise AcceptanceError("CONFIG_DATA_ROOT_SYMLINK_FORBIDDEN")
    return resolved


def load_config(path: Path, environ: dict[str, str] | None = None) -> dict[str, Any]:
    environ = os.environ if environ is None else environ
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AcceptanceError("CONFIG_UNREADABLE") from error
    if not isinstance(config, dict) or config.get("schema") != SCHEMA:
        raise AcceptanceError("CONFIG_SCHEMA_UNSUPPORTED")
    if _contains_plain_secret(config):
        raise AcceptanceError("CONFIG_PLAINTEXT_SECRET_FORBIDDEN")
    allowed = {
        "schema", "run_id", "isolated", "allow_destructive", "platform_profile",
        "data_root", "data_root_env", "seed", "allow_http_test_services",
        "service_urls", "artifacts", "secret_env",
    }
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise AcceptanceError(f"CONFIG_FIELD_UNKNOWN: {','.join(unknown)}")
    if config.get("isolated") is not True or config.get("allow_destructive") is not True:
        raise AcceptanceError("CONFIG_ISOLATION_CONFIRMATION_REQUIRED")
    run_id = config.get("run_id")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id) or run_id.startswith("replace-"):
        raise AcceptanceError("CONFIG_RUN_ID_INVALID")
    profile = config.get("platform_profile")
    if not isinstance(profile, str) or not profile.strip():
        raise AcceptanceError("CONFIG_PLATFORM_PROFILE_REQUIRED")
    seed = config.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed <= 2**63 - 1:
        raise AcceptanceError("CONFIG_SEED_INVALID")
    secret_env = config.get("secret_env", {})
    if not isinstance(secret_env, dict):
        raise AcceptanceError("CONFIG_SECRET_ENV_INVALID")
    for alias, reference in secret_env.items():
        if not isinstance(alias, str) or not isinstance(reference, str) or not ENV_NAME.fullmatch(reference):
            raise AcceptanceError("CONFIG_SECRET_ENV_INVALID")
        if not environ.get(reference):
            raise AcceptanceError(f"CONFIG_ENV_MISSING: {reference}")
    urls = config.get("service_urls", {})
    if not isinstance(urls, dict):
        raise AcceptanceError("CONFIG_SERVICE_URLS_INVALID")
    for name, value in urls.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise AcceptanceError("CONFIG_SERVICE_URL_INVALID")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise AcceptanceError("CONFIG_SERVICE_URL_INVALID")
        if parsed.query or parsed.fragment:
            raise AcceptanceError("CONFIG_SERVICE_URL_INVALID")
        if parsed.scheme == "http" and config.get("allow_http_test_services") is not True:
            raise AcceptanceError("CONFIG_HTTP_SERVICE_REQUIRES_TEST_FLAG")
    artifacts = config.get("artifacts", {})
    if not isinstance(artifacts, dict) or any(not isinstance(name, str) or not isinstance(value, str) for name, value in artifacts.items()):
        raise AcceptanceError("CONFIG_ARTIFACTS_INVALID")
    normalized = dict(config)
    normalized["data_root"] = str(_resolve_data_root(config, environ))
    normalized.pop("data_root_env", None)
    return normalized


def prepare_isolated_root(config: dict[str, Any]) -> Path:
    root = Path(config["data_root"])
    marker = root / ".opennexus-acceptance-isolated.json"
    expected = {"schema": SCHEMA, "run_id": config["run_id"]}
    if root.exists():
        if root.is_symlink():
            raise AcceptanceError("CONFIG_DATA_ROOT_SYMLINK_FORBIDDEN")
        if not root.is_dir():
            raise AcceptanceError("CONFIG_DATA_ROOT_NOT_DIRECTORY")
        children = list(root.iterdir())
        if children:
            if not marker.is_file():
                raise AcceptanceError("CONFIG_DATA_ROOT_NOT_EMPTY_OR_MARKED")
            try:
                actual = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise AcceptanceError("CONFIG_ISOLATION_MARKER_INVALID") from error
            if actual != expected:
                raise AcceptanceError("CONFIG_ISOLATION_MARKER_MISMATCH")
    else:
        root.mkdir(parents=True)
    marker.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return root


def select_cases(suite: str, case: str | None) -> tuple[str, ...]:
    selected = ALL_CASES if suite == "all" else CASE_SUITES[suite]
    if case is None:
        return selected
    normalized = case.upper()
    if normalized not in ALL_CASES:
        raise AcceptanceError("CASE_UNKNOWN")
    if normalized not in selected:
        raise AcceptanceError("CASE_NOT_IN_SUITE")
    return (normalized,)


def _redactor(config: dict[str, Any], report_root: Path, environ: dict[str, str]):
    replacements = [
        (str(ROOT), "$REPO"),
        (config["data_root"], "$DATA"),
        (str(report_root), "$REPORT"),
        (str(Path.home()), "$HOME"),
    ]
    for reference in config.get("secret_env", {}).values():
        value = environ.get(reference, "")
        if value:
            replacements.append((value, "[REDACTED]"))

    def redact(text: str) -> str:
        for source, target in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
            text = text.replace(source, target).replace(source.replace("\\", "/"), target)
        return text

    return redact


def _base_result(case_id: str, status: str, reason: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "case_id": case_id,
        "status": status,
        "reason": reason,
        "assertions": [],
        "metrics": {
            "peak_rss_bytes": None,
            "max_process_count": None,
            "denied_access_count": None,
        },
        "files": [],
        "revisions": [],
        "command": None,
    }


def _has_administrator_permission() -> bool:
    if os.name == "nt":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _validate_driver_result(case_id: str, result: Any, required_metrics: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    if not isinstance(result, dict) or result.get("schema") != SCHEMA:
        return ["RESULT_SCHEMA_INVALID"]
    if result.get("case_id") != case_id:
        errors.append("RESULT_CASE_ID_MISMATCH")
    if result.get("status") not in {"PASSED", "FAILED", "SKIPPED", "NOT_APPLICABLE"}:
        errors.append("RESULT_STATUS_INVALID")
    assertions = result.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        errors.append("RESULT_ASSERTIONS_MISSING")
    elif any(not isinstance(item, dict) or item.get("status") not in {"PASSED", "FAILED"} for item in assertions):
        errors.append("RESULT_ASSERTION_INVALID")
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("RESULT_METRICS_MISSING")
    else:
        for name in required_metrics:
            value = metrics.get(name)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                errors.append(f"RESULT_METRIC_MISSING:{name}")
    for name in ("files", "revisions"):
        if not isinstance(result.get(name), list):
            errors.append(f"RESULT_{name.upper()}_INVALID")
    return errors


def run_case(
    case_id: str,
    config_path: Path,
    config: dict[str, Any],
    report_root: Path,
    environ: dict[str, str] | None = None,
    drivers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    environ = dict(os.environ if environ is None else environ)
    drivers = CASE_DRIVERS if drivers is None else drivers
    started = time.monotonic()
    started_utc = _utc_now()
    definition = drivers.get(case_id)
    if definition is None:
        result = _base_result(case_id, "NOT_IMPLEMENTED", "No repository-owned production acceptance driver is registered.")
        result.update({"started_utc": started_utc, "finished_utc": _utc_now(), "duration_ms": 0, "exit_code": None})
        return result
    if definition.get("requires_admin") and not _has_administrator_permission():
        result = _base_result(case_id, "BLOCKED", "Administrator permission required by this acceptance case is unavailable.")
        result.update({"started_utc": started_utc, "finished_utc": _utc_now(), "duration_ms": 0, "exit_code": None})
        return result
    required_platforms = tuple(definition.get("platform_profiles", ()))
    if required_platforms and config["platform_profile"] not in required_platforms:
        result = _base_result(case_id, "BLOCKED", "Configured platform profile is not supported by this acceptance driver.")
        result["supported_platform_profiles"] = list(required_platforms)
        result.update({"started_utc": started_utc, "finished_utc": _utc_now(), "duration_ms": 0, "exit_code": None})
        return result
    driver = (ROOT / str(definition["driver"])).resolve()
    if not _inside(driver, ROOT) or not driver.is_file():
        result = _base_result(case_id, "BLOCKED", "Registered acceptance driver is missing or outside the repository.")
        result.update({"started_utc": started_utc, "finished_utc": _utc_now(), "duration_ms": 0, "exit_code": None})
        return result
    missing = [name for name in definition.get("required_artifacts", ()) if not Path(config.get("artifacts", {}).get(name, "")).is_file()]
    if missing:
        result = _base_result(case_id, "BLOCKED", "Required artifacts are missing.")
        result["missing_artifacts"] = missing
        result.update({"started_utc": started_utc, "finished_utc": _utc_now(), "duration_ms": 0, "exit_code": None})
        return result
    missing_secrets = [name for name in definition.get("required_secret_env", ()) if name not in config.get("secret_env", {})]
    if missing_secrets:
        result = _base_result(case_id, "BLOCKED", "Required secret environment references are missing.")
        result["missing_secret_env"] = missing_secrets
        result.update({"started_utc": started_utc, "finished_utc": _utc_now(), "duration_ms": 0, "exit_code": None})
        return result
    result_path = report_root / "driver-results" / f"{case_id}.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(driver), "--config", str(config_path), "--output", str(result_path)]
    child_env = dict(environ)
    child_env.update({
        "OPENNEXUS_ACCEPTANCE_CASE_ID": case_id,
        "OPENNEXUS_ACCEPTANCE_DATA_ROOT": config["data_root"],
        "OPENNEXUS_ACCEPTANCE_REPORT_ROOT": str(report_root),
    })
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=child_env,
            capture_output=True,
            text=True,
            timeout=int(definition.get("timeout_seconds", 1800)),
            check=False,
        )
        exit_code = completed.returncode
        output = completed.stdout + ("\n" if completed.stdout and completed.stderr else "") + completed.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        exit_code = None
        output = (error.stdout or "") + (error.stderr or "")
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
    redact = _redactor(config, report_root, environ)
    encoded = redact(output).encode("utf-8", errors="replace")[:MAX_LOG_BYTES]
    (report_root / "logs").mkdir(exist_ok=True)
    (report_root / "logs" / f"{case_id}.log").write_bytes(encoded)
    if timed_out:
        result = _base_result(case_id, "FAILED", "Acceptance driver timed out.")
    elif not result_path.is_file():
        result = _base_result(case_id, "FAILED", "Acceptance driver did not produce a result.")
    else:
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result = _base_result(case_id, "FAILED", "Acceptance driver result is unreadable.")
    errors = _validate_driver_result(case_id, result, tuple(definition.get("required_metrics", ())))
    if not isinstance(result, dict):
        result = _base_result(case_id, "FAILED", "Acceptance driver result must be a JSON object.")
    if errors or exit_code != 0 or result.get("status") != "PASSED":
        if result.get("status") == "PASSED":
            result["status"] = "FAILED"
        result["runner_errors"] = errors + ([] if exit_code in {0, None} else ["DRIVER_EXIT_NONZERO"])
    serialized = json.dumps(result, ensure_ascii=False)
    leaked = [reference for reference in config.get("secret_env", {}).values() if environ.get(reference) and environ[reference] in serialized]
    if leaked:
        result = _base_result(case_id, "FAILED", "Acceptance result contained configured secret material.")
        result["runner_errors"] = ["RESULT_SECRET_LEAK"]
    result.update({
        "started_utc": started_utc,
        "finished_utc": _utc_now(),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "exit_code": exit_code,
        "log": f"logs/{case_id}.log",
        "command": [
            "python", str(definition["driver"]), "--config", "$CONFIG",
            "--output", f"$REPORT/driver-results/{case_id}.json",
        ],
    })
    return result


def _write_junit(path: Path, results: list[dict[str, Any]], elapsed: float) -> None:
    failures = sum(result["status"] != "PASSED" for result in results)
    suite = ElementTree.Element("testsuite", {
        "name": "OpenNexus phase3 production acceptance",
        "tests": str(len(results)),
        "failures": str(failures),
        "errors": "0",
        "skipped": "0",
        "time": f"{elapsed:.3f}",
    })
    for result in results:
        case = ElementTree.SubElement(suite, "testcase", {
            "classname": "phase3.production",
            "name": result["case_id"],
            "time": f"{result.get('duration_ms', 0) / 1000:.3f}",
        })
        if result["status"] != "PASSED":
            failure = ElementTree.SubElement(case, "failure", {"type": result["status"]})
            failure.text = str(result.get("reason") or ",".join(result.get("runner_errors", ())))
    ElementTree.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def _repository_evidence(config: dict[str, Any]) -> dict[str, Any]:
    locks = {}
    for relative in ("backend/uv.lock", "frontend/pnpm-lock.yaml", "frontend/src-tauri/Cargo.lock", "server sync/uv.lock", "community-server/uv.lock"):
        path = ROOT / relative
        if path.is_file():
            locks[relative] = _sha256(path)
    artifacts = {}
    for name, raw in config.get("artifacts", {}).items():
        path = Path(raw)
        artifacts[name] = _sha256(path) if path.is_file() else None
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
    return {"commit": commit or None, "lock_sha256": locks, "artifact_sha256": artifacts}


def repository_changes() -> tuple[str, ...]:
    paths: set[str] = set()
    commands = (
        ["git", "diff", "--name-only", "-z", "--", ".", ":(exclude)backend/data/vault"],
        ["git", "diff", "--cached", "--name-only", "-z", "--", ".", ":(exclude)backend/data/vault"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    )
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, check=False)
        if completed.returncode != 0:
            raise AcceptanceError("SOURCE_STATE_UNAVAILABLE")
        for raw in completed.stdout.split(b"\0"):
            if not raw:
                continue
            path = raw.decode("utf-8", errors="replace").replace("\\", "/")
            if path != "backend/data/vault" and not path.startswith("backend/data/vault/"):
                paths.add(path)
    return tuple(sorted(paths))


def execute(args: argparse.Namespace, environ: dict[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    if args.list_cases:
        payload = {suite: list(cases) for suite, cases in CASE_SUITES.items()}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else "\n".join(f"{suite}: {', '.join(cases)}" for suite, cases in CASE_SUITES.items()))
        return 0
    if not args.suite or not args.config or not args.report_dir:
        raise AcceptanceError("ARGUMENTS_REQUIRED")
    selected = select_cases(args.suite, args.case)
    config_path = Path(args.config).resolve()
    config = load_config(config_path, environ)
    changes = repository_changes()
    if changes:
        preview = ",".join(changes[:10])
        raise AcceptanceError(f"SOURCE_TREE_DIRTY: {preview}")
    if args.suite == "all" and not config.get("platform_profile"):
        raise AcceptanceError("CONFIG_PLATFORM_PROFILE_REQUIRED")
    prepare_isolated_root(config)
    report_root = Path(args.report_dir).resolve(strict=False)
    if _inside(report_root, VAULT_ROOT) or _inside(VAULT_ROOT, report_root):
        raise AcceptanceError("REPORT_PERSONAL_VAULT_FORBIDDEN")
    if report_root.exists() and (report_root.is_symlink() or not report_root.is_dir()):
        raise AcceptanceError("REPORT_DIRECTORY_INVALID")
    if report_root.exists() and any(report_root.iterdir()):
        raise AcceptanceError("REPORT_DIRECTORY_NOT_EMPTY")
    report_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    results = [run_case(case_id, config_path, config, report_root, environ) for case_id in selected]
    cases_root = report_root / "cases"
    cases_root.mkdir()
    for result in results:
        (cases_root / f"{result['case_id']}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elapsed = time.monotonic() - started
    _write_junit(report_root / "junit.xml", results, elapsed)
    manifest = {"schema": SCHEMA, "suites": {name: list(cases) for name, cases in CASE_SUITES.items()}, "all_cases": list(ALL_CASES)}
    if len(ALL_CASES) != 30 or len(set(ALL_CASES)) != 30:
        raise AcceptanceError("CASE_MANIFEST_INCOMPLETE")
    (report_root / "case-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = sum(result["status"] == "PASSED" for result in results)
    summary = {
        "schema": SCHEMA,
        "run_id": config["run_id"],
        "suite": args.suite,
        "platform_profile": config["platform_profile"],
        "status": "PASSED" if passed == len(results) else "NOT_PASSED",
        "selected": list(selected),
        "passed": passed,
        "failed": len(results) - passed,
        "duration_ms": round(elapsed * 1000),
        "generated_utc": _utc_now(),
        "config_sha256": _sha256(config_path),
        "contains_credentials": False,
        **_repository_evidence(config),
    }
    (report_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": summary["status"], "report_dir": str(report_root), "passed": passed, "total": len(results)}, ensure_ascii=False))
    return 0 if summary["status"] == "PASSED" else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--suite", choices=(*CASE_SUITES, "all"))
    result.add_argument("--case")
    result.add_argument("--config")
    result.add_argument("--report-dir")
    result.add_argument("--list-cases", action="store_true")
    result.add_argument("--json", action="store_true", help="Use JSON with --list-cases")
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        return execute(parser().parse_args(argv))
    except AcceptanceError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
