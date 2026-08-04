"""Strict normalized coding-trace adapter for Dynamic Sentience Maps.

The public contract is provider-neutral. Source-specific adapters validate one
pinned producer schema and emit the same canonical observable stream. The
first supported source is Mindwalk trace v1.

This module deliberately does *not* infer hypotheses, evidence relations,
confidence, or disconfirmation checks from filenames or free text. Those
fields are required by DSM's existing kappa, Phi-star, and VKD definitions but
are absent from Mindwalk trace v1, so their status is UNDECIDABLE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

CANONICAL_STREAM_SCHEMA = "dsm.coding-trace.observable-stream.v1"
MEASUREMENT_SCHEMA = "dsm.coding-trace.measurement.v1"
FREEZE_READINESS_SCHEMA = "dsm.coding-trace.freeze-readiness.v1"
SUPPORTED_SOURCE_FORMATS = ("mindwalk.trace.v1",)

MINDWALK_TRACE_VERSION = 1
MINDWALK_UPSTREAM_COMMIT = "e208b6b8504138843f671e031f28129b66003a67"
MINDWALK_SCHEMA_BLOB_SHA = "6faf7f439cfd02ed0a69bc1bbfa570e7793a784e"
MINDWALK_SCHEMA_FILE_SHA256 = "2d64c75b4cfe512ddd7e2d89be7c3ac62a39a1e8645269b84304123041fbb358"

DSM_METRIC_REQUIREMENTS = {
    "kappa_micros": (
        "leading_hypothesis_confidence_history",
    ),
    "phi_star_micros": (
        "active_hypothesis_set",
        "disconfirmation_checks",
        "evidence_to_hypothesis_relations",
    ),
    "vkd_micros": (
        "active_hypothesis_set",
        "leading_hypothesis_confidence",
        "disconfirmation_checks",
        "evidence_to_hypothesis_relations",
        "action_pressure",
        "fixed_tool_call_budget",
    ),
}

_ACTIONS = ("search", "read", "edit", "exec", "verify", "other")
_TOUCHES = ("hit", "read", "edit")
_MARK_TYPES = ("compaction", "user-message", "subagent")
_OUTSIDE_SCOPES = ("home", "tmp", "other")
_OBSERVABILITY = ("exact", "estimated", "unavailable")


class CodingTraceError(ValueError):
    """Raised when a source trace violates its pinned adapter contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def hash_json(value: Any) -> str:
    return _sha256(canonical_json(value))


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise CodingTraceError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_nonfinite(value: str) -> None:
    raise CodingTraceError(f"non-finite JSON number: {value}")


