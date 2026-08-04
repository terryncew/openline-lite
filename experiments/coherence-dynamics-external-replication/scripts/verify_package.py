from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "REPLICATION_PROTOCOL.json",
    "EXTERNAL_SCHEMA_REPAIR_RECEIPT.json",
    "RESULT_SERIALIZATION_REPAIR_RECEIPT.json",
    "SOURCE_PROFILE_LOCK.json",
    "SOURCE_PROFILE_RECOVERY.json",
    "PROFILE_RECOVERY_REPAIR_RECEIPT.json",
    "RESULT_RULE.json",
    "SOURCE_REGISTER.json",
    "RUNTIME_LOCK.json",
    "RUNTIME_REPAIR_RECEIPT.json",
    "ORIGINAL_AUDIT_PIP_FREEZE.txt",
    "requirements.txt",
    "src/external_replication/adapter.py",
    "src/external_replication/profile.py",
    "src/external_replication/prepare.py",
    "src/external_replication/evaluate.py",
    "src/external_replication/runner.py",
    "scripts/acquire_external.py",
    "scripts/acquire_nebius.py",
    "scripts/verify_runtime.py",
    "scripts/verify_recovered_profile.py",
    "scripts/verify_final.py",
    "tests/test_replication.py",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    missing = [name for name in REQUIRED if not (ROOT / name).is_file()]
    if missing:
        raise SystemExit(f"missing files: {missing}")
    protocol = json.loads((ROOT / "REPLICATION_PROTOCOL.json").read_text())
    lock = ROOT / "SOURCE_PROFILE_LOCK.json"
    recovery = ROOT / "SOURCE_PROFILE_RECOVERY.json"
    rule = ROOT / "RESULT_RULE.json"
    if protocol["source_profile_lock_sha256"] != sha(lock):
        raise SystemExit("source profile lock hash mismatch")
    if protocol["source_profile_recovery_sha256"] != sha(recovery):
        raise SystemExit("source recovery protocol hash mismatch")
    if protocol["result_rule_sha256"] != sha(rule):
        raise SystemExit("external result rule hash mismatch")
    if protocol["replication_id"] != "CD_EXTERNAL_CODING_TRAJECTORY_REPLICATION_003":
        raise SystemExit("replication identity mismatch")
    if protocol["status"] != "FROZEN_AFTER_EXTERNAL_SCHEMA_REPAIR_BEFORE_EXTERNAL_OUTCOME_SCORING":
        raise SystemExit("schema-repaired protocol not frozen")
    external = protocol["external_dataset"]
    if external["file_sha256"] != "3dd8ec3546cf771ce4ab2ac6c51ccefdd621197fa997a2cefb430b50df808fb6":
        raise SystemExit("external file hash pin changed")
    if external["included_sources"] != {"swe-smith-claude-3-7-sonnet": 5000}:
        raise SystemExit("label-complete included cohort changed")
    if external["excluded_source_reasons"] != {
        "kwai-klear-swe-smith-mini": "NO_INDEPENDENT_RESOLVED_OUTCOME_IN_PINNED_EXTERNAL_FILE",
        "nebius-swe-rebench-openhands": "SOURCE_OVERLAP_WITH_DEVELOPMENT_CORPUS",
    }:
        raise SystemExit("external exclusion boundary changed")
    if protocol["positive_gate"] != {
        "bootstrap_lower_95_gt": 0.0,
        "each_included_source_delta_gt": 0.0,
        "pr_auc_delta_gt": 0.02,
        "roc_auc_delta_gte": -0.005,
    }:
        raise SystemExit("numeric positive gate changed")
    serialization_receipt = json.loads((ROOT / "RESULT_SERIALIZATION_REPAIR_RECEIPT.json").read_text())
    repair = serialization_receipt["repair"]
    if not repair["strict_json_preserved"]:
        raise SystemExit("strict JSON was weakened")
    if any(repair[name] for name in (
        "source_profile_changed",
        "source_thresholds_changed",
        "external_features_changed",
        "external_labels_changed",
        "external_result_rule_changed",
        "external_refit_added",
    )):
        raise SystemExit("serialization repair crossed a scientific boundary")
    if serialization_receipt["scientific_status"] != "RESULT_NOT_PERSISTED_RERUN_REQUIRED":
        raise SystemExit("serialization repair status mismatch")
    schema_receipt = json.loads((ROOT / "EXTERNAL_SCHEMA_REPAIR_RECEIPT.json").read_text())
    if schema_receipt["prior_external_rows_scored"] != 0:
        raise SystemExit("schema repair occurred after external scoring")
    if schema_receipt["repair"]["external_result_rule_changed"]:
        raise SystemExit("external result rule was altered")
    recovery_value = json.loads(recovery.read_text())
    if not recovery_value["external_blindness"]["profile_must_be_written_and_hashed_before_external_acquisition"]:
        raise SystemExit("profile sealing order weakened")
    if recovery_value["source_metric_sanity"]["max_absolute_delta_each_metric"] != 0.0001:
        raise SystemExit("source recovery sanity bound changed")
    runtime = json.loads((ROOT / "RUNTIME_LOCK.json").read_text())
    if runtime["python"]["version"] != "3.11.15":
        raise SystemExit("original Python runtime pin changed")
    if runtime["packages"]["numpy"] != "2.4.6" or runtime["packages"]["scikit-learn"] != "1.9.0":
        raise SystemExit("original numerical runtime pins changed")
    if sha(ROOT / "ORIGINAL_AUDIT_PIP_FREEZE.txt") != runtime["source_pip_freeze_sha256"]:
        raise SystemExit("original pip freeze hash mismatch")
    print(json.dumps({
        "status": "PASS",
        "required_files": len(REQUIRED),
        "protocol_sha256": sha(ROOT / "REPLICATION_PROTOCOL.json"),
        "profile_lock_sha256": sha(lock),
        "source_recovery_sha256": sha(recovery),
        "result_rule_sha256": sha(rule),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
