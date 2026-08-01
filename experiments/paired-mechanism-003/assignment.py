from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from key_derivation import derive_key, new_descriptor, pop_secret_hex_from_env

from common import (
    BENCHMARK_REVISION,
    EXPERIMENT_ID,
    SOURCE_SCIENTIFIC_EXPERIMENT_ID,
    canonical_json_bytes,
    load_json,
    sha256_bytes,
    sha256_file,
)


def deterministic_zip(out_path: Path, files: list[tuple[str, bytes]]) -> str:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, data)
    return sha256_file(out_path)


def verify_pair_set(pair_set: dict):
    if pair_set.get("experiment_id") != SOURCE_SCIENTIFIC_EXPERIMENT_ID:
        raise ValueError("inherited pair set source experiment_id mismatch")
    pairs = pair_set.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 30:
        raise ValueError("pair set must contain exactly 30 pairs")
    expected = [f"P{i:02d}" for i in range(1, 31)]
    if [p.get("pair_id") for p in pairs] != expected:
        raise ValueError("pair IDs must be exactly P01..P30 in order")
    for p in pairs:
        required = {"pair_id", "task_id", "checkpoint_ref"}
        if not required <= set(p):
            raise ValueError(f"missing assignment identity for {p.get('pair_id')}")


def generate_assignment(
    *,
    pair_set_path: Path,
    public_dir: Path,
    sealed_dir: Path,
    key_derivation_secret_hex: str,
    key_context: str,
    design_sha256: str,
    pair_set_sha256: str,
    signal_schema_sha256: str,
    perturbation_sha256: str,
    preflight_pass_sha256: str,
    runner_manifest_sha256: str,
    publication_commitment_sha256: str,
    scorer_freeze_sha256: str,
    dry_run: bool = False,
) -> dict:
    for d in (public_dir, sealed_dir):
        if d.exists() and any(d.iterdir()):
            raise ValueError(f"output directory must be empty: {d}")
        d.mkdir(parents=True, exist_ok=True)
    pair_bytes = pair_set_path.read_bytes()
    if sha256_bytes(pair_bytes) != pair_set_sha256:
        raise ValueError("pair-set hash mismatch")
    pair_set = json.loads(pair_bytes)
    verify_pair_set(pair_set)

    public_rows = []
    secret_rows = []
    for pair in pair_set["pairs"]:
        # Independent cryptographic draws: assignment and within-pair execution order.
        perturbed_suffix = secrets.choice(("X", "Y"))
        order = ["X", "Y"]
        if secrets.randbits(1):
            order.reverse()
        rank = {suffix: i + 1 for i, suffix in enumerate(order)}
        for suffix in ("X", "Y"):
            opaque_id = f'{pair["pair_id"]}-{suffix}'
            public_rows.append(
                {
                    "pair_id": pair["pair_id"],
                    "opaque_execution_id": opaque_id,
                    "task_id": pair["task_id"],
                    "checkpoint_id": pair["checkpoint_ref"],
                    "execution_order": rank[suffix],
                }
            )
            secret_rows.append(
                {
                    "pair_id": pair["pair_id"],
                    "opaque_execution_id": opaque_id,
                    "condition": "PERTURBED" if suffix == perturbed_suffix else "CLEAN",
                }
            )

    manifest = {
        "schema": "openline.paired-mechanism-benchmark.blinded-manifest.v1",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "benchmark_design_sha256": design_sha256,
        "pair_set_sha256": pair_set_sha256,
        "signal_schema_sha256": signal_schema_sha256,
        "perturbation_spec_sha256": perturbation_sha256,
        "preflight_pass_sha256": preflight_pass_sha256,
        "publication_commitment_sha256": publication_commitment_sha256,
        "scorer_freeze_sha256": scorer_freeze_sha256,
        "executions": public_rows,
    }
    # The assignment itself contains only 30 binary choices (~30 bits). A bare hash of that
    # low-entropy map would let a scorer brute-force labels from the public commitment.
    # Bind a fresh 256-bit secret nonce INSIDE the encrypted plaintext before hashing it.
    # The nonce is revealed only when the sealed condition material is legitimately decrypted.
    commitment_nonce = secrets.token_bytes(32)
    secret_map = {
        "schema": "openline.paired-mechanism-benchmark.condition-map.v1",
        "experiment_id": EXPERIMENT_ID,
        "commitment_nonce_b64": base64.b64encode(commitment_nonce).decode("ascii"),
        "conditions": secret_rows,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    secret_bytes = canonical_json_bytes(secret_map)
    plaintext_sha = sha256_bytes(secret_bytes)

    key_derivation = new_descriptor(key_context)
    key = derive_key(key_derivation_secret_hex, key_derivation, expected_run_context=key_context)
    nonce = os.urandom(12)
    aad_obj = {
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "benchmark_design_sha256": design_sha256,
        "pair_set_sha256": pair_set_sha256,
        "signal_schema_sha256": signal_schema_sha256,
        "perturbation_spec_sha256": perturbation_sha256,
        "preflight_pass_sha256": preflight_pass_sha256,
        "runner_manifest_sha256": runner_manifest_sha256,
        "publication_commitment_sha256": publication_commitment_sha256,
        "scorer_freeze_sha256": scorer_freeze_sha256,
        "condition_map_plaintext_sha256": plaintext_sha,
        "condition_map_commitment_scheme": "SHA256_CANONICAL_JSON_WITH_SECRET_256BIT_NONCE",
        "key_derivation": key_derivation,
    }
    aad = canonical_json_bytes(aad_obj)
    cipher = AESGCM(key).encrypt(nonce, secret_bytes, aad)

    commitment = {
        "schema": "openline.paired-mechanism-benchmark.blind-commitment.v3",
        **aad_obj,
        "blinded_manifest_sha256": sha256_bytes(manifest_bytes),
        "cipher": "AES-256-GCM",
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "aad_b64": base64.b64encode(aad).decode("ascii"),
        "condition_map_ciphertext_sha256": sha256_bytes(cipher),
    }
    commitment_bytes = canonical_json_bytes(commitment)

    (public_dir / "blinded_run_manifest.json").write_bytes(manifest_bytes)
    (sealed_dir / "blind_commitment.json").write_bytes(commitment_bytes)
    (sealed_dir / "condition_map.enc").write_bytes(cipher)

    sealed_manifest = {
        "schema": "openline.paired-mechanism-benchmark.sealed-condition-manifest.v2",
        "experiment_id": EXPERIMENT_ID,
        "files": {
            "blind_commitment.json": sha256_file(sealed_dir / "blind_commitment.json"),
            "condition_map.enc": sha256_file(sealed_dir / "condition_map.enc"),
        },
        "secret_key_present": False,
        "key_derivation_secret_present": False,
        "derived_key_present": False,
        "plaintext_condition_map_present": False,
        "key_derivation": key_derivation,
    }
    sealed_manifest_bytes = canonical_json_bytes(sealed_manifest)
    (sealed_dir / "SEALED_CONDITION_MANIFEST.json").write_bytes(sealed_manifest_bytes)
    sealed_zip = sealed_dir / "SEALED_CONDITION_BUNDLE.zip"
    sealed_zip_sha = deterministic_zip(
        sealed_zip,
        [
            ("blind_commitment.json", commitment_bytes),
            ("condition_map.enc", cipher),
            ("SEALED_CONDITION_MANIFEST.json", sealed_manifest_bytes),
        ],
    )

    lock = {
        "schema": "openline.paired-mechanism-benchmark.assignment-lock.v2",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "dry_run": bool(dry_run),
        "assignment_created": True,
        "pair_count": 30,
        "execution_count": 60,
        "blinded_manifest_sha256": sha256_bytes(manifest_bytes),
        "condition_map_ciphertext_sha256": sha256_bytes(cipher),
        "condition_map_plaintext_sha256": plaintext_sha,
        "condition_map_commitment_scheme": "SHA256_CANONICAL_JSON_WITH_SECRET_256BIT_NONCE",
        "publication_commitment_sha256": publication_commitment_sha256,
        "scorer_freeze_sha256": scorer_freeze_sha256,
        "commitment_nonce_bits": 256,
        "sealed_condition_bundle_sha256": sealed_zip_sha,
        "secret_key_present_in_public": False,
        "secret_key_present_in_sealed_condition": False,
        "key_derivation_secret_present_in_artifacts": False,
        "derived_key_persisted": False,
        "plaintext_key_artifact_created": False,
        "key_derivation_secret_exported": False,
        "key_derivation_scheme": key_derivation["scheme"],
        "key_derivation_run_context_sha256": key_derivation["run_context_sha256"],
    }
    (public_dir / "ASSIGNMENT_LOCK.json").write_bytes(canonical_json_bytes(lock))
    return lock


def decrypt_map_in_memory(sealed_dir: Path, key_derivation_secret_hex: str, expected_key_context: str) -> dict:
    commitment = load_json(sealed_dir / "blind_commitment.json")
    key = derive_key(
        key_derivation_secret_hex,
        commitment.get("key_derivation"),
        expected_run_context=expected_key_context,
    )
    cipher = (sealed_dir / "condition_map.enc").read_bytes()
    if sha256_bytes(cipher) != commitment["condition_map_ciphertext_sha256"]:
        raise ValueError("condition ciphertext hash mismatch")
    nonce = base64.b64decode(commitment["nonce_b64"])
    aad = base64.b64decode(commitment["aad_b64"])
    plain = AESGCM(key).decrypt(nonce, cipher, aad)
    if sha256_bytes(plain) != commitment["condition_map_plaintext_sha256"]:
        raise ValueError("condition plaintext commitment mismatch")
    return json.loads(plain)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair-set", required=True)
    ap.add_argument("--public-dir", required=True)
    ap.add_argument("--sealed-dir", required=True)
    ap.add_argument("--key-derivation-secret-env", default="OLP_003_KEY_DERIVATION_SECRET")
    ap.add_argument("--key-context", required=True)
    ap.add_argument("--design-sha256", required=True)
    ap.add_argument("--pair-set-sha256", required=True)
    ap.add_argument("--signal-schema-sha256", required=True)
    ap.add_argument("--perturbation-sha256", required=True)
    ap.add_argument("--preflight-pass-sha256", required=True)
    ap.add_argument("--runner-manifest-sha256", required=True)
    ap.add_argument("--publication-commitment-sha256", required=True)
    ap.add_argument("--scorer-freeze-sha256", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    lock = generate_assignment(
        pair_set_path=Path(args.pair_set),
        public_dir=Path(args.public_dir),
        sealed_dir=Path(args.sealed_dir),
        key_derivation_secret_hex=pop_secret_hex_from_env(args.key_derivation_secret_env),
        key_context=args.key_context,
        design_sha256=args.design_sha256,
        pair_set_sha256=args.pair_set_sha256,
        signal_schema_sha256=args.signal_schema_sha256,
        perturbation_sha256=args.perturbation_sha256,
        preflight_pass_sha256=args.preflight_pass_sha256,
        runner_manifest_sha256=args.runner_manifest_sha256,
        publication_commitment_sha256=args.publication_commitment_sha256,
        scorer_freeze_sha256=args.scorer_freeze_sha256,
        dry_run=args.dry_run,
    )
    # Never print key or condition assignments.
    print(json.dumps({
        "status": "DRY_ASSIGNMENT_CREATED" if args.dry_run else "REAL_ASSIGNMENT_CREATED",
        "pair_count": lock["pair_count"],
        "execution_count": lock["execution_count"],
        "blinded_manifest_sha256": lock["blinded_manifest_sha256"],
        "sealed_condition_bundle_sha256": lock["sealed_condition_bundle_sha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
