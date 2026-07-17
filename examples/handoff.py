"""Create a four-step verified handoff entirely in process."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openline_lite import (
    EvidenceGateway,
    Policy,
    ReceiptGate,
    build_handoff_projection,
    generate_private_key_hex,
    issue_source_receipt,
    public_key_hex,
    verify_native_chain,
)
from openline_lite.canonical import dumps, sha256_hex
from openline_lite.wire import envelope_hash


def main() -> None:
    producer_key = generate_private_key_hex()
    gate_key = generate_private_key_hex()
    producer_trust = {"example-producer": public_key_hex(producer_key)}
    gate_trust = {"example-gate": public_key_hex(gate_key)}
    gateway = EvidenceGateway()
    gate = ReceiptGate(gate_id="example-gate", private_key=gate_key)
    base_time = datetime(2026, 7, 17, 19, 0, tzinfo=timezone.utc)

    sources: list[bytes] = []
    decisions: list[dict] = []
    policies: list[Policy] = []
    previous: dict | None = None

    for sequence in range(4):
        evidence = dumps({"ok": True, "value": sequence * sequence})
        policy = Policy.from_mapping(
            {
                "policy_id": f"example-step-{sequence}",
                "version": "1",
                "allowed_actions": ["tool_call"],
                "required_evidence": ["result"],
                "claim_rules": [
                    {
                        "id": "ok",
                        "evidence_id": "result",
                        "pointer": "/ok",
                        "expected": True,
                    },
                    {
                        "id": "value",
                        "evidence_id": "result",
                        "pointer": "/value",
                        "expected": sequence * sequence,
                    },
                ],
                "max_age_seconds": 300,
                "on_undecidable": "QUARANTINE",
                "rollback_supported": False,
            }
        )
        payload = {
            "schema": "olp.source.v1",
            "issuer": "example-agent",
            "issued_at": (base_time + timedelta(seconds=sequence))
            .isoformat()
            .replace("+00:00", "Z"),
            "run_id": "example-run",
            "sequence": sequence,
            "action": {"type": "tool_call", "name": "square"},
            "claim": f"The computed value is {sequence * sequence}.",
            "evidence": [{"id": "result", "sha256": sha256_hex(evidence)}],
        }
        if previous is not None:
            payload["parent_hash"] = envelope_hash(previous)

        receipt = issue_source_receipt(payload, producer_key, "example-producer")
        source_bytes = dumps(receipt)
        intake = gateway.inspect(
            source_bytes,
            source_format="olp.source.v1",
            trusted_keys=producer_trust,
        )
        decision = gate.decide(
            intake,
            artifacts={"result": evidence},
            policy=policy,
            now=base_time + timedelta(seconds=10),
        )
        sources.append(source_bytes)
        decisions.append(decision.receipt)
        policies.append(policy)
        previous = receipt

    chain = verify_native_chain(sources, producer_trust)
    projection = build_handoff_projection(
        chain,
        decisions,
        gate_trust,
        allowed_policy_hashes={policy.sha256 for policy in policies},
        max_claims=2,
    )
    print(projection.render_jsonl(), end="")


if __name__ == "__main__":
    main()
