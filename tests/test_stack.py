from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from openline_lite import (
    EvidenceGateway,
    Policy,
    ReceiptGate,
    generate_private_key_hex,
    issue_source_receipt,
    public_key_hex,
    verify_decision_receipt,
)
from openline_lite.canonical import CanonicalJSONError, dumps, loads, sha256_hex
from openline_lite.wire import issue_decision_receipt


NOW = datetime(2026, 7, 17, 19, 0, tzinfo=timezone.utc)


class StackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.producer_key = generate_private_key_hex()
        self.gate_key = generate_private_key_hex()
        self.artifact = b'{"found":true}'
        self.payload = {
            "schema": "olp.source.v1",
            "issuer": "agent-a",
            "issued_at": "2026-07-17T18:59:30Z",
            "run_id": "run-a",
            "sequence": 0,
            "action": {"type": "tool_call", "name": "lookup"},
            "claim": "Record found.",
            "evidence": [{"id": "tool-output", "sha256": sha256_hex(self.artifact)}],
        }
        self.envelope = issue_source_receipt(
            self.payload, self.producer_key, "agent-key"
        )
        self.trust = {"agent-key": public_key_hex(self.producer_key)}
        self.policy = Policy.from_mapping(
            {
                "policy_id": "action-policy",
                "version": "1",
                "allowed_actions": ["tool_call"],
                "required_evidence": ["tool-output"],
                "claim_rules": [
                    {
                        "id": "record-found",
                        "evidence_id": "tool-output",
                        "pointer": "/found",
                        "expected": True,
                    }
                ],
                "max_age_seconds": 300,
                "on_undecidable": "QUARANTINE",
                "rollback_supported": True,
            }
        )
        self.gate = ReceiptGate(gate_id="receiver", private_key=self.gate_key)

    def intake(self, envelope=None, trust=None):
        return EvidenceGateway().inspect(
            dumps(envelope or self.envelope),
            source_format="olp.source.v1",
            trusted_keys=self.trust if trust is None else trust,
        )

    def test_complete_evidence_commits_and_decision_verifies(self) -> None:
        result = self.gate.decide(
            self.intake(),
            artifacts={"tool-output": self.artifact},
            policy=self.policy,
            now=NOW,
        )
        self.assertEqual((result.verdict, result.decision), ("VERIFIED", "COMMIT"))
        verified = verify_decision_receipt(
            result.receipt, {"receiver": public_key_hex(self.gate_key)}
        )
        self.assertTrue(verified["valid"], verified["errors"])

    def test_valid_signature_missing_evidence_quarantines(self) -> None:
        result = self.gate.decide(
            self.intake(), artifacts={}, policy=self.policy, now=NOW
        )
        self.assertEqual(
            (result.verdict, result.decision), ("UNDECIDABLE", "QUARANTINE")
        )
        self.assertIn("evidence:artifact_missing:tool-output", result.reason_codes)

    def test_badge_policy_withholds_badge(self) -> None:
        value = self.policy.to_dict()
        value["on_undecidable"] = "NO_BADGE"
        result = self.gate.decide(
            self.intake(), artifacts={}, policy=Policy.from_mapping(value), now=NOW
        )
        self.assertEqual((result.verdict, result.decision), ("UNDECIDABLE", "NO_BADGE"))

    def test_disallowed_action_denies_or_requests_rollback(self) -> None:
        value = self.policy.to_dict()
        value["allowed_actions"] = ["memory_write"]
        policy = Policy.from_mapping(value)
        denied = self.gate.decide(
            self.intake(),
            artifacts={"tool-output": self.artifact},
            policy=policy,
            now=NOW,
        )
        rollback = self.gate.decide(
            self.intake(),
            artifacts={"tool-output": self.artifact},
            policy=policy,
            now=NOW,
            side_effect_observed=True,
        )
        self.assertEqual(denied.decision, "DENY")
        self.assertEqual(rollback.decision, "ROLLBACK_REQUEST")

    def test_signature_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.envelope)
        tampered["payload"]["claim"] = "Different claim."
        result = self.gate.decide(
            self.intake(tampered),
            artifacts={"tool-output": self.artifact},
            policy=self.policy,
            now=NOW,
        )
        self.assertEqual((result.verdict, result.decision), ("REJECTED", "DENY"))

    def test_untrusted_but_self_consistent_source_is_undecidable(self) -> None:
        result = self.gate.decide(
            self.intake(trust={}),
            artifacts={"tool-output": self.artifact},
            policy=self.policy,
            now=NOW,
        )
        self.assertEqual(
            (result.verdict, result.decision), ("UNDECIDABLE", "QUARANTINE")
        )
        self.assertIn("provenance:source_key_untrusted", result.reason_codes)

    def test_artifact_hash_mismatch_denies(self) -> None:
        result = self.gate.decide(
            self.intake(),
            artifacts={"tool-output": b"wrong"},
            policy=self.policy,
            now=NOW,
        )
        self.assertEqual((result.verdict, result.decision), ("REJECTED", "DENY"))

    def test_perfectly_signed_complete_but_unsupported_claim_is_denied(self) -> None:
        contradicting = b'{"found":false}'
        payload = copy.deepcopy(self.payload)
        payload["evidence"][0]["sha256"] = sha256_hex(contradicting)
        envelope = issue_source_receipt(payload, self.producer_key, "agent-key")
        result = self.gate.decide(
            self.intake(envelope),
            artifacts={"tool-output": contradicting},
            policy=self.policy,
            now=NOW,
        )
        self.assertEqual((result.verdict, result.decision), ("REJECTED", "DENY"))
        self.assertIn(
            "claim_support:claim_fact_mismatch:record-found", result.reason_codes
        )

    def test_no_claim_rules_is_undecidable(self) -> None:
        value = self.policy.to_dict()
        value["claim_rules"] = []
        result = self.gate.decide(
            self.intake(),
            artifacts={"tool-output": self.artifact},
            policy=Policy.from_mapping(value),
            now=NOW,
        )
        self.assertEqual(
            (result.verdict, result.decision), ("UNDECIDABLE", "QUARANTINE")
        )
        self.assertIn("claim_support:claim_rules_missing", result.reason_codes)

    def test_expired_receipt_is_undecidable(self) -> None:
        result = self.gate.decide(
            self.intake(),
            artifacts={"tool-output": self.artifact},
            policy=self.policy,
            now=datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            (result.verdict, result.decision), ("UNDECIDABLE", "QUARANTINE")
        )

    def test_duplicate_json_keys_and_floats_are_rejected(self) -> None:
        with self.assertRaises(CanonicalJSONError):
            loads('{"a":1,"a":2}')
        with self.assertRaises(CanonicalJSONError):
            dumps({"value": 1.5})

    def test_unknown_source_format_stays_undecidable(self) -> None:
        intake = EvidenceGateway().inspect(
            b"{}", source_format="foreign.unknown.v1", trusted_keys={}
        )
        result = self.gate.decide(intake, artifacts={}, policy=self.policy, now=NOW)
        self.assertEqual(
            (result.verdict, result.decision), ("UNDECIDABLE", "QUARANTINE")
        )

    def test_source_size_limit_fails_closed(self) -> None:
        intake = EvidenceGateway(max_source_bytes=8).inspect(
            dumps(self.envelope), source_format="olp.source.v1", trusted_keys=self.trust
        )
        result = self.gate.decide(intake, artifacts={}, policy=self.policy, now=NOW)
        self.assertEqual((result.verdict, result.decision), ("REJECTED", "DENY"))
        self.assertIn("integrity:source_size_limit_exceeded", result.reason_codes)

    def test_gate_identity_is_bound_inside_decision(self) -> None:
        result = self.gate.decide(
            self.intake(),
            artifacts={"tool-output": self.artifact},
            policy=self.policy,
            now=NOW,
        )
        payload = copy.deepcopy(result.receipt["payload"])
        payload["gate_id"] = "different-gate"
        resealed = issue_decision_receipt(payload, self.gate_key, "receiver")
        verified = verify_decision_receipt(
            resealed, {"receiver": public_key_hex(self.gate_key)}
        )
        self.assertFalse(verified["valid"])
        self.assertIn("gate_id_key_id_mismatch", verified["errors"])

    def test_resealed_false_decision_fails_semantic_recomputation(self) -> None:
        result = self.gate.decide(
            self.intake(),
            artifacts={"tool-output": self.artifact},
            policy=self.policy,
            now=NOW,
        )
        false_payload = copy.deepcopy(result.receipt["payload"])
        false_payload["decision"] = "DENY"
        tampered = issue_decision_receipt(false_payload, self.gate_key, "receiver")
        verified = verify_decision_receipt(
            tampered, {"receiver": public_key_hex(self.gate_key)}
        )
        self.assertFalse(verified["valid"])
        self.assertIn("decision_recompute_mismatch", verified["errors"])


if __name__ == "__main__":
    unittest.main()
