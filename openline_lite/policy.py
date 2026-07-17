"""Receiver-owned policy for the minimal gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import object_hash, validate


VALID_STATUSES = {"pass", "fail", "unavailable"}


@dataclass(frozen=True)
class ClaimRule:
    """A bounded receiver-owned fact that must be present in JSON evidence."""

    rule_id: str
    evidence_id: str
    pointer: str
    expected: Any

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ClaimRule":
        if set(value) != {"id", "evidence_id", "pointer", "expected"}:
            raise ValueError("claim_rule_fields_invalid")
        if not isinstance(value["id"], str) or not value["id"]:
            raise ValueError("claim_rule_id_invalid")
        if not isinstance(value["evidence_id"], str) or not value["evidence_id"]:
            raise ValueError("claim_rule_evidence_id_invalid")
        if not isinstance(value["pointer"], str) or (
            value["pointer"] and not value["pointer"].startswith("/")
        ):
            raise ValueError("claim_rule_pointer_invalid")
        validate(value["expected"], path="$.claim_rule.expected")
        return cls(
            rule_id=value["id"],
            evidence_id=value["evidence_id"],
            pointer=value["pointer"],
            expected=value["expected"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.rule_id,
            "evidence_id": self.evidence_id,
            "pointer": self.pointer,
            "expected": self.expected,
        }


@dataclass(frozen=True)
class Policy:
    policy_id: str
    version: str
    allowed_actions: tuple[str, ...]
    required_evidence: tuple[str, ...]
    claim_rules: tuple[ClaimRule, ...]
    max_age_seconds: int
    on_undecidable: str = "QUARANTINE"
    rollback_supported: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Policy":
        required = {
            "policy_id",
            "version",
            "allowed_actions",
            "required_evidence",
            "claim_rules",
            "max_age_seconds",
            "on_undecidable",
            "rollback_supported",
        }
        if set(value) != required:
            raise ValueError("policy_fields_invalid")
        if not isinstance(value["policy_id"], str) or not value["policy_id"]:
            raise ValueError("policy_id_invalid")
        if not isinstance(value["version"], str) or not value["version"]:
            raise ValueError("policy_version_invalid")
        if value["on_undecidable"] not in {"QUARANTINE", "NO_BADGE"}:
            raise ValueError("policy_undecidable_disposition_invalid")
        if (
            not isinstance(value["max_age_seconds"], int)
            or isinstance(value["max_age_seconds"], bool)
            or value["max_age_seconds"] < 0
        ):
            raise ValueError("policy_max_age_invalid")
        if not isinstance(value["rollback_supported"], bool):
            raise ValueError("policy_rollback_invalid")
        for field in ("allowed_actions", "required_evidence"):
            if not isinstance(value[field], list) or not all(
                isinstance(item, str) and item for item in value[field]
            ):
                raise ValueError(f"policy_{field}_invalid")
            if len(set(value[field])) != len(value[field]):
                raise ValueError(f"policy_{field}_duplicate")
        if not isinstance(value["claim_rules"], list):
            raise ValueError("policy_claim_rules_invalid")
        rules = tuple(
            ClaimRule.from_mapping(item)
            for item in value["claim_rules"]
            if isinstance(item, dict)
        )
        if len(rules) != len(value["claim_rules"]):
            raise ValueError("policy_claim_rules_invalid")
        if len({rule.rule_id for rule in rules}) != len(rules):
            raise ValueError("policy_claim_rule_duplicate")
        required_evidence = tuple(value["required_evidence"])
        if any(rule.evidence_id not in required_evidence for rule in rules):
            raise ValueError("claim_rule_evidence_not_required")
        return cls(
            policy_id=str(value["policy_id"]),
            version=str(value["version"]),
            allowed_actions=tuple(value["allowed_actions"]),
            required_evidence=required_evidence,
            claim_rules=rules,
            max_age_seconds=value["max_age_seconds"],
            on_undecidable=value["on_undecidable"],
            rollback_supported=value["rollback_supported"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "allowed_actions": list(self.allowed_actions),
            "required_evidence": list(self.required_evidence),
            "claim_rules": [rule.to_dict() for rule in self.claim_rules],
            "max_age_seconds": self.max_age_seconds,
            "on_undecidable": self.on_undecidable,
            "rollback_supported": self.rollback_supported,
        }

    @property
    def sha256(self) -> str:
        return object_hash(self.to_dict())


def disposition_for(
    statuses: list[str],
    *,
    policy: Policy,
    side_effect_observed: bool,
) -> tuple[str, str]:
    if not statuses or any(status not in VALID_STATUSES for status in statuses):
        raise ValueError("assessment_status_invalid")
    if "fail" in statuses:
        return (
            "REJECTED",
            "ROLLBACK_REQUEST"
            if side_effect_observed and policy.rollback_supported
            else "DENY",
        )
    if "unavailable" in statuses:
        return "UNDECIDABLE", policy.on_undecidable
    return "VERIFIED", "COMMIT"
