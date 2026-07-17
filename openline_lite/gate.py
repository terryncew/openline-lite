"""Receipt Gate: receiver policy in, signed next-use decision out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .canonical import sha256_hex
from .claim_support import evaluate_claim_support
from .gateway import Check, FAIL, PASS, UNAVAILABLE, IntakeResult
from .policy import Policy, disposition_for
from .wire import DECISION_SCHEMA, issue_decision_receipt


VERIFIED = "VERIFIED"
REJECTED = "REJECTED"
UNDECIDABLE = "UNDECIDABLE"


def _parse_time(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_missing")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DecisionResult:
    verdict: str
    decision: str
    reason_codes: tuple[str, ...]
    receipt: dict[str, Any]


class ReceiptGate:
    def __init__(self, *, gate_id: str, private_key: str) -> None:
        self.gate_id = gate_id
        self.private_key = private_key

    def decide(
        self,
        intake: IntakeResult,
        *,
        artifacts: dict[str, bytes],
        policy: Policy,
        now: datetime | None = None,
        side_effect_observed: bool = False,
    ) -> DecisionResult:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        payload = intake.payload
        assessments: dict[str, Check] = {
            "integrity": intake.integrity,
            "provenance": intake.provenance,
            "normalization": intake.normalization,
        }

        if not payload:
            unavailable = Check(UNAVAILABLE, ("source_payload_unavailable",))
            assessments["policy"] = unavailable
            assessments["freshness"] = unavailable
            assessments["evidence"] = unavailable
            assessments["claim_support"] = unavailable
        else:
            action_type = None
            action = payload.get("action")
            if isinstance(action, dict):
                action_type = action.get("type")
            if action_type in policy.allowed_actions:
                assessments["policy"] = Check(PASS)
            else:
                assessments["policy"] = Check(
                    FAIL, ("action_not_allowed",), {"action_type": action_type}
                )

            try:
                issued_at = _parse_time(str(payload.get("issued_at", "")))
                age = int((current - issued_at).total_seconds())
                if age < 0:
                    assessments["freshness"] = Check(
                        FAIL, ("receipt_from_future",), {"age_seconds": age}
                    )
                elif age > policy.max_age_seconds:
                    assessments["freshness"] = Check(
                        UNAVAILABLE, ("receipt_expired",), {"age_seconds": age}
                    )
                else:
                    assessments["freshness"] = Check(PASS, (), {"age_seconds": age})
            except (TypeError, ValueError):
                assessments["freshness"] = Check(FAIL, ("receipt_timestamp_invalid",))

            commitments = {
                item["id"]: item["sha256"]
                for item in payload.get("evidence", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
            evidence_reasons: list[str] = []
            evidence_fail = False
            evidence_details: dict[str, Any] = {"verified": []}
            for evidence_id in policy.required_evidence:
                expected = commitments.get(evidence_id)
                artifact = artifacts.get(evidence_id)
                if expected is None:
                    evidence_reasons.append(f"commitment_missing:{evidence_id}")
                elif artifact is None:
                    evidence_reasons.append(f"artifact_missing:{evidence_id}")
                elif sha256_hex(artifact) != expected:
                    evidence_reasons.append(f"artifact_hash_mismatch:{evidence_id}")
                    evidence_fail = True
                else:
                    evidence_details["verified"].append(evidence_id)
            if evidence_fail:
                assessments["evidence"] = Check(
                    FAIL, tuple(evidence_reasons), evidence_details
                )
            elif evidence_reasons:
                assessments["evidence"] = Check(
                    UNAVAILABLE, tuple(evidence_reasons), evidence_details
                )
            else:
                assessments["evidence"] = Check(PASS, (), evidence_details)

            assessments["claim_support"] = evaluate_claim_support(artifacts, policy)

        statuses = {name: check.status for name, check in assessments.items()}
        reasons = sorted(
            f"{name}:{reason}"
            for name, check in assessments.items()
            for reason in check.reason_codes
        )
        verdict, decision = disposition_for(
            list(statuses.values()),
            policy=policy,
            side_effect_observed=side_effect_observed,
        )

        decision_payload = {
            "schema": DECISION_SCHEMA,
            "gate_id": self.gate_id,
            "issued_at": current.isoformat().replace("+00:00", "Z"),
            "source_format": intake.source_format,
            "source_sha256": intake.source_sha256,
            "policy": {**policy.to_dict(), "sha256": policy.sha256},
            "verdict": verdict,
            "decision": decision,
            "reason_codes": reasons,
            "side_effect_observed": side_effect_observed,
            "assessments": {
                name: check.to_dict() for name, check in assessments.items()
            },
        }
        receipt = issue_decision_receipt(
            decision_payload, self.private_key, self.gate_id
        )
        return DecisionResult(verdict, decision, tuple(reasons), receipt)
