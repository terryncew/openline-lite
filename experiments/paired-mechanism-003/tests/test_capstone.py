from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
import sys
sys.path.insert(0, str(ROOT))

from assignment import deterministic_zip, generate_assignment
from blind_score import blind_score, score_trace
from common import (
    BENCHMARK_REVISION,
    EXPERIMENT_ID,
    PINNED_MODEL,
    PUBLICATION_COMMITMENT_SHA256,
    REASONING_EFFORT,
    SCORER_FREEZE_SHA256,
    SCIENTIFIC_HASHES,
    canonical_json_bytes,
    pretty_json_bytes,
    sha256_bytes,
    sha256_file,
)
from independent_verify_scores import verify as independent_verify
from unblind_publish import interpretation, unblind_and_publish


def trace_for(oid: str) -> dict:
    pid = oid[:3]
    steps = [
        {
            "index": 1,
            "tool_name": "read_file",
            "write_events": 0,
            "revision_events": 0,
            "dependency_edges_after_step": ["step:0001|reads|file:a"],
            "state_fields_after_step": {"file:a": "sha256:1"},
        },
        {
            "index": 2,
            "tool_name": "apply_patch",
            "write_events": 1,
            "revision_events": 1,
            "dependency_edges_after_step": ["step:0001|reads|file:a", "step:0002|writes|file:a"],
            "state_fields_after_step": {"file:a": "sha256:2"},
        },
        {
            "index": 3,
            "tool_name": "read_file",
            "write_events": 0,
            "revision_events": 0,
            "dependency_edges_after_step": [
                "step:0001|reads|file:a",
                "step:0002|writes|file:a",
                "step:0003|reads|file:b",
            ],
            "state_fields_after_step": {"file:a": "sha256:2", "file:b": "sha256:3"},
        },
        {
            "index": 4,
            "tool_name": "apply_patch",
            "write_events": 2,
            "revision_events": 1,
            "dependency_edges_after_step": [
                "step:0001|reads|file:a",
                "step:0002|writes|file:a",
                "step:0003|reads|file:b",
                "step:0004|writes|file:b",
            ],
            "state_fields_after_step": {"file:a": "sha256:4", "file:b": "sha256:5"},
        },
    ]
    return {
        "schema": "openline.paired-mechanism-benchmark.opaque-trace.v1",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "opaque_execution_id": oid,
        "pair_id": pid,
        "disposition": "TRACE_VALID",
        "scoring_anchor": "immediately_before_eligible_read_result_delivery",
        "steps": steps,
        "step_count": len(steps),
        "termination": {"kind": "FINAL_OUTPUT"},
        "benchmark_model_calls_observed": 4,
        "benchmark_completed_responses_observed": 4,
        "benchmark_retry_count_observed": 0,
        "infrastructure_wait_seconds_observed": 180.0,
        "active_api_seconds_observed": 1.0,
        "requested_model": PINNED_MODEL,
        "returned_models": [PINNED_MODEL],
        "reasoning_effort": REASONING_EFFORT,
        "raw_tool_payloads_present": False,
        "unblinded": False,
    }


