from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from common import BENCHMARK_REVISION, EXPERIMENT_ID, canonical_json_bytes

KEY_DERIVATION_SECRET_ENV = "OLP_003_KEY_DERIVATION_SECRET"
KEY_DERIVATION_SCHEME = "HKDF-SHA256-32-V1"
_SECRET_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def validate_secret_hex(value: str) -> bytes:
    if not isinstance(value, str) or not _SECRET_RE.fullmatch(value):
        raise ValueError("key derivation secret must be exactly 64 hexadecimal characters")
    raw = bytes.fromhex(value)
    if len(raw) != 32 or raw == b"\x00" * 32:
        raise ValueError("key derivation secret must encode a nonzero 256-bit value")
    return raw


def pop_secret_hex_from_env(env_name: str = KEY_DERIVATION_SECRET_ENV) -> str:
    if env_name != KEY_DERIVATION_SECRET_ENV:
        raise ValueError("unexpected key derivation secret environment variable name")
    value = os.environ.pop(env_name, None)
    if value is None:
        raise ValueError(f"required GitHub Actions secret is unavailable: {env_name}")
    validate_secret_hex(value)
    return value


def validate_run_context(run_context: str) -> str:
    if not isinstance(run_context, str) or not (16 <= len(run_context) <= 512):
        raise ValueError("key derivation run context length invalid")
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in run_context):
        raise ValueError("key derivation run context must be printable ASCII without whitespace")
    return run_context


def new_descriptor(run_context: str) -> dict:
    run_context = validate_run_context(run_context)
    salt = secrets.token_bytes(32)
    return {
        "scheme": KEY_DERIVATION_SCHEME,
        "secret_identifier": KEY_DERIVATION_SECRET_ENV,
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "run_context": run_context,
        "run_context_sha256": hashlib.sha256(run_context.encode("ascii")).hexdigest(),
        "derived_key_persisted": False,
        "plaintext_key_artifact_created": False,
        "key_derivation_secret_exported": False,
    }


def validate_descriptor(descriptor: dict, *, expected_run_context: str) -> bytes:
    expected_run_context = validate_run_context(expected_run_context)
    if not isinstance(descriptor, dict):
        raise ValueError("key derivation descriptor missing")
    exact = {
        "scheme": KEY_DERIVATION_SCHEME,
        "secret_identifier": KEY_DERIVATION_SECRET_ENV,
        "run_context": expected_run_context,
        "run_context_sha256": hashlib.sha256(expected_run_context.encode("ascii")).hexdigest(),
        "derived_key_persisted": False,
        "plaintext_key_artifact_created": False,
        "key_derivation_secret_exported": False,
    }
    for key, expected in exact.items():
        if descriptor.get(key) != expected:
            raise ValueError(f"key derivation descriptor mismatch: {key}")
    try:
        salt = base64.b64decode(descriptor["salt_b64"], validate=True)
    except Exception as exc:
        raise ValueError("key derivation salt is invalid base64") from exc
    if len(salt) != 32:
        raise ValueError("key derivation salt must be 256 bits")
    return salt


def _info(run_context: str) -> bytes:
    return canonical_json_bytes({
        "scheme": KEY_DERIVATION_SCHEME,
        "experiment_id": EXPERIMENT_ID,
        "benchmark_revision": BENCHMARK_REVISION,
        "run_context": validate_run_context(run_context),
    })


def derive_key(secret_hex: str, descriptor: dict, *, expected_run_context: str) -> bytes:
    ikm = validate_secret_hex(secret_hex)
    salt = validate_descriptor(descriptor, expected_run_context=expected_run_context)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=_info(expected_run_context),
    ).derive(ikm)
