from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from external_replication.adapter import (
    ExternalSchemaError,
    normalized_model,
    normalize_messages,
    record_and_label,
    repository_from_external,
    source_admissibility,
)
from external_replication.canonical import canonical_bytes
from external_replication.evaluate import bootstrap, disposition, operating_metrics_for_result
from external_replication.prepare import prepare_external
from external_replication.profile import apply_profile, serialize_pipeline


class Tests(unittest.TestCase):
    def test_message_normalization(self):
        raw = json.dumps([
            {"role": "assistant", "content": "", "tool_calls_json": json.dumps([{"function": {"name": "pytest", "arguments": "-q"}}])},
            {"role": "tool", "content": "1 passed"},
        ])
        out = normalize_messages(raw)
        self.assertEqual(out[0]["role"], "ai")
        self.assertIn("pytest", out[0]["text"])
        self.assertEqual(out[1]["role"], "user")

    def test_repository(self):
        self.assertEqual(repository_from_external("iterative__dvc.1d6ea681.pr_3727.x"), "iterative__dvc")

    def test_model_null_is_provenance_unknown_not_identity_failure(self):
        self.assertEqual(normalized_model(None), "unknown")
        self.assertEqual(normalized_model(""), "unknown")

    def test_overlap_classification(self):
        row = {"source_dataset": "nebius-swe-rebench-openhands"}
        self.assertEqual(source_admissibility(row), "EXCLUDED_SOURCE_OVERLAP")

    def test_klear_classification_is_unscorable(self):
        row = {"source_dataset": "kwai-klear-swe-smith-mini"}
        self.assertEqual(source_admissibility(row), "EXCLUDED_NO_OUTCOME_LABEL")

    def test_klear_cannot_reach_labeled_extractor(self):
        row = {
            "session_id": "s",
            "source_dataset": "kwai-klear-swe-smith-mini",
            "source_id": "owner__repo.x",
            "recorded_model": None,
            "messages_json": "[]",
            "ground_truth_meta_json": json.dumps({"instance_id": "owner__repo.x"}),
        }
        with self.assertRaises(ExternalSchemaError):
            record_and_label(row)

    def test_label_and_identity(self):
        row = {
            "session_id": "s",
            "source_dataset": "swe-smith-claude-3-7-sonnet",
            "source_id": "owner__repo.x",
            "recorded_model": "m",
            "messages_json": json.dumps([{"role": "assistant", "content": "pytest -q"}, {"role": "tool", "content": "1 passed"}]),
            "ground_truth_meta_json": json.dumps({"resolved": True, "instance_id": "owner__repo.x"}),
        }
        record, label = record_and_label(row)
        self.assertEqual(label["target"], 1)
        self.assertEqual(record.repository, "owner__repo")

    def test_included_row_requires_real_label(self):
        row = {
            "session_id": "s",
            "source_dataset": "swe-smith-claude-3-7-sonnet",
            "source_id": "owner__repo.x",
            "recorded_model": "m",
            "messages_json": "[]",
            "ground_truth_meta_json": json.dumps({"instance_id": "owner__repo.x"}),
        }
        with self.assertRaisesRegex(ExternalSchemaError, "resolved label"):
            record_and_label(row)


    def test_prepare_external_filters_unscorable_without_fabrication(self):
        rows = [
            {
                "session_id": "swe::one",
                "source_dataset": "swe-smith-claude-3-7-sonnet",
                "source_id": "owner__repo.task.one",
                "recorded_model": "claude",
                "messages_json": json.dumps([
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "fix"},
                    {"role": "assistant", "content": "pytest -q"},
                    {"role": "tool", "content": "1 passed"},
                ]),
                "ground_truth_meta_json": json.dumps({"instance_id": "owner__repo.task", "resolved": True}),
            },
            {
                "session_id": "klear::one",
                "source_dataset": "kwai-klear-swe-smith-mini",
                "source_id": "other__repo.task",
                "recorded_model": None,
                "messages_json": "[]",
                "ground_truth_meta_json": json.dumps({"instance_id": "other__repo.task"}),
            },
            {
                "session_id": "nebius::one",
                "source_dataset": "nebius-swe-rebench-openhands",
                "source_id": "third__repo.task",
                "recorded_model": "m",
                "messages_json": "[]",
                "ground_truth_meta_json": json.dumps({"instance_id": "third__repo.task", "resolved": False}),
            },
        ]
        config = {
            "expected_total_rows": 3,
            "expected_source_rows": {
                "swe-smith-claude-3-7-sonnet": 1,
                "kwai-klear-swe-smith-mini": 1,
                "nebius-swe-rebench-openhands": 1,
            },
            "included_sources": {"swe-smith-claude-3-7-sonnet": 1},
            "excluded_source_reasons": {
                "kwai-klear-swe-smith-mini": "NO_INDEPENDENT_RESOLVED_OUTCOME_IN_PINNED_EXTERNAL_FILE",
                "nebius-swe-rebench-openhands": "SOURCE_OVERLAP_WITH_DEVELOPMENT_CORPUS",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "external.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            output = Path(directory) / "prepared"
            receipt = prepare_external(path, output, config)
            self.assertEqual(receipt["rows"], 1)
            audit = json.loads((output / "EXTERNAL_SCHEMA_AUDIT.json").read_text())
            self.assertEqual(audit["admissibility_rows"]["EXCLUDED_NO_OUTCOME_LABEL"], 1)
            self.assertEqual(audit["admissibility_rows"]["EXCLUDED_SOURCE_OVERLAP"], 1)
            labels = pd.read_csv(output / "labels_sealed.csv")
            self.assertEqual(len(labels), 1)
            self.assertEqual(labels.iloc[0]["source_dataset"], "swe-smith-claude-3-7-sonnet")

    def test_frozen_application_matches_sklearn(self):
        frame = pd.DataFrame({"a": [1.0, 2.0, np.nan, 4.0], "b": [0.0, 1.0, 2.0, 3.0]})
        target = [0, 0, 1, 1]
        columns = ["a", "b"]
        prep = ColumnTransformer([("numeric", Pipeline([("impute", SimpleImputer(strategy="median", keep_empty_features=True)), ("scale", StandardScaler())]), columns)], remainder="drop")
        pipeline = Pipeline([("prep", prep), ("model", LogisticRegression(C=1.0, class_weight="balanced", max_iter=3000, solver="liblinear", random_state=20260804))])
        pipeline.fit(frame, target)
        profile = serialize_pipeline(pipeline, family="simple", columns=columns, C=1.0, threshold=0.5)
        np.testing.assert_allclose(apply_profile(frame, profile), pipeline.predict_proba(frame)[:, 1], rtol=0, atol=1e-15)


    def test_zero_review_precision_is_explicit_null_not_nan(self):
        y = pd.Series([0, 1, 0, 1])
        probability = np.array([0.10, 0.20, 0.30, 0.40])
        result = operating_metrics_for_result(y, probability, 0.99)
        self.assertEqual(result["true_positive"], 0)
        self.assertEqual(result["false_positive"], 0)
        self.assertIsNone(result["precision"])
        self.assertEqual(result["metric_status"]["precision"], "UNDEFINED_NO_PREDICTED_POSITIVES")
        canonical_bytes(result)

    def test_available_operating_metrics_remain_numeric(self):
        y = pd.Series([0, 1, 0, 1])
        probability = np.array([0.10, 0.90, 0.20, 0.80])
        result = operating_metrics_for_result(y, probability, 0.50)
        self.assertEqual(result["precision"], 1.0)
        self.assertEqual(result["metric_status"]["precision"], "AVAILABLE")
        canonical_bytes(result)

    def test_positive_disposition_single_included_cohort(self):
        self.assertEqual(disposition(0.03, 0.0, {"lower_95": 0.01, "upper_95": 0.05}, {"swe": 0.03}), "CD_ADDS_EXTERNAL_SIGNAL")

    def test_negative_disposition(self):
        self.assertEqual(disposition(-0.02, 0.0, {"lower_95": -0.03, "upper_95": -0.01}, {"swe": -0.02}), "BASELINE_OUTPERFORMS_CD")

    def test_equivalent_disposition(self):
        self.assertEqual(disposition(0.005, 0.0, {"lower_95": -0.01, "upper_95": 0.02}, {"swe": 0.005}), "BASELINE_EQUIVALENT")

    def test_bootstrap_deterministic(self):
        frame = pd.DataFrame({"task_group": ["a", "a", "b", "b"], "target": [0, 1, 0, 1]})
        base = np.array([0.1, 0.8, 0.2, 0.7])
        extended = np.array([0.1, 0.9, 0.1, 0.8])
        self.assertEqual(bootstrap(frame, base, extended, 50), bootstrap(frame, base, extended, 50))

    def test_protocol_schema_repair_frozen(self):
        root = Path(__file__).resolve().parents[1]
        protocol = json.loads((root / "REPLICATION_PROTOCOL.json").read_text())
        self.assertEqual(protocol["replication_id"], "CD_EXTERNAL_CODING_TRAJECTORY_REPLICATION_003")
        self.assertEqual(protocol["status"], "FROZEN_AFTER_EXTERNAL_SCHEMA_REPAIR_BEFORE_EXTERNAL_OUTCOME_SCORING")
        self.assertEqual(protocol["external_dataset"]["expected_included_rows"], 5000)
        self.assertEqual(protocol["external_dataset"]["included_sources"], {"swe-smith-claude-3-7-sonnet": 5000})

    def test_numeric_result_rule_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        protocol = json.loads((root / "REPLICATION_PROTOCOL.json").read_text())
        self.assertEqual(protocol["positive_gate"]["pr_auc_delta_gt"], 0.02)
        self.assertEqual(protocol["positive_gate"]["bootstrap_lower_95_gt"], 0.0)
        self.assertEqual(protocol["positive_gate"]["roc_auc_delta_gte"], -0.005)

    def test_no_external_refit_language(self):
        root = Path(__file__).resolve().parents[1]
        protocol = json.loads((root / "REPLICATION_PROTOCOL.json").read_text())
        self.assertIn("without external fitting", protocol["modeling"])

    def test_source_recovery_protocol_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        recovery = json.loads((root / "SOURCE_PROFILE_RECOVERY.json").read_text())
        self.assertEqual(recovery["source_metric_sanity"]["max_absolute_delta_each_metric"], 1e-4)

    def test_source_lock_remains_historical(self):
        root = Path(__file__).resolve().parents[1]
        lock = json.loads((root / "SOURCE_PROFILE_LOCK.json").read_text())
        self.assertEqual(lock["freeze_rule"].split("within ")[-1], "1e-12.")

    def test_workflow_seals_profile_before_external(self):
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github/workflows/cd-external-replication.yml").read_text()
        self.assertLess(workflow.index("Verify source profile was sealed before external access"), workflow.index("Acquire pinned external dataset"))

    def test_workflow_uploads_schema_audit(self):
        root = Path(__file__).resolve().parents[3]
        workflow = (root / ".github/workflows/cd-external-replication.yml").read_text()
        self.assertIn("EXTERNAL_SCHEMA_AUDIT.json", workflow)

    def test_no_cv_reselection_in_recovery(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src/external_replication/profile.py").read_text()
        self.assertNotIn("fit_family", source)
        self.assertIn("pipeline(columns, C=C)", source)

    def test_result_serialization_repair_is_representational_only(self):
        root = Path(__file__).resolve().parents[1]
        receipt = json.loads((root / "RESULT_SERIALIZATION_REPAIR_RECEIPT.json").read_text())
        self.assertFalse(receipt["repair"]["source_profile_changed"])
        self.assertFalse(receipt["repair"]["source_thresholds_changed"])
        self.assertFalse(receipt["repair"]["external_result_rule_changed"])
        self.assertTrue(receipt["repair"]["strict_json_preserved"])
        self.assertEqual(receipt["scientific_status"], "RESULT_NOT_PERSISTED_RERUN_REQUIRED")

    def test_runtime_lock_retained_not_identity(self):
        root = Path(__file__).resolve().parents[1]
        runtime = json.loads((root / "RUNTIME_LOCK.json").read_text())
        self.assertEqual(runtime["status"], "PINNED_RUNTIME_RETAINED_FOR_EXECUTION_NOT_USED_AS_MODEL_IDENTITY")


if __name__ == "__main__":
    unittest.main()