def build_complete_public_bundle(public_dir: Path, blinded_manifest_path: Path) -> Path:
    payload: list[tuple[str, bytes]] = [("blinded_run_manifest.json", blinded_manifest_path.read_bytes())]
    for i in range(1, 31):
        pid = f"P{i:02d}"
        for suffix in ("X", "Y"):
            oid = f"{pid}-{suffix}"
            data = pretty_json_bytes(trace_for(oid))
            payload.append((f"executions/{oid}.json", data))
            payload.append((f"executions/{oid}.json.sha256", f"{sha256_bytes(data)}  {oid}.json\n".encode()))
        verification = {
            "schema": "openline.paired-mechanism-benchmark.pair-verification.v1",
            "experiment_id": EXPERIMENT_ID,
            "benchmark_revision": BENCHMARK_REVISION,
            "pair_id": pid,
            "opaque_execution_ids": [f"{pid}-X", f"{pid}-Y"],
            "pair_disposition": "PAIR_VALID_FOR_BLIND_SCORING",
            "benchmark_model_calls": 8,
            "benchmark_completed_responses": 8,
            "benchmark_retry_count": 0,
            "returned_models": [PINNED_MODEL],
            "reasoning_effort": REASONING_EFFORT,
            "unblinded": False,
        }
        payload.append((f"verification/{pid}.verification.json", pretty_json_bytes(verification)))
    content_manifest = {
        "schema": "openline.paired-mechanism-benchmark.public-content-manifest.v2",
        "experiment_id": EXPERIMENT_ID,
        "files": {name: sha256_bytes(data) for name, data in sorted(payload)},
        "infrastructure_receipts_included": True,
        "condition_labels_present": False,
        "plaintext_condition_map_present": False,
        "secret_key_present": False,
    }
    payload.append(("PUBLIC_CONTENT_MANIFEST.json", canonical_json_bytes(content_manifest)))
    public_dir.mkdir(parents=True, exist_ok=True)
    zip_path = public_dir / "PUBLIC_SCORER_EXECUTION_BUNDLE.zip"
    zip_sha = deterministic_zip(zip_path, payload)
    ids = [f"P{i:02d}-{s}" for i in range(1, 31) for s in ("X", "Y")]
    receipt = {
        "schema": "openline.paired-mechanism-benchmark.execution-receipt.v2",
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
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
        "infrastructure_failure_classes": {},
        "pair_outcome_receipt_count": 30,
        "opaque_execution_ids": ids,
        "execution_dispositions": [{"opaque_execution_id": oid, "disposition": "TRACE_VALID"} for oid in ids],
        "valid_execution_count": 60,
        "invalid_execution_count": 0,
        "benchmark_api_attempt_count": 240,
        "benchmark_completed_response_count": 240,
        "benchmark_retry_count": 0,
        "benchmark_input_tokens": 1,
        "benchmark_output_tokens": 1,
        "benchmark_total_tokens": 2,
        "benchmark_cached_input_tokens": 0,
        "infrastructure_wait_seconds": 1.0,
        "active_api_seconds": 1.0,
        "requested_model": PINNED_MODEL,
        "model_actually_returned_by_api": [PINNED_MODEL],
        "reasoning_effort": REASONING_EFFORT,
        "tool_budget_enforcement": "FROZEN_CONFIG_ENFORCED",
        "agent_tool_network": "DENIED",
        "condition_map_ciphertext_sha256": "c" * 64,
        "plaintext_condition_map_commitment_sha256": "d" * 64,
        "plaintext_condition_map_commitment_scheme": "SHA256_CANONICAL_JSON_WITH_SECRET_256BIT_NONCE",
        "commitment_nonce_bits": 256,
        "commitment_nonce_present_in_public_bundle": False,
        "public_bundle_sha256": zip_sha,
        "condition_bundle_sha256": "e" * 64,
        "secret_key_present_in_public_bundle": False,
        "secret_key_present_in_condition_bundle": False,
        "unblinded": False,
    }
    receipt_path = public_dir / "EXECUTION_RECEIPT.json"
    receipt_path.write_bytes(pretty_json_bytes(receipt))
    (public_dir / "EXECUTION_RECEIPT.json.sha256").write_text(
        f"{sha256_file(receipt_path)}  EXECUTION_RECEIPT.json\n", encoding="utf-8"
    )
    return zip_path


