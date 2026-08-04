from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "REPLICATION_PROTOCOL.json",
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
    if protocol["status"] != "FROZEN_AFTER_SOURCE_PROFILE_RECOVERY_REPAIR_BEFORE_EXTERNAL_ACQUISITION":
        raise SystemExit("repaired protocol not frozen")
    if protocol["external_dataset"]["excluded_sources"] != ["nebius-swe-rebench-openhands"]:
        raise SystemExit("Nebius overlap exclusion changed")
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
