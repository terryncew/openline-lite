from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCALE = 1_000_000
MAX_SAFE_INTEGER = (1 << 53) - 1
SESSION_SCHEMA = "openline.calibration-trial.session.v2"
OUTCOME_SCHEMA = "openline.calibration-trial.outcome.v2"
CONTINUATION_SCHEMA = "openline.calibration-trial.continuation.v1"
FREEZE_SCHEMA = "openline.calibration-trial.freeze.v2"
PREDICTION_SCHEMA = "openline.calibration-trial.prediction.v2"
ELIGIBILITY_SCHEMA = "openline.calibration-trial.eligibility.v2"
OUTCOME_UNLOCK_SCHEMA = "openline.calibration-trial.outcome-unlock.v1"
EVALUATION_SCHEMA = "openline.calibration-trial.evaluation.v2"
PREREG_SCHEMA = "openline.calibration-trial.preregistration.v2"
PROTOCOL_SCHEMA = "openline.calibration-trial.protocol.v2"
ALGORITHM_ID = "cole-portable-core-2.1-draft"
SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")


class TrialError(ValueError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def sha256_obj(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrialError(f"invalid JSON {path}: {exc}") from exc


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise TrialError(f"cannot read JSONL {path}: {exc}") from exc
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrialError(f"invalid JSONL {path}:{n}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise TrialError(f"JSONL row must be object: {path}:{n}")
        rows.append(row)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl_no_overwrite(
    path: Path,
    row: dict[str, Any],
    unique_key: tuple[str, Any] | None = None,
) -> None:
    if unique_key and path.exists():
        key, expected = unique_key
        for existing in load_jsonl(path):
            if existing.get(key) == expected:
                raise TrialError(f"refusing duplicate {key}={expected!r} in {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(canonical_json(row).decode("ascii") + "\n")


def parse_utc(text: str) -> datetime:
    if not isinstance(text, str) or not text.endswith("Z"):
        raise TrialError("timestamps must use UTC Z form")
    try:
        dt = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise TrialError(f"invalid timestamp {text!r}") from exc
    if dt.tzinfo is None:
        raise TrialError("timestamp lacks timezone")
    return dt.astimezone(timezone.utc)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _hash64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def validate_profile(profile: Any) -> None:
    fields = {
        "profile_id",
        "signal_schema_id",
        "calibration_status",
        "calibration_corpus_hash",
        "calibration_sample_count",
        "smoothing_window",
        "epsilon_window",
        "i_c_micros",
        "alpha_k_micros",
        "alpha_e_micros",
        "stability_delta_micros",
        "kappa_critical_micros",
        "phi_min_micros",
        "amber_kappa_micros",
        "amber_epsilon_micros",
        "amber_dhol_micros",
        "dhol_claim_weight_micros",
        "dhol_evidence_weight_micros",
        "dhol_relation_weight_micros",
    }
    if not isinstance(profile, dict) or set(profile) != fields:
        raise TrialError("receiver profile does not match COLE Portable Core 2.1 fields")
    if (
        not isinstance(profile["profile_id"], str)
        or not profile["profile_id"].isascii()
        or not profile["profile_id"]
    ):
        raise TrialError("profile_id must be non-empty ASCII")
    schema = profile["signal_schema_id"]
    if schema is not None and (not isinstance(schema, str) or not schema):
        raise TrialError("signal_schema_id must be null or non-empty")
    status = profile["calibration_status"]
    if status not in {
        "calibrated",
        "synthetic_conformance",
        "uncalibrated_reference",
    }:
        raise TrialError("invalid COLE calibration_status")
    corpus_hash = profile["calibration_corpus_hash"]
    if status == "calibrated":
        if not _hash64(corpus_hash) or profile["calibration_sample_count"] < 500:
            raise TrialError(
                "calibrated COLE profiles require corpus hash and >=500 samples"
            )
    elif corpus_hash is not None or profile["calibration_sample_count"] != 0:
        raise TrialError(
            "uncalibrated/synthetic COLE profiles cannot claim a calibration corpus"
        )
    ints = fields - {
        "profile_id",
        "signal_schema_id",
        "calibration_status",
        "calibration_corpus_hash",
    }
    for key in ints:
        value = profile[key]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or abs(value) > MAX_SAFE_INTEGER
        ):
            raise TrialError(f"profile {key} must be an interoperable integer")
        if value < 0:
            raise TrialError(f"profile {key} must be nonnegative")
    if (
        profile["smoothing_window"] < 1
        or profile["epsilon_window"] < 2
        or profile["i_c_micros"] < 1
    ):
        raise TrialError("invalid profile windows")
    weights = (
        "dhol_claim_weight_micros",
        "dhol_evidence_weight_micros",
        "dhol_relation_weight_micros",
    )
    if sum(profile[key] for key in weights) != SCALE:
        raise TrialError("delta_hol weights must sum to SCALE")


def protocol(path: Path) -> dict[str, Any]:
    p = load_json(path)  # JSON is valid YAML 1.2; stdlib-only on purpose.
    if not isinstance(p, dict) or p.get("schema") != PROTOCOL_SCHEMA:
        raise TrialError("unsupported protocol schema")
    measurement = p.get("measurement_contract")
    if not isinstance(measurement, dict):
        raise TrialError("measurement_contract missing")
    if measurement.get("algorithm_id") != ALGORITHM_ID:
        raise TrialError("unsupported measurement algorithm")
    validate_profile(measurement.get("receiver_profile"))
    if measurement.get("profile_authority") != "receiver":
        raise TrialError("measurement profile authority must be receiver")
    inference = p.get("primary_inference")
    if not isinstance(inference, dict):
        raise TrialError("primary_inference missing")
    alpha = inference.get("alpha_micros")
    confidence = inference.get("confidence_micros")
    if (
        not isinstance(alpha, int)
        or isinstance(alpha, bool)
        or not 0 < alpha < SCALE
        or not isinstance(confidence, int)
        or isinstance(confidence, bool)
        or not 0 < confidence < SCALE
    ):
        raise TrialError("invalid inference alpha/confidence")
    return p


def validate_graph(graph: Any) -> None:
    if not isinstance(graph, dict) or set(graph) != {"claims", "evidence", "relations"}:
        raise TrialError("graph must contain exactly claims/evidence/relations")
    if not all(isinstance(graph[k], list) for k in graph):
        raise TrialError("graph groups must be arrays")
    ids: dict[str, str] = {}
    claim_ids: list[str] = []
    evidence_ids: list[str] = []
    for claim in graph["claims"]:
        if not isinstance(claim, dict) or set(claim) != {
            "id",
            "content_hash",
            "material",
        }:
            raise TrialError("invalid claim shape")
        if (
            not isinstance(claim["id"], str)
            or not SAFE_ID.fullmatch(claim["id"])
            or claim["id"] in ids
        ):
            raise TrialError("invalid/duplicate claim id")
        if not _hash64(claim["content_hash"]) or not isinstance(
            claim["material"], bool
        ):
            raise TrialError("invalid claim")
        ids[claim["id"]] = "Claim"
        claim_ids.append(claim["id"])
    for evidence in graph["evidence"]:
        if not isinstance(evidence, dict) or set(evidence) != {
            "id",
            "content_hash",
            "observed",
        }:
            raise TrialError("invalid evidence shape")
        if (
            not isinstance(evidence["id"], str)
            or not SAFE_ID.fullmatch(evidence["id"])
            or evidence["id"] in ids
        ):
            raise TrialError("invalid/duplicate evidence id")
        if not _hash64(evidence["content_hash"]) or evidence["observed"] is not True:
            raise TrialError("invalid evidence")
        ids[evidence["id"]] = "Evidence"
        evidence_ids.append(evidence["id"])
    if claim_ids != sorted(claim_ids) or evidence_ids != sorted(evidence_ids):
        raise TrialError("graph nodes must be sorted by id")
    keys: list[tuple[str, str, str]] = []
    for rel in graph["relations"]:
        if not isinstance(rel, dict) or set(rel) != {
            "src",
            "dst",
            "relation_type",
        }:
            raise TrialError("invalid relation shape")
        src, dst, typ = rel["src"], rel["dst"], rel["relation_type"]
        if src not in ids or dst not in ids:
            raise TrialError("relation references missing node")
        if typ == "supports" and not (
            ids[src] == "Evidence" and ids[dst] == "Claim"
        ):
            raise TrialError("invalid supports relation")
        if typ == "contradicts" and not (
            ids[src] == "Claim" and ids[dst] == "Claim"
        ):
            raise TrialError("invalid contradicts relation")
        if typ == "depends_on" and ids[dst] != "Claim":
            raise TrialError("invalid depends_on relation")
        if typ not in {"supports", "contradicts", "depends_on"}:
            raise TrialError("unsupported relation type")
        keys.append((src, dst, typ))
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise TrialError("relations must be unique and sorted")


def validate_session(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "session_id",
        "handoff_at_utc",
        "transcript",
        "measurement_input",
    }:
        raise TrialError(
            "session has unexpected fields; outcome/post-handoff content is forbidden"
        )
    if (
        value["schema"] != SESSION_SCHEMA
        or not isinstance(value["session_id"], str)
        or not value["session_id"]
    ):
        raise TrialError("invalid session identity")
    parse_utc(value["handoff_at_utc"])
    transcript = value["transcript"]
    if not isinstance(transcript, list):
        raise TrialError("transcript must be array")
    for i, event in enumerate(transcript):
        if not isinstance(event, dict) or set(event) != {
            "index",
            "role",
            "text",
            "tool_name",
        }:
            raise TrialError("invalid transcript event")
        if (
            event["index"] != i
            or event["role"] not in {"user", "assistant", "tool"}
            or not isinstance(event["text"], str)
        ):
            raise TrialError("invalid transcript event fields")
        if event["role"] == "tool":
            if not isinstance(event["tool_name"], str) or not event["tool_name"]:
                raise TrialError("tool event requires tool_name")
        elif event["tool_name"] is not None:
            raise TrialError("non-tool event must have null tool_name")
    mi = value["measurement_input"]
    if not isinstance(mi, dict) or set(mi) != {
        "algorithm_id",
        "signal_points_micros",
        "previous_graph",
        "current_graph",
    }:
        raise TrialError(
            "measurement_input must contain observations only; producer profile/weights are forbidden"
        )
    if mi["algorithm_id"] != ALGORITHM_ID:
        raise TrialError("unsupported measurement algorithm")
    signal = mi["signal_points_micros"]
    if not isinstance(signal, list) or any(
        not isinstance(x, int)
        or isinstance(x, bool)
        or not 0 <= x <= SCALE
        for x in signal
    ):
        raise TrialError("signal points must be integer micros in [0,SCALE]")
    validate_graph(mi["previous_graph"])
    validate_graph(mi["current_graph"])
    return value


def validate_continuation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "session_id",
        "started_at_utc",
        "events",
        "ended",
    }:
        raise TrialError("invalid continuation shape")
    if (
        value["schema"] != CONTINUATION_SCHEMA
        or not isinstance(value["session_id"], str)
        or not value["session_id"]
    ):
        raise TrialError("invalid continuation identity")
    parse_utc(value["started_at_utc"])
    if not isinstance(value["ended"], bool) or not isinstance(value["events"], list):
        raise TrialError("invalid continuation fields")
    for i, event in enumerate(value["events"]):
        if not isinstance(event, dict) or set(event) != {
            "index",
            "role",
            "text",
            "tool_name",
        }:
            raise TrialError("invalid continuation event")
        if (
            event["index"] != i
            or event["role"] not in {"user", "assistant", "tool"}
            or not isinstance(event["text"], str)
        ):
            raise TrialError("invalid continuation event fields")
        if event["role"] == "tool":
            if not isinstance(event["tool_name"], str) or not event["tool_name"]:
                raise TrialError("continuation tool event requires tool_name")
        elif event["tool_name"] is not None:
            raise TrialError("continuation non-tool event must have null tool_name")
    return value


def validate_outcome(row: Any, p: dict[str, Any], *, expected_phase: str | None = None) -> dict[str, Any]:
    fields = {
        "schema",
        "phase",
        "session_id",
        "outcome",
        "kind",
        "correction_message_index",
        "continuation_sha256",
        "window_observed_assistant_turns",
        "continuation_ended",
        "labeled_at_utc",
        "outcome_unlock_sha256",
        "notes",
    }
    if (
        not isinstance(row, dict)
        or set(row) != fields
        or row.get("schema") != OUTCOME_SCHEMA
    ):
        raise TrialError("invalid outcome shape")
    if row["phase"] not in {"calibration", "prospective"}:
        raise TrialError("invalid outcome phase")
    if expected_phase is not None and row["phase"] != expected_phase:
        raise TrialError(f"outcome phase must be {expected_phase}")
    if row["phase"] == "calibration":
        if row["outcome_unlock_sha256"] is not None:
            raise TrialError("calibration outcome cannot bind a prospective unlock")
    elif not _hash64(row["outcome_unlock_sha256"]):
        raise TrialError("prospective outcome must bind an outcome unlock")
    if row["outcome"] not in {0, 1} or not isinstance(row["session_id"], str):
        raise TrialError("invalid outcome value")
    parse_utc(row["labeled_at_utc"])
    if not _hash64(row["continuation_sha256"]):
        raise TrialError("outcome continuation_sha256 must be lowercase SHA-256")
    n = row["window_observed_assistant_turns"]
    if (
        not isinstance(n, int)
        or isinstance(n, bool)
        or n < 0
        or n > p["outcome"]["window_assistant_turns"]
    ):
        raise TrialError("invalid observed outcome window")
    if not isinstance(row["continuation_ended"], bool) or not isinstance(
        row["notes"], str
    ):
        raise TrialError("invalid outcome metadata")
    if row["outcome"] == 1:
        if row["kind"] not in p["outcome"]["positive_kinds"]:
            raise TrialError("positive outcome kind is not preregistered")
        if (
            not isinstance(row["correction_message_index"], int)
            or isinstance(row["correction_message_index"], bool)
            or row["correction_message_index"] < 0
        ):
            raise TrialError("positive outcome requires correction message index")
    else:
        if row["kind"] is not None or row["correction_message_index"] is not None:
            raise TrialError("negative outcome cannot carry correction kind/index")
        if n < p["outcome"]["window_assistant_turns"] and not row["continuation_ended"]:
            raise TrialError("negative outcome requires complete window or ended continuation")
    return row


def validate_outcome_against_continuation(
    row: dict[str, Any], continuation: dict[str, Any], p: dict[str, Any]
) -> None:
    validate_continuation(continuation)
    if continuation["session_id"] != row["session_id"]:
        raise TrialError("outcome continuation session mismatch")
    if row["continuation_sha256"] != sha256_obj(continuation):
        raise TrialError("outcome continuation hash mismatch")
    if parse_utc(row["labeled_at_utc"]) < parse_utc(continuation["started_at_utc"]):
        raise TrialError("outcome label predates continuation start")
    window = p["outcome"]["window_assistant_turns"]
    events = continuation["events"]
    if row["continuation_ended"] is not continuation["ended"]:
        raise TrialError("outcome continuation-ended flag mismatch")
    if row["outcome"] == 1:
        idx = row["correction_message_index"]
        if idx >= len(events) or events[idx]["role"] != "user":
            raise TrialError("positive outcome must point to a human/user correction message")
        assistant_count = sum(1 for e in events[:idx] if e["role"] == "assistant")
        if assistant_count > window:
            raise TrialError("correction occurs outside preregistered window")
        if row["window_observed_assistant_turns"] != assistant_count:
            raise TrialError("positive outcome assistant-turn count mismatch")
    else:
        total_assistant = sum(1 for e in events if e["role"] == "assistant")
        observed = min(total_assistant, window)
        if total_assistant < window and not continuation["ended"]:
            raise TrialError("negative outcome requires full window or ended continuation")
        if row["window_observed_assistant_turns"] != observed:
            raise TrialError("negative outcome assistant-turn count mismatch")
