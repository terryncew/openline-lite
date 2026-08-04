from __future__ import annotations

import argparse
import json
import math
import tempfile
import zipfile
from pathlib import Path

from assignment import deterministic_zip
from common import (
    BENCHMARK_REVISION,
    EXPERIMENT_ID,
    PINNED_MODEL,
    PUBLICATION_COMMITMENT_SHA256,
    REASONING_EFFORT,
    SCORER_FREEZE_SHA256,
    SCIENTIFIC_HASHES,
    pretty_json_bytes,
    sha256_file,
)
from trace_format import assert_export_safe

ROOT = Path(__file__).resolve().parent
EXPECTED_IDS = [f"P{i:02d}-{s}" for i in range(1, 31) for s in ("X", "Y")]
EXPECTED_PAIRS = [f"P{i:02d}" for i in range(1, 31)]
ALLOWED_INVALIDITY_REASONS = {
    "CHECKPOINT_CANNOT_RESOLVE",
    "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS",
    "WORKSPACE_CANNOT_FORK_BYTE_IDENTICALLY",
    "REQUIRED_FROZEN_SIGNAL_OBSERVATIONS_CANNOT_BE_EMITTED",
}
MISSING = object()


def _load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def _seal(path: Path, obj: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pretty_json_bytes(obj))
    digest = sha256_file(path)
    (path.parent / f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest


def _verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        raise ValueError(f"missing SHA-256 sidecar: {path.name}")
    fields = sidecar.read_text("utf-8").strip().split()
    if len(fields) != 2 or fields[0] != sha256_file(path) or fields[1] != path.name:
        raise ValueError(f"invalid SHA-256 sidecar: {path.name}")


def _safe_extract(z: zipfile.ZipFile, target: Path) -> None:
    seen: set[str] = set()
    for info in z.infolist():
        name = info.filename
        if name in seen:
            raise ValueError(f"duplicate ZIP entry: {name}")
        seen.add(name)
        p = Path(name)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"unsafe ZIP entry: {name}")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"symlink ZIP entry forbidden: {name}")
    z.extractall(target)


def _verify_content_manifest(root: Path) -> dict:
    manifest_path = root / "PUBLIC_CONTENT_MANIFEST.json"
    cm = _load(manifest_path)
    if cm.get("schema") != "openline.paired-mechanism-benchmark.public-content-manifest.v2":
        raise ValueError("public content manifest schema mismatch")
    if cm.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("public content manifest experiment mismatch")
    expected = cm.get("files")
    if not isinstance(expected, dict):
        raise ValueError("public content manifest missing files mapping")
    actual: dict[str, str] = {}
    for p in sorted(x for x in root.rglob("*") if x.is_file() and x != manifest_path):
        rel = p.relative_to(root).as_posix()
        actual[rel] = sha256_file(p)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(k for k in set(actual) & set(expected) if actual[k] != expected[k])
        raise ValueError(f"public content manifest mismatch missing={missing} extra={extra} changed={changed}")
    if (
        cm.get("condition_labels_present") is not False
        or cm.get("plaintext_condition_map_present") is not False
        or cm.get("secret_key_present") is not False
    ):
        raise ValueError("public bundle privacy declaration mismatch")
    required = {"blinded_run_manifest.json"}
    required.update(f"executions/{oid}.json" for oid in EXPECTED_IDS)
    required.update(f"verification/{pid}.verification.json" for pid in EXPECTED_PAIRS)
    if not required <= set(expected):
        raise ValueError("public bundle is missing required complete-run files")
    if any(name.startswith("infrastructure/") for name in expected):
        raise ValueError("complete public bundle contains infrastructure failure receipt")
    return cm


