from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from public_trajectory_audit.nebius import BlindRecord

SCORABLE_SOURCES = {"swe-smith-claude-3-7-sonnet"}
OVERLAP_SOURCES = {"nebius-swe-rebench-openhands"}
UNSCORABLE_SOURCES = {"kwai-klear-swe-smith-mini"}
KNOWN_SOURCES = SCORABLE_SOURCES | OVERLAP_SOURCES | UNSCORABLE_SOURCES
EXTERNAL_COLUMNS = (
    "session_id",
    "source_dataset",
    "source_id",
    "recorded_model",
    "messages_json",
    "ground_truth_meta_json",
)


class ExternalSchemaError(ValueError):
    pass


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            else:
                parts.append(
                    json.dumps(
                        item,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                        default=str,
                    )
                )
        return "\n".join(parts)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def _tool_calls(value: Any) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    calls = value if isinstance(value, list) else [value]
    lines = []
    for call in calls:
        if not isinstance(call, Mapping):
            lines.append(_text(call))
            continue
        fn = call.get("function") if isinstance(call.get("function"), Mapping) else call
        name = fn.get("name") or call.get("name") or "tool"
        args = fn.get("arguments") or call.get("arguments") or ""
        lines.append(f"{name} {_text(args)}".strip())
    return "\n".join(lines)


def normalize_messages(messages_json: Any) -> tuple[dict[str, str], ...]:
    messages = json.loads(messages_json) if isinstance(messages_json, str) else messages_json
    if not isinstance(messages, list):
        raise ExternalSchemaError("messages_json must decode to list")
    out = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise ExternalSchemaError(f"message {index} must be object")
        role = message.get("role")
        if role == "assistant":
            mapped = "ai"
        elif role in {"tool", "function"}:
            mapped = "user"
        elif role in {"system", "user", "ai"}:
            mapped = role
        else:
            raise ExternalSchemaError(f"unsupported role: {role!r}")
        text = _text(message.get("content"))
        calls = (
            _tool_calls(message.get("tool_calls_json", message.get("tool_calls")))
            if mapped == "ai"
            else ""
        )
        if calls:
            text = (text + "\n" + calls).strip()
        out.append({"role": mapped, "text": text})
    return tuple(out)


def repository_from_external(instance_id: str) -> str:
    first = instance_id.split(".", 1)[0]
    if "__" in first:
        return first
    match = re.match(r"^(?P<repo>.+)-\d+$", instance_id)
    return match.group("repo") if match else first


def parse_meta(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ExternalSchemaError("ground_truth_meta_json must decode to object")
    return value


def normalized_model(value: Any) -> str:
    # recorded_model is provenance metadata, not part of trajectory identity.
    # The external corpus explicitly permits an unknown model.
    if value is None or value == "":
        return "unknown"
    if not isinstance(value, str):
        raise ExternalSchemaError("recorded_model must be string, null, or empty")
    return value


def source_admissibility(row: Mapping[str, Any]) -> str:
    source = row.get("source_dataset")
    if source not in KNOWN_SOURCES:
        raise ExternalSchemaError(f"unfrozen source cohort: {source!r}")
    if source in OVERLAP_SOURCES:
        return "EXCLUDED_SOURCE_OVERLAP"
    if source in UNSCORABLE_SOURCES:
        return "EXCLUDED_NO_OUTCOME_LABEL"
    return "INCLUDED_LABEL_COMPLETE"


def record_and_label(row: Mapping[str, Any]) -> tuple[BlindRecord, dict[str, Any]]:
    unknown = set(row) - set(EXTERNAL_COLUMNS)
    if unknown:
        raise ExternalSchemaError(f"unexpected source columns: {sorted(unknown)}")
    if source_admissibility(row) != "INCLUDED_LABEL_COMPLETE":
        raise ExternalSchemaError("non-scorable row reached labeled extractor")

    source = row.get("source_dataset")
    session_id = row.get("session_id")
    source_id = row.get("source_id")
    if not all(isinstance(value, str) and value for value in (session_id, source_id)):
        raise ExternalSchemaError("session_id and source_id must be non-empty strings")
    model = normalized_model(row.get("recorded_model"))

    meta = parse_meta(row.get("ground_truth_meta_json"))
    resolved = meta.get("resolved")
    if not isinstance(resolved, (bool, int)):
        raise ExternalSchemaError("included cohort resolved label must be bool")
    instance_id = (
        meta.get("instance_id")
        if isinstance(meta.get("instance_id"), str) and meta.get("instance_id")
        else source_id
    )
    trajectory = normalize_messages(row.get("messages_json"))
    identity = hashlib.sha256(
        json.dumps(
            {"session_id": session_id, "messages": trajectory},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()
    record = BlindRecord(
        identity,
        instance_id,
        model,
        repository_from_external(instance_id),
        trajectory,
    )
    label = {
        "trajectory_id": identity,
        "target": int(bool(resolved)),
        "source_dataset": source,
        "source_id": source_id,
        "session_id": session_id,
        "instance_id": instance_id,
        "repository": record.repository,
        "model_name": model,
        "task_group": instance_id,
    }
    return record, label


def iter_external_rows(path: Path, batch_size: int = 256) -> Iterable[dict[str, Any]]:
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                yield {key: row[key] for key in EXTERNAL_COLUMNS}
        return
    if path.suffix != ".parquet":
        raise ExternalSchemaError("external input must be parquet or jsonl")
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(path)
    missing = [column for column in EXTERNAL_COLUMNS if column not in parquet.schema_arrow.names]
    if missing:
        raise ExternalSchemaError(f"missing external columns: {missing}")
    for batch in parquet.iter_batches(
        columns=list(EXTERNAL_COLUMNS),
        batch_size=batch_size,
        use_threads=True,
    ):
        yield from batch.to_pylist()
