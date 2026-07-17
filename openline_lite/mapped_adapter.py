"""Declarative adapter for Ed25519-signed canonical JSON receipts.

The profile maps a foreign signed object into the small normalized payload the
Receipt Gate understands.  It never treats a producer's `verified` field as a
trust result: integrity is recomputed and the key must be pinned externally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import CanonicalJSONError, dumps, loads, object_hash, sha256_hex
from .crypto import verify
from .gateway import Check, FAIL, PASS, UNAVAILABLE, IntakeResult
from .pointer import JSONPointerError, resolve
from .wire import SOURCE_SCHEMA, validate_source_payload


@dataclass(frozen=True)
class AdapterProfile:
    profile_id: str
    source_format: str
    signed_object: str
    proof: dict[str, str]
    fields: dict[str, str | None]
    evidence_fields: dict[str, str | None]

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "AdapterProfile":
        if set(value) != {
            "profile_id",
            "source_format",
            "signed_object",
            "proof",
            "fields",
            "evidence_fields",
        }:
            raise ValueError("adapter_profile_fields_invalid")
        for field in ("profile_id", "source_format", "signed_object"):
            if not isinstance(value[field], str) or not value[field]:
                raise ValueError(f"adapter_{field}_invalid")
        if not value["signed_object"].startswith("/"):
            raise ValueError("adapter_signed_object_invalid")
        if value["source_format"] == SOURCE_SCHEMA:
            raise ValueError("adapter_native_format_reserved")

        proof_names = {"algorithm", "key_id", "public_key", "signature"}
        proof = value["proof"]
        if (
            not isinstance(proof, dict)
            or set(proof) != proof_names
            or not all(
                isinstance(path, str) and path.startswith("/")
                for path in proof.values()
            )
        ):
            raise ValueError("adapter_proof_invalid")

        field_names = {
            "issuer",
            "issued_at",
            "run_id",
            "sequence",
            "action_type",
            "action_name",
            "action_target",
            "claim",
            "evidence",
        }
        fields = value["fields"]
        if not isinstance(fields, dict) or set(fields) != field_names:
            raise ValueError("adapter_mapping_fields_invalid")
        for name, path in fields.items():
            if name == "action_target" and path is None:
                continue
            if not isinstance(path, str) or not path.startswith("/"):
                raise ValueError(f"adapter_mapping_invalid:{name}")
            signed_root = value["signed_object"].rstrip("/")
            if path != signed_root and not path.startswith(signed_root + "/"):
                raise ValueError(f"adapter_unsigned_mapping_forbidden:{name}")

        evidence_names = {"id", "sha256", "media_type"}
        evidence_fields = value["evidence_fields"]
        if (
            not isinstance(evidence_fields, dict)
            or set(evidence_fields) != evidence_names
        ):
            raise ValueError("adapter_evidence_fields_invalid")
        for name, field_name in evidence_fields.items():
            if name == "media_type" and field_name is None:
                continue
            if not isinstance(field_name, str) or not field_name:
                raise ValueError(f"adapter_evidence_field_invalid:{name}")

        return cls(
            profile_id=value["profile_id"],
            source_format=value["source_format"],
            signed_object=value["signed_object"],
            proof=dict(proof),
            fields=dict(fields),
            evidence_fields=dict(evidence_fields),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "source_format": self.source_format,
            "signed_object": self.signed_object,
            "proof": self.proof,
            "fields": self.fields,
            "evidence_fields": self.evidence_fields,
        }

    @property
    def sha256(self) -> str:
        return object_hash(self.to_dict())


class MappedEd25519JSONAdapter:
    def __init__(self, profile: AdapterProfile) -> None:
        self.profile = profile
        self.source_format = profile.source_format

    def _map_payload(self, document: dict[str, Any]) -> dict[str, Any]:
        mapped = {
            name: resolve(document, path)
            for name, path in self.profile.fields.items()
            if path is not None
        }
        evidence_value = mapped.pop("evidence")
        if not isinstance(evidence_value, list):
            raise ValueError("adapter_evidence_not_array")
        evidence: list[dict[str, Any]] = []
        for item in evidence_value:
            if not isinstance(item, dict):
                raise ValueError("adapter_evidence_entry_invalid")
            normalized = {
                "id": item.get(self.profile.evidence_fields["id"]),
                "sha256": item.get(self.profile.evidence_fields["sha256"]),
            }
            media_field = self.profile.evidence_fields["media_type"]
            if media_field is not None and media_field in item:
                normalized["media_type"] = item[media_field]
            evidence.append(normalized)

        action = {"type": mapped["action_type"], "name": mapped["action_name"]}
        if "action_target" in mapped:
            action["target"] = mapped["action_target"]
        payload = {
            "schema": SOURCE_SCHEMA,
            "issuer": mapped["issuer"],
            "issued_at": mapped["issued_at"],
            "run_id": mapped["run_id"],
            "sequence": mapped["sequence"],
            "action": action,
            "claim": mapped["claim"],
            "evidence": evidence,
            "extensions": {
                "adapter_profile_id": self.profile.profile_id,
                "adapter_profile_sha256": self.profile.sha256,
                "foreign_source_format": self.source_format,
            },
        }
        validate_source_payload(payload)
        return payload

    def assess(self, source_bytes: bytes, trusted_keys: dict[str, str]) -> IntakeResult:
        source_sha = sha256_hex(source_bytes)
        try:
            document = loads(source_bytes)
            if not isinstance(document, dict):
                raise ValueError("foreign_receipt_object_required")
        except (CanonicalJSONError, ValueError) as exc:
            unavailable = Check(UNAVAILABLE, ("foreign_receipt_unavailable",))
            return IntakeResult(
                source_format=self.source_format,
                source_sha256=source_sha,
                payload={},
                integrity=Check(FAIL, (str(exc),)),
                provenance=unavailable,
                normalization=unavailable,
            )

        key_id = ""
        public_key = ""
        try:
            algorithm = resolve(document, self.profile.proof["algorithm"])
            key_id = str(resolve(document, self.profile.proof["key_id"]))
            public_key = str(resolve(document, self.profile.proof["public_key"]))
            signature = str(resolve(document, self.profile.proof["signature"]))
            signed_value = resolve(document, self.profile.signed_object)
            signature_valid = algorithm == "Ed25519" and verify(
                dumps(signed_value), signature, public_key
            )
            integrity = (
                Check(
                    PASS,
                    (),
                    {
                        "signature_valid": True,
                        "signed_object_sha256": object_hash(signed_value),
                    },
                )
                if signature_valid
                else Check(FAIL, ("foreign_signature_invalid",))
            )
        except (CanonicalJSONError, JSONPointerError, TypeError, ValueError):
            integrity = Check(FAIL, ("foreign_proof_invalid",))

        trusted_key = trusted_keys.get(key_id)
        if integrity.status != PASS:
            provenance = Check(UNAVAILABLE, ("source_integrity_failed",))
        elif trusted_key is None:
            provenance = Check(
                UNAVAILABLE,
                ("source_key_untrusted",),
                {"key_id": key_id, "signature_self_consistent": True},
            )
        elif trusted_key != public_key:
            provenance = Check(FAIL, ("source_key_mismatch",), {"key_id": key_id})
        else:
            provenance = Check(PASS, (), {"key_id": key_id})

        try:
            payload = self._map_payload(document)
            normalization = Check(
                PASS,
                (),
                {
                    "profile_id": self.profile.profile_id,
                    "profile_sha256": self.profile.sha256,
                },
            )
        except (JSONPointerError, TypeError, ValueError) as exc:
            payload = {}
            normalization = Check(FAIL, (f"adapter_normalization_failed:{exc}",))

        return IntakeResult(
            source_format=self.source_format,
            source_sha256=source_sha,
            payload=payload,
            integrity=integrity,
            provenance=provenance,
            normalization=normalization,
        )
