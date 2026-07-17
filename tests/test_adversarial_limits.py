from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from openline_lite import (
    AdapterProfile,
    EvidenceGateway,
    MappedEd25519JSONAdapter,
    Policy,
    ReceiptGate,
    issue_source_receipt,
    public_key_hex,
    verify_native_chain,
)
from openline_lite.canonical import (
    MAX_CANONICAL_NODES,
    CanonicalJSONError,
    dumps,
    loads,
    sha256_hex,
    validate,
)
from openline_lite.claim_support import evaluate_claim_support
from openline_lite.pointer import JSONPointerError, resolve


PRODUCER_KEY = "77" * 32
GATE_KEY = "88" * 32
NOW = datetime(2026, 7, 17, 19, 0, tzinfo=timezone.utc)


def nested_json(depth: int = 2_000) -> bytes:
    return (b"[" * depth) + b"1" + (b"]" * depth)


def policy() -> Policy:
    return Policy.from_mapping(
        {
            "policy_id": "adversarial-depth-policy",
            "version": "1",
            "allowed_actions": ["tool_call"],
            "required_evidence": ["result"],
            "claim_rules": [
                {
                    "id": "ok",
                    "evidence_id": "result",
                    "pointer": "/ok",
                    "expected": True,
                }
            ],
            "max_age_seconds": 300,
            "on_undecidable": "QUARANTINE",
            "rollback_supported": False,
        }
    )


class CanonicalComplexityTests(unittest.TestCase):
    def test_deep_parsed_json_becomes_canonical_error(self) -> None:
        with self.assertRaisesRegex(CanonicalJSONError, "json_depth_limit_exceeded"):
            loads(nested_json())

    def test_deep_in_memory_value_uses_iterative_limit(self) -> None:
        value: object = 1
        for _ in range(2_000):
            value = [value]
        with self.assertRaisesRegex(CanonicalJSONError, "json_depth_limit_exceeded"):
            validate(value)

    def test_wide_value_hits_node_limit_before_stack_expansion(self) -> None:
        with self.assertRaisesRegex(CanonicalJSONError, "json_node_limit_exceeded"):
            validate([0] * MAX_CANONICAL_NODES)

    def test_gateway_fails_closed_on_deep_json(self) -> None:
        result = EvidenceGateway().inspect(
            nested_json(),
            source_format="olp.source.v1",
            trusted_keys={},
        )
        self.assertEqual(result.integrity.status, "fail")
        self.assertIn("json_depth_limit_exceeded", result.integrity.reason_codes)

    def test_claim_support_fails_closed_on_deep_json(self) -> None:
        result = evaluate_claim_support({"result": nested_json()}, policy())
        self.assertEqual(result.status, "fail")
        self.assertIn("claim_evidence_invalid_json:ok", result.reason_codes)

    def test_receipt_gate_denies_deep_committed_evidence(self) -> None:
        evidence = nested_json()
        payload = {
            "schema": "olp.source.v1",
            "issuer": "adversarial-agent",
            "issued_at": "2026-07-17T18:59:30Z",
            "run_id": "adversarial-run",
            "sequence": 0,
            "action": {"type": "tool_call", "name": "check"},
            "claim": "The check passed.",
            "evidence": [{"id": "result", "sha256": sha256_hex(evidence)}],
        }
        receipt = issue_source_receipt(payload, PRODUCER_KEY, "producer")
        intake = EvidenceGateway().inspect(
            dumps(receipt),
            source_format="olp.source.v1",
            trusted_keys={"producer": public_key_hex(PRODUCER_KEY)},
        )
        decision = ReceiptGate(gate_id="gate", private_key=GATE_KEY).decide(
            intake,
            artifacts={"result": evidence},
            policy=policy(),
            now=NOW,
        )
        self.assertEqual((decision.verdict, decision.decision), ("REJECTED", "DENY"))
        self.assertIn(
            "claim_support:claim_evidence_invalid_json:ok",
            decision.reason_codes,
        )

    def test_native_chain_returns_failure_on_deep_json(self) -> None:
        result = verify_native_chain([nested_json()], {})
        self.assertFalse(result.valid)
        self.assertEqual(result.status, "fail")

    def test_mapped_adapter_returns_failure_on_deep_json(self) -> None:
        profile = AdapterProfile.from_mapping(
            loads(Path("conformance/vendor-profile.json").read_bytes())
        )
        result = MappedEd25519JSONAdapter(profile).assess(nested_json(), {})
        self.assertEqual(result.integrity.status, "fail")
        self.assertEqual(result.normalization.status, "unavailable")


class JSONPointerTests(unittest.TestCase):
    def test_rfc6901_escaping(self) -> None:
        value = {"a/b": {"~key": 7}, "": 8}
        self.assertEqual(resolve(value, "/a~1b/~0key"), 7)
        self.assertEqual(resolve(value, "/"), 8)

    def test_invalid_escape_is_rejected(self) -> None:
        with self.assertRaisesRegex(JSONPointerError, "escape_invalid"):
            resolve({"~2": 1}, "/~2")

    def test_array_indexes_are_ascii_only(self) -> None:
        with self.assertRaisesRegex(JSONPointerError, "index_invalid"):
            resolve(["zero", "one"], "/١")

    def test_huge_array_index_fails_without_integer_conversion(self) -> None:
        with self.assertRaisesRegex(JSONPointerError, "json_pointer_missing"):
            resolve(["zero"], "/" + ("9" * 100_000))


if __name__ == "__main__":
    unittest.main()
