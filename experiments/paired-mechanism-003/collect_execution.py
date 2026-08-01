from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from assignment import deterministic_zip
from common import (
    BENCHMARK_REVISION,
    EXPERIMENT_ID,
    PINNED_MODEL,
    REASONING_EFFORT,
    PUBLICATION_COMMITMENT_SHA256,
    SCORER_FREEZE_SHA256,
    SCIENTIFIC_HASHES,
    canonical_json_bytes,
    pretty_json_bytes,
    sha256_bytes,
    sha256_file,
)
from trace_format import assert_export_safe

RUN_RE = re.compile(r"^P\d{2}-[XY]\.json$")
PAIR_RE = re.compile(r"^P\d{2}\.verification\.json$")
INFRA_RE = re.compile(r"^P\d{2}\.infrastructure\.json$")


def collect(*, pair_artifacts: Path, blinded_manifest: Path, assignment_lock: Path, sealed_condition_zip: Path, out_dir: Path, runner_manifest_sha256: str, preflight_pass_sha256: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_obj = json.loads(blinded_manifest.read_text("utf-8"))
    expected_ids = [r["opaque_execution_id"] for r in manifest_obj["executions"]]
    if len(expected_ids) != 60 or len(set(expected_ids)) != 60:
        raise ValueError("blinded manifest does not contain 60 unique opaque IDs")

    run_files = {p.stem: p for p in pair_artifacts.rglob("*.json") if RUN_RE.match(p.name)}
    pair_files = [p for p in pair_artifacts.rglob("*.verification.json") if PAIR_RE.match(p.name)]
    infra_files = [p for p in pair_artifacts.rglob("*.infrastructure.json") if INFRA_RE.match(p.name)]

    dispositions = []
    valid = invalid = 0
    returned_models = set()
    api_attempts = completed_responses = retry_count = 0
    input_tokens = output_tokens = total_tokens = cached_input_tokens = 0
    infrastructure_wait_seconds = active_api_seconds = 0.0
    infra_classes: dict[str, int] = {}
    pair_outcome_ids = set()

    for oid in expected_ids:
        p = run_files.get(oid)
        if p is None:
            dispositions.append({"opaque_execution_id": oid, "disposition": "MISSING_EXECUTION_RECORD"})
            continue
        obj = json.loads(p.read_text("utf-8")); assert_export_safe(obj)
        dispositions.append({"opaque_execution_id": oid, "disposition": obj.get("disposition")})
        if obj.get("disposition") == "TRACE_VALID": valid += 1
        elif obj.get("disposition") == "PAIR_INSTRUMENTATION_INVALID": invalid += 1
        else: raise ValueError(f"unknown execution disposition for {oid}")
        returned_models.update(obj.get("returned_models", []))

    for p in pair_files:
        obj = json.loads(p.read_text("utf-8")); assert_export_safe(obj)
        pid = obj.get("pair_id")
        if pid in pair_outcome_ids: raise ValueError(f"duplicate pair outcome receipt: {pid}")
        pair_outcome_ids.add(pid)
        api_attempts += int(obj.get("benchmark_model_calls", 0))
        completed_responses += int(obj.get("benchmark_completed_responses", 0))
        retry_count += int(obj.get("benchmark_retry_count", 0))
        input_tokens += int(obj.get("benchmark_input_tokens", 0))
        output_tokens += int(obj.get("benchmark_output_tokens", 0))
        total_tokens += int(obj.get("benchmark_total_tokens", 0))
        cached_input_tokens += int(obj.get("benchmark_cached_input_tokens", 0))
        infrastructure_wait_seconds += float(obj.get("infrastructure_wait_seconds", 0.0))
        active_api_seconds += float(obj.get("active_api_seconds", 0.0))
        returned_models.update(obj.get("returned_models", []))

    for p in infra_files:
        obj = json.loads(p.read_text("utf-8")); assert_export_safe(obj)
        pid = obj.get("pair_id")
        if pid in pair_outcome_ids: raise ValueError(f"duplicate pair outcome receipt: {pid}")
        pair_outcome_ids.add(pid)
        metrics = (obj.get("failure_detail") or {}).get("api_metrics") or {}
        api_attempts += int(metrics.get("api_attempt_count", 0))
        completed_responses += int(metrics.get("completed_response_count", 0))
        retry_count += int(metrics.get("retry_count", 0))
        input_tokens += int(metrics.get("input_tokens", 0))
        output_tokens += int(metrics.get("output_tokens", 0))
        total_tokens += int(metrics.get("total_tokens", 0))
        cached_input_tokens += int(metrics.get("cached_input_tokens", 0))
        infrastructure_wait_seconds += float(metrics.get("infrastructure_wait_seconds", 0.0))
        active_api_seconds += float(metrics.get("active_api_seconds", 0.0))
        returned_models.update(metrics.get("returned_models", []))
        cat = str(obj.get("failure_category") or "UNKNOWN")
        infra_classes[cat] = infra_classes.get(cat, 0) + 1

    payload_files: list[tuple[str, bytes]] = [("blinded_run_manifest.json", blinded_manifest.read_bytes())]
    for p in sorted(run_files.values(), key=lambda x: x.name):
        payload_files.append((f"executions/{p.name}", p.read_bytes()))
        side = p.with_name(p.name + ".sha256")
        if side.exists(): payload_files.append((f"executions/{side.name}", side.read_bytes()))
    for p in sorted(pair_files, key=lambda x: x.name): payload_files.append((f"verification/{p.name}", p.read_bytes()))
    for p in sorted(infra_files, key=lambda x: x.name):
        payload_files.append((f"infrastructure/{p.name}", p.read_bytes()))
        side = p.with_name(p.name + ".sha256")
        if side.exists(): payload_files.append((f"infrastructure/{side.name}", side.read_bytes()))

    content_manifest = {
        "schema": "openline.paired-mechanism-benchmark.public-content-manifest.v2",
        "experiment_id": EXPERIMENT_ID,
        "files": {name: sha256_bytes(data) for name, data in sorted(payload_files)},
        "infrastructure_receipts_included": True,
        "condition_labels_present": False,
        "plaintext_condition_map_present": False,
        "secret_key_present": False,
    }
    cm_bytes = canonical_json_bytes(content_manifest); payload_files.append(("PUBLIC_CONTENT_MANIFEST.json", cm_bytes))
    for name, data in payload_files:
        if b'"CLEAN"' in data or b'"PERTURBED"' in data:
            raise ValueError(f"assignment label leak in public payload: {name}")
        if b"secret_key.bin" in data or b"condition_map.enc" in data or b'"condition"' in data:
            raise ValueError(f"private assignment material leak in public payload: {name}")

    public_zip = out_dir / "PUBLIC_SCORER_EXECUTION_BUNDLE.zip"
    public_zip_sha = deterministic_zip(public_zip, payload_files)
    condition_zip_sha = sha256_file(sealed_condition_zip)
    complete = (
        len(run_files) == 60 and len(pair_files) == 30 and not infra_files
        and len(pair_outcome_ids) == 30
        and not any(d["disposition"] == "MISSING_EXECUTION_RECORD" for d in dispositions)
        and (not returned_models or returned_models == {PINNED_MODEL})
    )
    lock = json.loads(assignment_lock.read_text("utf-8"))
    if lock.get("publication_commitment_sha256") != PUBLICATION_COMMITMENT_SHA256:
        raise ValueError("assignment lock publication commitment mismatch")
    if lock.get("scorer_freeze_sha256") != SCORER_FREEZE_SHA256:
        raise ValueError("assignment lock scorer freeze mismatch")
    receipt = {
        "schema": "openline.paired-mechanism-benchmark.execution-receipt.v2",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "status": "EXECUTION_COMPLETE_BLIND" if complete else "EXECUTION_INCOMPLETE_BLIND",
        "scientific_payload_hashes": SCIENTIFIC_HASHES,
        "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
        "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
        "preflight_pass_sha256": preflight_pass_sha256,
        "runner_manifest_sha256": runner_manifest_sha256,
        "assignment_created": True,
        "pair_count": 30,
        "expected_execution_count": 60,
        "received_execution_record_count": len(run_files),
        "pair_verification_receipt_count": len(pair_files),
        "infrastructure_failure_receipt_count": len(infra_files),
        "infrastructure_failure_classes": infra_classes,
        "pair_outcome_receipt_count": len(pair_outcome_ids),
        "opaque_execution_ids": expected_ids,
        "execution_dispositions": dispositions,
        "valid_execution_count": valid,
        "invalid_execution_count": invalid,
        "benchmark_api_attempt_count": api_attempts,
        "benchmark_completed_response_count": completed_responses,
        "benchmark_retry_count": retry_count,
        "benchmark_input_tokens": input_tokens,
        "benchmark_output_tokens": output_tokens,
        "benchmark_total_tokens": total_tokens,
        "benchmark_cached_input_tokens": cached_input_tokens,
        "infrastructure_wait_seconds": infrastructure_wait_seconds,
        "active_api_seconds": active_api_seconds,
        "requested_model": PINNED_MODEL,
        "model_actually_returned_by_api": sorted(returned_models),
        "reasoning_effort": REASONING_EFFORT,
        "tool_budget_enforcement": "FROZEN_CONFIG_ENFORCED",
        "agent_tool_network": "DENIED",
        "condition_map_ciphertext_sha256": lock["condition_map_ciphertext_sha256"],
        "plaintext_condition_map_commitment_sha256": lock["condition_map_plaintext_sha256"],
        "plaintext_condition_map_commitment_scheme": lock["condition_map_commitment_scheme"],
        "commitment_nonce_bits": lock["commitment_nonce_bits"],
        "commitment_nonce_present_in_public_bundle": False,
        "public_bundle_sha256": public_zip_sha,
        "condition_bundle_sha256": condition_zip_sha,
        "secret_key_present_in_public_bundle": False,
        "secret_key_present_in_condition_bundle": False,
        "unblinded": False,
    }
    assert_export_safe(receipt)
    receipt_path = out_dir / "EXECUTION_RECEIPT.json"
    receipt_path.write_bytes(pretty_json_bytes(receipt))
    (out_dir / "EXECUTION_RECEIPT.json.sha256").write_text(f"{sha256_file(receipt_path)}  EXECUTION_RECEIPT.json\n", encoding="utf-8")
    return receipt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair-artifacts", required=True)
    ap.add_argument("--blinded-manifest", required=True)
    ap.add_argument("--assignment-lock", required=True)
    ap.add_argument("--sealed-condition-zip", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--runner-manifest-sha256", required=True)
    ap.add_argument("--preflight-pass-sha256", required=True)
    args = ap.parse_args()
    receipt = collect(
        pair_artifacts=Path(args.pair_artifacts), blinded_manifest=Path(args.blinded_manifest),
        assignment_lock=Path(args.assignment_lock), sealed_condition_zip=Path(args.sealed_condition_zip),
        out_dir=Path(args.out_dir), runner_manifest_sha256=args.runner_manifest_sha256,
        preflight_pass_sha256=args.preflight_pass_sha256,
    )
    print(json.dumps({"status": receipt["status"], "public_bundle_sha256": receipt["public_bundle_sha256"]}, indent=2))
    if receipt["status"] != "EXECUTION_COMPLETE_BLIND": raise SystemExit(2)

if __name__ == "__main__": main()