def _validate_execution_receipt(receipt: dict, public_zip_sha: str) -> None:
    assert_export_safe(receipt)
    exact = {
        "schema": "openline.paired-mechanism-benchmark.execution-receipt.v2",
        "experiment_id": EXPERIMENT_ID,
        "status": "EXECUTION_COMPLETE_BLIND",
        "scientific_payload_hashes": SCIENTIFIC_HASHES,
        "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
        "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
        "assignment_created": True,
        "pair_count": 30,
        "expected_execution_count": 60,
        "received_execution_record_count": 60,
        "pair_verification_receipt_count": 30,
        "infrastructure_failure_receipt_count": 0,
        "pair_outcome_receipt_count": 30,
        "requested_model": PINNED_MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "secret_key_present_in_public_bundle": False,
        "secret_key_present_in_condition_bundle": False,
        "unblinded": False,
    }
    for key, expected in exact.items():
        if receipt.get(key) != expected:
            raise ValueError(f"execution receipt field mismatch: {key}")
    if receipt.get("public_bundle_sha256") != public_zip_sha:
        raise ValueError("public execution ZIP hash mismatch")
    ids = receipt.get("opaque_execution_ids")
    if ids != EXPECTED_IDS:
        raise ValueError("execution receipt opaque-ID set/order mismatch")
    dispositions = receipt.get("execution_dispositions")
    if not isinstance(dispositions, list) or len(dispositions) != 60:
        raise ValueError("execution receipt disposition count mismatch")
    if [r.get("opaque_execution_id") for r in dispositions] != EXPECTED_IDS:
        raise ValueError("execution receipt disposition ID order mismatch")
    allowed = {"TRACE_VALID", "PAIR_INSTRUMENTATION_INVALID"}
    if any(r.get("disposition") not in allowed for r in dispositions):
        raise ValueError("execution receipt contains non-scoreable disposition")
    valid = receipt.get("valid_execution_count")
    invalid = receipt.get("invalid_execution_count")
    if type(valid) is not int or type(invalid) is not int or valid + invalid != 60:
        raise ValueError("execution receipt valid/invalid count mismatch")
    completed = receipt.get("benchmark_completed_response_count", 0)
    if type(completed) is not int or completed < 0:
        raise ValueError("execution receipt completed-response count invalid")
    returned = receipt.get("model_actually_returned_by_api")
    if not isinstance(returned, list) or any(x != PINNED_MODEL for x in returned):
        raise ValueError("execution receipt returned-model set invalid")
    if (completed > 0 or valid > 0) and returned != [PINNED_MODEL]:
        raise ValueError("complete scoreable execution did not bind the pinned returned model")


def _validate_manifest(manifest: dict) -> None:
    if manifest.get("schema") != "openline.paired-mechanism-benchmark.blinded-manifest.v1":
        raise ValueError("blinded manifest schema mismatch")
    exact = {
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "benchmark_design_sha256": SCIENTIFIC_HASHES["BENCHMARK_DESIGN_FROZEN.json"],
        "pair_set_sha256": SCIENTIFIC_HASHES["PAIR_SET_FROZEN.json"],
        "signal_schema_sha256": SCIENTIFIC_HASHES["SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json"],
        "perturbation_spec_sha256": SCIENTIFIC_HASHES["PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json"],
        "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
        "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
    }
    for key, expected in exact.items():
        if manifest.get(key) != expected:
            raise ValueError(f"blinded manifest field mismatch: {key}")
    rows = manifest.get("executions")
    if not isinstance(rows, list) or len(rows) != 60:
        raise ValueError("blinded manifest must contain 60 executions")
    ids = [r.get("opaque_execution_id") for r in rows]
    if ids != EXPECTED_IDS:
        raise ValueError("blinded manifest must contain exact ordered P01-X..P30-Y set")
    for row in rows:
        oid = row["opaque_execution_id"]
        if row.get("pair_id") != oid[:3]:
            raise ValueError("blinded manifest pair identity mismatch")
        if row.get("execution_order") not in (1, 2):
            raise ValueError("blinded manifest execution order invalid")
    for pid in EXPECTED_PAIRS:
        pair_rows = [r for r in rows if r["pair_id"] == pid]
        if sorted(r["execution_order"] for r in pair_rows) != [1, 2]:
            raise ValueError(f"blinded manifest execution order not balanced: {pid}")
    assert_export_safe(manifest)


def _validate_trace_envelope(trace: dict) -> None:
    assert_export_safe(trace)
    oid = trace.get("opaque_execution_id")
    pair_id = trace.get("pair_id")
    if oid not in EXPECTED_IDS or pair_id != oid[:3]:
        raise ValueError("trace opaque identity mismatch")
    if trace.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("trace experiment mismatch")
    if trace.get("benchmark_revision") != BENCHMARK_REVISION:
        raise ValueError("trace benchmark revision mismatch")
    if trace.get("requested_model") != PINNED_MODEL or trace.get("reasoning_effort") != REASONING_EFFORT:
        raise ValueError("trace model configuration mismatch")
    returned = trace.get("returned_models")
    if not isinstance(returned, list) or any(x != PINNED_MODEL for x in returned):
        raise ValueError("trace returned-model set invalid")
    if trace.get("unblinded") is not False:
        raise ValueError("trace must remain blinded")
    disposition = trace.get("disposition")
    if disposition == "TRACE_VALID":
        if trace.get("schema") != "openline.paired-mechanism-benchmark.opaque-trace.v1":
            raise ValueError("valid trace schema mismatch")
        if trace.get("scoring_anchor") != "immediately_before_eligible_read_result_delivery":
            raise ValueError("valid trace scoring anchor mismatch")
        if trace.get("raw_tool_payloads_present") is not False:
            raise ValueError("raw tool payloads present in scorer trace")
        steps = trace.get("steps")
        if not isinstance(steps, list) or trace.get("step_count") != len(steps):
            raise ValueError("valid trace step-count mismatch")
        if returned != [PINNED_MODEL]:
            raise ValueError("valid trace does not bind the pinned returned model")
    elif disposition == "PAIR_INSTRUMENTATION_INVALID":
        if trace.get("schema") != "openline.paired-mechanism-benchmark.invalid-execution.v1":
            raise ValueError("invalid trace schema mismatch")
        if trace.get("invalidity_reason") not in ALLOWED_INVALIDITY_REASONS:
            raise ValueError("invalid trace uses undeclared invalidity reason")
    else:
        raise ValueError(f"unknown trace disposition for {oid}")


