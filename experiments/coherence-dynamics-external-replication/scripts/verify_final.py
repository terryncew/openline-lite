from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--prepared-dir", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--protocol", required=True)
    arguments = parser.parse_args()
    result_dir = Path(arguments.result_dir)
    prepared_dir = Path(arguments.prepared_dir)
    profile_path = Path(arguments.profile)
    protocol_path = Path(arguments.protocol)
    result_path = result_dir / "EXTERNAL_REPLICATION_RESULT.json"
    receipt_path = result_dir / "RUN_RECEIPT.json"
    binding_path = prepared_dir / "FEATURE_LABEL_BINDING.json"
    schema_audit_path = prepared_dir / "EXTERNAL_SCHEMA_AUDIT.json"
    for path in (result_path, receipt_path, binding_path, schema_audit_path, profile_path, protocol_path):
        if not path.is_file():
            raise SystemExit(f"missing final binding file: {path}")
    result = json.loads(result_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    binding = json.loads(binding_path.read_text())
    audit = json.loads(schema_audit_path.read_text())
    protocol = json.loads(protocol_path.read_text())
    expected_rows = protocol["external_dataset"]["expected_included_rows"]
    if result["replication_id"] != protocol["replication_id"]:
        raise SystemExit("result replication identity mismatch")
    if result["rows"] != expected_rows or receipt["rows"] != expected_rows or binding["rows"] != expected_rows:
        raise SystemExit("external included row count mismatch")
    if result["profile_sha256"] != sha(profile_path) or receipt["profile_sha256"] != sha(profile_path):
        raise SystemExit("profile binding mismatch")
    if result["protocol_sha256"] != sha(protocol_path):
        raise SystemExit("protocol binding mismatch")
    if receipt["result_sha256"] != sha(result_path):
        raise SystemExit("result receipt hash mismatch")
    if receipt["features_sha256"] != sha(prepared_dir / "features_blind_075.csv"):
        raise SystemExit("features binding mismatch")
    if receipt["labels_sha256"] != sha(prepared_dir / "labels_sealed.csv"):
        raise SystemExit("labels binding mismatch")
    if result["feature_label_binding_sha256"] != sha(binding_path) or receipt["feature_label_binding_sha256"] != sha(binding_path):
        raise SystemExit("feature-label receipt binding mismatch")
    if result["external_schema_audit_sha256"] != sha(schema_audit_path) or receipt["external_schema_audit_sha256"] != sha(schema_audit_path):
        raise SystemExit("external schema audit binding mismatch")
    if audit["source_rows"] != protocol["external_dataset"]["expected_source_rows"]:
        raise SystemExit("schema audit source counts mismatch")
    if set(result["cohorts"]) != set(protocol["external_dataset"]["included_sources"]):
        raise SystemExit("unfrozen cohort reached final result")
    if result["api_or_model_calls"] != 0 or result["api_credit_spend_usd"] != 0.0:
        raise SystemExit("nonzero model calls or spend in result")
    if result["disposition"] not in {
        "CD_ADDS_EXTERNAL_SIGNAL",
        "MIXED_EXTERNAL_SIGNAL",
        "BASELINE_OUTPERFORMS_CD",
        "BASELINE_EQUIVALENT",
        "NO_RELIABLE_EXTERNAL_SIGNAL",
    }:
        raise SystemExit("unknown disposition")
    print(json.dumps({
        "status": "PASS",
        "replication_id": result["replication_id"],
        "rows": result["rows"],
        "disposition": result["disposition"],
        "result_sha256": sha(result_path),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
