"""Evidence Gateway: preserve source bytes, verify native integrity, record trust."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .canonical import CanonicalJSONError, loads, sha256_hex
from .wire import SOURCE_SCHEMA, inspect_envelope, validate_source_payload


Status = str
PASS = "pass"
FAIL = "fail"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Check:
    status: Status
    reason_codes: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "details": self.details,
        }


@dataclass(frozen=True)
class IntakeResult:
    source_format: str
    source_sha256: str
    payload: dict[str, Any]
    integrity: Check
    provenance: Check
    normalization: Check

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_format": self.source_format,
            "source_sha256": self.source_sha256,
            "payload": self.payload,
            "integrity": self.integrity.to_dict(),
            "provenance": self.provenance.to_dict(),
            "normalization": self.normalization.to_dict(),
        }


class Adapter(Protocol):
    source_format: str

    def assess(
        self, source_bytes: bytes, trusted_keys: dict[str, str]
    ) -> IntakeResult: ...


class NativeOLPAdapter:
    source_format = SOURCE_SCHEMA

    def assess(self, source_bytes: bytes, trusted_keys: dict[str, str]) -> IntakeResult:
        source_sha = sha256_hex(source_bytes)
        try:
            envelope = loads(source_bytes)
            if not isinstance(envelope, dict):
                raise ValueError("source_envelope_invalid")
            inspected = inspect_envelope(envelope)
            payload = inspected["payload"]
        except (CanonicalJSONError, TypeError, ValueError) as exc:
            return IntakeResult(
                source_format=self.source_format,
                source_sha256=source_sha,
                payload={},
                integrity=Check(FAIL, (str(exc),)),
                provenance=Check(UNAVAILABLE, ("source_integrity_failed",)),
                normalization=Check(UNAVAILABLE, ("source_payload_unavailable",)),
            )

        integrity = (
            Check(
                PASS,
                (),
                {
                    "signature_valid": True,
                    "payload_sha256": inspected["payload_sha256"],
                },
            )
            if inspected["valid"]
            else Check(FAIL, tuple(inspected["errors"]))
        )
        key_id = str(inspected.get("key_id", ""))
        embedded_key = str(inspected.get("public_key", ""))
        trusted_key = trusted_keys.get(key_id)
        if integrity.status != PASS:
            provenance = Check(UNAVAILABLE, ("source_integrity_failed",))
        elif trusted_key is None:
            provenance = Check(
                UNAVAILABLE,
                ("source_key_untrusted",),
                {"key_id": key_id, "signature_self_consistent": True},
            )
        elif trusted_key != embedded_key:
            provenance = Check(FAIL, ("source_key_mismatch",), {"key_id": key_id})
        else:
            provenance = Check(PASS, (), {"key_id": key_id})

        try:
            validate_source_payload(payload)
            normalization = Check(PASS, (), {"schema": SOURCE_SCHEMA})
        except (TypeError, ValueError) as exc:
            normalization = Check(FAIL, (str(exc),))
        return IntakeResult(
            source_format=self.source_format,
            source_sha256=source_sha,
            payload=payload,
            integrity=integrity,
            provenance=provenance,
            normalization=normalization,
        )


class EvidenceGateway:
    def __init__(
        self,
        adapters: list[Adapter] | None = None,
        *,
        max_source_bytes: int = 1_048_576,
    ) -> None:
        if max_source_bytes < 1:
            raise ValueError("max_source_bytes_invalid")
        installed = adapters or [NativeOLPAdapter()]
        self._adapters = {adapter.source_format: adapter for adapter in installed}
        self._max_source_bytes = max_source_bytes

    def inspect(
        self,
        source_bytes: bytes,
        *,
        source_format: str,
        trusted_keys: dict[str, str],
    ) -> IntakeResult:
        if len(source_bytes) > self._max_source_bytes:
            unavailable = Check(UNAVAILABLE, ("source_size_limit_exceeded",))
            return IntakeResult(
                source_format=source_format,
                source_sha256=sha256_hex(source_bytes),
                payload={},
                integrity=Check(FAIL, ("source_size_limit_exceeded",)),
                provenance=unavailable,
                normalization=unavailable,
            )
        adapter = self._adapters.get(source_format)
        if adapter is None:
            return IntakeResult(
                source_format=source_format,
                source_sha256=sha256_hex(source_bytes),
                payload={},
                integrity=Check(UNAVAILABLE, ("source_format_unsupported",)),
                provenance=Check(UNAVAILABLE, ("source_format_unsupported",)),
                normalization=Check(UNAVAILABLE, ("source_format_unsupported",)),
            )
        return adapter.assess(source_bytes, trusted_keys)
