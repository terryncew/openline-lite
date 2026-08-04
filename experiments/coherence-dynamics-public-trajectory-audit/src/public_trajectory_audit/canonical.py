from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


def json_safe(value: Any) -> Any:
    """Convert non-finite numeric diagnostics to explicit JSON nulls."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(json_safe(value), sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
    path.write_text(payload, encoding="utf-8")
    return sha256_file(path)
