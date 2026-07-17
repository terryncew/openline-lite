"""Bounded claim-support replay for receiver-owned JSON facts.

This deliberately does not infer truth from natural language.  It checks only
the explicit facts a receiver placed in its signed policy snapshot.
"""

from __future__ import annotations

from typing import Any

from .canonical import CanonicalJSONError, loads
from .gateway import Check, FAIL, PASS, UNAVAILABLE
from .pointer import JSONPointerError, resolve
from .policy import Policy


def evaluate_claim_support(artifacts: dict[str, bytes], policy: Policy) -> Check:
    if not policy.claim_rules:
        return Check(UNAVAILABLE, ("claim_rules_missing",), {"evaluated": []})

    parsed: dict[str, Any] = {}
    reasons: list[str] = []
    evaluated: list[dict[str, Any]] = []
    has_failure = False
    has_unavailable = False

    for rule in policy.claim_rules:
        artifact = artifacts.get(rule.evidence_id)
        if artifact is None:
            reasons.append(f"claim_artifact_missing:{rule.rule_id}")
            has_unavailable = True
            continue
        try:
            if rule.evidence_id not in parsed:
                parsed[rule.evidence_id] = loads(artifact)
            actual = resolve(parsed[rule.evidence_id], rule.pointer)
        except CanonicalJSONError:
            reasons.append(f"claim_evidence_invalid_json:{rule.rule_id}")
            has_failure = True
            continue
        except JSONPointerError:
            reasons.append(f"claim_fact_missing:{rule.rule_id}")
            has_failure = True
            continue

        matched = actual == rule.expected and type(actual) is type(rule.expected)
        evaluated.append(
            {
                "rule_id": rule.rule_id,
                "evidence_id": rule.evidence_id,
                "pointer": rule.pointer,
                "matched": matched,
            }
        )
        if not matched:
            reasons.append(f"claim_fact_mismatch:{rule.rule_id}")
            has_failure = True

    details = {"evaluated": evaluated}
    if has_failure:
        return Check(FAIL, tuple(sorted(reasons)), details)
    if has_unavailable:
        return Check(UNAVAILABLE, tuple(sorted(reasons)), details)
    return Check(PASS, (), details)
