"""Compact prompt projection derived from verified receipts and decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .canonical import dumps, object_hash
from .chain import ChainResult
from .wire import SOURCE_SCHEMA, verify_decision_receipt


HANDOFF_SCHEMA = "olp.handoff.v1"


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        return len(bytes.fromhex(value)) == 32
    except ValueError:
        return False


@dataclass(frozen=True)
class HandoffFact:
    rule_id: str
    evidence_id: str
    pointer: str
    expected: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "evidence_id": self.evidence_id,
            "pointer": self.pointer,
            "expected": self.expected,
        }


@dataclass(frozen=True)
class HandoffItem:
    sequence: int
    action: str
    source_claim: str
    support_facts: tuple[HandoffFact, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "action": self.action,
            "source_claim": self.source_claim,
            "support_facts": [fact.to_dict() for fact in self.support_facts],
        }

    def to_prompt_dict(self) -> dict[str, Any]:
        """Return only receiver-selected facts for prompt carryover.

        ``source_claim`` remains in the full projection for audit context, but
        is intentionally absent here: the bounded equality rules verify the
        listed facts, not arbitrary natural-language entailment.
        """

        return {
            "seq": self.sequence,
            "a": self.action,
            "facts": [
                [fact.evidence_id, fact.pointer, fact.expected]
                for fact in self.support_facts
            ],
        }


@dataclass(frozen=True)
class HandoffProjection:
    run_id: str
    chain_length: int
    tip_hash: str
    accepted_count: int
    omitted_accepted_count: int
    excluded_count: int
    excluded_by_reason: dict[str, int]
    ignored_decision_count: int
    allowed_policy_hashes: tuple[str, ...]
    items: tuple[HandoffItem, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": HANDOFF_SCHEMA,
            "run_id": self.run_id,
            "chain_length": self.chain_length,
            "tip_hash": self.tip_hash,
            "accepted_count": self.accepted_count,
            "omitted_accepted_count": self.omitted_accepted_count,
            "excluded_count": self.excluded_count,
            "excluded_by_reason": dict(sorted(self.excluded_by_reason.items())),
            "ignored_decision_count": self.ignored_decision_count,
            "allowed_policy_hashes": list(self.allowed_policy_hashes),
            "items": [item.to_dict() for item in self.items],
        }

    def render_jsonl(self) -> str:
        """Render bounded, line-safe data for insertion into a model prompt.

        Full hashes and source-authored claims remain in ``to_dict`` and the
        retained receipts. The prompt view carries only receiver-selected
        support facts and uses hash prefixes to stay small. JSON encoding keeps
        string-valued facts line-safe; it does not make strings into trusted
        instructions.
        """

        header = {
            "s": HANDOFF_SCHEMA,
            "k": "h",
            "run": self.run_id,
            "n": self.chain_length,
            "tip": self.tip_hash[:16],
            "ok": self.accepted_count,
            "show": len(self.items),
            "drop": self.excluded_count,
            "pn": len(self.allowed_policy_hashes),
            "ps": object_hash(list(self.allowed_policy_hashes))[:16],
        }
        lines = [dumps(header).decode("utf-8")]
        for item in self.items:
            compact = {"k": "i", **item.to_prompt_dict()}
            lines.append(dumps(compact).decode("utf-8"))
        return "\n".join(lines) + "\n"

    def render_text(self) -> str:
        """Backward-compatible alias for the JSONL prompt projection."""

        return self.render_jsonl()


def build_handoff_projection(
    chain: ChainResult,
    decision_receipts: list[Mapping[str, Any]],
    trusted_gate_keys: Mapping[str, str],
    *,
    allowed_policy_hashes: Iterable[str],
    max_claims: int = 3,
    max_claim_chars: int = 280,
    max_action_chars: int = 128,
    max_decisions: int = 512,
    max_facts_per_item: int = 64,
    max_fact_bytes: int = 4096,
) -> HandoffProjection:
    """Admit only sources with trusted, policy-approved COMMIT decisions.

    A valid signature is insufficient. Decision receipts must be internally
    consistent, signed by a pinned gate, refer to this exact native source
    chain, and use a receiver-approved policy hash. Conflicting eligible
    decisions exclude the source instead of letting one COMMIT win.
    """

    if not chain.valid:
        raise ValueError("handoff_chain_not_verified")
    if (
        max_claims < 1
        or max_claim_chars < 1
        or max_action_chars < 1
        or max_facts_per_item < 1
        or max_fact_bytes < 1
    ):
        raise ValueError("handoff_limit_invalid")
    if max_decisions < 1 or len(decision_receipts) > max_decisions:
        raise ValueError("handoff_decision_limit_exceeded")

    policy_hash_values = list(allowed_policy_hashes)
    if not policy_hash_values or any(
        not _is_sha256(value) for value in policy_hash_values
    ):
        raise ValueError("handoff_policy_allowlist_invalid")
    policy_hashes = tuple(sorted(set(policy_hash_values)))

    run_id = str(chain.items[0].payload["run_id"])
    if len(run_id) > 128:
        raise ValueError("handoff_run_id_too_large")

    source_hashes = {item.source_sha256 for item in chain.items}
    eligible: dict[str, list[dict[str, Any]]] = {
        source_hash: [] for source_hash in source_hashes
    }
    ignored_decisions = 0

    for index, receipt in enumerate(decision_receipts):
        if not isinstance(receipt, Mapping):
            raise ValueError(f"handoff_decision_invalid:{index}:object_required")
        checked = verify_decision_receipt(receipt, trusted_gate_keys)
        if not checked["valid"]:
            errors = ",".join(checked["errors"])
            raise ValueError(f"handoff_decision_invalid:{index}:{errors}")
        payload = checked["payload"]
        if payload.get("source_format") != SOURCE_SCHEMA:
            raise ValueError(f"handoff_decision_source_format_invalid:{index}")
        source_hash = str(payload.get("source_sha256", ""))
        if source_hash not in source_hashes:
            raise ValueError(f"handoff_decision_source_outside_chain:{index}")
        policy = payload.get("policy", {})
        policy_hash = policy.get("sha256") if isinstance(policy, Mapping) else None
        if policy_hash not in policy_hashes:
            ignored_decisions += 1
            continue
        eligible[source_hash].append(dict(payload))

    accepted: list[HandoffItem] = []
    excluded_by_reason: dict[str, int] = {}

    def exclude(reason: str) -> None:
        excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1

    for item in chain.items:
        decisions = eligible[item.source_sha256]
        if not decisions:
            exclude("no_allowed_policy_decision")
            continue
        if any(
            payload.get("verdict") != "VERIFIED" or payload.get("decision") != "COMMIT"
            for payload in decisions
        ):
            exclude("eligible_non_commit_or_conflict")
            continue

        action = item.payload.get("action", {})
        action_name = str(action.get("name", "")) if isinstance(action, Mapping) else ""
        source_claim = str(item.payload.get("claim", ""))
        if len(action_name) > max_action_chars:
            exclude("action_too_large")
            continue
        if len(source_claim) > max_claim_chars:
            exclude("source_claim_too_large")
            continue

        facts: dict[bytes, HandoffFact] = {}
        for decision in decisions:
            policy = decision["policy"]
            if len(policy["claim_rules"]) > max_facts_per_item:
                raise ValueError("handoff_fact_limit_exceeded")
            for rule in policy["claim_rules"]:
                fact = HandoffFact(
                    rule_id=str(rule["id"]),
                    evidence_id=str(rule["evidence_id"]),
                    pointer=str(rule["pointer"]),
                    expected=rule["expected"],
                )
                serialized_fact = dumps(fact.to_dict())
                if len(serialized_fact) > max_fact_bytes:
                    raise ValueError("handoff_fact_size_limit_exceeded")
                facts[serialized_fact] = fact
                if len(facts) > max_facts_per_item:
                    raise ValueError("handoff_fact_limit_exceeded")
        accepted.append(
            HandoffItem(
                sequence=int(item.payload["sequence"]),
                action=action_name,
                source_claim=source_claim,
                support_facts=tuple(facts[key] for key in sorted(facts)),
            )
        )

    visible = tuple(accepted[-max_claims:])
    return HandoffProjection(
        run_id=run_id,
        chain_length=len(chain.items),
        tip_hash=chain.items[-1].envelope_hash,
        accepted_count=len(accepted),
        omitted_accepted_count=max(0, len(accepted) - len(visible)),
        excluded_count=sum(excluded_by_reason.values()),
        excluded_by_reason=excluded_by_reason,
        ignored_decision_count=ignored_decisions,
        allowed_policy_hashes=policy_hashes,
        items=visible,
    )
