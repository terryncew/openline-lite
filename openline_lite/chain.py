"""Verification for a small native OpenLine source-receipt chain."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import loads
from .gateway import FAIL, PASS, UNAVAILABLE, EvidenceGateway, IntakeResult
from .wire import SOURCE_SCHEMA, envelope_hash


@dataclass(frozen=True)
class ChainItem:
    index: int
    source_sha256: str
    envelope_hash: str
    payload: dict[str, Any]
    intake: IntakeResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "source_sha256": self.source_sha256,
            "envelope_hash": self.envelope_hash,
            "sequence": self.payload.get("sequence"),
            "run_id": self.payload.get("run_id"),
        }


@dataclass(frozen=True)
class ChainResult:
    status: str
    reason_codes: tuple[str, ...]
    items: tuple[ChainItem, ...]

    @property
    def valid(self) -> bool:
        return self.status == PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "valid": self.valid,
            "reason_codes": list(self.reason_codes),
            "length": len(self.items),
            "tip_hash": self.items[-1].envelope_hash if self.items else None,
            "items": [item.to_dict() for item in self.items],
        }


def verify_native_chain(
    source_receipts: list[bytes],
    trusted_keys: dict[str, str],
    *,
    max_source_bytes: int = 1_048_576,
    max_items: int = 256,
    max_total_bytes: int = 16_777_216,
) -> ChainResult:
    """Verify trust and parent linkage for a complete native run chain.

    The first receipt must use sequence zero and no parent hash.  Each later
    parent hash is the deterministic envelope hash of the previous receipt,
    independent of whitespace in the stored JSON bytes.
    """

    if max_items < 1 or max_total_bytes < 1:
        raise ValueError("chain_limit_invalid")
    if not source_receipts:
        return ChainResult(FAIL, ("chain_empty",), ())
    if len(source_receipts) > max_items:
        return ChainResult(FAIL, ("chain_item_limit_exceeded",), ())
    if any(not isinstance(item, bytes) for item in source_receipts):
        return ChainResult(FAIL, ("chain_item_type_invalid",), ())
    if sum(len(item) for item in source_receipts) > max_total_bytes:
        return ChainResult(FAIL, ("chain_total_size_limit_exceeded",), ())

    gateway = EvidenceGateway(max_source_bytes=max_source_bytes)
    items: list[ChainItem] = []
    reasons: list[str] = []
    has_failure = False
    has_unavailable = False

    for index, source_bytes in enumerate(source_receipts):
        intake = gateway.inspect(
            source_bytes,
            source_format=SOURCE_SCHEMA,
            trusted_keys=trusted_keys,
        )
        for name, check in (
            ("integrity", intake.integrity),
            ("provenance", intake.provenance),
            ("normalization", intake.normalization),
        ):
            if check.status == FAIL:
                has_failure = True
            elif check.status == UNAVAILABLE:
                has_unavailable = True
            reasons.extend(
                f"item:{index}:{name}:{reason}" for reason in check.reason_codes
            )

        try:
            envelope = loads(source_bytes)
            canonical_hash = envelope_hash(envelope)
        except (TypeError, ValueError):
            canonical_hash = ""
        items.append(
            ChainItem(
                index=index,
                source_sha256=intake.source_sha256,
                envelope_hash=canonical_hash,
                payload=intake.payload,
                intake=intake,
            )
        )

    first = items[0].payload
    if first:
        if first.get("sequence") != 0:
            reasons.append("chain:first_sequence_not_zero")
            has_failure = True
        if first.get("parent_hash") is not None:
            reasons.append("chain:first_parent_present")
            has_failure = True

    for index in range(1, len(items)):
        previous = items[index - 1]
        current = items[index]
        if not previous.payload or not current.payload:
            continue
        if current.payload.get("sequence") != previous.payload.get("sequence", -1) + 1:
            reasons.append(f"chain:sequence_gap:{index}")
            has_failure = True
        if current.payload.get("parent_hash") != previous.envelope_hash:
            reasons.append(f"chain:parent_mismatch:{index}")
            has_failure = True
        if current.payload.get("run_id") != previous.payload.get("run_id"):
            reasons.append(f"chain:run_id_changed:{index}")
            has_failure = True
        if current.payload.get("issuer") != previous.payload.get("issuer"):
            reasons.append(f"chain:issuer_changed:{index}")
            has_failure = True

    status = FAIL if has_failure else UNAVAILABLE if has_unavailable else PASS
    return ChainResult(status, tuple(sorted(set(reasons))), tuple(items))
