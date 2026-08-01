from __future__ import annotations

import argparse
import json
import math
import tempfile
import zipfile
from pathlib import Path

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

EXPECTED_IDS = [f"P{i:02d}-{s}" for i in range(1, 31) for s in ("X", "Y")]
EXPECTED_PAIRS = [f"P{i:02d}" for i in range(1, 31)]
ALLOWED_INVALIDITY_REASONS = {
    "CHECKPOINT_CANNOT_RESOLVE",
    "NO_ELIGIBLE_READ_WITHIN_FIRST_20_COMMON_PREFIX_TOOL_CALLS",
    "WORKSPACE_CANNOT_FORK_BYTE_IDENTICALLY",
    "REQUIRED_FROZEN_SIGNAL_OBSERVATIONS_CANNOT_BE_EMITTED",
}
_SENTINEL = object()


def load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def verify_sidecar(path: Path) -> None:
    side = path.with_name(path.name + ".sha256")
    if not side.exists():
        raise ValueError(f"missing sidecar: {path.name}")
    parts = side.read_text("utf-8").strip().split()
    if len(parts) != 2 or parts[0] != sha256_file(path) or parts[1] != path.name:
        raise ValueError(f"invalid sidecar: {path.name}")


def safe_extract(path: Path, target: Path) -> None:
    with zipfile.ZipFile(path) as z:
        names: set[str] = set()
        for info in z.infolist():
            p = Path(info.filename)
            if info.filename in names or p.is_absolute() or ".." in p.parts:
                raise ValueError("unsafe or duplicate ZIP member")
            names.add(info.filename)
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("symlink ZIP member forbidden")
        z.extractall(target)


def verify_public_content(root: Path) -> dict:
    cm_path = root / "PUBLIC_CONTENT_MANIFEST.json"
    cm = load(cm_path)
    if cm.get("schema") != "openline.paired-mechanism-benchmark.public-content-manifest.v2":
        raise ValueError("public manifest schema mismatch")
    if cm.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("public manifest experiment mismatch")
    expected = cm.get("files")
    if not isinstance(expected, dict):
        raise ValueError("public manifest missing files")
    actual = {
        p.relative_to(root).as_posix(): sha256_file(p)
        for p in sorted(x for x in root.rglob("*") if x.is_file() and x != cm_path)
    }
    if actual != expected:
        raise ValueError("public content manifest mismatch")
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
        raise ValueError("public bundle missing complete-run files")
    if any(name.startswith("infrastructure/") for name in expected):
        raise ValueError("complete public bundle contains infrastructure receipt")
    return cm


