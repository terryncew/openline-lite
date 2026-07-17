"""Small deterministic JSON profile used by OpenLine Lite.

The profile deliberately rejects floats and duplicate keys.  Integers, strings,
booleans, nulls, arrays, and string-keyed objects are supported.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


class CanonicalJSONError(ValueError):
    """The value is outside the OpenLine Lite canonical JSON profile."""


MAX_CANONICAL_DEPTH = 128
MAX_CANONICAL_NODES = 100_000


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalJSONError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def loads(data: bytes | str) -> Any:
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        value = json.loads(text, object_pairs_hook=_pairs_without_duplicates)
    except RecursionError as exc:
        raise CanonicalJSONError("json_depth_limit_exceeded") from exc
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanonicalJSONError("invalid_json") from exc
    validate(value)
    return value


def validate(value: Any, *, path: str = "$") -> None:
    """Validate without recursive Python calls and with fixed complexity limits."""

    stack: list[tuple[Any, str, int]] = [(value, path, 0)]
    visited = 0

    while stack:
        current, current_path, depth = stack.pop()
        visited += 1
        if visited > MAX_CANONICAL_NODES:
            raise CanonicalJSONError("json_node_limit_exceeded")
        if depth > MAX_CANONICAL_DEPTH:
            raise CanonicalJSONError("json_depth_limit_exceeded")

        if current is None or isinstance(current, (bool, str)):
            continue
        if isinstance(current, int) and not isinstance(current, bool):
            continue
        if isinstance(current, float):
            raise CanonicalJSONError(f"float_unsupported:{current_path}")
        if isinstance(current, Mapping):
            if len(current) > MAX_CANONICAL_NODES - visited - len(stack):
                raise CanonicalJSONError("json_node_limit_exceeded")
            children: list[tuple[Any, str, int]] = []
            for key, child in current.items():
                if not isinstance(key, str):
                    raise CanonicalJSONError(f"non_string_key:{current_path}")
                children.append((child, f"{current_path}.{key}", depth + 1))
            stack.extend(reversed(children))
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, (bytes, bytearray, str)
        ):
            if len(current) > MAX_CANONICAL_NODES - visited - len(stack):
                raise CanonicalJSONError("json_node_limit_exceeded")
            stack.extend(
                (current[index], f"{current_path}[{index}]", depth + 1)
                for index in range(len(current) - 1, -1, -1)
            )
            continue
        raise CanonicalJSONError(
            f"unsupported_type:{current_path}:{type(current).__name__}"
        )


def dumps(value: Any) -> bytes:
    validate(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def pretty(value: Any) -> str:
    validate(value)
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_hash(value: Any) -> str:
    return sha256_hex(dumps(value))