def _component_signal(step: dict, previous_edges: set[str], previous_state: dict[str, str]) -> tuple[int, dict]:
    required = {"index", "tool_name", "write_events", "revision_events", "dependency_edges_after_step", "state_fields_after_step"}
    if not required <= set(step):
        raise ValueError("required frozen step observation missing")
    writes = step["write_events"]
    revisions = step["revision_events"]
    edges_raw = step["dependency_edges_after_step"]
    state = step["state_fields_after_step"]
    if type(step["index"]) is not int or step["index"] < 1 or not isinstance(step["tool_name"], str):
        raise ValueError("invalid step identity")
    if type(writes) is not int or type(revisions) is not int or writes < 0 or revisions < 0 or revisions > writes:
        raise ValueError("invalid write/revision observation")
    if not isinstance(edges_raw, list) or not all(isinstance(x, str) for x in edges_raw):
        raise ValueError("invalid dependency edge set")
    if len(edges_raw) != len(set(edges_raw)) or edges_raw != sorted(edges_raw):
        raise ValueError("dependency edge set must be unique and canonical")
    if not isinstance(state, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in state.items()):
        raise ValueError("invalid state field mapping")

    edges = set(edges_raw)
    union_edges = previous_edges | edges
    dependency_churn = 0 if not union_edges else (len(previous_edges ^ edges) * 1_000_000) // len(union_edges)
    revision_pressure = 0 if writes == 0 else (revisions * 1_000_000) // writes
    keys = set(previous_state) | set(state)
    changed = sum(1 for k in keys if previous_state.get(k, MISSING) != state.get(k, MISSING))
    state_change_density = 0 if not keys else (changed * 1_000_000) // len(keys)
    signal = (
        333_334 * revision_pressure
        + 333_333 * dependency_churn
        + 333_333 * state_change_density
    ) // 1_000_000
    if not 0 <= signal <= 1_000_000:
        raise ValueError("composite signal outside frozen domain")
    return signal, {
        "revision_pressure_micros": revision_pressure,
        "dependency_churn_micros": dependency_churn,
        "state_change_density_micros": state_change_density,
    }


def score_trace(trace: dict) -> dict:
    _validate_trace_envelope(trace)
    oid = trace["opaque_execution_id"]
    pair_id = trace["pair_id"]
    if trace["disposition"] == "PAIR_INSTRUMENTATION_INVALID":
        return {
            "schema": "openline.paired-mechanism-benchmark.blind-score-record.v1",
            "experiment_id": EXPERIMENT_ID,
            "opaque_execution_id": oid,
            "pair_id": pair_id,
            "score_status": "PAIR_INSTRUMENTATION_INVALID",
            "invalidity_reason": trace["invalidity_reason"],
            "kappa_micros": None,
            "delta_hol_status": "UNAVAILABLE_NO_FROZEN_OPERATIONAL_TRANSFORM",
            "condition_metadata_seen": False,
            "unblinded": False,
        }

    steps = trace["steps"]
    if len(steps) < 3:
        raise ValueError(f"trace has fewer than three steps: {oid}")
    if [s.get("index") for s in steps] != list(range(1, len(steps) + 1)):
        raise ValueError(f"trace step order mismatch: {oid}")

    previous_edges: set[str] = set()
    previous_state: dict[str, str] = {}
    signals: list[int] = []
    components: list[dict] = []
    for step in steps:
        signal, row = _component_signal(step, previous_edges, previous_state)
        signals.append(signal)
        components.append({"index": step["index"], **row, "signal_micros": signal})
        previous_edges = set(step["dependency_edges_after_step"])
        previous_state = dict(step["state_fields_after_step"])

    points: list[int] = []
    for x0, x1, x2 in zip(signals, signals[1:], signals[2:]):
        numerator = abs(x2 - 2 * x1 + x0)
        dx = x1 - x0
        base = 1_000_000**2 + dx**2
        point = (numerator * 1_000_000**3) // (base * math.isqrt(base))
        points.append(point)
    kappa = max(points)
    return {
        "schema": "openline.paired-mechanism-benchmark.blind-score-record.v1",
        "experiment_id": EXPERIMENT_ID,
        "opaque_execution_id": oid,
        "pair_id": pair_id,
        "score_status": "AVAILABLE",
        "step_count": len(steps),
        "signal_components": components,
        "signal_micros": signals,
        "kappa_point_micros": points,
        "kappa_micros": kappa,
        "delta_hol_status": "UNAVAILABLE_NO_FROZEN_OPERATIONAL_TRANSFORM",
        "condition_metadata_seen": False,
        "unblinded": False,
    }


