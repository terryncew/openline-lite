from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from openline_lite import (
    AdapterProfile,
    EvidenceGateway,
    MappedEd25519JSONAdapter,
    Policy,
    ReceiptGate,
    generate_private_key_hex,
    public_key_hex,
)
from openline_lite.canonical import dumps, sha256_hex
from openline_lite.crypto import sign


PROFILE_VALUE = {
    "profile_id": "example.vendor.v1",
    "source_format": "example.vendor.receipt.v1",
    "signed_object": "/record",
    "proof": {
        "algorithm": "/proof/algorithm",
        "key_id": "/proof/key",
        "public_key": "/proof/public",
        "signature": "/proof/signature",
    },
    "fields": {
        "issuer": "/record/actor",
        "issued_at": "/record/time",
        "run_id": "/record/run",
        "sequence": "/record/sequence",
        "action_type": "/record/action/kind",
        "action_name": "/record/action/name",
        "action_target": None,
        "claim": "/record/conclusion",
        "evidence": "/record/artifacts",
    },
    "evidence_fields": {"id": "name", "sha256": "digest", "media_type": "type"},
}


class MappedAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.key = generate_private_key_hex()
        self.evidence = b'{"approved":true}'
        self.record = {
            "actor": "vendor-agent",
            "time": "2026-07-17T18:59:30Z",
            "run": "foreign-run",
            "sequence": 7,
            "action": {"kind": "tool_call", "name": "approve"},
            "conclusion": "The request was approved.",
            "artifacts": [
                {
                    "name": "result",
                    "digest": sha256_hex(self.evidence),
                    "type": "application/json",
                }
            ],
        }
        self.receipt = {
            "record": self.record,
            "proof": {
                "algorithm": "Ed25519",
                "key": "vendor-key",
                "public": public_key_hex(self.key),
                "signature": sign(dumps(self.record), self.key),
            },
        }
        profile = AdapterProfile.from_mapping(PROFILE_VALUE)
        self.gateway = EvidenceGateway([MappedEd25519JSONAdapter(profile)])

    def test_foreign_signed_receipt_can_commit_after_mapping_and_appraisal(
        self,
    ) -> None:
        intake = self.gateway.inspect(
            dumps(self.receipt),
            source_format="example.vendor.receipt.v1",
            trusted_keys={"vendor-key": public_key_hex(self.key)},
        )
        self.assertEqual(intake.integrity.status, "pass")
        self.assertEqual(intake.provenance.status, "pass")
        self.assertEqual(intake.normalization.status, "pass")
        policy = Policy.from_mapping(
            {
                "policy_id": "foreign-policy",
                "version": "1",
                "allowed_actions": ["tool_call"],
                "required_evidence": ["result"],
                "claim_rules": [
                    {
                        "id": "approved",
                        "evidence_id": "result",
                        "pointer": "/approved",
                        "expected": True,
                    }
                ],
                "max_age_seconds": 300,
                "on_undecidable": "QUARANTINE",
                "rollback_supported": False,
            }
        )
        result = ReceiptGate(
            gate_id="receiver", private_key=generate_private_key_hex()
        ).decide(
            intake,
            artifacts={"result": self.evidence},
            policy=policy,
            now=datetime(2026, 7, 17, 19, 0, tzinfo=timezone.utc),
        )
        self.assertEqual((result.verdict, result.decision), ("VERIFIED", "COMMIT"))

    def test_producer_verified_field_cannot_override_bad_signature(self) -> None:
        tampered = copy.deepcopy(self.receipt)
        tampered["record"]["conclusion"] = "Changed after signing."
        tampered["verified"] = True
        intake = self.gateway.inspect(
            dumps(tampered),
            source_format="example.vendor.receipt.v1",
            trusted_keys={"vendor-key": public_key_hex(self.key)},
        )
        self.assertEqual(intake.integrity.status, "fail")
        self.assertEqual(intake.provenance.status, "unavailable")

    def test_profile_cannot_map_unsigned_fields(self) -> None:
        profile = copy.deepcopy(PROFILE_VALUE)
        profile["fields"]["claim"] = "/unsigned_claim"
        with self.assertRaisesRegex(
            ValueError, "adapter_unsigned_mapping_forbidden:claim"
        ):
            AdapterProfile.from_mapping(profile)

    def test_foreign_self_signature_without_pinned_trust_is_undecidable(self) -> None:
        intake = self.gateway.inspect(
            dumps(self.receipt),
            source_format="example.vendor.receipt.v1",
            trusted_keys={},
        )
        self.assertEqual(intake.integrity.status, "pass")
        self.assertEqual(intake.provenance.status, "unavailable")


if __name__ == "__main__":
    unittest.main()
