from __future__ import annotations

import argparse
import base64
import json
import statistics
import tempfile
import zipfile
from pathlib import Path

from assignment import decrypt_map_in_memory, deterministic_zip
from key_derivation import pop_secret_hex_from_env, validate_descriptor
from common import (
    BENCHMARK_REVISION,
    EXPERIMENT_ID,
    PUBLICATION_COMMITMENT_SHA256,
    SCORER_FREEZE_SHA256,
    SCIENTIFIC_HASHES,
    canonical_json_bytes,
    pretty_json_bytes,
    sha256_bytes,
    sha256_file,
)

ROOT = Path(__file__).resolve().parent
EXPECTED_IDS = [f"P{i:02d}-{s}" for i in range(1, 31) for s in ("X", "Y")]
EXPECTED_PAIRS = [f"P{i:02d}" for i in range(1, 31)]


def load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def safe_extract(path: Path, target: Path) -> None:
    with zipfile.ZipFile(path) as z:
        seen: set[str] = set()
        for info in z.infolist():
            p = Path(info.filename)
            if info.filename in seen or p.is_absolute() or ".." in p.parts:
                raise ValueError("unsafe or duplicate ZIP member")
            seen.add(info.filename)
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("symlink ZIP member forbidden")
        z.extractall(target)


def seal(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    digest = sha256_file(path)
    (path.parent / f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest


def verify_sidecar(path: Path) -> None:
    side = path.with_name(path.name + ".sha256")
    if not side.exists():
        raise ValueError(f"missing sidecar: {path.name}")
    parts = side.read_text("utf-8").strip().split()
    if len(parts) != 2 or parts[0] != sha256_file(path) or parts[1] != path.name:
        raise ValueError(f"invalid sidecar: {path.name}")


def interpretation(wins: int, losses: int, ties: int, denominator: int) -> tuple[str, str]:
    if wins + losses + ties != denominator:
        raise ValueError("paired outcome counts do not equal denominator")
    if denominator != 30:
        return (
            "DESCRIPTIVE_ONLY_FROZEN_30_PAIR_DENOMINATOR_NOT_MET",
            "Mechanically invalid pairs reduced the evaluable denominator. Report the observed counts, but do not apply the frozen 15-of-30 interpretation or claim directional sensitivity.",
        )
    if wins > 15:
        return (
            "DIRECTIONAL_SENSITIVITY",
            "The frozen measurement path produced higher kappa under the controlled upstream information-loss perturbation in more than half of the 30 matched pairs. This supports directional mechanism sensitivity only.",
        )
    if wins == 15:
        return (
            "CHANCE_LEVEL_NO_USEFUL_SEPARATION",
            "The primary paired win count is exactly 15 of 30. The frozen interpretation permits no useful separation claim.",
        )
    return (
        "ADVERSE_RESULT",
        "The perturbation produced higher kappa in fewer than 15 of 30 pairs. The result is adverse to the preregistered directional hypothesis and is reported without rescue.",
    )


def render_markdown(result: dict) -> str:
    p = result["primary_result"]
    return f"""# OLP Core 2.1 paired mechanism experiment 003 — final capstone result

**Status:** `{result['status']}`  
**Primary interpretation:** `{p['interpretation_code']}`

Experiment 003 was frozen as a publish-regardless capstone before assignment. The result below is reported whether favorable, chance-level, adverse, tied, or not evaluable under the frozen denominator.

## Primary result

- Evaluable matched pairs: **{p['evaluable_pair_count']} of 30**
- κ higher under the perturbation: **{p['perturbation_higher_count']}**
- κ higher under the control: **{p['control_higher_count']}**
- Ties: **{p['tie_count']}**

{p['interpretation_text']}

## Claim boundary

This benchmark tests directional response to one controlled silent read-tail truncation. It does not establish prediction of real failures, a universal threshold, task correctness, or commercial value. The prospective Calibration Trial remains the predictive-validity test.

## Publication lock

The condition material was introduced to the scoring/publication path once, only after 60 blind score records, the blinded aggregate, and independent blind-score verification were sealed. Execution necessarily used private condition material to apply the intervention, but the blind scorer and independent verifier did not receive it. No post-result metric substitution, pair replacement, denominator extension, or same-design rerun is authorized.
"""


def _validate_lock(lock: dict, sealed_zip: Path) -> None:
    exact = {
        "schema": "openline.paired-mechanism-benchmark.assignment-lock.v2",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "dry_run": False,
        "assignment_created": True,
        "pair_count": 30,
        "execution_count": 60,
        "condition_map_commitment_scheme": "SHA256_CANONICAL_JSON_WITH_SECRET_256BIT_NONCE",
        "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
        "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
        "commitment_nonce_bits": 256,
        "secret_key_present_in_public": False,
        "secret_key_present_in_sealed_condition": False,
        "key_derivation_secret_present_in_artifacts": False,
        "derived_key_persisted": False,
        "plaintext_key_artifact_created": False,
        "key_derivation_secret_exported": False,
        "key_derivation_scheme": "HKDF-SHA256-32-V1",
    }
    for key, expected in exact.items():
        if lock.get(key) != expected:
            raise ValueError(f"assignment lock mismatch: {key}")
    for key in (
        "blinded_manifest_sha256",
        "condition_map_ciphertext_sha256",
        "condition_map_plaintext_sha256",
        "sealed_condition_bundle_sha256",
        "key_derivation_run_context_sha256",
    ):
        value = lock.get(key)
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"assignment lock hash invalid: {key}")
    if sha256_file(sealed_zip) != lock["sealed_condition_bundle_sha256"]:
        raise ValueError("sealed condition bundle hash mismatch")


def _validate_sealed_material(sealed_root: Path, lock: dict, expected_key_context: str) -> dict:
    expected_names = {
        "blind_commitment.json",
        "condition_map.enc",
        "SEALED_CONDITION_MANIFEST.json",
    }
    actual_names = {p.relative_to(sealed_root).as_posix() for p in sealed_root.rglob("*") if p.is_file()}
    if actual_names != expected_names:
        raise ValueError("sealed condition bundle file set mismatch")
    manifest = load(sealed_root / "SEALED_CONDITION_MANIFEST.json")
    if manifest.get("schema") != "openline.paired-mechanism-benchmark.sealed-condition-manifest.v2":
        raise ValueError("sealed condition manifest schema mismatch")
    if manifest.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("sealed condition manifest experiment mismatch")
    if (
        manifest.get("secret_key_present") is not False
        or manifest.get("key_derivation_secret_present") is not False
        or manifest.get("derived_key_present") is not False
        or manifest.get("plaintext_condition_map_present") is not False
    ):
        raise ValueError("sealed condition privacy declaration mismatch")
    files = manifest.get("files")
    expected_files = {
        "blind_commitment.json": sha256_file(sealed_root / "blind_commitment.json"),
        "condition_map.enc": sha256_file(sealed_root / "condition_map.enc"),
    }
    if files != expected_files:
        raise ValueError("sealed condition manifest hash mismatch")

    commitment = load(sealed_root / "blind_commitment.json")
    exact = {
        "schema": "openline.paired-mechanism-benchmark.blind-commitment.v3",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "benchmark_design_sha256": SCIENTIFIC_HASHES["BENCHMARK_DESIGN_FROZEN.json"],
        "pair_set_sha256": SCIENTIFIC_HASHES["PAIR_SET_FROZEN.json"],
        "signal_schema_sha256": SCIENTIFIC_HASHES["SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json"],
        "perturbation_spec_sha256": SCIENTIFIC_HASHES["PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json"],
        "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
        "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
        "condition_map_plaintext_sha256": lock["condition_map_plaintext_sha256"],
        "condition_map_commitment_scheme": "SHA256_CANONICAL_JSON_WITH_SECRET_256BIT_NONCE",
        "condition_map_ciphertext_sha256": lock["condition_map_ciphertext_sha256"],
        "blinded_manifest_sha256": lock["blinded_manifest_sha256"],
        "cipher": "AES-256-GCM",
    }
    for key, expected in exact.items():
        if commitment.get(key) != expected:
            raise ValueError(f"blind commitment mismatch: {key}")
    descriptor = commitment.get("key_derivation")
    validate_descriptor(descriptor, expected_run_context=expected_key_context)
    if manifest.get("key_derivation") != descriptor:
        raise ValueError("sealed manifest key derivation descriptor mismatch")
    if lock.get("key_derivation_run_context_sha256") != descriptor.get("run_context_sha256"):
        raise ValueError("assignment lock key derivation context mismatch")
    cipher = (sealed_root / "condition_map.enc").read_bytes()
    if sha256_bytes(cipher) != lock["condition_map_ciphertext_sha256"]:
        raise ValueError("condition ciphertext hash mismatch")
    try:
        nonce = base64.b64decode(commitment["nonce_b64"], validate=True)
        aad = base64.b64decode(commitment["aad_b64"], validate=True)
    except Exception as exc:
        raise ValueError("blind commitment base64 invalid") from exc
    if len(nonce) != 12:
        raise ValueError("AES-GCM nonce length mismatch")
    aad_obj = {
        "experiment_id": commitment["experiment_id"],
        "benchmark_revision": commitment["benchmark_revision"],
        "benchmark_design_sha256": commitment["benchmark_design_sha256"],
        "pair_set_sha256": commitment["pair_set_sha256"],
        "signal_schema_sha256": commitment["signal_schema_sha256"],
        "perturbation_spec_sha256": commitment["perturbation_spec_sha256"],
        "preflight_pass_sha256": commitment["preflight_pass_sha256"],
        "runner_manifest_sha256": commitment["runner_manifest_sha256"],
        "publication_commitment_sha256": commitment["publication_commitment_sha256"],
        "scorer_freeze_sha256": commitment["scorer_freeze_sha256"],
        "condition_map_plaintext_sha256": commitment["condition_map_plaintext_sha256"],
        "condition_map_commitment_scheme": commitment["condition_map_commitment_scheme"],
        "key_derivation": commitment["key_derivation"],
    }
    if aad != canonical_json_bytes(aad_obj):
        raise ValueError("blind commitment AAD mismatch")
    return commitment


def _validate_secret_map(secret_map: dict, lock: dict) -> dict[str, str]:
    if secret_map.get("schema") != "openline.paired-mechanism-benchmark.condition-map.v1":
        raise ValueError("condition map schema mismatch")
    if secret_map.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("condition map experiment mismatch")
    if sha256_bytes(canonical_json_bytes(secret_map)) != lock["condition_map_plaintext_sha256"]:
        raise ValueError("decrypted condition map does not reproduce plaintext commitment")
    try:
        commitment_nonce = base64.b64decode(secret_map["commitment_nonce_b64"], validate=True)
    except Exception as exc:
        raise ValueError("condition-map commitment nonce invalid") from exc
    if len(commitment_nonce) != 32:
        raise ValueError("condition-map commitment nonce must be 256 bits")
    rows = secret_map.get("conditions")
    if not isinstance(rows, list) or len(rows) != 60:
        raise ValueError("condition map must contain 60 rows")
    mapping: dict[str, str] = {}
    for row in rows:
        oid = row.get("opaque_execution_id")
        pair_id = row.get("pair_id")
        condition = row.get("condition")
        if oid not in EXPECTED_IDS or pair_id != oid[:3] or condition not in {"CLEAN", "PERTURBED"}:
            raise ValueError("condition map row invalid")
        if oid in mapping:
            raise ValueError("duplicate condition map opaque ID")
        mapping[oid] = condition
    if set(mapping) != set(EXPECTED_IDS):
        raise ValueError("condition map opaque-ID set mismatch")
    for pid in EXPECTED_PAIRS:
        vals = sorted(mapping[f"{pid}-{s}"] for s in ("X", "Y"))
        if vals != ["CLEAN", "PERTURBED"]:
            raise ValueError("condition map is not balanced within pair")
    return mapping


def _unblind_and_publish_impl(*, blind_dir: Path, verification_dir: Path, sealed_zip: Path, key_derivation_secret_hex: str, key_context: str, assignment_lock_path: Path, out_dir: Path) -> dict:
    if sha256_file(ROOT / "PUBLICATION_COMMITMENT_003.json") != PUBLICATION_COMMITMENT_SHA256:
        raise ValueError("publication commitment hash mismatch")
    if sha256_file(ROOT / "SCORER_FREEZE_003.json") != SCORER_FREEZE_SHA256:
        raise ValueError("scorer freeze hash mismatch")

    gate_path = blind_dir / "CAPSTONE_GATE.json"
    aggregate_path = blind_dir / "BLINDED_SCORE_AGGREGATE.json"
    score_zip = blind_dir / "BLINDED_SCORE_BUNDLE.zip"
    verification_path = verification_dir / "INDEPENDENT_BLIND_SCORE_VERIFICATION.json"
    for path in (gate_path, aggregate_path, score_zip, verification_path):
        verify_sidecar(path)

    gate = load(gate_path)
    if gate.get("status") != "READY_FOR_ONE_TIME_SCORING_PATH_UNBLIND" or gate.get("ready_for_unblind") is not True:
        raise ValueError("blind gate does not authorize unblind")
    if gate.get("score_record_count") != 60:
        raise ValueError("blind gate record count mismatch")
    if sha256_file(score_zip) != gate.get("blinded_score_bundle_sha256"):
        raise ValueError("score bundle hash mismatch")
    if sha256_file(aggregate_path) != gate.get("blinded_score_aggregate_sha256"):
        raise ValueError("blinded aggregate hash mismatch")

    independent = load(verification_path)
    exact_verification = {
        "schema": "openline.paired-mechanism-benchmark.independent-blind-score-verification.v1",
        "experiment_id": EXPERIMENT_ID,
        "status": "INDEPENDENT_BLIND_SCORE_VERIFICATION_PASS",
        "records_recomputed": 60,
        "blinded_score_aggregate_sha256": sha256_file(aggregate_path),
        "public_execution_bundle_sha256": gate.get("public_execution_bundle_sha256"),
        "blinded_score_bundle_sha256": sha256_file(score_zip),
        "condition_material_accessed": False,
        "condition_metadata_seen": False,
        "unblinded": False,
    }
    for key, expected in exact_verification.items():
        if independent.get(key) != expected:
            raise ValueError(f"independent verification mismatch: {key}")

    lock = load(assignment_lock_path)
    _validate_lock(lock, sealed_zip)
    mapping: dict[str, str]
    scores: dict[str, dict] = {}
    aggregate: dict
    with tempfile.TemporaryDirectory(prefix="olp003-unblind-") as td:
        td_path = Path(td)
        scores_root, sealed_root = td_path / "scores", td_path / "sealed"
        scores_root.mkdir(); sealed_root.mkdir()
        safe_extract(score_zip, scores_root)
        safe_extract(sealed_zip, sealed_root)
        _validate_sealed_material(sealed_root, lock, key_context)

        bundled_aggregate_path = scores_root / "BLINDED_SCORE_AGGREGATE.json"
        verify_sidecar(bundled_aggregate_path)
        if bundled_aggregate_path.read_bytes() != aggregate_path.read_bytes():
            raise ValueError("bundled and standalone blinded aggregates differ")
        aggregate = load(bundled_aggregate_path)
        exact_aggregate = {
            "schema": "openline.paired-mechanism-benchmark.blinded-score-aggregate.v1",
            "experiment_id": EXPERIMENT_ID,
            "status": "BLINDED_SCORE_AGGREGATE_SEALED",
            "scientific_payload_hashes": SCIENTIFIC_HASHES,
            "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
            "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
            "score_record_count": 60,
            "condition_metadata_seen": False,
            "condition_map_opened_by_scoring_path": False,
            "unblinded": False,
        }
        for key, expected in exact_aggregate.items():
            if aggregate.get(key) != expected:
                raise ValueError(f"blinded aggregate mismatch: {key}")
        if set(aggregate.get("score_record_sha256", {})) != set(EXPECTED_IDS):
            raise ValueError("blinded aggregate score-record set mismatch")

        valid_pairs = aggregate.get("valid_pair_ids")
        invalid_pairs_list = aggregate.get("instrumentation_invalid_pair_ids")
        if not isinstance(valid_pairs, list) or not isinstance(invalid_pairs_list, list):
            raise ValueError("blinded aggregate pair lists missing")
        if sorted(valid_pairs + invalid_pairs_list) != EXPECTED_PAIRS or set(valid_pairs) & set(invalid_pairs_list):
            raise ValueError("blinded aggregate pair partition mismatch")
        if aggregate.get("valid_pair_count") != len(valid_pairs) or aggregate.get("instrumentation_invalid_pair_count") != len(invalid_pairs_list):
            raise ValueError("blinded aggregate pair count mismatch")

        for oid in EXPECTED_IDS:
            p = scores_root / "scores" / f"{oid}.score.json"
            verify_sidecar(p)
            if sha256_file(p) != aggregate["score_record_sha256"].get(oid):
                raise ValueError(f"missing or changed score record: {oid}")
            score = load(p)
            if (
                score.get("schema") != "openline.paired-mechanism-benchmark.blind-score-record.v1"
                or score.get("experiment_id") != EXPERIMENT_ID
                or score.get("opaque_execution_id") != oid
                or score.get("pair_id") != oid[:3]
                or score.get("condition_metadata_seen") is not False
                or score.get("unblinded") is not False
            ):
                raise ValueError(f"score record envelope mismatch: {oid}")
            scores[oid] = score

        # This is the only point at which condition material enters the scoring/publication path.
        secret_map = decrypt_map_in_memory(
            sealed_root,
            key_derivation_secret_hex,
            key_context,
        )
        mapping = _validate_secret_map(secret_map, lock)
    pair_results: list[dict] = []
    wins = losses = ties = 0
    effects: list[int] = []
    invalid_pairs = set(aggregate.get("instrumentation_invalid_pair_ids", []))
    for pid in EXPECTED_PAIRS:
        pair_oids = (f"{pid}-X", f"{pid}-Y")
        if pid in invalid_pairs:
            pair_scores = [scores[oid] for oid in pair_oids]
            if {s.get("score_status") for s in pair_scores} != {"PAIR_INSTRUMENTATION_INVALID"}:
                raise ValueError(f"invalid pair has available score: {pid}")
            if len({s.get("invalidity_reason") for s in pair_scores}) != 1:
                raise ValueError(f"invalid pair reason mismatch: {pid}")
            pair_results.append({
                "pair_id": pid,
                "pair_status": "PAIR_INSTRUMENTATION_INVALID_PRE_UNBLIND",
                "invalidity_reason": pair_scores[0].get("invalidity_reason"),
                "primary_effect_micros": None,
            })
            continue
        by_condition: dict[str, tuple[str, dict]] = {}
        for oid in pair_oids:
            by_condition[mapping[oid]] = (oid, scores[oid])
        if set(by_condition) != {"CLEAN", "PERTURBED"}:
            raise ValueError(f"pair condition join failed: {pid}")
        clean_oid, clean_score = by_condition["CLEAN"]
        perturbed_oid, perturbed_score = by_condition["PERTURBED"]
        if clean_score.get("score_status") != "AVAILABLE" or perturbed_score.get("score_status") != "AVAILABLE":
            raise ValueError(f"evaluable pair has unavailable score: {pid}")
        clean = clean_score.get("kappa_micros")
        perturbed = perturbed_score.get("kappa_micros")
        if type(clean) is not int or type(perturbed) is not int:
            raise ValueError(f"available pair has nonnumeric score: {pid}")
        effect = perturbed - clean
        effects.append(effect)
        if effect > 0:
            outcome = "PERTURBATION_HIGHER"; wins += 1
        elif effect < 0:
            outcome = "CONTROL_HIGHER"; losses += 1
        else:
            outcome = "TIE"; ties += 1
        pair_results.append({
            "pair_id": pid,
            "pair_status": "EVALUABLE",
            "control_opaque_execution_id": clean_oid,
            "perturbation_opaque_execution_id": perturbed_oid,
            "control_kappa_micros": clean,
            "perturbation_kappa_micros": perturbed,
            "primary_effect_micros": effect,
            "primary_outcome": outcome,
        })

    denominator = len(effects)
    code, text = interpretation(wins, losses, ties, denominator)
    result = {
        "schema": "openline.paired-mechanism-benchmark.final-capstone-result.v1",
        "experiment_id": EXPERIMENT_ID,
        "status": "FINAL_CAPSTONE_SCIENTIFIC_RESULT_SEALED",
        "publication_commitment_sha256": PUBLICATION_COMMITMENT_SHA256,
        "scorer_freeze_sha256": SCORER_FREEZE_SHA256,
        "scientific_payload_hashes": SCIENTIFIC_HASHES,
        "public_execution_bundle_sha256": gate.get("public_execution_bundle_sha256"),
        "blinded_score_aggregate_sha256": sha256_file(aggregate_path),
        "blinded_score_bundle_sha256": sha256_file(score_zip),
        "independent_blind_score_verification_sha256": sha256_file(verification_path),
        "sealed_condition_bundle_sha256": sha256_file(sealed_zip),
        "condition_map_plaintext_commitment_sha256": lock["condition_map_plaintext_sha256"],
        "condition_map_commitment_verified": True,
        "key_derivation_scheme": lock["key_derivation_scheme"],
        "derived_key_persisted": False,
        "plaintext_key_artifact_created": False,
        "key_derivation_secret_exported": False,
        "scoring_publication_path_condition_map_open_count": 1,
        "condition_material_accessed_by_blind_scorer": False,
        "condition_material_accessed_by_independent_verifier": False,
        "execution_path_condition_material_accessed_to_apply_intervention": True,
        "unblinded": True,
        "primary_result": {
            "metric": "kappa_micros",
            "evaluable_pair_count": denominator,
            "frozen_pair_count": 30,
            "perturbation_higher_count": wins,
            "control_higher_count": losses,
            "tie_count": ties,
            "effect_distribution_micros": effects,
            "effect_mean_micros": (sum(effects) / denominator) if denominator else None,
            "effect_median_micros": statistics.median(effects) if effects else None,
            "interpretation_code": code,
            "interpretation_text": text,
        },
        "secondary_result": {
            "metric": "delta_hol",
            "status": "UNAVAILABLE_NO_FROZEN_OPERATIONAL_TRANSFORM",
            "cannot_rescue_primary": True,
        },
        "instrumentation_invalid_pair_ids": sorted(invalid_pairs),
        "pair_results": pair_results,
        "claim_boundary": "Directional sensitivity to one controlled upstream silent read-tail truncation only; no predictive-validity, threshold-detection, task-correctness, or commercial claim.",
        "publication_required": True,
        "publish_regardless_commitment_honored": True,
        "rerun_or_replacement_authorized": False,
        "successor_same_design_authorized": False,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "FINAL_BENCHMARK_RESULT.json"
    result_sha = seal(result_path, pretty_json_bytes(result))
    md_path = out_dir / "PUBLICATION_SUMMARY.md"
    md_sha = seal(md_path, render_markdown(result).encode("utf-8"))
    files = [
        ("FINAL_BENCHMARK_RESULT.json", result_path.read_bytes()),
        ("FINAL_BENCHMARK_RESULT.json.sha256", (out_dir / "FINAL_BENCHMARK_RESULT.json.sha256").read_bytes()),
        ("PUBLICATION_SUMMARY.md", md_path.read_bytes()),
        ("PUBLICATION_SUMMARY.md.sha256", (out_dir / "PUBLICATION_SUMMARY.md.sha256").read_bytes()),
        ("BLINDED_SCORE_AGGREGATE.json", aggregate_path.read_bytes()),
        ("BLINDED_SCORE_AGGREGATE.json.sha256", (blind_dir / "BLINDED_SCORE_AGGREGATE.json.sha256").read_bytes()),
        ("BLINDED_SCORE_BUNDLE.zip", score_zip.read_bytes()),
        ("BLINDED_SCORE_BUNDLE.zip.sha256", (blind_dir / "BLINDED_SCORE_BUNDLE.zip.sha256").read_bytes()),
        ("INDEPENDENT_BLIND_SCORE_VERIFICATION.json", verification_path.read_bytes()),
        ("INDEPENDENT_BLIND_SCORE_VERIFICATION.json.sha256", (verification_dir / "INDEPENDENT_BLIND_SCORE_VERIFICATION.json.sha256").read_bytes()),
        ("PUBLICATION_COMMITMENT_003.json", (ROOT / "PUBLICATION_COMMITMENT_003.json").read_bytes()),
        ("SCORER_FREEZE_003.json", (ROOT / "SCORER_FREEZE_003.json").read_bytes()),
    ]
    zip_path = out_dir / "FINAL_CAPSTONE_PUBLICATION_BUNDLE.zip"
    bundle_sha = deterministic_zip(zip_path, files)
    (out_dir / "FINAL_CAPSTONE_PUBLICATION_BUNDLE.zip.sha256").write_text(
        f"{bundle_sha}  FINAL_CAPSTONE_PUBLICATION_BUNDLE.zip\n", encoding="utf-8"
    )
    receipt = {
        "schema": "openline.paired-mechanism-benchmark.final-publication-receipt.v1",
        "experiment_id": EXPERIMENT_ID,
        "status": "FINAL_CAPSTONE_PUBLICATION_BUNDLE_SEALED",
        "final_result_sha256": result_sha,
        "publication_summary_sha256": md_sha,
        "publication_bundle_sha256": bundle_sha,
        "publication_required": True,
        "unblinded": True,
    }
    seal(out_dir / "FINAL_PUBLICATION_RECEIPT.json", pretty_json_bytes(receipt))
    return receipt



def unblind_and_publish(*, blind_dir: Path, verification_dir: Path, sealed_zip: Path, key_derivation_secret_hex: str, key_context: str, assignment_lock_path: Path, out_dir: Path) -> dict:
    return _unblind_and_publish_impl(
        blind_dir=blind_dir,
        verification_dir=verification_dir,
        sealed_zip=sealed_zip,
        key_derivation_secret_hex=key_derivation_secret_hex,
        key_context=key_context,
        assignment_lock_path=assignment_lock_path,
        out_dir=out_dir,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--blind-dir", required=True)
    ap.add_argument("--verification-dir", required=True)
    ap.add_argument("--sealed-condition-zip", required=True)
    ap.add_argument("--key-derivation-secret-env", default="OLP_003_KEY_DERIVATION_SECRET")
    ap.add_argument("--key-context", required=True)
    ap.add_argument("--assignment-lock", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    receipt = unblind_and_publish(
        blind_dir=Path(args.blind_dir),
        verification_dir=Path(args.verification_dir),
        sealed_zip=Path(args.sealed_condition_zip),
        key_derivation_secret_hex=pop_secret_hex_from_env(args.key_derivation_secret_env),
        key_context=args.key_context,
        assignment_lock_path=Path(args.assignment_lock),
        out_dir=Path(args.out_dir),
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