def _infrastructure_result(execution_receipt: dict, out_dir: Path, public_zip_sha: str) -> dict:
    result = {
        "schema": "openline.paired-mechanism-benchmark.final-capstone-result.v1",
        "experiment_id": EXPERIMENT_ID,
        "status": "FINAL_CAPSTONE_INFRASTRUCTURE_ABORTED_BLIND",
        "scientific_result": "NOT_EVALUABLE",
        "reason": "The frozen run did not produce the complete scorer input required for the preregistered result. Partial traces are not scored or stitched.",
        "execution_status": execution_receipt.get("status"),
        "received_execution_record_count": execution_receipt.get("received_execution_record_count"),
        "expected_execution_count": execution_receipt.get("expected_execution_count", 60),
        "infrastructure_failure_receipt_count": execution_receipt.get("infrastructure_failure_receipt_count"),
        "infrastructure_failure_classes": execution_receipt.get("infrastructure_failure_classes", {}),
        "public_execution_bundle_sha256": public_zip_sha,
        "assignment_created": execution_receipt.get("assignment_created"),
        "condition_material_accessed_by_scoring_path": False,
        "unblinded": False,
        "partial_scoring_performed": False,
        "publication_required": True,
        "publish_regardless_commitment_honored": True,
        "rerun_or_replacement_authorized": False,
        "successor_same_design_authorized": False,
        "claim_boundary": "Infrastructure disposition only; no condition-linked scientific inference is permitted.",
    }
    _seal(out_dir / "FINAL_BENCHMARK_RESULT.json", result)
    gate = {
        "schema": "openline.paired-mechanism-benchmark.capstone-gate.v1",
        "experiment_id": EXPERIMENT_ID,
        "status": "STOP_BLIND_INFRASTRUCTURE_RESULT_PUBLISHABLE",
        "ready_for_unblind": False,
        "final_result_file": "FINAL_BENCHMARK_RESULT.json",
        "unblinded": False,
    }
    _seal(out_dir / "CAPSTONE_GATE.json", gate)
    return gate


