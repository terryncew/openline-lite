"""Strict RFC 6901-style JSON Pointer resolution for already-parsed values."""

from __future__ import annotations

from typing import Any


class JSONPointerError(ValueError):
    """A pointer was malformed or did not resolve."""


def _decode(token: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            output.append(char)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise JSONPointerError("json_pointer_escape_invalid")
        output.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(output)


def resolve(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise JSONPointerError("json_pointer_invalid")
    current = value
    for encoded in pointer[1:].split("/"):
        token = _decode(encoded)
        if isinstance(current, dict):
            if token not in current:
                raise JSONPointerError("json_pointer_missing")
            current = current[token]
        elif isinstance(current, list):
            if not token or any(char < "0" or char > "9" for char in token):
                raise JSONPointerError("json_pointer_index_invalid")
            if len(token) > 1 and token.startswith("0"):
                raise JSONPointerError("json_pointer_index_invalid")
            if len(token) > len(str(max(0, len(current) - 1))):
                raise JSONPointerError("json_pointer_missing")
            index = int(token)
            if index >= len(current):
                raise JSONPointerError("json_pointer_missing")
            current = current[index]
        else:
            raise JSONPointerError("json_pointer_not_container")
    return current