def strict_json_loads(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodingTraceError(f"invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CodingTraceError("trace root must be an object")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    label: str,
) -> None:
    actual = set(value)
    missing = sorted(required - actual)
    unknown = sorted(actual - required - optional)
    if missing:
        raise CodingTraceError(f"{label} missing required fields: {missing}")
    if unknown:
        raise CodingTraceError(f"{label} contains unknown fields: {unknown}")


def _integer(value: Any, *, minimum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CodingTraceError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: Any, *, minimum: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CodingTraceError(f"{label} must be a finite number")
    output = float(value)
    if not math.isfinite(output) or output < minimum:
        raise CodingTraceError(f"{label} must be a finite number >= {minimum}")
    return output


def _string(value: Any, *, label: str, allow_empty: bool = True) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        qualifier = "non-empty " if not allow_empty else ""
        raise CodingTraceError(f"{label} must be a {qualifier}string")
    return value


def _boolean(value: Any, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise CodingTraceError(f"{label} must be boolean")
    return value


def _validate_action_counts(value: Any, *, label: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise CodingTraceError(f"{label} must be an object")
    _exact_keys(value, required=set(_ACTIONS), label=label)
    return {
        action: _integer(value[action], minimum=0, label=f"{label}.{action}")
        for action in _ACTIONS
    }


def validate_mindwalk_trace(document: Mapping[str, Any]) -> None:
    """Validate the pinned Mindwalk trace v1 shape and recomputable invariants."""

    _exact_keys(
        document,
        required={"version", "session", "events", "marks", "stats"},
        label="trace",
    )
    if _integer(document["version"], minimum=0, label="trace.version") != MINDWALK_TRACE_VERSION:
        raise CodingTraceError("unsupported Mindwalk trace version")

    session = document["session"]
    if not isinstance(session, Mapping):
        raise CodingTraceError("trace.session must be an object")
    _exact_keys(
        session,
        required={"id", "harness", "eventCount"},
        optional={"model", "title", "cwd", "commit", "startedAt", "endedAt", "path"},
        label="trace.session",
    )
    _string(session["id"], label="trace.session.id", allow_empty=False)
    _string(session["harness"], label="trace.session.harness", allow_empty=False)
    for name in ("model", "title", "cwd", "commit", "startedAt", "endedAt", "path"):
        if name in session:
            _string(session[name], label=f"trace.session.{name}")
    event_count = _integer(session["eventCount"], minimum=0, label="trace.session.eventCount")

    events = document["events"]
    if not isinstance(events, list):
        raise CodingTraceError("trace.events must be an array")
    if event_count != len(events):
        raise CodingTraceError("trace.session.eventCount does not match trace.events")

    last_seq: int | None = None
    action_counts = Counter({action: 0 for action in _ACTIONS})
    error_counts = Counter({action: 0 for action in _ACTIONS})
    result_bytes = 0
    for index, event in enumerate(events):
        label = f"trace.events[{index}]"
        if not isinstance(event, Mapping):
            raise CodingTraceError(f"{label} must be an object")
        _exact_keys(
            event,
            required={"seq", "tool", "action", "targets", "resultBytes", "isError", "summary"},
            optional={"ts", "outside"},
            label=label,
        )
        seq = _integer(event["seq"], minimum=0, label=f"{label}.seq")
        if last_seq is not None and seq <= last_seq:
            raise CodingTraceError("trace event seq values must be strictly increasing")
        last_seq = seq
        _string(event["tool"], label=f"{label}.tool")
        action = event["action"]
        if action not in _ACTIONS:
            raise CodingTraceError(f"{label}.action is unsupported")
        if "ts" in event:
            _string(event["ts"], label=f"{label}.ts")
        _string(event["summary"], label=f"{label}.summary")
        amount = _integer(event["resultBytes"], minimum=0, label=f"{label}.resultBytes")
        is_error = _boolean(event["isError"], label=f"{label}.isError")
        action_counts[action] += 1
        error_counts[action] += int(is_error)
        result_bytes += amount

        targets = event["targets"]
        if not isinstance(targets, list):
            raise CodingTraceError(f"{label}.targets must be an array")
        for target_index, target in enumerate(targets):
            target_label = f"{label}.targets[{target_index}]"
            if not isinstance(target, Mapping):
                raise CodingTraceError(f"{target_label} must be an object")
            _exact_keys(
                target,
                required={"path", "touch"},
                optional={"fileId", "lines", "weak"},
                label=target_label,
            )
            _string(target["path"], label=f"{target_label}.path", allow_empty=False)
            if target["touch"] not in _TOUCHES:
                raise CodingTraceError(f"{target_label}.touch is unsupported")
            if "fileId" in target:
                _integer(target["fileId"], minimum=0, label=f"{target_label}.fileId")
            if "weak" in target:
                _boolean(target["weak"], label=f"{target_label}.weak")
            if "lines" in target:
                lines = target["lines"]
                if not isinstance(lines, list):
                    raise CodingTraceError(f"{target_label}.lines must be an array")
                for range_index, line_range in enumerate(lines):
                    range_label = f"{target_label}.lines[{range_index}]"
                    if not isinstance(line_range, list) or len(line_range) != 2:
                        raise CodingTraceError(f"{range_label} must contain exactly two integers")
                    start = _integer(line_range[0], minimum=1, label=f"{range_label}[0]")
                    end = _integer(line_range[1], minimum=1, label=f"{range_label}[1]")
                    if end < start:
                        raise CodingTraceError(f"{range_label} end precedes start")

        outside = event.get("outside", [])
        if not isinstance(outside, list):
            raise CodingTraceError(f"{label}.outside must be an array")
        for outside_index, item in enumerate(outside):
            outside_label = f"{label}.outside[{outside_index}]"
            if not isinstance(item, Mapping):
                raise CodingTraceError(f"{outside_label} must be an object")
            _exact_keys(item, required={"scope", "path"}, label=outside_label)
            if item["scope"] not in _OUTSIDE_SCOPES:
                raise CodingTraceError(f"{outside_label}.scope is unsupported")
            _string(item["path"], label=f"{outside_label}.path", allow_empty=False)

    marks = document["marks"]
    if not isinstance(marks, list):
        raise CodingTraceError("trace.marks must be an array")
    event_seqs = {int(event["seq"]) for event in events}
    mark_counts = Counter({kind: 0 for kind in _MARK_TYPES})
    last_mark_seq: int | None = None
    for index, mark in enumerate(marks):
        label = f"trace.marks[{index}]"
        if not isinstance(mark, Mapping):
            raise CodingTraceError(f"{label} must be an object")
        _exact_keys(mark, required={"seq", "type"}, optional={"note"}, label=label)
        seq = _integer(mark["seq"], minimum=0, label=f"{label}.seq")
        if last_mark_seq is not None and seq < last_mark_seq:
            raise CodingTraceError("trace mark seq values must be nondecreasing")
        last_mark_seq = seq
        if events and seq > int(events[-1]["seq"]):
            raise CodingTraceError(f"{label}.seq is beyond the final event")
        if seq not in event_seqs:
            raise CodingTraceError(f"{label}.seq does not identify an event")
        kind = mark["type"]
        if kind not in _MARK_TYPES:
            raise CodingTraceError(f"{label}.type is unsupported")
        mark_counts[kind] += 1
        if "note" in mark:
            _string(mark["note"], label=f"{label}.note")

    stats = document["stats"]
    if not isinstance(stats, Mapping):
        raise CodingTraceError("trace.stats must be an object")
    stat_fields = {
        "filesInRepo",
        "fovea",
        "parafovea",
        "edited",
        "eventsBeforeFirstEdit",
        "regressionRate",
        "errorRate",
        "actions",
        "errors",
        "maxEditsPerFile",
        "churnFiles",
        "userTurns",
        "compactions",
        "subagents",
        "resultBytes",
        "editsAfterLastVerify",
        "observability",
    }
    _exact_keys(stats, required=stat_fields, label="trace.stats")
    for name in stat_fields - {"regressionRate", "errorRate", "actions", "errors", "observability"}:
        _integer(stats[name], minimum=0, label=f"trace.stats.{name}")
    _number(stats["regressionRate"], minimum=0, label="trace.stats.regressionRate")
    _number(stats["errorRate"], minimum=0, label="trace.stats.errorRate")
    source_actions = _validate_action_counts(stats["actions"], label="trace.stats.actions")
    source_errors = _validate_action_counts(stats["errors"], label="trace.stats.errors")
    observability = stats["observability"]
    if not isinstance(observability, Mapping):
        raise CodingTraceError("trace.stats.observability must be an object")
    _exact_keys(observability, required={"reads", "errors"}, label="trace.stats.observability")
    for name in ("reads", "errors"):
        if observability[name] not in _OBSERVABILITY:
            raise CodingTraceError(f"trace.stats.observability.{name} is unsupported")

    if source_actions != dict(action_counts):
        raise CodingTraceError("trace.stats.actions does not match trace.events")
    if source_errors != dict(error_counts):
        raise CodingTraceError("trace.stats.errors does not match trace.events")
    if int(stats["resultBytes"]) != result_bytes:
        raise CodingTraceError("trace.stats.resultBytes does not match trace.events")
    if int(stats["userTurns"]) != mark_counts["user-message"]:
        raise CodingTraceError("trace.stats.userTurns does not match trace.marks")
    if int(stats["compactions"]) != mark_counts["compaction"]:
        raise CodingTraceError("trace.stats.compactions does not match trace.marks")
    if int(stats["subagents"]) != mark_counts["subagent"]:
        raise CodingTraceError("trace.stats.subagents does not match trace.marks")


def _canonical_target(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": target["path"],
        "touch": target["touch"],
        "file_id": target.get("fileId"),
        "lines": target.get("lines", []),
        "weak": bool(target.get("weak", False)),
    }


def _metric_availability() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "status": "UNDECIDABLE",
            "missing_observables": list(requirements),
            "reason": (
                "The pinned source trace does not expose the semantic state required "
                "by DSM's existing metric definition."
            ),
        }
        for name, requirements in DSM_METRIC_REQUIREMENTS.items()
    }


def adapt_mindwalk_trace(path: str | Path) -> dict[str, Any]:
    """Adapt one strict Mindwalk trace v1 file into the provider-neutral stream."""

    source = Path(path)
    raw = source.read_bytes()
    document = strict_json_loads(raw)
    validate_mindwalk_trace(document)

    marks_by_seq: dict[int, list[str]] = defaultdict(list)
    for mark in document["marks"]:
        marks_by_seq[int(mark["seq"])].append(str(mark["type"]))

    events: list[dict[str, Any]] = []
    for step, event in enumerate(document["events"], start=1):
        events.append({
            "step": step,
            "source_seq": int(event["seq"]),
            "timestamp": event.get("ts"),
            "tool": event["tool"],
            "action": event["action"],
            "targets": [_canonical_target(target) for target in event["targets"]],
            "outside_targets": [
                {"scope": item["scope"]}
                for item in event.get("outside", [])
            ],
            "result_bytes": int(event["resultBytes"]),
            "is_error": bool(event["isError"]),
            "marks": sorted(marks_by_seq.get(int(event["seq"]), [])),
        })

    body = {
        "schema": CANONICAL_STREAM_SCHEMA,
        "repository_context": {
            "files_in_repo": int(document["stats"]["filesInRepo"]),
            "read_observability": document["stats"]["observability"]["reads"],
            "error_observability": document["stats"]["observability"]["errors"],
        },
        "events": events,
        "metric_availability": _metric_availability(),
        "input_boundary": {
            "free_text_used": False,
            "excluded_source_fields": [
                "session.*",
                "events[].summary",
                "marks[].note",
                "stats.* except filesInRepo and observability",
            ],
            "forbidden_outcome_inputs": [
                "final_tests",
                "human_verdict",
                "evaluation_report",
                "completion_status",
                "failure_label",
            ],
            "outcomes_loaded": False,
        },
    }
    return {**body, "payload_sha256": hash_json(body)}


def adapt_trace(path: str | Path, *, source_format: str) -> dict[str, Any]:
    """Adapt one supported source format into the canonical observable stream."""

    if source_format == "mindwalk.trace.v1":
        return adapt_mindwalk_trace(path)
    raise CodingTraceError(
        f"unsupported source format {source_format!r}; expected one of {SUPPORTED_SOURCE_FORMATS}"
    )


def conventional_prefix_baselines(stream: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Compute conventional, outcome-blind prefix signals from canonical events.

    These values are comparison baselines. They are not Coherence Dynamics
    metrics and must not be relabelled as kappa, Phi-star, or VKD.
    """

    if stream.get("schema") != CANONICAL_STREAM_SCHEMA:
        raise CodingTraceError("unsupported canonical stream schema")
    stream_body = dict(stream)
    claimed_digest = stream_body.pop("payload_sha256", None)
    if claimed_digest != hash_json(stream_body):
        raise CodingTraceError("canonical stream hash mismatch")
    events = stream.get("events")
    if not isinstance(events, list):
        raise CodingTraceError("canonical stream events must be an array")
    files_in_repo = int(stream.get("repository_context", {}).get("files_in_repo", 0))

    touched: set[str] = set()
    total_touches = 0
    errors = 0
    consecutive_errors = 0
    result_bytes = 0
    edits_since_verify = 0
    successful_verifies = 0
    compactions = 0
    rows: list[dict[str, Any]] = []

    for index, event in enumerate(events, start=1):
        if int(event.get("step", -1)) != index:
            raise CodingTraceError("canonical event steps must be contiguous starting at 1")
        targets = event.get("targets", [])
        if not isinstance(targets, list):
            raise CodingTraceError("canonical event targets must be an array")
        for target in targets:
            total_touches += 1
            touched.add(str(target["path"]))
        is_error = bool(event["is_error"])
        errors += int(is_error)
        consecutive_errors = consecutive_errors + 1 if is_error else 0
        result_bytes += int(event["result_bytes"])
        if event["action"] == "verify" and not is_error:
            successful_verifies += 1
            edits_since_verify = 0
        elif event["action"] == "edit":
            edits_since_verify += 1
        compactions += sum(mark == "compaction" for mark in event.get("marks", []))

        revisit_count = total_touches - len(touched)
        rows.append({
            "step": index,
            "source_seq": int(event["source_seq"]),
            "signals": {
                "error_rate_micros": errors * 1_000_000 // index,
                "consecutive_error_events": consecutive_errors,
                "revisit_rate_micros": revisit_count * 1_000_000 // max(1, total_touches),
                "edits_since_latest_successful_verify": edits_since_verify,
                "exploration_breadth_micros": (
                    len(touched) * 1_000_000 // files_in_repo if files_in_repo else None
                ),
                "result_bytes_per_event": result_bytes // index,
                "compaction_rate_micros": compactions * 1_000_000 // index,
                "successful_verify_count": successful_verifies,
            },
        })
    return rows


def measure_trace(path: str | Path, *, source_format: str) -> dict[str, Any]:
    """Produce a canonical stream, conventional baselines, and DSM status."""

    stream = adapt_trace(path, source_format=source_format)
    source_raw = Path(path).read_bytes()
    source_document = strict_json_loads(source_raw)
    session = source_document["session"]
    body = {
        "schema": MEASUREMENT_SCHEMA,
        "source_format": source_format,
        "source_evidence": {
            "format": "mindwalk.trace.v1",
            "adapter": "mindwalk-v1-to-dsm-observables-v1",
            "source_version": MINDWALK_TRACE_VERSION,
            "upstream_commit": MINDWALK_UPSTREAM_COMMIT,
            "upstream_schema_blob_sha": MINDWALK_SCHEMA_BLOB_SHA,
            "upstream_schema_file_sha256": MINDWALK_SCHEMA_FILE_SHA256,
            "source_file_sha256": _sha256(source_raw),
            "source_canonical_sha256": hash_json(source_document),
            "session": {
                "id": session["id"],
                "harness": session["harness"],
                "model": session.get("model"),
                "repository_commit": session.get("commit"),
                "started_at": session.get("startedAt"),
                "ended_at": session.get("endedAt"),
                "event_count": len(source_document["events"]),
            },
            "included_in_metric_input": False,
        },
        "observable_stream": stream,
        "conventional_prefix_baselines": conventional_prefix_baselines(stream),
        "dsm_warning": {
            "status": "UNDECIDABLE",
            "first_warning_step": None,
            "reason": (
                "No existing DSM metric is computable from the pinned source trace, "
                "so no DSM threshold may be calibrated or applied."
            ),
        },
        "outcomes_loaded": False,
        "claim_boundary": (
            "This artifact proves strict trace normalization and conventional structural "
            "baseline extraction. It does not establish an early-warning advantage."
        ),
    }
    return {**body, "measurement_sha256": hash_json(body)}


def assess_freeze_readiness(measurements: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fail closed before any calibration or held-out outcome file is opened."""

    if not measurements:
        raise CodingTraceError("freeze readiness requires calibration measurements")
    hashes: list[str] = []
    for index, measurement in enumerate(measurements):
        if measurement.get("schema") != MEASUREMENT_SCHEMA:
            raise CodingTraceError(f"measurements[{index}] has unsupported schema")
        measurement_body = dict(measurement)
        claimed_digest = measurement_body.pop("measurement_sha256", None)
        if claimed_digest != hash_json(measurement_body):
            raise CodingTraceError(f"measurements[{index}] hash mismatch")
        warning = measurement.get("dsm_warning", {})
        status = str(warning.get("status"))
        if status != "UNDECIDABLE":
            raise CodingTraceError(
                f"measurements[{index}] has unsupported DSM availability status"
            )
        if warning.get("first_warning_step") is not None:
            raise CodingTraceError(
                f"measurements[{index}] contains an unsupported warning"
            )
        if measurement.get("outcomes_loaded") is not False:
            raise CodingTraceError(f"measurements[{index}] crossed the outcome boundary")
        if (
            measurement.get("source_evidence", {}).get("included_in_metric_input")
            is not False
        ):
            raise CodingTraceError(
                f"measurements[{index}] exposes provenance to the metric input"
            )
        availability = (
            measurement.get("observable_stream", {}).get("metric_availability", {})
        )
        if set(availability) != set(DSM_METRIC_REQUIREMENTS) or any(
            availability[name].get("status") != "UNDECIDABLE"
            for name in DSM_METRIC_REQUIREMENTS
        ):
            raise CodingTraceError(
                f"measurements[{index}] has unsupported metric availability"
            )
        digest = str(claimed_digest)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise CodingTraceError(f"measurements[{index}] has invalid measurement hash")
        hashes.append(digest)
    return {
        "schema": FREEZE_READINESS_SCHEMA,
        "status": "UNDECIDABLE",
        "profile_created": False,
        "outcomes_loaded": False,
        "calibration_measurement_hashes": hashes,
        "reason": "The supported measurements lack the observables required by the existing DSM metrics.",
        "next_required_evidence": "Use a trace source that exposes structured hypotheses, confidence, evidence relations, and discriminating checks.",
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Adapt a normalized coding trace into DSM observables.")
    parser.add_argument("trace", help="Path to the source trace JSON")
    parser.add_argument(
        "--source-format",
        default="mindwalk.trace.v1",
        choices=SUPPORTED_SOURCE_FORMATS,
    )
    parser.add_argument("-o", "--output", help="Write the deterministic measurement JSON")
    args = parser.parse_args()

    result = measure_trace(args.trace, source_format=args.source_format)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