def blind_score(*, public_dir: Path, out_dir: Path) -> dict:
    execution_receipt_path = public_dir / "EXECUTION_RECEIPT.json"
    public_zip_path = public_dir / "PUBLIC_SCORER_EXECUTION_BUNDLE.zip"
    if not execution_receipt_path.exists() or not public_zip_path.exists():
        raise ValueError("public execution artifact is incomplete")
    execution_receipt = _load(execution_receipt_path)
    if execution_receipt.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("execution receipt experiment mismatch")
    public_zip_sha = sha256_file(public_zip_path)
    if execution_receipt.get("status") != "EXECUTION_COMPLETE_BLIND":
        if execution_receipt.get("public_bundle_sha256") != public_zip_sha:
            raise ValueError("incomplete execution ZIP hash mismatch")
        return _infrastructure_result(execution_receipt, out_dir, public_zip_sha)

    _verify_sidecar(execution_receipt_path)
    _validate_execution_receipt(execution_receipt, public_zip_sha)

    with tempfile.TemporaryDirectory(prefix="olp003-blind-score-") as td:
        extracted = Path(td)
        with zipfile.ZipFile(public_zip_path) as z:
            _safe_extract(z, extracted)
        content_manifest = _verify_content_manifest(extracted)
        manifest = _load(extracted / "blinded_run_manifest.json")
        _validate_manifest(manifest)

        score_dir = out_dir / "scores"
        score_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict] = []
        record_hashes: dict[str, str] = {}
        trace_hashes: dict[str, str] = {}
        for oid in EXPECTED_IDS:
            trace_path = extracted / "executions" / f"{oid}.json"
            if not trace_path.exists():
                raise ValueError(f"missing execution record after complete receipt: {oid}")
            trace = _load(trace_path)
            record = score_trace(trace)
            record["source_trace_sha256"] = sha256_file(trace_path)
            path = score_dir / f"{oid}.score.json"
            path.write_bytes(pretty_json_bytes(record))
            digest = sha256_file(path)
            (score_dir / f"{oid}.score.json.sha256").write_text(f"{digest}  {oid}.score.json\n", encoding="utf-8")
            records.append(record)
            record_hashes[oid] = digest
            trace_hashes[oid] = record["source_trace_sha256"]

        valid_pairs: list[str] = []
        invalid_pairs: list[str] = []
        for pid in EXPECTED_PAIRS:
            pair = [r for r in records if r["pair_id"] == pid]
            statuses = {r["score_status"] for r in pair}
            if statuses == {"AVAILABLE"}:
                valid_pairs.append(pid)
            elif statuses == {"PAIR_INSTRUMENTATION_INVALID"}:
                reasons = {r.get("invalidity_reason") for r in pair}
                if len(reasons) != 1:
                    raise ValueError(f"pair has inconsistent invalidity reasons: {pid}")
                invalid_pairs.append(pid)
            else:
                raise ValueError(f"pair has asymmetric score availability: {pid}")

        aggregate = {
            "schema": "openline.paired-mechanism-benchmark.blinded-score-aggregate.v1",
            "experiment_id": EXPERIMENT_ID,
            "status": "BLINDED_SCORE_AGGREGATE_SEALED",
            "scientific_payload_hashes": SCIENTIFIC_HASHES,
            "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
            "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
            "execution_receipt_sha256": sha256_file(execution_receipt_path),
            "public_content_manifest_sha256": sha256_file(extracted / "PUBLIC_CONTENT_MANIFEST.json"),
            "public_execution_bundle_sha256": public_zip_sha,
            "score_record_count": 60,
            "score_record_sha256": record_hashes,
            "source_trace_sha256": trace_hashes,
            "valid_pair_ids": valid_pairs,
            "instrumentation_invalid_pair_ids": invalid_pairs,
            "valid_pair_count": len(valid_pairs),
            "instrumentation_invalid_pair_count": len(invalid_pairs),
            "primary_metric": "kappa_micros",
            "condition_metadata_seen": False,
            "condition_map_opened_by_scoring_path": False,
            "unblinded": False,
            "secondary_delta_hol_status": "UNAVAILABLE_NO_FROZEN_OPERATIONAL_TRANSFORM",
            "secondary_cannot_rescue_primary": True,
        }
        aggregate_path = out_dir / "BLINDED_SCORE_AGGREGATE.json"
        aggregate_sha = _seal(aggregate_path, aggregate)

        bundle_items: list[tuple[str, bytes]] = [
            ("BLINDED_SCORE_AGGREGATE.json", aggregate_path.read_bytes()),
            ("BLINDED_SCORE_AGGREGATE.json.sha256", (out_dir / "BLINDED_SCORE_AGGREGATE.json.sha256").read_bytes()),
        ]
        for p in sorted(score_dir.iterdir()):
            bundle_items.append((f"scores/{p.name}", p.read_bytes()))
        score_zip = out_dir / "BLINDED_SCORE_BUNDLE.zip"
        score_zip_sha = deterministic_zip(score_zip, bundle_items)
        (out_dir / "BLINDED_SCORE_BUNDLE.zip.sha256").write_text(
            f"{score_zip_sha}  BLINDED_SCORE_BUNDLE.zip\n", encoding="utf-8"
        )
        gate = {
            "schema": "openline.paired-mechanism-benchmark.capstone-gate.v1",
            "experiment_id": EXPERIMENT_ID,
            "status": "READY_FOR_ONE_TIME_SCORING_PATH_UNBLIND",
            "ready_for_unblind": True,
            "score_record_count": 60,
            "blinded_score_aggregate_sha256": aggregate_sha,
            "blinded_score_bundle_sha256": score_zip_sha,
            "public_execution_bundle_sha256": public_zip_sha,
            "condition_material_accessed_by_scoring_path": False,
            "unblinded": False,
        }
        _seal(out_dir / "CAPSTONE_GATE.json", gate)
        return gate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--public-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    gate = blind_score(public_dir=Path(args.public_dir), out_dir=Path(args.out_dir))
    print(json.dumps(gate, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
