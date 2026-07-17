"""OpenLine Lite source and decision receipt envelopes."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .canonical import dumps, object_hash
from .crypto import public_key_hex, sign, verify


SOURCE_SCHEMA = "olp.source.v1"
DECISION_SCHEMA = "olp.decision.v1"
ENVELOPE_FIELDS = {"payload", "payload_sha256", "proof"}
PROOF_FIELDS = {"alg", "key_id", "public_key", "signature"}
SOURCE_FIELDS = {
    "schema",
    "issuer",
    "issued_at",
    "run_id",
    "sequence",
    "parent_hash",
    "action",
    "claim",
    "evidence",
    "extensions",
}
DECISION_FIELDS = {
    "schema",
    "gate_id",
    "issued_at",
    "source_format",
    "source_sha256",
    "policy",
    "verdict",
    "decision",
    "reason_codes",
    "side_effect_observed",
    "assessments",
}


def _exact_fields(
    value: Mapping[str, Any], allowed: set[str], required: set[str], label: str
) -> None:
    missing = required - set(value)
    unknown = set(value) - allowed
    if missing:
        raise ValueError(f"{label}_missing:{','.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"{label}_unknown:{','.join(sorted(unknown))}")


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        return len(bytes.fromhex(value)) == 32
    except ValueError:
        return False


def validate_source_payload(payload: Mapping[str, Any]) -> None:
    _exact_fields(
        payload,
        SOURCE_FIELDS,
        SOURCE_FIELDS - {"parent_hash", "extensions"},
        "source",
    )
    if payload["schema"] != SOURCE_SCHEMA:
        raise ValueError("source_schema_unsupported")
    if not isinstance(payload["issuer"], str) or not payload["issuer"]:
        raise ValueError("source_issuer_invalid")
    if not isinstance(payload["issued_at"], str) or not payload["issued_at"]:
        raise ValueError("source_issued_at_invalid")
    if not isinstance(payload["run_id"], str) or not payload["run_id"]:
        raise ValueError("source_run_id_invalid")
    if (
        not isinstance(payload["sequence"], int)
        or isinstance(payload["sequence"], bool)
        or payload["sequence"] < 0
    ):
        raise ValueError("source_sequence_invalid")
    if payload.get("parent_hash") is not None:
        parent = payload["parent_hash"]
        if not _is_sha256(parent):
            raise ValueError("source_parent_hash_invalid")
    action = payload["action"]
    if not isinstance(action, Mapping):
        raise ValueError("source_action_invalid")
    _exact_fields(action, {"type", "name", "target"}, {"type", "name"}, "action")
    if not all(
        isinstance(action[key], str) and action[key] for key in ("type", "name")
    ):
        raise ValueError("source_action_invalid")
    if "target" in action and not isinstance(action["target"], str):
        raise ValueError("source_target_invalid")
    if not isinstance(payload["claim"], str) or not payload["claim"]:
        raise ValueError("source_claim_invalid")
    evidence = payload["evidence"]
    if not isinstance(evidence, list):
        raise ValueError("source_evidence_invalid")
    seen: set[str] = set()
    for item in evidence:
        if not isinstance(item, Mapping):
            raise ValueError("source_evidence_entry_invalid")
        _exact_fields(
            item, {"id", "sha256", "media_type"}, {"id", "sha256"}, "evidence"
        )
        evidence_id = item["id"]
        digest = item["sha256"]
        if not isinstance(evidence_id, str) or not evidence_id or evidence_id in seen:
            raise ValueError("source_evidence_id_invalid")
        if not _is_sha256(digest):
            raise ValueError("source_evidence_hash_invalid")
        if "media_type" in item and not isinstance(item["media_type"], str):
            raise ValueError("source_evidence_media_type_invalid")
        seen.add(evidence_id)
    if "extensions" in payload and not isinstance(payload["extensions"], Mapping):
        raise ValueError("source_extensions_invalid")


def _issue(payload: Mapping[str, Any], private_key: str, key_id: str) -> dict[str, Any]:
    body = deepcopy(dict(payload))
    payload_bytes = dumps(body)
    return {
        "payload": body,
        "payload_sha256": object_hash(body),
        "proof": {
            "alg": "Ed25519",
            "key_id": key_id,
            "public_key": public_key_hex(private_key),
            "signature": sign(payload_bytes, private_key),
        },
    }


def issue_source_receipt(
    payload: Mapping[str, Any], private_key: str, key_id: str
) -> dict[str, Any]:
    validate_source_payload(payload)
    return _issue(payload, private_key, key_id)


def issue_decision_receipt(
    payload: Mapping[str, Any], private_key: str, key_id: str
) -> dict[str, Any]:
    validate_decision_payload(payload)
    return _issue(payload, private_key, key_id)


def validate_decision_payload(payload: Mapping[str, Any]) -> None:
    _exact_fields(payload, DECISION_FIELDS, DECISION_FIELDS, "decision")
    if payload.get("schema") != DECISION_SCHEMA:
        raise ValueError("decision_schema_unsupported")
    if not isinstance(payload.get("gate_id"), str) or not payload["gate_id"]:
        raise ValueError("decision_gate_id_invalid")
    if not isinstance(payload.get("issued_at"), str) or not payload["issued_at"]:
        raise ValueError("decision_issued_at_invalid")
    if (
        not isinstance(payload.get("source_format"), str)
        or not payload["source_format"]
    ):
        raise ValueError("decision_source_format_invalid")
    if not _is_sha256(payload.get("source_sha256")):
        raise ValueError("decision_source_hash_invalid")
    if payload.get("verdict") not in {"VERIFIED", "REJECTED", "UNDECIDABLE"}:
        raise ValueError("decision_verdict_invalid")
    if payload.get("decision") not in {
        "COMMIT",
        "QUARANTINE",
        "DENY",
        "NO_BADGE",
        "ROLLBACK_REQUEST",
    }:
        raise ValueError("decision_disposition_invalid")
    if not isinstance(payload.get("side_effect_observed"), bool):
        raise ValueError("decision_side_effect_invalid")
    if not isinstance(payload.get("reason_codes"), list) or not all(
        isinstance(item, str) for item in payload["reason_codes"]
    ):
        raise ValueError("decision_reasons_invalid")
    if not isinstance(payload.get("policy"), Mapping):
        raise ValueError("decision_policy_invalid")
    if not isinstance(payload.get("assessments"), Mapping):
        raise ValueError("decision_assessments_invalid")


def envelope_hash(envelope: Mapping[str, Any]) -> str:
    return object_hash(envelope)


def inspect_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if set(envelope) != ENVELOPE_FIELDS:
        errors.append("envelope_fields_invalid")
    payload = envelope.get("payload")
    proof = envelope.get("proof")
    if not isinstance(payload, Mapping):
        errors.append("payload_invalid")
        payload = {}
    if not isinstance(proof, Mapping) or set(proof) != PROOF_FIELDS:
        errors.append("proof_invalid")
        proof = {}
    try:
        expected_hash = object_hash(payload)
    except (TypeError, ValueError):
        expected_hash = ""
        errors.append("payload_canonicalization_failed")
    if envelope.get("payload_sha256") != expected_hash:
        errors.append("payload_hash_mismatch")
    if proof.get("alg") != "Ed25519":
        errors.append("algorithm_unsupported")
    signature_valid = (
        verify(
            dumps(payload),
            str(proof.get("signature", "")),
            str(proof.get("public_key", "")),
        )
        if expected_hash
        else False
    )
    if not signature_valid:
        errors.append("signature_invalid")
    return {
        "valid": not errors,
        "errors": errors,
        "payload": dict(payload),
        "payload_sha256": expected_hash,
        "key_id": proof.get("key_id"),
        "public_key": proof.get("public_key"),
        "signature_valid": signature_valid,
    }


def verify_decision_receipt(
    receipt: Mapping[str, Any], trusted_gate_keys: Mapping[str, str]
) -> dict[str, Any]:
    inspected = inspect_envelope(receipt)
    payload = inspected["payload"]
    errors = list(inspected["errors"])
    try:
        validate_decision_payload(payload)
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    key_id = inspected.get("key_id")
    if payload.get("gate_id") != key_id:
        errors.append("gate_id_key_id_mismatch")
    expected_key = trusted_gate_keys.get(str(key_id))
    if expected_key is None:
        errors.append("gate_key_untrusted")
    elif expected_key != inspected.get("public_key"):
        errors.append("gate_key_mismatch")

    try:
        from .policy import Policy, disposition_for

        policy_value = dict(payload["policy"])
        claimed_policy_hash = policy_value.pop("sha256")
        policy = Policy.from_mapping(policy_value)
        if claimed_policy_hash != policy.sha256:
            errors.append("policy_hash_mismatch")

        assessments = payload["assessments"]
        expected_assessments = {
            "integrity",
            "provenance",
            "normalization",
            "policy",
            "freshness",
            "evidence",
            "claim_support",
        }
        if set(assessments) != expected_assessments:
            raise ValueError("assessment_set_invalid")
        statuses: list[str] = []
        recomputed_reasons: list[str] = []
        for name, assessment in assessments.items():
            if not isinstance(assessment, Mapping):
                raise ValueError("assessment_invalid")
            status = assessment.get("status")
            reason_codes = assessment.get("reason_codes")
            if (
                not isinstance(status, str)
                or not isinstance(reason_codes, list)
                or not all(isinstance(reason, str) for reason in reason_codes)
            ):
                raise ValueError("assessment_invalid")
            statuses.append(status)
            recomputed_reasons.extend(f"{name}:{reason}" for reason in reason_codes)
        verdict, decision = disposition_for(
            statuses,
            policy=policy,
            side_effect_observed=payload["side_effect_observed"],
        )
        if (payload["verdict"], payload["decision"]) != (verdict, decision):
            errors.append("decision_recompute_mismatch")
        if payload["reason_codes"] != sorted(recomputed_reasons):
            errors.append("reason_codes_recompute_mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    return {"valid": not errors, "errors": sorted(set(errors)), "payload": payload}
