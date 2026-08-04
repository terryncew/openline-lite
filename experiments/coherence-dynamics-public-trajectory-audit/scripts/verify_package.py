#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "AUDIT_PROTOCOL.json",
    "SOURCE_REGISTER.json",
    "FEATURE_SCHEMA.json",
    "LEAKAGE_POLICY.json",
    "RESULT_RULE.json",
    "RUN_STATUS.json",
    "DATA_ACQUISITION_ATTEMPTS.json",
    "README.md",
    "requirements.txt",
]
ALLOWED_RESULTS = {
    "CD_ADDS_HELDOUT_SIGNAL",
    "BASELINE_OUTPERFORMS_CD",
    "BASELINE_EQUIVALENT",
    "NO_RELIABLE_SIGNAL",
}


def sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_static() -> None:
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            raise SystemExit(f"missing required file: {name}")
    status = json.loads((ROOT / "RUN_STATUS.json").read_text(encoding="utf-8"))
    if status["scientific_result"] != "NOT_RUN":
        raise SystemExit("source status must remain the pre-execution status")
    if status["model_api_calls"] != 0 or status["api_credit_spend_usd"] != 0.0:
        raise SystemExit("non-zero model use or spend in source status")
    register = json.loads((ROOT / "SOURCE_REGISTER.json").read_text(encoding="utf-8"))
    for entry in register.get("governing_sources", []):
        local_path = entry.get("local_path")
        expected = entry.get("sha256")
        if local_path and expected:
            path = ROOT / local_path
            if not path.is_file() or sha256(path) != expected:
                raise SystemExit(f"provenance mismatch: {local_path}")
        hash_record = entry.get("local_hash_record")
        artifact_hash = entry.get("artifact_sha256")
        if hash_record and artifact_hash:
            first = (ROOT / hash_record).read_text(encoding="utf-8").split()[0]
            if first != artifact_hash:
                raise SystemExit(f"provenance hash record mismatch: {hash_record}")
    forbidden_tokens = ("OPENAI_API_KEY", "api.openai.com", "client.responses", "chat.completions")
    scanned = list((ROOT / "src").rglob("*.py")) + [path for path in (ROOT / "scripts").rglob("*.py") if path.name != "verify_package.py"]
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden_tokens):
            raise SystemExit(f"model API token/reference present in {path.relative_to(ROOT)}")


def verify_runtime(result_dir: Path, prepared_dir: Path, data_manifest: Path) -> None:
    result_path = result_dir / "AUDIT_RESULT.json"
    receipt_path = result_dir / "RUN_RECEIPT.json"
    summary_path = result_dir / "EXECUTION_SUMMARY.md"
    binding_path = prepared_dir / "FEATURE_LABEL_BINDING.json"
    features_path = prepared_dir / "features_blind.csv"
    labels_path = prepared_dir / "labels_sealed.csv"
    for path in (result_path, receipt_path, summary_path, binding_path, features_path, labels_path, data_manifest):
        if not path.is_file():
            raise SystemExit(f"missing runtime artifact: {path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    if result.get("overall_disposition") not in ALLOWED_RESULTS:
        raise SystemExit("invalid or missing scientific disposition")
    if result.get("api_or_model_calls") != 0 or result.get("api_credit_spend_usd") != 0.0:
        raise SystemExit("result reports model calls or spend")
    if receipt.get("model_api_calls") != 0 or receipt.get("api_credit_spend_usd") != 0.0:
        raise SystemExit("receipt reports model calls or spend")
    if receipt.get("audit_result_sha256") != sha256(result_path):
        raise SystemExit("audit result receipt binding mismatch")
    if receipt.get("feature_label_binding_sha256") != sha256(binding_path):
        raise SystemExit("feature-label binding receipt mismatch")
    if receipt.get("data_manifest_sha256") != sha256(data_manifest):
        raise SystemExit("data manifest receipt mismatch")
    if binding.get("features_sha256") != sha256(features_path) or binding.get("labels_sha256") != sha256(labels_path):
        raise SystemExit("prepared artifact hash mismatch")
    with features_path.open(newline="", encoding="utf-8") as stream:
        feature_fields = set(next(csv.reader(stream)))
    forbidden = {"target", "exit_status", "generated_patch", "eval_logs", "reward", "pass", "eval_details"}
    if feature_fields & forbidden:
        raise SystemExit(f"forbidden fields in blind features: {sorted(feature_fields & forbidden)}")
    with labels_path.open(newline="", encoding="utf-8") as stream:
        label_fields = set(next(csv.reader(stream)))
    if label_fields != {"trajectory_id", "instance_id", "target"}:
        raise SystemExit(f"unexpected sealed label fields: {sorted(label_fields)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir")
    parser.add_argument("--prepared-dir")
    parser.add_argument("--data-manifest")
    args = parser.parse_args()
    verify_static()
    runtime_args = (args.result_dir, args.prepared_dir, args.data_manifest)
    if any(runtime_args) and not all(runtime_args):
        raise SystemExit("runtime verification requires result, prepared, and data manifest paths")
    if all(runtime_args):
        verify_runtime(Path(args.result_dir), Path(args.prepared_dir), Path(args.data_manifest))
        print("RUNTIME_VERIFICATION_PASS")
    else:
        print("STATIC_PACKAGE_VERIFICATION_PASS")


if __name__ == "__main__":
    main()
