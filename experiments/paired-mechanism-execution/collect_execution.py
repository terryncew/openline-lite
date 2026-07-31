from __future__ import annotations

import argparse
import json
import re
import shutil
import zipfile
from pathlib import Path

from assignment import deterministic_zip
from common import (
    BENCHMARK_REVISION,
    EXPERIMENT_ID,
    FROZEN_HASHES,
    PINNED_MODEL,
    PREFLIGHT_PASS_SHA256,
    REASONING_EFFORT,
    canonical_json_bytes,
    pretty_json_bytes,
    sha256_bytes,
    sha256_file,
)
from trace_format import assert_export_safe

RUN_RE = re.compile(r"^P\d{2}-[XY]\.json$")
PAIR_RE = re.compile(r"^P\d{2}\.verification\.json$")


def collect(*, pair_artifacts: Path, blinded_manifest: Path, assignment_lock: Path, sealed_condition_zip: Path, out_dir: Path, runner_manifest_sha256: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_obj = json.loads(blinded_manifest.read_text("utf-8"))
    expected_ids = [r["opaque_execution_id"] for r in manifest_obj["executions"]]
    if len(expected_ids) != 60 or len(set(expected_ids)) != 60:
        raise ValueError("blinded manifest does not contain 60 unique opaque IDs")

    run_files = {p.stem: p for p in pair_artifacts.rglob("*.json") if RUN_RE.match(p.name)}
    pair_files = [p for p in pair_artifacts.rglob("*.verification.json") if PAIR_RE.match(p.name)]
    dispositions = []
    valid = invalid = 0
    returned_models = set()
    model_calls = 0
    for oid in expected_ids:
        p = run_files.get(oid)
        if p is None:
            dispositions.append({"opaque_execution_id": oid, "disposition": "MISSING_EXECUTION_RECORD"})
            continue
        obj = json.loads(p.read_text("utf-8"))
        assert_export_safe(obj)
        dispositions.append({"opaque_execution_id": oid, "disposition": obj.get("disposition")})
        if obj.get("disposition") == "TRACE_VALID":
            valid += 1
        elif obj.get("disposition") == "PAIR_INSTRUMENTATION_INVALID":
            invalid += 1
        else:
            raise ValueError(f"unknown execution disposition for {oid}")
        returned_models.update(obj.get("returned_models", []))

    for p in pair_files:
        obj = json.loads(p.read_text("utf-8"))
        assert_export_safe(obj)
        model_calls += int(obj.get("benchmark_model_calls", 0))
        returned_models.update(obj.get("returned_models", []))

    # Build scorer payload only from public material. Never include sealed condition or key bytes.
    payload_files: list[tuple[str, bytes]] = []
    payload_files.append(("blinded_run_manifest.json", blinded_manifest.read_bytes()))
    for p in sorted(run_files.values(), key=lambda x: x.name):
        payload_files.append((f"executions/{p.name}", p.read_bytes()))
        side = p.with_name(p.name + ".sha256")
        if side.exists():
            payload_files.append((f"executions/{side.name}", side.read_bytes()))
    for p in sorted(pair_files, key=lambda x: x.name):
        payload_files.append((f"verification/{p.name}", p.read_bytes()))

    content_manifest = {
        "schema": "openline.paired-mechanism-benchmark.public-content-manifest.v1",
        "experiment_id": EXPERIMENT_ID,
        "files": {name: sha256_bytes(data) for name, data in sorted(payload_files)},
        "condition_labels_present": False,
        "plaintext_condition_map_present": False,
        "secret_key_present": False,
    }
    cm_bytes = canonical_json_bytes(content_manifest)
    payload_files.append(("PUBLIC_CONTENT_MANIFEST.json", cm_bytes))

    # Direct byte scan: labels/key/map must not be reconstructible from scorer payload.
    for name, data in payload_files:
        if b'"CLEAN"' in data or b'"PERTURBED"' in data:
            raise ValueError(f"assignment label leak in public payload: {name}")
        if b"secret_key.bin" in data or b"condition_map.enc" in data or b'"condition"' in data:
            raise ValueError(f"private assignment material leak in public payload: {name}")

    public_zip = out_dir / "PUBLIC_SCORER_EXECUTION_BUNDLE.zip"
    public_zip_sha = deterministic_zip(public_zip, payload_files)
    condition_zip_sha = sha256_file(sealed_condition_zip)
    complete = len(run_files) == 60 and len(pair_files) == 30 and len(dispositions) == 60 and not any(d["disposition"] == "MISSING_EXECUTION_RECORD" for d in dispositions)
    if returned_models and returned_models != {PINNED_MODEL}:
        complete = False

    receipt = {
        "schema": "openline.paired-mechanism-benchmark.execution-receipt.v1",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "status": "EXECUTION_COMPLETE_BLIND" if complete else "EXECUTION_INCOMPLETE_BLIND",
        "frozen_hashes": FROZEN_HASHES,
        "preflight_pass_sha256": PREFLIGHT_PASS_SHA256,
        "runner_manifest_sha256": runner_manifest_sha256,
        "assignment_created": True,
        "pair_count": 30,
        "expected_execution_count": 60,
        "received_execution_record_count": len(run_files),
        "opaque_execution_ids": expected_ids,
        "execution_dispositions": dispositions,
        "valid_execution_count": valid,
        "invalid_execution_count": invalid,
        "benchmark_model_call_count": model_calls,
        "requested_model": PINNED_MODEL,
        "model_actually_returned_by_api": sorted(returned_models),
        "reasoning_effort": REASONING_EFFORT,
        "tool_budget_enforcement": "FROZEN_CONFIG_ENFORCED",
        "agent_tool_network": "DENIED",
        "condition_map_ciphertext_sha256": json.loads(assignment_lock.read_text("utf-8"))["condition_map_ciphertext_sha256"],
        "plaintext_condition_map_commitment_sha256": json.loads(assignment_lock.read_text("utf-8"))["condition_map_plaintext_sha256"],
        "plaintext_condition_map_commitment_scheme": json.loads(assignment_lock.read_text("utf-8"))["condition_map_commitment_scheme"],
        "commitment_nonce_bits": json.loads(assignment_lock.read_text("utf-8"))["commitment_nonce_bits"],
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
    args = ap.parse_args()
    receipt = collect(
        pair_artifacts=Path(args.pair_artifacts),
        blinded_manifest=Path(args.blinded_manifest),
        assignment_lock=Path(args.assignment_lock),
        sealed_condition_zip=Path(args.sealed_condition_zip),
        out_dir=Path(args.out_dir),
        runner_manifest_sha256=args.runner_manifest_sha256,
    )
    print(json.dumps({"status": receipt["status"], "public_bundle_sha256": receipt["public_bundle_sha256"]}, indent=2))
    if receipt["status"] != "EXECUTION_COMPLETE_BLIND":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