class CapstoneTests(unittest.TestCase):
    def test_publication_commitment_is_frozen_and_no_rerun(self):
        obj = json.loads((ROOT / "PUBLICATION_COMMITMENT_003.json").read_text("utf-8"))
        self.assertEqual(sha256_file(ROOT / "PUBLICATION_COMMITMENT_003.json"), PUBLICATION_COMMITMENT_SHA256)
        self.assertTrue(obj["publication_required"])
        self.assertTrue(obj["publication_independent_of_result_favorability"])
        self.assertFalse(obj["same_design_rerun_authorized"])
        self.assertFalse(obj["successor_rescue_experiment_authorized"])

    def test_scorer_freeze_binds_all_three_sources(self):
        obj = json.loads((ROOT / "SCORER_FREEZE_003.json").read_text("utf-8"))
        self.assertEqual(sha256_file(ROOT / "SCORER_FREEZE_003.json"), SCORER_FREEZE_SHA256)
        for rel, expected in obj["source_files"].items():
            self.assertEqual(sha256_file(ROOT / rel), expected)
        self.assertEqual(obj["secondary_metric"]["status"], "UNAVAILABLE_NO_FROZEN_OPERATIONAL_TRANSFORM")

    def test_frozen_integer_scorer_is_deterministic(self):
        a = score_trace(trace_for("P01-X"))
        b = score_trace(trace_for("P01-X"))
        self.assertEqual(a, b)
        self.assertEqual(a["score_status"], "AVAILABLE")
        self.assertIsInstance(a["kappa_micros"], int)
        self.assertGreater(a["kappa_micros"], 0)

    def test_scorer_rejects_boolean_as_integer_observation(self):
        trace = trace_for("P01-X")
        trace["steps"][0]["write_events"] = False
        with self.assertRaises(ValueError):
            score_trace(trace)

    def test_interpretation_is_publish_regardless(self):
        self.assertEqual(interpretation(16, 14, 0, 30)[0], "DIRECTIONAL_SENSITIVITY")
        self.assertEqual(interpretation(15, 15, 0, 30)[0], "CHANCE_LEVEL_NO_USEFUL_SEPARATION")
        self.assertEqual(interpretation(14, 16, 0, 30)[0], "ADVERSE_RESULT")
        self.assertEqual(interpretation(14, 14, 0, 28)[0], "DESCRIPTIVE_ONLY_FROZEN_30_PAIR_DENOMINATOR_NOT_MET")

    def test_incomplete_execution_yields_publishable_blind_final_result(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); public = d / "public"; out = d / "out"; public.mkdir()
            zpath = public / "PUBLIC_SCORER_EXECUTION_BUNDLE.zip"
            with zipfile.ZipFile(zpath, "w"):
                pass
            receipt = {
                "experiment_id": EXPERIMENT_ID,
                "status": "EXECUTION_INCOMPLETE_BLIND",
                "public_bundle_sha256": sha256_file(zpath),
                "assignment_created": True,
                "expected_execution_count": 60,
                "received_execution_record_count": 14,
                "infrastructure_failure_receipt_count": 3,
                "infrastructure_failure_classes": {"HTTP_429_RATE_LIMIT_TRANSIENT": 3},
            }
            (public / "EXECUTION_RECEIPT.json").write_bytes(pretty_json_bytes(receipt))
            gate = blind_score(public_dir=public, out_dir=out)
            self.assertFalse(gate["ready_for_unblind"])
            result = json.loads((out / "FINAL_BENCHMARK_RESULT.json").read_text("utf-8"))
            self.assertEqual(result["status"], "FINAL_CAPSTONE_INFRASTRUCTURE_ABORTED_BLIND")
            self.assertFalse(result["partial_scoring_performed"])
            self.assertFalse(result["unblinded"])
            self.assertTrue(result["publication_required"])

    def _make_complete_chain(self, d: Path):
        pub, sealed, secret = d / "assignment-public", d / "sealed", d / "secret"
        lock = generate_assignment(
            pair_set_path=ROOT / "frozen_scientific/PAIR_SET_FROZEN.json",
            public_dir=pub,
            sealed_dir=sealed,
            secret_dir=secret,
            design_sha256=SCIENTIFIC_HASHES["BENCHMARK_DESIGN_FROZEN.json"],
            pair_set_sha256=SCIENTIFIC_HASHES["PAIR_SET_FROZEN.json"],
            signal_schema_sha256=SCIENTIFIC_HASHES["SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json"],
            perturbation_sha256=SCIENTIFIC_HASHES["PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json"],
            preflight_pass_sha256="a" * 64,
            runner_manifest_sha256="b" * 64,
            publication_commitment_sha256=PUBLICATION_COMMITMENT_SHA256,
            scorer_freeze_sha256=SCORER_FREEZE_SHA256,
            dry_run=False,
        )
        public_run = d / "public-run"
        build_complete_public_bundle(public_run, pub / "blinded_run_manifest.json")
        blind = d / "blind"
        gate = blind_score(public_dir=public_run, out_dir=blind)
        verify_dir = d / "verify"
        independent_verify(
            public_dir=public_run,
            blind_dir=blind,
            out=verify_dir / "INDEPENDENT_BLIND_SCORE_VERIFICATION.json",
        )
        return pub, sealed, secret, lock, public_run, blind, verify_dir, gate

    def test_complete_end_to_end_capstone_seals_result_after_independent_verification(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            pub, sealed, secret, lock, public_run, blind, verify_dir, gate = self._make_complete_chain(d)
            self.assertEqual(lock["publication_commitment_sha256"], PUBLICATION_COMMITMENT_SHA256)
            self.assertTrue(gate["ready_for_unblind"])
            final = d / "final"
            receipt = unblind_and_publish(
                blind_dir=blind,
                verification_dir=verify_dir,
                sealed_zip=sealed / "SEALED_CONDITION_BUNDLE.zip",
                key_path=secret / "secret_key.bin",
                assignment_lock_path=pub / "ASSIGNMENT_LOCK.json",
                out_dir=final,
            )
            self.assertEqual(receipt["status"], "FINAL_CAPSTONE_PUBLICATION_BUNDLE_SEALED")
            result = json.loads((final / "FINAL_BENCHMARK_RESULT.json").read_text("utf-8"))
            self.assertTrue(result["unblinded"])
            self.assertTrue(result["publication_required"])
            self.assertEqual(result["scoring_publication_path_condition_map_open_count"], 1)
            self.assertFalse(result["condition_material_accessed_by_blind_scorer"])
            self.assertFalse(result["condition_material_accessed_by_independent_verifier"])
            self.assertEqual(result["primary_result"]["evaluable_pair_count"], 30)
            total = (
                result["primary_result"]["perturbation_higher_count"]
                + result["primary_result"]["control_higher_count"]
                + result["primary_result"]["tie_count"]
            )
            self.assertEqual(total, 30)
            self.assertTrue((final / "FINAL_CAPSTONE_PUBLICATION_BUNDLE.zip").exists())
            self.assertTrue((final / "FINAL_CAPSTONE_PUBLICATION_BUNDLE.zip.sha256").exists())
            self.assertFalse((secret / "secret_key.bin").exists())

    def test_unblind_rejects_tampered_independent_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            pub, sealed, secret, lock, public_run, blind, verify_dir, gate = self._make_complete_chain(d)
            p = verify_dir / "INDEPENDENT_BLIND_SCORE_VERIFICATION.json"
            obj = json.loads(p.read_text("utf-8")); obj["records_recomputed"] = 59
            p.write_bytes(pretty_json_bytes(obj))
            (verify_dir / "INDEPENDENT_BLIND_SCORE_VERIFICATION.json.sha256").write_text(
                f"{sha256_file(p)}  INDEPENDENT_BLIND_SCORE_VERIFICATION.json\n", encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                unblind_and_publish(
                    blind_dir=blind,
                    verification_dir=verify_dir,
                    sealed_zip=sealed / "SEALED_CONDITION_BUNDLE.zip",
                    key_path=secret / "secret_key.bin",
                    assignment_lock_path=pub / "ASSIGNMENT_LOCK.json",
                    out_dir=d / "final",
                )
            self.assertFalse((secret / "secret_key.bin").exists())

    def test_unblind_rejects_plaintext_commitment_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            pub, sealed, secret, lock, public_run, blind, verify_dir, gate = self._make_complete_chain(d)
            lock_path = pub / "ASSIGNMENT_LOCK.json"
            obj = json.loads(lock_path.read_text("utf-8")); obj["condition_map_plaintext_sha256"] = "0" * 64
            lock_path.write_bytes(canonical_json_bytes(obj))
            with self.assertRaises(ValueError):
                unblind_and_publish(
                    blind_dir=blind,
                    verification_dir=verify_dir,
                    sealed_zip=sealed / "SEALED_CONDITION_BUNDLE.zip",
                    key_path=secret / "secret_key.bin",
                    assignment_lock_path=lock_path,
                    out_dir=d / "final",
                )
            self.assertFalse((secret / "secret_key.bin").exists())

    def test_workflow_blind_jobs_cannot_download_key(self):
        text = (REPO_ROOT / ".github/workflows/olp-30pair-003-execution.yml").read_text("utf-8")
        blind = text.split("  blind-score-and-capstone-gate:", 1)[1].split("  independently-verify-blind-scores:", 1)[0]
        verify = text.split("  independently-verify-blind-scores:", 1)[1].split("  unblind-once-and-publish:", 1)[0]
        unblind = text.split("  unblind-once-and-publish:", 1)[1].split("  publish-blind-infrastructure-capstone:", 1)[0]
        self.assertNotIn("secret-key-material", blind)
        self.assertNotIn("secret-key-material", verify)
        self.assertIn("secret-key-material-DO-NOT-SCORE", unblind)
        self.assertLess(text.index("blind-score-and-capstone-gate"), text.index("unblind-once-and-publish"))


if __name__ == "__main__":
    unittest.main()
