from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from public_trajectory_audit.action_parser import parse_action
from public_trajectory_audit.canonical import canonical_json, sha256_bytes
from public_trajectory_audit.curvature import curvature_point_micros, curvature_series_micros
from public_trajectory_audit.features import extract_prefix
from public_trajectory_audit.nebius import LeakageError, blind_record, repository_from_instance, sanitize_row
from public_trajectory_audit.split import repository_holdout


def fake_row(instance_id="repoA__pkg-1", target=True):
    return {
        "instance_id": instance_id,
        "model_name": "fake-model",
        "target": target,
        "trajectory": [
            {"role": "system", "text": "fixture"},
            {"role": "ai", "text": "search_dir bug src"},
            {"role": "user", "text": "src/a.py"},
            {"role": "ai", "text": "open src/a.py 1"},
            {"role": "user", "text": "def f(): pass"},
            {"role": "ai", "text": "edit src/a.py 1:1"},
            {"role": "user", "text": "Done!"},
            {"role": "ai", "text": "pytest -q"},
            {"role": "user", "text": "1 failed"},
            {"role": "ai", "text": "edit src/a.py 1:1"},
            {"role": "user", "text": "Done!"},
            {"role": "ai", "text": "pytest -q"},
            {"role": "user", "text": "1 passed"},
            {"role": "ai", "text": "submit"},
        ],
        "exit_status": "submitted",
        "generated_patch": "SECRET_PATCH",
        "eval_logs": "SECRET_OUTCOME",
    }


class AuditTests(unittest.TestCase):
    def test_exact_curvature_vectors(self):
        self.assertEqual(curvature_series_micros([0, 0, 0]), [0])
        self.assertGreater(curvature_point_micros(0, 500000, 0), 0)

    def test_forbidden_field_gate(self):
        with self.assertRaises(LeakageError):
            blind_record(fake_row())
        blind_record(sanitize_row(fake_row()))

    def test_label_swap_cannot_change_features(self):
        left = extract_prefix(blind_record(sanitize_row(fake_row(target=True))), 1.0)
        right = extract_prefix(blind_record(sanitize_row(fake_row(target=False))), 1.0)
        for key in left:
            if isinstance(left[key], float) and math.isnan(left[key]):
                self.assertTrue(isinstance(right[key], float) and math.isnan(right[key]))
            else:
                self.assertEqual(left[key], right[key])

    def test_forbidden_sentinel_cannot_change_features(self):
        left = fake_row(); right = fake_row()
        right["generated_patch"] = "CONTRADICTION CONTROL GOLD LABEL"
        right["eval_logs"] = "FAILED SECRET"
        a = extract_prefix(blind_record(sanitize_row(left)), 1.0)
        b = extract_prefix(blind_record(sanitize_row(right)), 1.0)
        self.assertEqual(a, b)

    def test_future_events_do_not_enter_prefix(self):
        row = fake_row()
        before = extract_prefix(blind_record(sanitize_row(row)), 0.25)
        row["trajectory"].extend([{"role": "ai", "text": "edit future.py 1:1"}, {"role": "user", "text": "ERROR"}])
        after = extract_prefix(blind_record(sanitize_row(row)), 0.25)
        before.pop("trajectory_id"); after.pop("trajectory_id")
        for key in before:
            if isinstance(before[key], float) and math.isnan(before[key]):
                self.assertTrue(isinstance(after[key], float) and math.isnan(after[key]))
            else:
                self.assertEqual(before[key], after[key])

    def test_action_parser_is_explicit(self):
        self.assertEqual(parse_action("I think we should search eventually").category, "other")
        self.assertEqual(parse_action("reasoning\npytest -q").category, "verify")


    def test_repository_identity_keeps_owner_and_repo(self):
        self.assertEqual(repository_from_instance("AnalogJ__lexicon-336"), "AnalogJ__lexicon")
        self.assertEqual(repository_from_instance("owner__repo-with-dash-42"), "owner__repo-with-dash")

    def test_group_split_has_no_repository_overlap(self):
        rows = []
        for repo in "ABCDEFGH":
            for i in range(4):
                rows.append({"instance_id": f"{repo}__x-{i}", "repository": repo, "target": i % 2})
        frame = pd.DataFrame(rows)
        train, test = repository_holdout(frame)
        self.assertFalse(set(frame.loc[train, "repository"]) & set(frame.loc[test, "repository"]))

    def test_canonical_determinism(self):
        a = {"b": 2, "a": [1, 2]}
        b = {"a": [1, 2], "b": 2}
        self.assertEqual(sha256_bytes(canonical_json(a)), sha256_bytes(canonical_json(b)))

    def test_statuses_are_honest(self):
        row = extract_prefix(blind_record(sanitize_row(fake_row())), 1.0)
        self.assertTrue(row["synchrony_status"].startswith("UNAVAILABLE"))
        self.assertTrue(row["frozen_003_mapper_status"].startswith("UNAVAILABLE"))
        self.assertTrue(math.isnan(row["error_rate_csd_var_w32_last"]))

    def test_state_swap_changes_observable_features(self):
        left = fake_row(); right = fake_row()
        right["trajectory"][4]["text"] = "different observable file contents"
        a = extract_prefix(blind_record(sanitize_row(left)), 1.0)
        b = extract_prefix(blind_record(sanitize_row(right)), 1.0)
        self.assertNotEqual(a["trajectory_id"], b["trajectory_id"])
        self.assertNotEqual(a["observation_chars"], b["observation_chars"])

    def test_null_result_is_explicit_not_fabricated(self):
        row = fake_row()
        row["trajectory"] = [{"role": "system", "text": "empty trace"}]
        result = extract_prefix(blind_record(sanitize_row(row)), 1.0)
        self.assertEqual(result["action_count"], 0)
        self.assertTrue(math.isnan(result["error_rate_kappa_max"]))
        self.assertTrue(result["synchrony_status"].startswith("UNAVAILABLE"))

    def test_offline_scientific_path_has_no_network_imports(self):
        root = Path(__file__).resolve().parents[1]
        forbidden = ("import requests", "import urllib", "import httpx", "import socket", "from requests", "from urllib", "from httpx", "from socket")
        paths = list((root / "src").rglob("*.py")) + [root / "scripts" / "run_audit.py", root / "scripts" / "run_selftest.py"]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertFalse(any(token in text for token in forbidden), path)


if __name__ == "__main__":
    unittest.main()
