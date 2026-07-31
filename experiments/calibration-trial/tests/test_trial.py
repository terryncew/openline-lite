from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from calibration_trial.common import (  # noqa: E402
    TrialError,
    load_json,
    load_jsonl,
    sha256_obj,
    validate_session,
)
from calibration_trial.independent_verify import verify as independent_verify  # noqa: E402
from calibration_trial.trial import (  # noqa: E402
    _exact_paired_randomization,
    evaluate,
    freeze,
    label,
    register,
    score,
    unlock_outcomes,
)
from examples.synthetic_fixture import build, continuation, iso, session  # noqa: E402


class CalibrationTrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="olp-calibration-trial-"))
        self.protocol = self.tmp / "protocol.yaml"
        shutil.copy2(ROOT / "protocol.yaml", self.protocol)
        self.result = build(self.tmp / "fixture", self.protocol)
        fixture = self.tmp / "fixture"
        self.fr = fixture / "build" / "freeze.json"
        self.cal = fixture / "data" / "calibration"
        self.cal_cont = fixture / "continuations" / "calibration"
        self.cal_out = fixture / "labels" / "calibration.jsonl"
        self.test = fixture / "data" / "test"
        self.test_cont = fixture / "continuations" / "test"
        self.ledger = fixture / "build" / "eligibility.jsonl"
        self.pred = fixture / "build" / "predictions.jsonl"
        self.unlock = fixture / "build" / "outcome-unlock.json"
        self.test_out = fixture / "labels" / "test.jsonl"
        self.eval = fixture / "build" / "evaluation.json"
        self.unlock_at = datetime.fromisoformat(
            load_json(self.unlock)["generated_at_utc"].replace("Z", "+00:00")
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def verify(self, *, eligibility=None, predictions=None, unlock=None, outcomes=None, evaluation=None):
        return independent_verify(
            self.protocol,
            self.fr,
            self.cal,
            self.cal_cont,
            self.cal_out,
            self.test,
            eligibility or self.ledger,
            self.test_cont,
            predictions or self.pred,
            unlock or self.unlock,
            outcomes or self.test_out,
            evaluation or self.eval,
        )

    def label_call(self, continuation_path: Path, out: Path, **kwargs):
        return label(
            self.protocol,
            self.fr,
            self.ledger,
            self.pred,
            self.unlock,
            continuation_path,
            out,
            **kwargs,
        )

    def test_synthetic_mechanism_passes_end_to_end_and_independent_verifier(self):
        self.assertEqual(
            self.result["disposition"], "PRIMARY_SIGNAL_CLEARS_PREREGISTERED_GATE"
        )
        self.assertTrue(
            self.result["primary_gate"]["exact_randomization_p_le_alpha_against_all"]
        )
        report = self.verify()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["test_n"], 20)
        self.assertGreater(
            report["primary_minus_best_comparator_balanced_accuracy_micros"], 0
        )


    def test_all_one_class_prospective_endpoint_is_insufficient_sample(self):
        root = self.tmp / "all-zero-fixture"
        result = build(root, self.protocol, prospective_outcomes=[0] * 20)
        self.assertEqual(result["disposition"], "INSUFFICIENT_SAMPLE")
        self.assertFalse(result["eligible_for_primary_verdict"])
        self.assertEqual(result["class_counts"], {"correction": 0, "no_correction": 20})
        self.assertTrue(
            all(
                item == {
                    "status": "not_run",
                    "reason": "minimum_test_per_class_not_met",
                }
                for item in result["inference"].values()
            )
        )
        report = independent_verify(
            self.protocol,
            root / "build" / "freeze.json",
            root / "data" / "calibration",
            root / "continuations" / "calibration",
            root / "labels" / "calibration.jsonl",
            root / "data" / "test",
            root / "build" / "eligibility.jsonl",
            root / "continuations" / "test",
            root / "build" / "predictions.jsonl",
            root / "build" / "outcome-unlock.json",
            root / "labels" / "test.jsonl",
            root / "build" / "evaluation.json",
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["disposition"], "INSUFFICIENT_SAMPLE")

    def test_exact_test_class_floor_runs_inference(self):
        root = self.tmp / "exact-floor-fixture"
        outcomes = [1] * 3 + [0] * 17
        result = build(root, self.protocol, prospective_outcomes=outcomes)
        self.assertTrue(result["eligible_for_primary_verdict"])
        self.assertEqual(result["class_counts"], {"correction": 3, "no_correction": 17})
        self.assertTrue(
            all(item.get("status") != "not_run" for item in result["inference"].values())
        )
        report = independent_verify(
            self.protocol,
            root / "build" / "freeze.json",
            root / "data" / "calibration",
            root / "continuations" / "calibration",
            root / "labels" / "calibration.jsonl",
            root / "data" / "test",
            root / "build" / "eligibility.jsonl",
            root / "continuations" / "test",
            root / "build" / "predictions.jsonl",
            root / "build" / "outcome-unlock.json",
            root / "labels" / "test.jsonl",
            root / "build" / "evaluation.json",
        )
        self.assertEqual(report["status"], "PASS")
        self.assertNotEqual(report["disposition"], "INSUFFICIENT_SAMPLE")

    def test_calibration_continuation_must_start_after_handoff(self):
        sid = "cal-001"
        session_doc = load_json(self.cal / f"{sid}.json")
        continuation_path = self.cal_cont / f"{sid}.json"
        continuation_doc = load_json(continuation_path)
        handoff = datetime.fromisoformat(
            session_doc["handoff_at_utc"].replace("Z", "+00:00")
        )
        continuation_doc["started_at_utc"] = iso(handoff - timedelta(days=1))
        continuation_path.write_text(
            json.dumps(continuation_doc, indent=2, sort_keys=True) + "\n"
        )
        outcomes = load_jsonl(self.cal_out)
        for row in outcomes:
            if row["session_id"] == sid:
                row["continuation_sha256"] = sha256_obj(continuation_doc)
        self.cal_out.write_text(
            "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in outcomes
            )
        )
        with self.assertRaisesRegex(TrialError, "continuation must start strictly after"):
            freeze(
                self.protocol,
                self.cal,
                self.cal_cont,
                self.cal_out,
                self.tmp / "bad-freeze.json",
                at="2026-01-25T00:00:00Z",
            )

        freeze_doc = load_json(self.fr)
        manifest = []
        for path in sorted(self.cal.glob("*.json")):
            current = load_json(path)
            current_sid = current["session_id"]
            manifest.append(
                {
                    "session_id": current_sid,
                    "session_sha256": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
                    "continuation_sha256": sha256_obj(load_json(self.cal_cont / f"{current_sid}.json")),
                    "outcome": next(
                        row["outcome"] for row in outcomes if row["session_id"] == current_sid
                    ),
                }
            )
        freeze_doc["calibration_manifest_sha256"] = sha256_obj(manifest)
        malicious_freeze = self.tmp / "malicious-freeze.json"
        malicious_freeze.write_text(
            json.dumps(freeze_doc, indent=2, sort_keys=True) + "\n"
        )
        with self.assertRaisesRegex(Exception, "continuation must start strictly after"):
            independent_verify(
                self.protocol,
                malicious_freeze,
                self.cal,
                self.cal_cont,
                self.cal_out,
                self.test,
                self.ledger,
                self.test_cont,
                self.pred,
                self.unlock,
                self.test_out,
                self.eval,
            )

    def test_calibration_continuation_strict_after_boundary(self):
        sid = "cal-001"
        session_doc = load_json(self.cal / f"{sid}.json")
        continuation_path = self.cal_cont / f"{sid}.json"
        continuation_doc = load_json(continuation_path)
        handoff = datetime.fromisoformat(
            session_doc["handoff_at_utc"].replace("Z", "+00:00")
        )

        def write_started_at(started_at):
            continuation_doc["started_at_utc"] = iso(started_at)
            continuation_path.write_text(
                json.dumps(continuation_doc, indent=2, sort_keys=True) + "\n"
            )
            outcomes = load_jsonl(self.cal_out)
            for row in outcomes:
                if row["session_id"] == sid:
                    row["continuation_sha256"] = sha256_obj(continuation_doc)
            self.cal_out.write_text(
                "".join(
                    json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                    for row in outcomes
                )
            )

        write_started_at(handoff + timedelta(seconds=1))
        freeze(
            self.protocol,
            self.cal,
            self.cal_cont,
            self.cal_out,
            self.tmp / "after-boundary-freeze.json",
            at="2026-01-25T00:00:00Z",
        )

        write_started_at(handoff)
        with self.assertRaisesRegex(TrialError, "continuation must start strictly after"):
            freeze(
                self.protocol,
                self.cal,
                self.cal_cont,
                self.cal_out,
                self.tmp / "equal-boundary-freeze.json",
                at="2026-01-25T00:00:00Z",
            )

    def test_pre_freeze_handoff_cannot_be_registered_as_test(self):
        path = self.tmp / "pre-freeze.json"
        path.write_text(
            json.dumps(
                session("bad-time", "2026-01-24T23:59:00Z", 1, 999),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        with self.assertRaises(TrialError):
            register(
                self.protocol,
                self.fr,
                path,
                self.tmp / "x-eligibility.jsonl",
                at="2026-01-24T23:59:30Z",
            )

    def test_registration_after_preregistered_lag_is_rejected(self):
        handoff = datetime(2026, 2, 20, tzinfo=timezone.utc)
        path = self.tmp / "late-registration.json"
        path.write_text(
            json.dumps(
                session("late-registration", iso(handoff), 1, 998),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        with self.assertRaises(TrialError):
            register(
                self.protocol,
                self.fr,
                path,
                self.tmp / "late-eligibility.jsonl",
                at=iso(handoff + timedelta(seconds=601)),
            )

    def test_prediction_after_preregistered_lag_is_rejected(self):
        handoff = datetime(2026, 2, 20, tzinfo=timezone.utc)
        path = self.tmp / "late-prediction.json"
        path.write_text(
            json.dumps(
                session("late-prediction", iso(handoff), 1, 998),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        ledger = self.tmp / "late-prediction-eligibility.jsonl"
        register(
            self.protocol,
            self.fr,
            path,
            ledger,
            at=iso(handoff + timedelta(seconds=30)),
        )
        with self.assertRaises(TrialError):
            score(
                self.protocol,
                self.fr,
                path,
                ledger,
                self.tmp / "late-pred.jsonl",
                at=iso(handoff + timedelta(seconds=601)),
            )

    def test_score_cannot_skip_earlier_registered_handoff(self):
        ledger = self.tmp / "skip-eligibility.jsonl"
        predictions = self.tmp / "skip-predictions.jsonl"
        t1 = datetime(2026, 2, 20, tzinfo=timezone.utc)
        t2 = datetime(2026, 2, 21, tzinfo=timezone.utc)
        p1 = self.tmp / "first-eligible.json"
        p2 = self.tmp / "second-eligible.json"
        p1.write_text(
            json.dumps(session("first-eligible", iso(t1), 0, 991), indent=2, sort_keys=True)
            + "\n"
        )
        p2.write_text(
            json.dumps(session("second-eligible", iso(t2), 1, 992), indent=2, sort_keys=True)
            + "\n"
        )
        register(self.protocol, self.fr, p1, ledger, at=iso(t1 + timedelta(seconds=30)))
        register(self.protocol, self.fr, p2, ledger, at=iso(t2 + timedelta(seconds=30)))
        with self.assertRaises(TrialError):
            score(
                self.protocol,
                self.fr,
                p2,
                ledger,
                predictions,
                at=iso(t2 + timedelta(seconds=60)),
            )

    def test_fixed_prospective_n_cannot_be_extended(self):
        self.assertEqual(len(load_jsonl(self.pred)), 20)
        handoff = datetime(2026, 3, 1, tzinfo=timezone.utc)
        path = self.tmp / "twenty-first.json"
        path.write_text(
            json.dumps(
                session("twenty-first", iso(handoff), 0, 995), indent=2, sort_keys=True
            )
            + "\n"
        )
        with self.assertRaises(TrialError):
            register(
                self.protocol,
                self.fr,
                path,
                self.ledger,
                at=iso(handoff + timedelta(seconds=30)),
            )

    def test_protocol_mutation_after_freeze_invalidates_registration(self):
        p = json.loads(self.protocol.read_text())
        p["title"] = "changed after freeze"
        self.protocol.write_text(json.dumps(p, indent=2, sort_keys=True) + "\n")
        handoff = datetime(2026, 2, 20, tzinfo=timezone.utc)
        path = self.tmp / "mutated-protocol-session.json"
        path.write_text(
            json.dumps(
                session("mutated-protocol", iso(handoff), 1, 997),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        with self.assertRaises(TrialError):
            register(
                self.protocol,
                self.fr,
                path,
                self.tmp / "mutated-eligibility.jsonl",
                at=iso(handoff + timedelta(seconds=30)),
            )

    def test_session_cannot_supply_producer_metric_profile_or_weights(self):
        s = session("producer-profile", "2026-02-20T00:00:00Z", 1, 996)
        s["measurement_input"]["profile"] = {
            "dhol_claim_weight_micros": 1_000_000,
            "dhol_evidence_weight_micros": 0,
            "dhol_relation_weight_micros": 0,
        }
        with self.assertRaises(TrialError):
            validate_session(s)

    def test_receiver_owned_profile_is_protocol_bound(self):
        p = json.loads(self.protocol.read_text())
        profile = p["measurement_contract"]["receiver_profile"]
        profile["dhol_claim_weight_micros"] += 1
        profile["dhol_evidence_weight_micros"] -= 1
        self.protocol.write_text(json.dumps(p, indent=2, sort_keys=True) + "\n")
        handoff = datetime(2026, 2, 20, tzinfo=timezone.utc)
        path = self.tmp / "receiver-profile-drift.json"
        path.write_text(
            json.dumps(
                session("receiver-profile-drift", iso(handoff), 1, 996),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        with self.assertRaises(TrialError):
            register(
                self.protocol,
                self.fr,
                path,
                self.tmp / "profile-eligibility.jsonl",
                at=iso(handoff + timedelta(seconds=30)),
            )

    def test_session_rejects_any_embedded_outcome_field(self):
        s = session("leak", "2026-02-20T00:00:00Z", 1, 1)
        s["outcome"] = 1
        with self.assertRaises(TrialError):
            validate_session(s)

    def test_outcome_unlock_refuses_incomplete_prediction_set(self):
        rows = load_jsonl(self.pred)[:-1]
        partial = self.tmp / "partial-predictions.jsonl"
        partial.write_text(
            "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in rows
            )
        )
        with self.assertRaises(TrialError):
            unlock_outcomes(
                self.protocol,
                self.fr,
                self.ledger,
                partial,
                self.tmp / "premature-unlock.json",
                at=iso(self.unlock_at),
            )

    def test_outcome_unlock_refuses_overwrite(self):
        with self.assertRaises(TrialError):
            unlock_outcomes(
                self.protocol,
                self.fr,
                self.ledger,
                self.pred,
                self.unlock,
                at=iso(self.unlock_at),
            )

    def test_positive_label_must_point_to_human_message(self):
        sid = load_jsonl(self.pred)[0]["session_id"]
        cpath = self.test_cont / f"{sid}.json"
        with self.assertRaises(TrialError):
            self.label_call(
                cpath,
                self.tmp / "bad-label.jsonl",
                session_id=sid,
                outcome=1,
                kind="constraint",
                correction_message_index=0,
                notes="bad pointer",
                at=iso(self.unlock_at + timedelta(seconds=100)),
            )

    def test_negative_label_requires_full_window_or_ended_continuation(self):
        sid = load_jsonl(self.pred)[0]["session_id"]
        pred = load_jsonl(self.pred)[0]
        start = datetime.fromisoformat(pred["predicted_at_utc"].replace("Z", "+00:00")) + timedelta(seconds=1)
        c = {
            "schema": "openline.calibration-trial.continuation.v1",
            "session_id": sid,
            "started_at_utc": iso(start),
            "events": [
                {"index": 0, "role": "assistant", "text": "one", "tool_name": None},
                {"index": 1, "role": "user", "text": "ok", "tool_name": None},
            ],
            "ended": False,
        }
        cp = self.tmp / "short-open.json"
        cp.write_text(json.dumps(c, indent=2, sort_keys=True) + "\n")
        with self.assertRaises(TrialError):
            self.label_call(
                cp,
                self.tmp / "bad-negative.jsonl",
                session_id=sid,
                outcome=0,
                kind=None,
                correction_message_index=None,
                notes="incomplete",
                at=iso(self.unlock_at + timedelta(seconds=100)),
            )

    def test_prediction_must_precede_continuation_start(self):
        pred = load_jsonl(self.pred)[0]
        sid = pred["session_id"]
        pdt = datetime.fromisoformat(pred["predicted_at_utc"].replace("Z", "+00:00"))
        c = continuation(sid, 1, iso(pdt - timedelta(seconds=1)))
        cp = self.tmp / "future-leak.json"
        cp.write_text(json.dumps(c, indent=2, sort_keys=True) + "\n")
        with self.assertRaises(TrialError):
            self.label_call(
                cp,
                self.tmp / "future-leak-label.jsonl",
                session_id=sid,
                outcome=1,
                kind="constraint",
                correction_message_index=3,
                notes="must fail",
                at=iso(self.unlock_at + timedelta(seconds=100)),
            )

    def test_prediction_equal_to_continuation_start_is_rejected(self):
        pred = load_jsonl(self.pred)[0]
        sid = pred["session_id"]
        cp = self.tmp / "equal-start.json"
        cp.write_text(
            json.dumps(continuation(sid, 1, pred["predicted_at_utc"]), indent=2, sort_keys=True)
            + "\n"
        )
        with self.assertRaises(TrialError):
            self.label_call(
                cp,
                self.tmp / "equal-start-label.jsonl",
                session_id=sid,
                outcome=1,
                kind="constraint",
                correction_message_index=3,
                notes="must fail",
                at=iso(self.unlock_at + timedelta(seconds=100)),
            )

    def test_prospective_label_cannot_predate_outcome_unlock(self):
        pred = load_jsonl(self.pred)[0]
        sid = pred["session_id"]
        cpath = self.test_cont / f"{sid}.json"
        with self.assertRaises(TrialError):
            self.label_call(
                cpath,
                self.tmp / "early-label.jsonl",
                session_id=sid,
                outcome=0,
                kind=None,
                correction_message_index=None,
                notes="must fail",
                at=iso(self.unlock_at - timedelta(seconds=1)),
            )

    def test_outcome_label_cannot_predate_continuation_start(self):
        pred = load_jsonl(self.pred)[0]
        sid = pred["session_id"]
        start = self.unlock_at + timedelta(seconds=100)
        c = continuation(sid, 1, iso(start))
        cp = self.tmp / "label-before-continuation.json"
        cp.write_text(json.dumps(c, indent=2, sort_keys=True) + "\n")
        with self.assertRaises(TrialError):
            self.label_call(
                cp,
                self.tmp / "label-before-continuation.jsonl",
                session_id=sid,
                outcome=1,
                kind="constraint",
                correction_message_index=3,
                notes="must fail",
                at=iso(self.unlock_at + timedelta(seconds=50)),
            )

    def test_exact_inference_does_not_call_a_small_win_significant(self):
        y = [0, 1, 0, 1]
        primary = [0, 1, 0, 1]
        comparator = [1, 1, 0, 1]
        result = _exact_paired_randomization(y, primary, comparator, 50_000)
        self.assertGreater(result["observed_delta_balanced_accuracy_micros"], 0)
        self.assertFalse(result["significant"])
        self.assertGreater(result["p_value_micros_ceil"], 50_000)

    def test_synthetic_result_reports_interval_for_every_comparator(self):
        self.assertEqual(set(self.result["inference"]), set(self.result["comparators"]))
        for result in self.result["inference"].values():
            self.assertEqual(
                result["bootstrap_interval"]["confidence_micros"], 950_000
            )
            self.assertIn("p_value_numerator", result["exact_randomization"])
            self.assertIn("p_value_denominator", result["exact_randomization"])

    def test_independent_verifier_catches_prediction_tamper(self):
        rows = load_jsonl(self.pred)
        rows[0]["metrics"]["delta_hol_micros"] += 1
        tampered = self.tmp / "tampered.jsonl"
        tampered.write_text(
            "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in rows
            )
        )
        with self.assertRaises(Exception):
            self.verify(predictions=tampered)

    def test_independent_verifier_catches_outcome_unlock_tamper(self):
        unlock = load_json(self.unlock)
        unlock["generated_at_utc"] = "2026-01-01T00:00:00Z"
        tampered = self.tmp / "tampered-unlock.json"
        tampered.write_text(json.dumps(unlock, indent=2, sort_keys=True) + "\n")
        with self.assertRaises(Exception):
            self.verify(unlock=tampered)

    def test_independent_verifier_catches_pre_unlock_label_timestamp(self):
        rows = load_jsonl(self.test_out)
        rows[0]["labeled_at_utc"] = iso(self.unlock_at - timedelta(seconds=1))
        tampered = self.tmp / "early-outcomes.jsonl"
        tampered.write_text(
            "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in rows
            )
        )
        with self.assertRaises(Exception):
            self.verify(outcomes=tampered)

    def test_omitted_earlier_post_freeze_handoff_fails_closed(self):
        earlier = self.test / "omitted-earlier.json"
        earlier.write_text(
            json.dumps(
                session("omitted-earlier", "2026-01-25T12:00:00Z", 0, 994),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        with self.assertRaises(Exception):
            self.verify()
        with self.assertRaises(TrialError):
            evaluate(
                self.protocol,
                self.fr,
                self.test,
                self.ledger,
                self.test_cont,
                self.pred,
                self.unlock,
                self.test_out,
                self.tmp / "attack-evaluation.json",
                at=iso(self.unlock_at + timedelta(hours=2)),
            )

    def test_eligibility_chain_tamper_is_rejected(self):
        rows = load_jsonl(self.ledger)
        rows[0]["registered_at_utc"] = "2026-01-26T00:00:31Z"
        tampered = self.tmp / "tampered-eligibility.jsonl"
        tampered.write_text(
            "".join(
                json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                for row in rows
            )
        )
        with self.assertRaises(Exception):
            self.verify(eligibility=tampered)

    def test_independent_verifier_does_not_import_candidate_modules(self):
        text = (ROOT / "calibration_trial" / "independent_verify.py").read_text()
        self.assertNotIn("from .", text)
        self.assertNotIn("import calibration_trial", text)


if __name__ == "__main__":
    unittest.main()
