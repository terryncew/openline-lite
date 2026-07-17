"""Black-box-style conformance checkpoint for the smallest useful stack."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from openline_lite import (
    AdapterProfile,
    EvidenceGateway,
    MappedEd25519JSONAdapter,
    NativeOLPAdapter,
    Policy,
    ReceiptGate,
    build_handoff_projection,
    issue_source_receipt,
    public_key_hex,
    verify_native_chain,
    verify_decision_receipt,
)
from openline_lite.canonical import dumps, loads, sha256_hex
from openline_lite.crypto import sign
from openline_lite.wire import envelope_hash


PRODUCER_KEY = "11" * 32
GATE_KEY = "22" * 32
NOW = datetime(2026, 7, 17, 19, 0, tzinfo=timezone.utc)


def _policy() -> Policy:
    return Policy.from_mapping(
        {
            "policy_id": "conformance-policy",
            "version": "2",
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


def _native_payload(
    evidence: bytes, *, sequence: int = 0, parent_hash: str | None = None
) -> dict:
    payload = {
        "schema": "olp.source.v1",
        "issuer": "conformance-agent",
        "issued_at": "2026-07-17T18:59:30Z",
        "run_id": "conformance-run",
        "sequence": sequence,
        "action": {"type": "tool_call", "name": "approve"},
        "claim": "The request was approved.",
        "evidence": [{"id": "result", "sha256": sha256_hex(evidence)}],
    }
    if parent_hash is not None:
        payload["parent_hash"] = parent_hash
    return payload


def _decision(
    gateway: EvidenceGateway,
    source: bytes,
    source_format: str,
    evidence: dict[str, bytes],
):
    intake = gateway.inspect(
        source,
        source_format=source_format,
        trusted_keys={"producer-key": public_key_hex(PRODUCER_KEY)},
    )
    return ReceiptGate(gate_id="conformance-gate", private_key=GATE_KEY).decide(
        intake, artifacts=evidence, policy=_policy(), now=NOW
    )


def run() -> dict[str, dict[str, object]]:
    supporting = b'{"approved":true}'
    contradicting = b'{"approved":false}'
    native = EvidenceGateway()

    complete_receipt = issue_source_receipt(
        _native_payload(supporting), PRODUCER_KEY, "producer-key"
    )
    hostile_receipt = issue_source_receipt(
        _native_payload(contradicting), PRODUCER_KEY, "producer-key"
    )
    cases = {
        "native_complete": _decision(
            native, dumps(complete_receipt), "olp.source.v1", {"result": supporting}
        ),
        "native_missing": _decision(
            native, dumps(complete_receipt), "olp.source.v1", {}
        ),
        "native_signed_unsupported": _decision(
            native, dumps(hostile_receipt), "olp.source.v1", {"result": contradicting}
        ),
    }

    profile_path = Path(__file__).with_name("vendor-profile.json")
    profile_value = loads(profile_path.read_bytes())
    profile = AdapterProfile.from_mapping(profile_value)
    foreign_record = {
        "actor": "foreign-agent",
        "issued": "2026-07-17T18:59:30Z",
        "run": "foreign-run",
        "sequence": 4,
        "action": {"type": "tool_call", "name": "approve"},
        "claim": "The request was approved.",
        "evidence": [
            {
                "id": "result",
                "hash": sha256_hex(supporting),
                "media_type": "application/json",
            }
        ],
    }
    foreign_receipt = {
        "record": foreign_record,
        "proof": {
            "algorithm": "Ed25519",
            "key_id": "producer-key",
            "public_key": public_key_hex(PRODUCER_KEY),
            "signature": sign(dumps(foreign_record), PRODUCER_KEY),
        },
    }
    mapped = EvidenceGateway([NativeOLPAdapter(), MappedEd25519JSONAdapter(profile)])
    cases["foreign_mapped"] = _decision(
        mapped,
        dumps(foreign_receipt),
        "conformance.vendor.receipt.v1",
        {"result": supporting},
    )

    gate_trust = {"conformance-gate": public_key_hex(GATE_KEY)}
    false_payload = deepcopy(cases["native_complete"].receipt["payload"])
    false_payload["decision"] = "DENY"
    from openline_lite.wire import issue_decision_receipt

    false_receipt = issue_decision_receipt(false_payload, GATE_KEY, "conformance-gate")
    false_check = verify_decision_receipt(false_receipt, gate_trust)

    second_receipt = issue_source_receipt(
        _native_payload(
            supporting,
            sequence=1,
            parent_hash=envelope_hash(complete_receipt),
        ),
        PRODUCER_KEY,
        "producer-key",
    )
    second_decision = _decision(
        native,
        dumps(second_receipt),
        "olp.source.v1",
        {"result": supporting},
    )
    chain = verify_native_chain(
        [dumps(complete_receipt), dumps(second_receipt)],
        {"producer-key": public_key_hex(PRODUCER_KEY)},
    )
    projection = build_handoff_projection(
        chain,
        [cases["native_complete"].receipt, second_decision.receipt],
        gate_trust,
        allowed_policy_hashes={_policy().sha256},
        max_claims=1,
    )

    output = {
        name: {
            "verdict": result.verdict,
            "decision": result.decision,
            "decision_receipt_valid": verify_decision_receipt(
                result.receipt, gate_trust
            )["valid"],
        }
        for name, result in cases.items()
    }
    output["resealed_false_decision"] = {
        "verdict": "N/A",
        "decision": "DENY",
        "decision_receipt_valid": false_check["valid"],
        "errors": false_check["errors"],
    }
    output["verified_handoff"] = {
        "chain_valid": chain.valid,
        "accepted_count": projection.accepted_count,
        "visible_sequences": [item.sequence for item in projection.items],
        "policy_allowlist_count": len(projection.allowed_policy_hashes),
    }
    return output


def main() -> int:
    output = run()
    expected = {
        "native_complete": ("VERIFIED", "COMMIT", True),
        "native_missing": ("UNDECIDABLE", "QUARANTINE", True),
        "native_signed_unsupported": ("REJECTED", "DENY", True),
        "foreign_mapped": ("VERIFIED", "COMMIT", True),
        "resealed_false_decision": ("N/A", "DENY", False),
    }
    failures = []
    for name, (verdict, decision, valid) in expected.items():
        actual = output[name]
        if (
            actual["verdict"],
            actual["decision"],
            actual["decision_receipt_valid"],
        ) != (
            verdict,
            decision,
            valid,
        ):
            failures.append(name)
    if output["verified_handoff"] != {
        "chain_valid": True,
        "accepted_count": 2,
        "visible_sequences": [1],
        "policy_allowlist_count": 1,
    }:
        failures.append("verified_handoff")
    print(json.dumps(output, indent=2, sort_keys=True))
    if failures:
        print(json.dumps({"conformance_failures": failures}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
