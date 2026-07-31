from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pre_run_gate
from assignment import decrypt_map_in_memory, generate_assignment
from collect_execution import collect
from common import FROZEN_HASHES, PREFLIGHT_PASS_SHA256, sha256_file
from execute_pair import make_invalid_record, make_trace
from responses_agent import TOOL_DEFS
from perturbation import OneShotEligibleReadDelivery, final_quarter_truncate
from trace_format import OperationalMapper, ToolObservation, assert_export_safe
from tool_runtime import ToolRuntime


class ExecutionHarnessTests(unittest.TestCase):
    def setUp(self):
        self.frozen = HERE / "frozen"
        self.pair_set = self.frozen / "PAIR_SET_FROZEN.json"

    def _copy_frozen(self, target: Path):
        target.mkdir(parents=True)
        for p in self.frozen.iterdir():
            if p.is_file():
                shutil.copy2(p, target / p.name)

    def test_frozen_hash_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "frozen"
            self._copy_frozen(f)
            (f / "PAIR_SET_FROZEN.json").write_bytes((f / "PAIR_SET_FROZEN.json").read_bytes() + b" ")
            with mock.patch.object(pre_run_gate, "FROZEN", f), mock.patch.object(pre_run_gate, "verify_network_sandbox", lambda out: None):
                with self.assertRaises(SystemExit):
                    pre_run_gate.run_gate(out=Path(td) / "blocked.json", require_api_key=False)
                obj = json.loads((Path(td) / "EXECUTION_BLOCKED.json").read_text())
                self.assertEqual(obj["failed_stage"], "frozen_hashes")
                self.assertEqual(obj["benchmark_model_calls"], 0)
                self.assertEqual(obj["real_condition_assignments"], 0)

    def test_missing_api_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(pre_run_gate, "verify_network_sandbox", lambda out: None), mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                pre_run_gate.run_gate(out=Path(td) / "blocked.json", require_api_key=True)
            obj = json.loads((Path(td) / "EXECUTION_BLOCKED.json").read_text())
            self.assertEqual(obj["failed_stage"], "api_key")

    def _config_tamper(self, field, value):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        f = root / "frozen"
        self._copy_frozen(f)
        obj = json.loads((f / "PAIR_SET_FROZEN.json").read_text())
        obj["common_execution_config"][field] = value
        # Intentionally make the tampered bytes match a patched expected hash so this test reaches config validation.
        (f / "PAIR_SET_FROZEN.json").write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
        expected = dict(pre_run_gate.FROZEN_HASHES)
        expected["PAIR_SET_FROZEN.json"] = sha256_file(f / "PAIR_SET_FROZEN.json")
        return td, f, expected

    def test_wrong_model_fails_closed(self):
        td, f, expected = self._config_tamper("model", "wrong-model")
        with td, mock.patch.object(pre_run_gate, "FROZEN", f), mock.patch.object(pre_run_gate, "FROZEN_HASHES", expected), mock.patch.object(pre_run_gate, "verify_network_sandbox", lambda out: None), mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True):
            with self.assertRaises(SystemExit):
                pre_run_gate.run_gate(out=Path(td.name) / "blocked.json", require_api_key=True)
            self.assertEqual(json.loads((Path(td.name) / "EXECUTION_BLOCKED.json").read_text())["failed_stage"], "execution_config")

    def test_wrong_reasoning_effort_fails_closed(self):
        td, f, expected = self._config_tamper("reasoning_effort", "high")
        with td, mock.patch.object(pre_run_gate, "FROZEN", f), mock.patch.object(pre_run_gate, "FROZEN_HASHES", expected), mock.patch.object(pre_run_gate, "verify_network_sandbox", lambda out: None), mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=True):
            with self.assertRaises(SystemExit):
                pre_run_gate.run_gate(out=Path(td.name) / "blocked.json", require_api_key=True)
            self.assertEqual(json.loads((Path(td.name) / "EXECUTION_BLOCKED.json").read_text())["failed_stage"], "execution_config")

    def _disposable_assignment(self, root: Path):
        pub, sealed, secret = root / "pub", root / "sealed", root / "secret"
        lock = generate_assignment(
            pair_set_path=self.pair_set,
            public_dir=pub,
            sealed_dir=sealed,
            secret_dir=secret,
            design_sha256=FROZEN_HASHES["BENCHMARK_DESIGN_FROZEN.json"],
            pair_set_sha256=FROZEN_HASHES["PAIR_SET_FROZEN.json"],
            signal_schema_sha256=FROZEN_HASHES["SIGNAL_SCHEMA_FROZEN_SCOPE_REPAIRED.json"],
            perturbation_sha256=FROZEN_HASHES["PERTURBATION_SPEC_FROZEN_SCOPE_REPAIRED.json"],
            preflight_pass_sha256=PREFLIGHT_PASS_SHA256,
            runner_manifest_sha256="0" * 64,
            dry_run=True,
        )
        return pub, sealed, secret, lock

    def test_disposable_assignment_is_30_pairs_60_opaque_balanced(self):
        with tempfile.TemporaryDirectory() as td:
            pub, sealed, secret, lock = self._disposable_assignment(Path(td))
            manifest = json.loads((pub / "blinded_run_manifest.json").read_text())
            self.assertEqual(len(manifest["executions"]), 60)
            self.assertEqual(len({r["opaque_execution_id"] for r in manifest["executions"]}), 60)
            secret_map = decrypt_map_in_memory(sealed, secret / "secret_key.bin")
            by_pair = {}
            for row in secret_map["conditions"]:
                by_pair.setdefault(row["pair_id"], []).append(row["condition"])
            self.assertEqual(len(by_pair), 30)
            self.assertTrue(all(sorted(v) == ["CLEAN", "PERTURBED"] for v in by_pair.values()))
            public_bytes = b"".join(p.read_bytes() for p in pub.iterdir() if p.is_file())
            self.assertNotIn(b'"CLEAN"', public_bytes)
            self.assertNotIn(b'"PERTURBED"', public_bytes)
            self.assertFalse((pub / "secret_key.bin").exists())
            self.assertFalse((pub / "condition_map.enc").exists())
            self.assertFalse((sealed / "secret_key.bin").exists())
            self.assertFalse((sealed / "condition_map.json").exists())
            self.assertEqual(lock["pair_count"], 30)
            self.assertEqual(lock["execution_count"], 60)

    def test_condition_commitment_has_secret_256bit_nonce_not_public(self):
        with tempfile.TemporaryDirectory() as td:
            pub, sealed, secret, lock = self._disposable_assignment(Path(td))
            secret_map = decrypt_map_in_memory(sealed, secret / "secret_key.bin")
            import base64
            nonce = base64.b64decode(secret_map["commitment_nonce_b64"])
            self.assertEqual(len(nonce), 32)
            self.assertEqual(lock["condition_map_commitment_scheme"], "SHA256_CANONICAL_JSON_WITH_SECRET_256BIT_NONCE")
            self.assertEqual(lock["commitment_nonce_bits"], 256)
            public_bytes = b"".join(p.read_bytes() for p in pub.iterdir() if p.is_file())
            sealed_public_bytes = b"".join(p.read_bytes() for p in sealed.iterdir() if p.is_file())
            self.assertNotIn(secret_map["commitment_nonce_b64"].encode(), public_bytes)
            self.assertNotIn(secret_map["commitment_nonce_b64"].encode(), sealed_public_bytes)
            # A candidate built from the known 60 condition rows but without the secret nonce cannot hit the commitment.
            candidate = {
                "schema": secret_map["schema"],
                "experiment_id": secret_map["experiment_id"],
                "conditions": secret_map["conditions"],
            }
            from common import canonical_json_bytes, sha256_bytes
            self.assertNotEqual(sha256_bytes(canonical_json_bytes(candidate)), lock["condition_map_plaintext_sha256"])

    def test_commitment_nonce_is_fresh_across_disposable_assignments(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, sealed1, secret1, _ = self._disposable_assignment(root / "a")
            _, sealed2, secret2, _ = self._disposable_assignment(root / "b")
            m1 = decrypt_map_in_memory(sealed1, secret1 / "secret_key.bin")
            m2 = decrypt_map_in_memory(sealed2, secret2 / "secret_key.bin")
            self.assertNotEqual(m1["commitment_nonce_b64"], m2["commitment_nonce_b64"])

    def test_pre_run_failure_uses_literal_execution_blocked_filename(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(pre_run_gate, "verify_network_sandbox", lambda out: None), mock.patch.dict(os.environ, {}, clear=True):
            requested = Path(td) / "PRE_RUN_GATE.json"
            with self.assertRaises(SystemExit):
                pre_run_gate.run_gate(out=requested, require_api_key=True)
            self.assertFalse(requested.exists())
            blocked = Path(td) / "EXECUTION_BLOCKED.json"
            self.assertTrue(blocked.exists())
            self.assertEqual(json.loads(blocked.read_text())["status"], "EXECUTION_BLOCKED")

    def test_unicode_final_quarter_truncation_exact(self):
        text = "A🙂Bé中Z"  # six Unicode code points
        # ceil(6/4)=2 => keep first four code points exactly.
        self.assertEqual(final_quarter_truncate(text), "A🙂Bé")
        text2 = "abcdefg"  # ceil(7/4)=2 => keep five
        self.assertEqual(final_quarter_truncate(text2), "abcde")

    def test_perturbation_delivery_exactly_once(self):
        d = OneShotEligibleReadDelivery()
        self.assertEqual(d.deliver("abcdefgh", alter=True), "abcdef")
        with self.assertRaises(RuntimeError):
            d.deliver("abcdefgh", alter=True)
        clean = OneShotEligibleReadDelivery()
        self.assertEqual(clean.deliver("abcdefgh", alter=False), "abcdefgh")

    def test_condition_specific_instrumentation_is_rejected(self):
        self.assertNotIn("condition", inspect.signature(OperationalMapper.record_completed_tool).parameters)
        with self.assertRaises(ValueError):
            assert_export_safe({"condition": "CLEAN"})
        with self.assertRaises(ValueError):
            assert_export_safe({"x": "PERTURBED"})

    def test_mapper_exports_only_frozen_observations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "a.txt").write_text("one")
            m = OperationalMapper(root)
            (root / "a.txt").write_text("two")
            step = m.record_completed_tool(ToolObservation("run_shell"))
            self.assertEqual(set(step), {"index", "tool_name", "write_events", "revision_events", "dependency_edges_after_step", "state_fields_after_step"})
            self.assertEqual(step["write_events"], 1)
            self.assertEqual(step["revision_events"], 1)

    def test_invalid_pair_records_use_original_opaque_ids_no_replacement(self):
        ids = ["P07-X", "P07-Y"]
        rows = [make_invalid_record(i, "P07", "REQUIRED_FROZEN_SIGNAL_OBSERVATIONS_CANNOT_BE_EMITTED", model_calls=2, returned_models={"gpt-5.5-2026-04-23"}) for i in ids]
        self.assertEqual([r["opaque_execution_id"] for r in rows], ids)
        self.assertTrue(all(r["pair_id"] == "P07" for r in rows))
        src = (HERE / "execute_pair.py").read_text("utf-8")
        self.assertNotIn("replacement_pair", src)
        self.assertNotIn("substitute_pair", src)

    def test_raw_perturbation_fields_rejected_from_export(self):
        for key in ["original_text", "returned_text", "original_length", "returned_length", "truncation_fraction", "perturbation_applied"]:
            with self.assertRaises(ValueError, msg=key):
                assert_export_safe({key: "x"})

    def test_public_scorer_bundle_cannot_reconstruct_labels_or_key(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pub, sealed, secret, lock = self._disposable_assignment(root / "assign")
            pair_artifacts = root / "pairs"
            pair_artifacts.mkdir()
            manifest = json.loads((pub / "blinded_run_manifest.json").read_text())
            for row in manifest["executions"]:
                oid = row["opaque_execution_id"]
                trace = make_trace(
                    oid,
                    row["pair_id"],
                    [
                        {"index": 1, "tool_name": "read_file", "write_events": 0, "revision_events": 0, "dependency_edges_after_step": ["step:0001|reads|file:a"], "state_fields_after_step": {"file:a": "sha256:" + "1"*64}},
                        {"index": 2, "tool_name": "read_file", "write_events": 0, "revision_events": 0, "dependency_edges_after_step": ["step:0001|reads|file:a", "step:0002|reads|file:b"], "state_fields_after_step": {"file:a": "sha256:" + "1"*64, "file:b": "sha256:" + "2"*64}},
                        {"index": 3, "tool_name": "apply_patch", "write_events": 1, "revision_events": 1, "dependency_edges_after_step": ["step:0003|writes|file:a"], "state_fields_after_step": {"file:a": "sha256:" + "3"*64}},
                    ],
                    {"kind": "FINAL_AGENT_ANSWER", "tool_calls_total": 3, "active_seconds": 1.0},
                    model_calls=2,
                    returned_models={"gpt-5.5-2026-04-23"},
                )
                p = pair_artifacts / f"{oid}.json"
                p.write_text(json.dumps(trace))
                (pair_artifacts / f"{oid}.json.sha256").write_text("dummy")
            for i in range(1,31):
                pr = {"schema":"openline.paired-mechanism-benchmark.pair-verification.v1","experiment_id":"olp-core21-paired-mechanism-001","pair_id":f"P{i:02d}","pair_disposition":"PAIR_VALID_FOR_BLIND_SCORING","benchmark_model_calls":4,"returned_models":["gpt-5.5-2026-04-23"],"unblinded":False}
                (pair_artifacts / f"P{i:02d}.verification.json").write_text(json.dumps(pr))
            out = root / "out"
            receipt = collect(
                pair_artifacts=pair_artifacts,
                blinded_manifest=pub / "blinded_run_manifest.json",
                assignment_lock=pub / "ASSIGNMENT_LOCK.json",
                sealed_condition_zip=sealed / "SEALED_CONDITION_BUNDLE.zip",
                out_dir=out,
                runner_manifest_sha256="0" * 64,
            )
            self.assertEqual(receipt["status"], "EXECUTION_COMPLETE_BLIND")
            with zipfile.ZipFile(out / "PUBLIC_SCORER_EXECUTION_BUNDLE.zip") as z:
                all_bytes = b"".join(z.read(n) for n in z.namelist())
            self.assertNotIn(b'"CLEAN"', all_bytes)
            self.assertNotIn(b'"PERTURBED"', all_bytes)
            self.assertNotIn((secret / "secret_key.bin").read_bytes(), all_bytes)
            self.assertNotIn(b"condition_map.enc", all_bytes)
            for token in [b"original_text", b"returned_text", b"original_length", b"returned_length", b"truncation_fraction", b"perturbation_applied"]:
                self.assertNotIn(token, all_bytes)


    def test_agent_shell_environment_excludes_runner_secrets(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": "secret-openai",
            "GITHUB_TOKEN": "secret-github",
            "PATH": os.environ.get("PATH", ""),
        }, clear=True):
            env = ToolRuntime(Path(td))._safe_env()
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("secret-openai", json.dumps(env))
            self.assertNotIn("secret-github", json.dumps(env))


    def test_tool_descriptions_match_frozen_pair_set_exactly(self):
        cfg = json.loads(self.pair_set.read_text("utf-8"))["common_execution_config"]
        actual = {t["name"]: t["description"] for t in TOOL_DEFS}
        self.assertEqual(actual, cfg["tools"])

    def test_execution_workspace_fetches_only_preflight_parent_not_child(self):
        src = (HERE / "execute_pair.py").read_text("utf-8")
        self.assertIn('"--depth=1"', src)
        self.assertIn('expected_parent', src)
        self.assertNotIn('["git", "fetch", "-q", "--depth=2", "--no-tags", "origin", pair["task_commit_sha"]]', src)
        self.assertIn("historical child/task commit", src)

    def test_assignment_once_guard_checks_artifact_and_prior_job_history(self):
        src = (HERE / "guard_once.py").read_text("utf-8")
        self.assertIn("assignment_lock_artifact", src)
        self.assertIn("prior_assign_job_attempt", src)
        self.assertIn("assign-once", src)
        self.assertIn("event=push", src)
        self.assertNotIn("event=workflow_dispatch", src)
        workflow = (HERE.parents[1] / ".github" / "workflows" / "olp-30pair-execution.yml").read_text("utf-8")
        self.assertIn("Assignment job reruns are forbidden", workflow)

    def test_public_execution_filenames_are_opaque(self):
        allowed = re.compile(r"^P\d{2}-[XY]\.json$")
        for i in range(1, 31):
            self.assertRegex(f"P{i:02d}-X.json", allowed)
            self.assertRegex(f"P{i:02d}-Y.json", allowed)
        self.assertNotRegex("CLEAN.json", allowed)
        self.assertNotRegex("PERTURBED.json", allowed)

    def test_workflow_forbids_behavioral_rerun_attempts(self):
        workflow = (HERE.parents[1] / ".github" / "workflows" / "olp-30pair-execution.yml").read_text("utf-8")
        self.assertIn('GITHUB_RUN_ATTEMPT', workflow)
        self.assertIn('Behavioral execution reruns are forbidden', workflow)
        self.assertIn('on:\n  push:\n    tags:', workflow)
        self.assertIn('RUN_REAL_OLP_CORE21_PAIRED_MECHANISM_001', workflow)
        self.assertIn('refs/tags/${REAL_RUN_TAG}', workflow)
        self.assertIn('git merge-base --is-ancestor 54d906cce8354bd58d1fd664a5028c4e0ec1f0be HEAD', workflow)
        self.assertNotIn('workflow_dispatch:', workflow)
        self.assertNotIn('inputs.confirmation', workflow)

    def test_execution_bundle_contains_no_scorer_or_unblinder_code(self):
        names = {p.name for p in HERE.glob("*.py")}
        self.assertNotIn("score.py", names)
        self.assertNotIn("scorer.py", names)
        self.assertNotIn("unblind.py", names)
        self.assertNotIn("unblind_once.py", names)
        workflow = (HERE.parents[1] / ".github" / "workflows" / "olp-30pair-execution.yml").read_text("utf-8")
        self.assertNotIn("unblind_once.py", workflow)
        self.assertNotIn("score.py", workflow)
        self.assertNotIn("scorer.py", workflow)

    def test_no_real_assignment_artifact_exists_after_tests(self):
        build = HERE / "build" / "assignment"
        self.assertFalse(build.exists())


if __name__ == "__main__":
    unittest.main()
