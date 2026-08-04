from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

FORBIDDEN_EXTRACTOR_FIELDS = frozenset({"target", "exit_status", "generated_patch", "eval_logs", "reward", "pass", "eval_details"})
ALLOWED_EXTRACTOR_FIELDS = frozenset({"instance_id", "model_name", "trajectory"})
_ISSUE_SUFFIX = re.compile(r"^(?P<repository>.+)-(?P<issue_number>\d+)$")


class LeakageError(ValueError):
    pass


@dataclass(frozen=True)
class BlindRecord:
    trajectory_id: str
    instance_id: str
    model_name: str
    repository: str
    trajectory: tuple[dict[str, Any], ...]


def repository_from_instance(instance_id: str) -> str:
    """Return the full benchmark repository identity, not merely its owner."""
    match = _ISSUE_SUFFIX.match(instance_id)
    return match.group("repository") if match else instance_id


def _trajectory(value: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("trajectory must be a list or JSON-encoded list")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"trajectory[{index}] must be an object")
        role = item.get("role")
        if role not in {"system", "ai", "user", "assistant"}:
            raise ValueError(f"trajectory[{index}] has unsupported role")
        text = item.get("text", "")
        if text is None:
            text = item.get("system_prompt", "")
        if not isinstance(text, str):
            raise ValueError(f"trajectory[{index}].text must be a string")
        rows.append({"role": "ai" if role == "assistant" else role, "text": text})
    return tuple(rows)


def blind_record(row: Mapping[str, Any]) -> BlindRecord:
    present_forbidden = sorted(FORBIDDEN_EXTRACTOR_FIELDS.intersection(row))
    if present_forbidden:
        raise LeakageError(f"extractor received forbidden fields: {present_forbidden}")
    unknown = sorted(set(row) - ALLOWED_EXTRACTOR_FIELDS)
    if unknown:
        raise LeakageError(f"extractor received unknown fields: {unknown}")
    instance_id = row.get("instance_id")
    model_name = row.get("model_name")
    if not isinstance(instance_id, str) or not instance_id:
        raise ValueError("instance_id must be non-empty")
    if not isinstance(model_name, str) or not model_name:
        raise ValueError("model_name must be non-empty")
    trajectory = _trajectory(row.get("trajectory"))
    identity_payload = json.dumps(
        {"instance_id": instance_id, "model_name": model_name, "trajectory": trajectory},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    trajectory_id = hashlib.sha256(identity_payload).hexdigest()
    return BlindRecord(trajectory_id, instance_id, model_name, repository_from_instance(instance_id), trajectory)


def sanitize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {name: row[name] for name in ("instance_id", "model_name", "trajectory")}


def label_row(row: Mapping[str, Any]) -> dict[str, Any]:
    target = row.get("target")
    if not isinstance(target, (bool, int)):
        raise ValueError("target must be bool")
    blind = blind_record(sanitize_row(row))
    return {"trajectory_id": blind.trajectory_id, "instance_id": blind.instance_id, "target": int(bool(target))}


def iter_jsonl(path: str) -> Iterable[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number} must be an object")
            yield value
