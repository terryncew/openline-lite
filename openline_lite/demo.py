"""Small deterministic demo including the perfectly-signed hostile control."""

from __future__ import annotations

from datetime import datetime, timezone

from .canonical import dumps, sha256_hex
from .crypto import generate_private_key_hex, public_key_hex
from .gate import ReceiptGate
from .gateway import EvidenceGateway
from .policy import Policy
from .wire import issue_source_receipt, verify_decision_receipt


NOW = datetime(2026, 7, 17, 19, 0, tzinfo=timezone.utc)


def _source_payload(evidence_hash: str) -> dict:
    return {
        "schema": "olp.source.v1",
        "issuer": "demo-agent",
        "issued_at": "2026-07-17T18:59:30Z",
        "run_id": "demo-run",
        "sequence": 0,
        "action": {"type": "tool_call", "name": "lookup_record"},
        "claim": "The requested record was found.",
        "evidence": [{"id": "tool-output", "sha256": evidence_hash}],
    }


def run_demo() -> dict:
    producer_key = generate_private_key_hex()
    gate_key = generate_private_key_hex()
    supporting = b'{"found":true}'
    contradicting = b'{"found":false}'
    trusted = {"demo-agent-key": public_key_hex(producer_key)}
    gateway = EvidenceGateway()

    envelope = issue_source_receipt(
        _source_payload(sha256_hex(supporting)), producer_key, "demo-agent-key"
    )
    intake = gateway.inspect(
        dumps(envelope), source_format="olp.source.v1", trusted_keys=trusted
    )
    hostile_envelope = issue_source_receipt(
        _source_payload(sha256_hex(contradicting)), producer_key, "demo-agent-key"
    )
    hostile_intake = gateway.inspect(
        dumps(hostile_envelope), source_format="olp.source.v1", trusted_keys=trusted
    )
    gate = ReceiptGate(gate_id="demo-receiver", private_key=gate_key)

    action_policy = Policy.from_mapping(
        {
            "policy_id": "demo-action",
            "version": "2",
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
    badge_policy = Policy.from_mapping(
        {
            **action_policy.to_dict(),
            "policy_id": "demo-badge",
            "on_undecidable": "NO_BADGE",
        }
    )
    deny_policy = Policy.from_mapping(
        {
            **action_policy.to_dict(),
            "policy_id": "demo-deny",
            "allowed_actions": ["memory_write"],
        }
    )

    cases = {
        "complete": gate.decide(
            intake, artifacts={"tool-output": supporting}, policy=action_policy, now=NOW
        ),
        "missing": gate.decide(intake, artifacts={}, policy=action_policy, now=NOW),
        "badge": gate.decide(intake, artifacts={}, policy=badge_policy, now=NOW),
        "denied": gate.decide(
            intake, artifacts={"tool-output": supporting}, policy=deny_policy, now=NOW
        ),
        "rollback": gate.decide(
            intake,
            artifacts={"tool-output": supporting},
            policy=deny_policy,
            now=NOW,
            side_effect_observed=True,
        ),
        "signed_but_unsupported": gate.decide(
            hostile_intake,
            artifacts={"tool-output": contradicting},
            policy=action_policy,
            now=NOW,
        ),
    }
    gate_trust = {"demo-receiver": public_key_hex(gate_key)}
    return {
        name: {
            "verdict": result.verdict,
            "decision": result.decision,
            "reason_codes": list(result.reason_codes),
            "decision_receipt_valid": verify_decision_receipt(
                result.receipt, gate_trust
            )["valid"],
        }
        for name, result in cases.items()
    }
