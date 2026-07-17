"""Ed25519 helpers with raw hexadecimal keys."""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _hex_bytes(value: str, expected: int, label: str) -> bytes:
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{label}_not_hex") from exc
    if len(raw) != expected:
        raise ValueError(f"{label}_wrong_length")
    return raw


def generate_private_key_hex() -> str:
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()


def public_key_hex(private_key: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(
        _hex_bytes(private_key, 32, "private_key")
    )
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        .hex()
    )


def sign(data: bytes, private_key: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(
        _hex_bytes(private_key, 32, "private_key")
    )
    return key.sign(data).hex()


def verify(data: bytes, signature: str, public_key: str) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(
            _hex_bytes(public_key, 32, "public_key")
        )
        key.verify(_hex_bytes(signature, 64, "signature"), data)
        return True
    except (InvalidSignature, ValueError):
        return False