def validate_manifest(manifest: dict) -> None:
    exact = {
        "schema": "openline.paired-mechanism-benchmark.blinded-manifest.v1",
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
            raise ValueError(f"blinded manifest mismatch: {key}")
    rows = manifest.get("executions")
    if not isinstance(rows, list) or [r.get("opaque_execution_id") for r in rows] != EXPECTED_IDS:
        raise ValueError("blinded manifest opaque-ID set/order mismatch")
    assert_export_safe(manifest)


def validate_trace(trace: dict, expected_oid: str) -> None:
    assert_export_safe(trace)
    if trace.get("opaque_execution_id") != expected_oid or trace.get("pair_id") != expected_oid[:3]:
        raise ValueError("trace identity mismatch")
    if trace.get("experiment_id") != EXPERIMENT_ID or trace.get("benchmark_revision") != BENCHMARK_REVISION:
        raise ValueError("trace experiment/revision mismatch")
    if trace.get("requested_model") != PINNED_MODEL or trace.get("reasoning_effort") != REASONING_EFFORT:
        raise ValueError("trace model configuration mismatch")
    returned = trace.get("returned_models")
    if not isinstance(returned, list) or any(x != PINNED_MODEL for x in returned):
        raise ValueError("trace returned-model set invalid")
    if trace.get("unblinded") is not False:
        raise ValueError("trace is not blind")
    if trace.get("disposition") == "TRACE_VALID":
        if trace.get("schema") != "openline.paired-mechanism-benchmark.opaque-trace.v1":
            raise ValueError("valid trace schema mismatch")
        if trace.get("raw_tool_payloads_present") is not False:
            raise ValueError("raw payloads present")
        if trace.get("scoring_anchor") != "immediately_before_eligible_read_result_delivery":
            raise ValueError("scoring anchor mismatch")
        if returned != [PINNED_MODEL]:
            raise ValueError("valid trace does not bind pinned model")
    elif trace.get("disposition") == "PAIR_INSTRUMENTATION_INVALID":
        if trace.get("schema") != "openline.paired-mechanism-benchmark.invalid-execution.v1":
            raise ValueError("invalid trace schema mismatch")
        if trace.get("invalidity_reason") not in ALLOWED_INVALIDITY_REASONS:
            raise ValueError("undeclared invalidity reason")
    else:
        raise ValueError("unknown execution disposition")


def independent_score(trace: dict) -> dict:
    disposition = trace.get("disposition")
    oid = trace.get("opaque_execution_id")
    pair_id = trace.get("pair_id")
    if disposition == "PAIR_INSTRUMENTATION_INVALID":
        return {
            "score_status": "PAIR_INSTRUMENTATION_INVALID",
            "invalidity_reason": trace.get("invalidity_reason"),
            "kappa_micros": None,
            "signal_micros": None,
            "signal_components": None,
            "kappa_point_micros": None,
            "step_count": None,
        }
    if disposition != "TRACE_VALID":
        raise ValueError("unknown execution disposition")
    steps = trace.get("steps")
    if not isinstance(steps, list) or len(steps) < 3 or trace.get("step_count") != len(steps):
        raise ValueError("invalid trace step count")
    prev_edges: frozenset[str] = frozenset()
    prev_state: dict[str, str] = {}
    values: list[int] = []
    components: list[dict] = []
    for expected_index, step in enumerate(steps, 1):
        if step.get("index") != expected_index or type(step.get("index")) is not int:
            raise ValueError("step sequence mismatch")
        w, r = step.get("write_events"), step.get("revision_events")
        edges_list, state = step.get("dependency_edges_after_step"), step.get("state_fields_after_step")
        if type(w) is not int or type(r) is not int or w < 0 or r < 0 or r > w:
            raise ValueError("bad write observation")
        if not isinstance(edges_list, list) or not all(isinstance(x, str) for x in edges_list):
            raise ValueError("bad edges")
        if len(edges_list) != len(set(edges_list)) or edges_list != sorted(edges_list):
            raise ValueError("edges are not canonical")
        if not isinstance(state, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in state.items()):
            raise ValueError("bad state")
        edges = frozenset(edges_list)
        union = prev_edges | edges
        dc = 0 if not union else 1_000_000 * len(prev_edges ^ edges) // len(union)
        rp = 0 if w == 0 else 1_000_000 * r // w
        keys = prev_state.keys() | state.keys()
        changes = sum(prev_state.get(k, _SENTINEL) != state.get(k, _SENTINEL) for k in keys)
        sd = 0 if not keys else 1_000_000 * changes // len(keys)
        value = (333_334 * rp + 333_333 * dc + 333_333 * sd) // 1_000_000
        if not 0 <= value <= 1_000_000:
            raise ValueError("signal outside frozen domain")
        values.append(value)
        components.append({
            "index": expected_index,
            "revision_pressure_micros": rp,
            "dependency_churn_micros": dc,
            "state_change_density_micros": sd,
            "signal_micros": value,
        })
        prev_edges, prev_state = edges, dict(state)
    kappas: list[int] = []
    for idx in range(2, len(values)):
        a, b, c = values[idx - 2], values[idx - 1], values[idx]
        numerator = abs(c - 2 * b + a)
        dx = b - a
        base = 1_000_000_000_000 + dx * dx
        kappas.append(numerator * 1_000_000_000_000_000_000 // (base * math.isqrt(base)))
    return {
        "score_status": "AVAILABLE",
        "invalidity_reason": None,
        "kappa_micros": max(kappas),
        "signal_micros": values,
        "signal_components": components,
        "kappa_point_micros": kappas,
        "step_count": len(steps),
    }


def verify(*, public_dir: Path, blind_dir: Path, out: Path) -> dict:
    public_zip = public_dir / "PUBLIC_SCORER_EXECUTION_BUNDLE.zip"
    score_zip = blind_dir / "BLINDED_SCORE_BUNDLE.zip"
    gate_path = blind_dir / "CAPSTONE_GATE.json"
    aggregate_path = blind_dir / "BLINDED_SCORE_AGGREGATE.json"
    verify_sidecar(gate_path)
    verify_sidecar(aggregate_path)
    verify_sidecar(score_zip)
    gate = load(gate_path)
    if gate.get("status") != "READY_FOR_ONE_TIME_SCORING_PATH_UNBLIND" or gate.get("ready_for_unblind") is not True:
        raise ValueError("blind scoring gate is not ready")
    if gate.get("score_record_count") != 60:
        raise ValueError("blind scoring gate record count mismatch")
    if sha256_file(score_zip) != gate.get("blinded_score_bundle_sha256"):
        raise ValueError("blind score bundle hash mismatch")
    if sha256_file(public_zip) != gate.get("public_execution_bundle_sha256"):
        raise ValueError("public execution bundle hash mismatch")

    with tempfile.TemporaryDirectory(prefix="olp003-independent-") as td:
        td = Path(td)
        pub, scores = td / "public", td / "scores"
        pub.mkdir(); scores.mkdir()
        safe_extract(public_zip, pub)
        safe_extract(score_zip, scores)
        verify_public_content(pub)
        validate_manifest(load(pub / "blinded_run_manifest.json"))

        aggregate = load(scores / "BLINDED_SCORE_AGGREGATE.json")
        if aggregate != load(aggregate_path):
            raise ValueError("standalone and bundled aggregate differ")
        if sha256_file(scores / "BLINDED_SCORE_AGGREGATE.json") != gate.get("blinded_score_aggregate_sha256"):
            raise ValueError("aggregate hash mismatch")
        if aggregate.get("status") != "BLINDED_SCORE_AGGREGATE_SEALED":
            raise ValueError("aggregate status mismatch")
        if aggregate.get("scientific_payload_hashes") != SCIENTIFIC_HASHES:
            raise ValueError("aggregate scientific payload mismatch")
        if aggregate.get("publication_commitment_sha256") != PUBLICATION_COMMITMENT_SHA256:
            raise ValueError("aggregate publication commitment mismatch")
        if aggregate.get("scorer_freeze_sha256") != SCORER_FREEZE_SHA256:
            raise ValueError("aggregate scorer freeze mismatch")
        if aggregate.get("score_record_count") != 60 or set(aggregate.get("score_record_sha256", {})) != set(EXPECTED_IDS):
            raise ValueError("aggregate score-record set mismatch")
        if set(aggregate.get("source_trace_sha256", {})) != set(EXPECTED_IDS):
            raise ValueError("aggregate source-trace set mismatch")
        if aggregate.get("condition_metadata_seen") is not False or aggregate.get("unblinded") is not False:
            raise ValueError("aggregate is not blind")

        expected_score_members = {"BLINDED_SCORE_AGGREGATE.json", "BLINDED_SCORE_AGGREGATE.json.sha256"}
        expected_score_members.update(f"scores/{oid}.score.json" for oid in EXPECTED_IDS)
        expected_score_members.update(f"scores/{oid}.score.json.sha256" for oid in EXPECTED_IDS)
        actual_score_members = {p.relative_to(scores).as_posix() for p in scores.rglob("*") if p.is_file()}
        if actual_score_members != expected_score_members:
            raise ValueError("blind score bundle file set mismatch")

        checked = 0
        valid_pairs: list[str] = []
        invalid_pairs: list[str] = []
        statuses_by_pair: dict[str, list[tuple[str, str | None]]] = {pid: [] for pid in EXPECTED_PAIRS}
        for oid in EXPECTED_IDS:
            trace_path = pub / "executions" / f"{oid}.json"
            trace = load(trace_path)
            validate_trace(trace, oid)
            if sha256_file(trace_path) != aggregate["source_trace_sha256"][oid]:
                raise ValueError(f"source trace hash mismatch: {oid}")
            score_path = scores / "scores" / f"{oid}.score.json"
            score_side = score_path.with_name(score_path.name + ".sha256")
            verify_sidecar(score_path)
            score = load(score_path)
            if sha256_file(score_path) != aggregate["score_record_sha256"][oid]:
                raise ValueError(f"sealed score hash mismatch: {oid}")
            expected = independent_score(trace)
            exact = {
                "schema": "openline.paired-mechanism-benchmark.blind-score-record.v1",
                "experiment_id": EXPERIMENT_ID,
                "opaque_execution_id": oid,
                "pair_id": oid[:3],
                "score_status": expected["score_status"],
                "kappa_micros": expected["kappa_micros"],
                "source_trace_sha256": sha256_file(trace_path),
                "delta_hol_status": "UNAVAILABLE_NO_FROZEN_OPERATIONAL_TRANSFORM",
                "condition_metadata_seen": False,
                "unblinded": False,
            }
            for key, value in exact.items():
                if score.get(key) != value:
                    raise ValueError(f"independent score field mismatch {oid}: {key}")
            if expected["score_status"] == "AVAILABLE":
                for key in ("step_count", "signal_components", "signal_micros", "kappa_point_micros"):
                    if score.get(key) != expected[key]:
                        raise ValueError(f"independent metric mismatch {oid}: {key}")
            else:
                if score.get("invalidity_reason") != expected["invalidity_reason"]:
                    raise ValueError(f"invalidity reason mismatch: {oid}")
            statuses_by_pair[oid[:3]].append((expected["score_status"], expected["invalidity_reason"]))
            checked += 1

        for pid in EXPECTED_PAIRS:
            rows = statuses_by_pair[pid]
            statuses = {x[0] for x in rows}
            if statuses == {"AVAILABLE"}:
                valid_pairs.append(pid)
            elif statuses == {"PAIR_INSTRUMENTATION_INVALID"} and len({x[1] for x in rows}) == 1:
                invalid_pairs.append(pid)
            else:
                raise ValueError(f"independent pair availability mismatch: {pid}")
        if aggregate.get("valid_pair_ids") != valid_pairs or aggregate.get("instrumentation_invalid_pair_ids") != invalid_pairs:
            raise ValueError("aggregate pair classification mismatch")
        if aggregate.get("valid_pair_count") != len(valid_pairs) or aggregate.get("instrumentation_invalid_pair_count") != len(invalid_pairs):
            raise ValueError("aggregate pair counts mismatch")

    receipt = {
        "schema": "openline.paired-mechanism-benchmark.independent-blind-score-verification.v1",
        "experiment_id": EXPERIMENT_ID,
        "status": "INDEPENDENT_BLIND_SCORE_VERIFICATION_PASS",
        "records_recomputed": checked,
        "valid_pair_count": len(valid_pairs),
        "instrumentation_invalid_pair_count": len(invalid_pairs),
        "blinded_score_aggregate_sha256": sha256_file(aggregate_path),
        "public_execution_bundle_sha256": sha256_file(public_zip),
        "blinded_score_bundle_sha256": sha256_file(score_zip),
        "condition_material_accessed": False,
        "condition_metadata_seen": False,
        "unblinded": False,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pretty_json_bytes(receipt))
    digest = sha256_file(out)
    (out.parent / f"{out.name}.sha256").write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    return receipt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--public-dir", required=True)
    ap.add_argument("--blind-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    receipt = verify(public_dir=Path(args.public_dir), blind_dir=Path(args.blind_dir), out=Path(args.out))
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
