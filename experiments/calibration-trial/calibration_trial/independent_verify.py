"""Independent verifier for the Calibration Trial.

This module intentionally does not import candidate scorer modules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from math import isqrt
from pathlib import Path
from typing import Any

SCALE = 1_000_000
MAX_SAFE_INTEGER = (1 << 53) - 1
PROTOCOL_SCHEMA = "openline.calibration-trial.protocol.v2"
SESSION_SCHEMA = "openline.calibration-trial.session.v2"
OUTCOME_SCHEMA = "openline.calibration-trial.outcome.v2"
CONTINUATION_SCHEMA = "openline.calibration-trial.continuation.v1"
FREEZE_SCHEMA = "openline.calibration-trial.freeze.v2"
PREDICTION_SCHEMA = "openline.calibration-trial.prediction.v2"
ELIGIBILITY_SCHEMA = "openline.calibration-trial.eligibility.v2"
OUTCOME_UNLOCK_SCHEMA = "openline.calibration-trial.outcome-unlock.v1"
EVALUATION_SCHEMA = "openline.calibration-trial.evaluation.v2"
ALGORITHM_ID = "cole-portable-core-2.1-draft"


class VerificationError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def hash_obj(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise VerificationError(f"JSONL row must be object: {path}:{line_number}")
        rows.append(row)
    return rows


def utc(text: str) -> datetime:
    if not isinstance(text, str) or not text.endswith("Z"):
        raise VerificationError("timestamp must be UTC Z form")
    return datetime.fromisoformat(text[:-1] + "+00:00").astimezone(timezone.utc)


def hash64(value: Any) -> bool:
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
        raise VerificationError("bad receiver profile shape")
    if not isinstance(profile["profile_id"], str) or not profile["profile_id"]:
        raise VerificationError("bad profile id")
    status = profile["calibration_status"]
    if status not in {"calibrated", "synthetic_conformance", "uncalibrated_reference"}:
        raise VerificationError("bad profile calibration status")
    corpus_hash = profile["calibration_corpus_hash"]
    if status == "calibrated":
        if not hash64(corpus_hash) or profile["calibration_sample_count"] < 500:
            raise VerificationError("bad calibrated profile provenance")
    elif corpus_hash is not None or profile["calibration_sample_count"] != 0:
        raise VerificationError("bad uncalibrated profile provenance")
    for key in fields - {
        "profile_id",
        "signal_schema_id",
        "calibration_status",
        "calibration_corpus_hash",
    }:
        value = profile[key]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or abs(value) > MAX_SAFE_INTEGER
        ):
            raise VerificationError(f"bad profile integer: {key}")
    if (
        profile["smoothing_window"] < 1
        or profile["epsilon_window"] < 2
        or profile["i_c_micros"] < 1
    ):
        raise VerificationError("bad profile windows")
    if sum(
        profile[key]
        for key in (
            "dhol_claim_weight_micros",
            "dhol_evidence_weight_micros",
            "dhol_relation_weight_micros",
        )
    ) != SCALE:
        raise VerificationError("bad dhol weights")


def validate_protocol(p: Any) -> dict[str, Any]:
    if not isinstance(p, dict) or p.get("schema") != PROTOCOL_SCHEMA:
        raise VerificationError("bad protocol")
    measurement = p.get("measurement_contract")
    if not isinstance(measurement, dict) or measurement.get("algorithm_id") != ALGORITHM_ID:
        raise VerificationError("bad measurement contract")
    if measurement.get("profile_authority") != "receiver":
        raise VerificationError("measurement profile is not receiver-owned")
    validate_profile(measurement.get("receiver_profile"))
    inference = p.get("primary_inference")
    if not isinstance(inference, dict):
        raise VerificationError("missing inference contract")
    if not 0 < inference.get("alpha_micros", -1) < SCALE:
        raise VerificationError("bad inference alpha")
    if not 0 < inference.get("confidence_micros", -1) < SCALE:
        raise VerificationError("bad inference confidence")
    return p


def validate_graph(graph: Any) -> None:
    if not isinstance(graph, dict) or set(graph) != {"claims", "evidence", "relations"}:
        raise VerificationError("bad graph shape")
    if not all(isinstance(graph[key], list) for key in graph):
        raise VerificationError("bad graph arrays")
    ids: dict[str, str] = {}
    claim_ids = []
    evidence_ids = []
    for claim in graph["claims"]:
        if not isinstance(claim, dict) or set(claim) != {"id", "content_hash", "material"}:
            raise VerificationError("bad claim")
        if claim["id"] in ids or not hash64(claim["content_hash"]) or not isinstance(claim["material"], bool):
            raise VerificationError("bad claim fields")
        ids[claim["id"]] = "Claim"
        claim_ids.append(claim["id"])
    for evidence in graph["evidence"]:
        if not isinstance(evidence, dict) or set(evidence) != {"id", "content_hash", "observed"}:
            raise VerificationError("bad evidence")
        if evidence["id"] in ids or not hash64(evidence["content_hash"]) or evidence["observed"] is not True:
            raise VerificationError("bad evidence fields")
        ids[evidence["id"]] = "Evidence"
        evidence_ids.append(evidence["id"])
    if claim_ids != sorted(claim_ids) or evidence_ids != sorted(evidence_ids):
        raise VerificationError("unsorted graph nodes")
    keys = []
    for relation in graph["relations"]:
        if not isinstance(relation, dict) or set(relation) != {"src", "dst", "relation_type"}:
            raise VerificationError("bad relation")
        src, dst, typ = relation["src"], relation["dst"], relation["relation_type"]
        if src not in ids or dst not in ids:
            raise VerificationError("relation references missing node")
        if typ == "supports" and not (ids[src] == "Evidence" and ids[dst] == "Claim"):
            raise VerificationError("bad supports relation")
        if typ == "contradicts" and not (ids[src] == "Claim" and ids[dst] == "Claim"):
            raise VerificationError("bad contradicts relation")
        if typ == "depends_on" and ids[dst] != "Claim":
            raise VerificationError("bad depends_on relation")
        if typ not in {"supports", "contradicts", "depends_on"}:
            raise VerificationError("unsupported relation")
        keys.append((src, dst, typ))
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise VerificationError("bad relation ordering")


def validate_session(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "session_id",
        "handoff_at_utc",
        "transcript",
        "measurement_input",
    }:
        raise VerificationError("bad session shape")
    if value["schema"] != SESSION_SCHEMA or not isinstance(value["session_id"], str):
        raise VerificationError("bad session identity")
    utc(value["handoff_at_utc"])
    for index, event in enumerate(value["transcript"]):
        if not isinstance(event, dict) or set(event) != {"index", "role", "text", "tool_name"}:
            raise VerificationError("bad transcript event")
        if event["index"] != index or event["role"] not in {"user", "assistant", "tool"} or not isinstance(event["text"], str):
            raise VerificationError("bad transcript event fields")
        if event["role"] == "tool":
            if not isinstance(event["tool_name"], str) or not event["tool_name"]:
                raise VerificationError("tool event missing name")
        elif event["tool_name"] is not None:
            raise VerificationError("non-tool event has tool name")
    measurement = value["measurement_input"]
    if not isinstance(measurement, dict) or set(measurement) != {
        "algorithm_id",
        "signal_points_micros",
        "previous_graph",
        "current_graph",
    }:
        raise VerificationError("session contains non-observation measurement fields")
    if measurement["algorithm_id"] != ALGORITHM_ID:
        raise VerificationError("bad measurement algorithm")
    signal = measurement["signal_points_micros"]
    if not isinstance(signal, list) or any(
        not isinstance(point, int) or isinstance(point, bool) or not 0 <= point <= SCALE
        for point in signal
    ):
        raise VerificationError("bad signal")
    validate_graph(measurement["previous_graph"])
    validate_graph(measurement["current_graph"])
    return value


def validate_continuation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema", "session_id", "started_at_utc", "events", "ended"}:
        raise VerificationError("bad continuation")
    if value["schema"] != CONTINUATION_SCHEMA or not isinstance(value["session_id"], str):
        raise VerificationError("bad continuation identity")
    utc(value["started_at_utc"])
    if not isinstance(value["events"], list) or not isinstance(value["ended"], bool):
        raise VerificationError("bad continuation fields")
    for index, event in enumerate(value["events"]):
        if not isinstance(event, dict) or set(event) != {"index", "role", "text", "tool_name"}:
            raise VerificationError("bad continuation event")
        if event["index"] != index or event["role"] not in {"user", "assistant", "tool"} or not isinstance(event["text"], str):
            raise VerificationError("bad continuation event fields")
        if event["role"] == "tool":
            if not isinstance(event["tool_name"], str) or not event["tool_name"]:
                raise VerificationError("continuation tool event missing name")
        elif event["tool_name"] is not None:
            raise VerificationError("continuation non-tool has tool name")
    return value


def validate_outcome(row: Any, p: dict[str, Any], expected_phase: str) -> dict[str, Any]:
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
    if not isinstance(row, dict) or set(row) != fields or row.get("schema") != OUTCOME_SCHEMA:
        raise VerificationError("bad outcome shape")
    if row["phase"] != expected_phase:
        raise VerificationError("bad outcome phase")
    if expected_phase == "calibration":
        if row["outcome_unlock_sha256"] is not None:
            raise VerificationError("calibration outcome binds unlock")
    elif not hash64(row["outcome_unlock_sha256"]):
        raise VerificationError("prospective outcome missing unlock")
    if row["outcome"] not in {0, 1}:
        raise VerificationError("bad outcome value")
    utc(row["labeled_at_utc"])
    if not hash64(row["continuation_sha256"]):
        raise VerificationError("bad continuation hash")
    n = row["window_observed_assistant_turns"]
    if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= p["outcome"]["window_assistant_turns"]:
        raise VerificationError("bad outcome window")
    if row["outcome"] == 1:
        if row["kind"] not in p["outcome"]["positive_kinds"]:
            raise VerificationError("bad positive kind")
        if not isinstance(row["correction_message_index"], int) or isinstance(row["correction_message_index"], bool):
            raise VerificationError("bad correction index")
    elif row["kind"] is not None or row["correction_message_index"] is not None:
        raise VerificationError("negative outcome has correction metadata")
    return row


def validate_outcome_continuation(row: dict[str, Any], continuation: dict[str, Any], p: dict[str, Any]) -> None:
    validate_continuation(continuation)
    if continuation["session_id"] != row["session_id"] or hash_obj(continuation) != row["continuation_sha256"]:
        raise VerificationError("outcome/continuation mismatch")
    if utc(row["labeled_at_utc"]) < utc(continuation["started_at_utc"]):
        raise VerificationError("label predates continuation")
    window = p["outcome"]["window_assistant_turns"]
    events = continuation["events"]
    if row["continuation_ended"] is not continuation["ended"]:
        raise VerificationError("continuation ended mismatch")
    if row["outcome"] == 1:
        index = row["correction_message_index"]
        if index >= len(events) or events[index]["role"] != "user":
            raise VerificationError("positive label does not point to human event")
        observed = sum(1 for event in events[:index] if event["role"] == "assistant")
        if observed > window or observed != row["window_observed_assistant_turns"]:
            raise VerificationError("positive label window mismatch")
    else:
        total = sum(1 for event in events if event["role"] == "assistant")
        observed = min(total, window)
        if total < window and not continuation["ended"]:
            raise VerificationError("negative label lacks full window")
        if observed != row["window_observed_assistant_turns"]:
            raise VerificationError("negative label window mismatch")


def smooth(values: list[int], window: int) -> list[int]:
    return [
        sum(values[max(0, i - window + 1) : i + 1]) // min(window, i + 1)
        for i in range(len(values))
    ]


def curvature_point(x0: int, x1: int, x2: int) -> int:
    numerator = abs(x2 - 2 * x1 + x0)
    dx = x1 - x0
    base = SCALE * SCALE + dx * dx
    return numerator * SCALE**3 // (base * isqrt(base))


def graph_groups(graph: dict[str, Any]) -> dict[str, set[str]]:
    return {
        name: {hash_obj(item) for item in graph[name]}
        for name in ("claims", "evidence", "relations")
    }


def set_drift(a: set[str], b: set[str]) -> int:
    union = a | b
    return 0 if not union else len(union - (a & b)) * SCALE // len(union)


def metrics(session: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    clean = {k: v for k, v in session.items() if not k.startswith("_")}
    validate_session(clean)
    validate_profile(profile)
    measurement = clean["measurement_input"]
    signal = measurement["signal_points_micros"]
    smoothed = smooth(signal, profile["smoothing_window"])
    kappa = None
    if len(signal) >= 3:
        kappa = max(
            curvature_point(smoothed[i - 1], smoothed[i], smoothed[i + 1])
            for i in range(1, len(smoothed) - 1)
        )
    epsilon = None
    window = profile["epsilon_window"]
    if len(smoothed) >= window:
        epsilon_values = []
        for end in range(window, len(smoothed) + 1):
            sample = smoothed[end - window : end]
            total = sum(sample)
            numerator = window * sum(value * value for value in sample) - total * total
            epsilon_values.append(isqrt(max(0, numerator)) // window)
        epsilon = max(epsilon_values)
    current = graph_groups(measurement["current_graph"])
    previous = graph_groups(measurement["previous_graph"])
    vector = {
        "claim_micros": set_drift(current["claims"], previous["claims"]),
        "evidence_micros": set_drift(current["evidence"], previous["evidence"]),
        "relation_micros": set_drift(current["relations"], previous["relations"]),
    }
    weighted = (
        profile["dhol_claim_weight_micros"] * vector["claim_micros"] ** 2
        + profile["dhol_evidence_weight_micros"] * vector["evidence_micros"] ** 2
        + profile["dhol_relation_weight_micros"] * vector["relation_micros"] ** 2
    ) // SCALE
    delta_hol = isqrt(weighted)
    phi = None
    vkd = None
    if kappa is not None and epsilon is not None:
        denom = (
            SCALE
            + profile["alpha_k_micros"] * kappa // SCALE
            + profile["alpha_e_micros"] * epsilon // SCALE
            + profile["stability_delta_micros"]
        )
        phi = profile["i_c_micros"] * SCALE // denom
        vkd = min(profile["kappa_critical_micros"] - kappa, phi - profile["phi_min_micros"])
    return {
        "kappa_micros": kappa,
        "epsilon_micros": epsilon,
        "delta_hol_micros": delta_hol,
        "phi_star_micros": phi,
        "vkd_micros": vkd,
        "drift_vector": vector,
    }


def features(session: dict[str, Any], terms: list[str]) -> dict[str, int]:
    clean = {k: v for k, v in session.items() if not k.startswith("_")}
    validate_session(clean)
    transcript = clean["transcript"]
    assistants = [event["text"] for event in transcript if event["role"] == "assistant"]
    last = assistants[-1].lower() if assistants else ""
    return {
        "session_length": sum(1 for event in transcript if event["role"] in {"user", "assistant"}),
        "tool_call_count": sum(1 for event in transcript if event["role"] == "tool"),
        "keyword_heuristic": int(any(term.lower() in last for term in terms)),
    }


def classify(y: list[int], predictions: list[int]) -> dict[str, int]:
    tp = sum(a == 1 and b == 1 for a, b in zip(y, predictions))
    tn = sum(a == 0 and b == 0 for a, b in zip(y, predictions))
    fp = sum(a == 0 and b == 1 for a, b in zip(y, predictions))
    fn = sum(a == 1 and b == 0 for a, b in zip(y, predictions))
    pos, neg = tp + fn, tn + fp
    sensitivity = 0 if pos == 0 else tp * SCALE // pos
    specificity = 0 if neg == 0 else tn * SCALE // neg
    return {
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "sensitivity_micros": sensitivity,
        "specificity_micros": specificity,
        "balanced_accuracy_micros": (sensitivity + specificity) // 2,
        "accuracy_micros": (tp + tn) * SCALE // len(y) if y else 0,
    }


def predict(values: list[int], threshold: int, direction: str) -> list[int]:
    return [
        int(value >= threshold) if direction == "high" else int(value <= threshold)
        for value in values
    ]


def fit(values: list[int], y: list[int], direction: str) -> dict[str, Any]:
    unique = sorted(set(values))
    candidates = sorted(set([unique[0] - 1, *unique, unique[-1] + 1]))
    options = []
    for threshold in candidates:
        stats = classify(y, predict(values, threshold, direction))
        options.append((stats, threshold))
    stats, threshold = max(
        options,
        key=lambda item: (
            item[0]["balanced_accuracy_micros"],
            item[0]["specificity_micros"],
            item[0]["sensitivity_micros"],
            -item[1] if direction == "high" else item[1],
        ),
    )
    return {"threshold": threshold, "direction": direction, "calibration": stats}


def load_sessions(path: Path) -> list[dict[str, Any]]:
    rows = []
    for item in sorted(path.glob("*.json")):
        session = validate_session(read_json(item))
        rows.append({**session, "_path": str(item), "_sha256": hash_file(item)})
    if len({row["session_id"] for row in rows}) != len(rows):
        raise VerificationError("duplicate sessions")
    return rows


def load_continuations(path: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for item in sorted(path.glob("*.json")):
        continuation = validate_continuation(read_json(item))
        sid = continuation["session_id"]
        if sid in result:
            raise VerificationError("duplicate continuation")
        result[sid] = {**continuation, "_sha256": hash_obj(continuation)}
    return result


def load_outcomes(
    path: Path,
    p: dict[str, Any],
    continuations: dict[str, dict[str, Any]],
    expected_phase: str,
    unlock_hash: str | None = None,
) -> dict[str, dict[str, Any]]:
    result = {}
    for row in read_jsonl(path):
        validate_outcome(row, p, expected_phase)
        sid = row["session_id"]
        if sid in result or sid not in continuations:
            raise VerificationError("outcome alignment")
        if unlock_hash is not None and row["outcome_unlock_sha256"] != unlock_hash:
            raise VerificationError("outcome unlock mismatch")
        clean = {k: v for k, v in continuations[sid].items() if not k.startswith("_")}
        validate_outcome_continuation(row, clean, p)
        result[sid] = row
    return result


def validate_eligibility(
    path: Path,
    p: dict[str, Any],
    freeze_doc: dict[str, Any],
    test_sessions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    previous = None
    last_key = None
    seen = set()
    fields = {
        "schema",
        "trial_id",
        "eligibility_index",
        "session_id",
        "handoff_at_utc",
        "registered_at_utc",
        "protocol_sha256",
        "freeze_sha256",
        "session_sha256",
        "previous_eligibility_hash",
        "eligibility_hash",
    }
    for index, row in enumerate(rows, 1):
        if set(row) != fields or row.get("schema") != ELIGIBILITY_SCHEMA:
            raise VerificationError("bad eligibility row")
        sid = row["session_id"]
        if sid in seen or sid not in test_sessions:
            raise VerificationError("eligibility session mismatch")
        seen.add(sid)
        if row["trial_id"] != p["trial_id"] or row["protocol_sha256"] != freeze_doc["protocol_sha256"]:
            raise VerificationError("eligibility protocol mismatch")
        if row["freeze_sha256"] != hash_obj(freeze_doc) or row["eligibility_index"] != index:
            raise VerificationError("eligibility freeze/index mismatch")
        session = test_sessions[sid]
        if row["session_sha256"] != session["_sha256"] or row["handoff_at_utc"] != session["handoff_at_utc"]:
            raise VerificationError("eligibility session binding mismatch")
        handoff = utc(row["handoff_at_utc"])
        registered = utc(row["registered_at_utc"])
        max_lag = p["split"]["max_prediction_lag_seconds"]
        if handoff <= utc(freeze_doc["generated_at_utc"]) or registered < handoff or registered > handoff + timedelta(seconds=max_lag):
            raise VerificationError("eligibility timing mismatch")
        key = (handoff, sid)
        if last_key is not None and key <= last_key:
            raise VerificationError("eligibility order mismatch")
        last_key = key
        if row["previous_eligibility_hash"] != previous:
            raise VerificationError("eligibility chain mismatch")
        base = {key: value for key, value in row.items() if key != "eligibility_hash"}
        if row["eligibility_hash"] != hash_obj(base):
            raise VerificationError("eligibility hash mismatch")
        previous = hash_obj(row)
    return rows


def validate_predictions(
    path: Path,
    p: dict[str, Any],
    freeze_doc: dict[str, Any],
    eligibility: list[dict[str, Any]],
    test_sessions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    if len(rows) != len(eligibility):
        raise VerificationError("prediction count mismatch")
    previous = None
    fields = {
        "schema",
        "trial_id",
        "session_id",
        "handoff_at_utc",
        "predicted_at_utc",
        "protocol_sha256",
        "freeze_sha256",
        "receiver_profile_sha256",
        "session_sha256",
        "metrics",
        "features",
        "primary_prediction",
        "secondary_predictions",
        "comparator_predictions",
        "eligibility_index",
        "eligibility_hash",
        "previous_prediction_hash",
        "prediction_hash",
    }
    receiver_profile = p["measurement_contract"]["receiver_profile"]
    terms = p["comparators"]["keyword_heuristic"]["case_insensitive_terms"]
    for index, (entry, row) in enumerate(zip(eligibility, rows), 1):
        if set(row) != fields or row.get("schema") != PREDICTION_SCHEMA:
            raise VerificationError("bad prediction row")
        sid = entry["session_id"]
        if row["session_id"] != sid or sid not in test_sessions:
            raise VerificationError("prediction order mismatch")
        session = test_sessions[sid]
        if row["trial_id"] != p["trial_id"] or row["protocol_sha256"] != freeze_doc["protocol_sha256"]:
            raise VerificationError("prediction protocol mismatch")
        if row["freeze_sha256"] != hash_obj(freeze_doc) or row["receiver_profile_sha256"] != freeze_doc["receiver_profile_sha256"]:
            raise VerificationError("prediction freeze/profile mismatch")
        if row["session_sha256"] != session["_sha256"] or row["handoff_at_utc"] != session["handoff_at_utc"]:
            raise VerificationError("prediction session mismatch")
        if row["eligibility_index"] != index or row["eligibility_hash"] != entry["eligibility_hash"]:
            raise VerificationError("prediction eligibility mismatch")
        handoff = utc(row["handoff_at_utc"])
        predicted = utc(row["predicted_at_utc"])
        registered = utc(entry["registered_at_utc"])
        if predicted < registered or predicted < handoff or predicted > handoff + timedelta(seconds=p["split"]["max_prediction_lag_seconds"]):
            raise VerificationError("prediction timing mismatch")
        expected_metrics = metrics(session, receiver_profile)
        expected_features = features(session, terms)
        primary = predict(
            [expected_metrics["delta_hol_micros"]],
            freeze_doc["primary"]["threshold"],
            freeze_doc["primary"]["direction"],
        )[0]
        comparator_predictions = {
            "always_safe": 0,
            "keyword_heuristic": expected_features["keyword_heuristic"],
            "session_length": predict(
                [expected_features["session_length"]],
                freeze_doc["comparators"]["session_length"]["threshold"],
                "high",
            )[0],
            "tool_call_count": predict(
                [expected_features["tool_call_count"]],
                freeze_doc["comparators"]["tool_call_count"]["threshold"],
                "high",
            )[0],
        }
        secondary_predictions = {}
        for name, config in freeze_doc["secondary"].items():
            if config.get("status") != "frozen" or expected_metrics.get(name) is None:
                secondary_predictions[name] = None
            else:
                secondary_predictions[name] = predict(
                    [expected_metrics[name]], config["threshold"], config["direction"]
                )[0]
        expected_base = {
            "schema": PREDICTION_SCHEMA,
            "trial_id": p["trial_id"],
            "session_id": sid,
            "handoff_at_utc": session["handoff_at_utc"],
            "predicted_at_utc": row["predicted_at_utc"],
            "protocol_sha256": freeze_doc["protocol_sha256"],
            "freeze_sha256": hash_obj(freeze_doc),
            "receiver_profile_sha256": freeze_doc["receiver_profile_sha256"],
            "session_sha256": session["_sha256"],
            "metrics": expected_metrics,
            "features": expected_features,
            "primary_prediction": primary,
            "secondary_predictions": secondary_predictions,
            "comparator_predictions": comparator_predictions,
            "eligibility_index": entry["eligibility_index"],
            "eligibility_hash": entry["eligibility_hash"],
            "previous_prediction_hash": previous,
        }
        expected = {**expected_base, "prediction_hash": hash_obj(expected_base)}
        if row != expected:
            raise VerificationError(f"prediction cannot be independently reproduced: {sid}")
        previous = hash_obj(row)
    return rows


def validate_unlock(
    path: Path,
    p: dict[str, Any],
    freeze_doc: dict[str, Any],
    eligibility: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    unlock = read_json(path)
    fields = {
        "schema",
        "trial_id",
        "generated_at_utc",
        "protocol_sha256",
        "freeze_sha256",
        "eligibility_ledger_sha256",
        "eligibility_chain_tail",
        "predictions_sha256",
        "prediction_chain_tail",
        "test_n",
        "claim",
    }
    if not isinstance(unlock, dict) or set(unlock) != fields or unlock.get("schema") != OUTCOME_UNLOCK_SCHEMA:
        raise VerificationError("bad outcome unlock")
    if unlock["trial_id"] != p["trial_id"] or unlock["protocol_sha256"] != freeze_doc["protocol_sha256"]:
        raise VerificationError("unlock protocol mismatch")
    if unlock["freeze_sha256"] != hash_obj(freeze_doc):
        raise VerificationError("unlock freeze mismatch")
    if len(predictions) != p["sample"]["test_n"] or len(eligibility) != p["sample"]["test_n"]:
        raise VerificationError("unlock before fixed N")
    if (
        unlock["eligibility_ledger_sha256"] != hash_obj(eligibility)
        or unlock["eligibility_chain_tail"] != hash_obj(eligibility[-1])
        or unlock["predictions_sha256"] != hash_obj(predictions)
        or unlock["prediction_chain_tail"] != hash_obj(predictions[-1])
        or unlock["test_n"] != p["sample"]["test_n"]
    ):
        raise VerificationError("unlock binding mismatch")
    if utc(unlock["generated_at_utc"]) < max(utc(row["predicted_at_utc"]) for row in predictions):
        raise VerificationError("unlock predates final prediction")
    return unlock


def delta_fraction(y: list[int], primary: list[int], comparator: list[int]) -> tuple[int, int]:
    pos = y.count(1)
    neg = y.count(0)
    if pos == 0 or neg == 0:
        raise VerificationError("inference needs both classes")
    numerator = 0
    for label, left, right in zip(y, primary, comparator):
        weight = neg if label == 1 else pos
        numerator += weight * (int(left == label) - int(right == label))
    return numerator, 2 * pos * neg


def ceil_ratio(numerator: int, denominator: int) -> int:
    return -((-numerator) // denominator)


def randomization(y: list[int], primary: list[int], comparator: list[int], alpha: int) -> dict[str, Any]:
    pos = y.count(1)
    neg = y.count(0)
    observed_num, observed_den = delta_fraction(y, primary, comparator)
    weights = []
    for label, left, right in zip(y, primary, comparator):
        if int(left == label) != int(right == label):
            weights.append(neg if label == 1 else pos)
    distribution: dict[int, int] = {0: 1}
    for weight in weights:
        next_distribution: defaultdict[int, int] = defaultdict(int)
        for value, count in distribution.items():
            next_distribution[value + weight] += count
            next_distribution[value - weight] += count
        distribution = dict(next_distribution)
    total = 1 << len(weights)
    extreme = sum(count for value, count in distribution.items() if value >= observed_num)
    return {
        "method": "exact_paired_randomization_balanced_accuracy",
        "alternative": "primary_greater",
        "discordant_n": len(weights),
        "observed_delta_numerator": observed_num,
        "observed_delta_denominator": observed_den,
        "observed_delta_balanced_accuracy_micros": observed_num * SCALE // observed_den,
        "p_value_numerator": extreme,
        "p_value_denominator": total,
        "p_value_micros_ceil": ceil_ratio(extreme * SCALE, total),
        "alpha_micros": alpha,
        "significant": observed_num > 0 and extreme * SCALE <= alpha * total,
    }


def bootstrap_sum(values: list[int], draws: int) -> dict[int, int]:
    multiplicities = Counter(values)
    distribution: dict[int, int] = {0: 1}
    for _ in range(draws):
        next_distribution: defaultdict[int, int] = defaultdict(int)
        for current, count in distribution.items():
            for value, multiplicity in multiplicities.items():
                next_distribution[current + value] += count * multiplicity
        distribution = dict(next_distribution)
    return distribution


def quantile(distribution: dict[int, int], total: int, q_micros: int) -> int:
    rank = max(1, ceil_ratio(q_micros * total, SCALE))
    cumulative = 0
    for value in sorted(distribution):
        cumulative += distribution[value]
        if cumulative >= rank:
            return value
    return max(distribution)


def bootstrap_interval(y: list[int], primary: list[int], comparator: list[int], confidence: int) -> dict[str, Any]:
    positive = []
    negative = []
    for label, left, right in zip(y, primary, comparator):
        diff = int(left == label) - int(right == label)
        (positive if label == 1 else negative).append(diff)
    pos = len(positive)
    neg = len(negative)
    if pos == 0 or neg == 0:
        raise VerificationError("bootstrap needs both classes")
    pos_dist = bootstrap_sum(positive, pos)
    neg_dist = bootstrap_sum(negative, neg)
    combined: defaultdict[int, int] = defaultdict(int)
    for pos_sum, pos_count in pos_dist.items():
        for neg_sum, neg_count in neg_dist.items():
            combined[pos_sum * neg + neg_sum * pos] += pos_count * neg_count
    total = pos**pos * neg**neg
    tail = (SCALE - confidence) // 2
    lower = quantile(dict(combined), total, tail)
    upper = quantile(dict(combined), total, SCALE - tail)
    denominator = 2 * pos * neg
    return {
        "method": "exact_enumerated_stratified_bootstrap_percentile",
        "confidence_micros": confidence,
        "quantile_rule": "smallest support value with cumulative mass >= requested quantile",
        "resample_space_size": total,
        "common_denominator": denominator,
        "lower_numerator": lower,
        "upper_numerator": upper,
        "lower_delta_balanced_accuracy_micros_floor": lower * SCALE // denominator,
        "upper_delta_balanced_accuracy_micros_ceil": ceil_ratio(upper * SCALE, denominator),
    }


def paired_inference(y: list[int], primary: list[int], comparator: list[int], p: dict[str, Any]) -> dict[str, Any]:
    return {
        "exact_randomization": randomization(y, primary, comparator, p["primary_inference"]["alpha_micros"]),
        "bootstrap_interval": bootstrap_interval(y, primary, comparator, p["primary_inference"]["confidence_micros"]),
    }


def verify(
    protocol_path: Path,
    freeze_path: Path,
    calibration_sessions: Path,
    calibration_continuations: Path,
    calibration_outcomes: Path,
    test_sessions: Path,
    eligibility_path: Path,
    test_continuations: Path,
    predictions_path: Path,
    outcome_unlock_path: Path,
    test_outcomes: Path,
    evaluation_path: Path,
) -> dict[str, Any]:
    p = validate_protocol(read_json(protocol_path))
    receiver_profile = p["measurement_contract"]["receiver_profile"]
    freeze_doc = read_json(freeze_path)
    if freeze_doc.get("schema") != FREEZE_SCHEMA or freeze_doc.get("protocol_sha256") != hash_file(protocol_path):
        raise VerificationError("freeze/protocol mismatch")

    all_calibration = load_sessions(calibration_sessions)
    target_calibration = p["sample"]["calibration_n"]
    if len(all_calibration) < target_calibration:
        raise VerificationError("calibration sample minimum not met")
    selected = sorted(
        all_calibration,
        key=lambda session: (utc(session["handoff_at_utc"]), session["session_id"]),
    )[-target_calibration:]
    calibration_cont = load_continuations(calibration_continuations)
    calibration_labels = load_outcomes(
        calibration_outcomes,
        p,
        calibration_cont,
        "calibration",
    )
    selected_ids = {session["session_id"] for session in selected}
    if set(calibration_labels) != selected_ids or set(calibration_cont) != selected_ids:
        raise VerificationError("calibration alignment")
    freeze_dt = utc(freeze_doc["generated_at_utc"])
    if any(utc(session["handoff_at_utc"]) >= freeze_dt for session in selected):
        raise VerificationError("calibration after freeze")
    if any(utc(calibration_labels[session["session_id"]]["labeled_at_utc"]) > freeze_dt for session in selected):
        raise VerificationError("late calibration label")
    for session in selected:
        continuation = calibration_cont[session["session_id"]]
        if utc(continuation["started_at_utc"]) <= utc(session["handoff_at_utc"]):
            raise VerificationError("calibration continuation must start strictly after handoff")
    y_cal = [calibration_labels[session["session_id"]]["outcome"] for session in selected]
    if y_cal.count(0) < p["sample"]["minimum_calibration_per_class"] or y_cal.count(1) < p["sample"]["minimum_calibration_per_class"]:
        raise VerificationError("calibration class minimum")
    calibration_rows = []
    for session in selected:
        measured = metrics(session, receiver_profile)
        if measured["delta_hol_micros"] is None:
            raise VerificationError("missing primary")
        observed_features = features(
            session,
            p["comparators"]["keyword_heuristic"]["case_insensitive_terms"],
        )
        calibration_rows.append((session, measured, observed_features))
    primary_freeze = fit(
        [measured["delta_hol_micros"] for _, measured, _ in calibration_rows],
        y_cal,
        "high",
    )
    secondary_freeze = {}
    for item in p["measurement_contract"]["secondary_metrics"]:
        values = [measured[item["name"]] for _, measured, _ in calibration_rows]
        secondary_freeze[item["name"]] = (
            {"status": "unavailable"}
            if any(value is None for value in values)
            else {"status": "frozen", **fit(values, y_cal, item["direction"])}
        )
    comparator_freeze = {
        "always_safe": {
            "type": "fixed",
            "prediction": 0,
            "calibration": classify(y_cal, [0] * len(y_cal)),
        },
        "keyword_heuristic": {
            "type": "fixed",
            "calibration": classify(
                y_cal,
                [observed["keyword_heuristic"] for _, _, observed in calibration_rows],
            ),
        },
        "session_length": {
            "type": "threshold",
            **fit(
                [observed["session_length"] for _, _, observed in calibration_rows],
                y_cal,
                "high",
            ),
        },
        "tool_call_count": {
            "type": "threshold",
            **fit(
                [observed["tool_call_count"] for _, _, observed in calibration_rows],
                y_cal,
                "high",
            ),
        },
    }
    calibration_manifest = [
        {
            "session_id": session["session_id"],
            "session_sha256": session["_sha256"],
            "continuation_sha256": calibration_cont[session["session_id"]]["_sha256"],
            "outcome": calibration_labels[session["session_id"]]["outcome"],
        }
        for session, _, _ in calibration_rows
    ]
    expected_freeze = {
        "schema": FREEZE_SCHEMA,
        "trial_id": p["trial_id"],
        "protocol_sha256": hash_file(protocol_path),
        "generated_at_utc": freeze_doc["generated_at_utc"],
        "calibration_max_handoff_at_utc": max(session["handoff_at_utc"] for session in selected),
        "calibration_n": len(selected),
        "calibration_selection": {
            "rule": "most_recent_pre_freeze",
            "input_session_count": len(all_calibration),
            "selected_session_ids": [session["session_id"] for session in selected],
        },
        "calibration_manifest_sha256": hash_obj(calibration_manifest),
        "measurement_contract_sha256": hash_obj(p["measurement_contract"]),
        "receiver_profile_sha256": hash_obj(receiver_profile),
        "primary": {"metric": "delta_hol_micros", **primary_freeze},
        "secondary": secondary_freeze,
        "comparators": comparator_freeze,
        "claim_boundary": (
            "Thresholds are fitted only to pre-freeze labeled sessions; the receiver-owned "
            "measurement profile and test inference rule are protocol-bound before held-out scoring."
        ),
    }
    if freeze_doc != expected_freeze:
        raise VerificationError("freeze cannot be independently reproduced")

    test_session_map = {session["session_id"]: session for session in load_sessions(test_sessions)}
    eligibility = validate_eligibility(eligibility_path, p, freeze_doc, test_session_map)
    if len(eligibility) != p["sample"]["test_n"]:
        raise VerificationError("eligibility ledger does not contain fixed prospective N")
    ids = [row["session_id"] for row in eligibility]
    if set(test_session_map) != set(ids):
        raise VerificationError("test session directory does not exactly match eligibility")
    predictions = validate_predictions(
        predictions_path, p, freeze_doc, eligibility, test_session_map
    )
    unlock = validate_unlock(
        outcome_unlock_path, p, freeze_doc, eligibility, predictions
    )
    unlock_hash = hash_obj(unlock)
    test_cont = load_continuations(test_continuations)
    test_labels = load_outcomes(
        test_outcomes,
        p,
        test_cont,
        "prospective",
        unlock_hash,
    )
    prediction_map = {row["session_id"]: row for row in predictions}
    if set(prediction_map) != set(test_labels) or set(prediction_map) != set(test_cont) or set(prediction_map) != set(test_session_map):
        raise VerificationError("test alignment")
    for sid in ids:
        if utc(prediction_map[sid]["predicted_at_utc"]) >= utc(test_cont[sid]["started_at_utc"]):
            raise VerificationError("prediction not sealed before continuation")
        if utc(test_labels[sid]["labeled_at_utc"]) < utc(unlock["generated_at_utc"]):
            raise VerificationError("label predates outcome unlock")

    y = [test_labels[sid]["outcome"] for sid in ids]
    primary_predictions = [prediction_map[sid]["primary_prediction"] for sid in ids]
    primary_stats = classify(y, primary_predictions)
    comparator_stats = {
        name: classify(
            y,
            [prediction_map[sid]["comparator_predictions"][name] for sid in ids],
        )
        for name in p["comparators"]
    }
    eligible = (
        y.count(0) >= p["sample"]["minimum_test_per_class"]
        and y.count(1) >= p["sample"]["minimum_test_per_class"]
    )
    if eligible:
        inference = {
            name: paired_inference(
                y,
                primary_predictions,
                [prediction_map[sid]["comparator_predictions"][name] for sid in ids],
                p,
            )
            for name in p["comparators"]
        }
    else:
        inference = {
            name: {
                "status": "not_run",
                "reason": "minimum_test_per_class_not_met",
            }
            for name in p["comparators"]
        }
    secondary_stats = {}
    for item in p["measurement_contract"]["secondary_metrics"]:
        name = item["name"]
        values = [prediction_map[sid]["secondary_predictions"].get(name) for sid in ids]
        secondary_stats[name] = None if any(value is None for value in values) else classify(y, values)
    best_name = sorted(
        comparator_stats,
        key=lambda name: (-comparator_stats[name]["balanced_accuracy_micros"], name),
    )[0]
    delta = primary_stats["balanced_accuracy_micros"] - comparator_stats[best_name]["balanced_accuracy_micros"]
    strictly_beats_all = all(
        primary_stats["balanced_accuracy_micros"] > stats["balanced_accuracy_micros"]
        for stats in comparator_stats.values()
    )
    significant_against_all = eligible and all(
        result["exact_randomization"]["significant"] for result in inference.values()
    )
    if not eligible:
        disposition = "INSUFFICIENT_SAMPLE"
    elif strictly_beats_all and significant_against_all:
        disposition = "PRIMARY_SIGNAL_CLEARS_PREREGISTERED_GATE"
    else:
        disposition = "DOES_NOT_CLEAR_PREREGISTERED_GATE"
    evaluation = read_json(evaluation_path)
    expected_evaluation = {
        "schema": EVALUATION_SCHEMA,
        "trial_id": p["trial_id"],
        "generated_at_utc": evaluation.get("generated_at_utc"),
        "protocol_sha256": hash_file(protocol_path),
        "freeze_sha256": hash_obj(freeze_doc),
        "receiver_profile_sha256": freeze_doc["receiver_profile_sha256"],
        "eligibility_ledger_sha256": hash_obj(eligibility),
        "eligibility_chain_tail": hash_obj(eligibility[-1]),
        "prediction_chain_tail": hash_obj(predictions[-1]),
        "outcome_unlock_sha256": unlock_hash,
        "test_outcomes_sha256": hash_obj([test_labels[sid] for sid in ids]),
        "test_n": len(ids),
        "class_counts": {"correction": y.count(1), "no_correction": y.count(0)},
        "eligible_for_primary_verdict": eligible,
        "primary": {"metric": "delta_hol_micros", **primary_stats},
        "comparators": comparator_stats,
        "inference": inference,
        "best_comparator": best_name,
        "primary_minus_best_comparator_balanced_accuracy_micros": delta,
        "primary_gate": {
            "strictly_beats_all_comparators": strictly_beats_all,
            "exact_randomization_p_le_alpha_against_all": significant_against_all,
            "alpha_micros": p["primary_inference"]["alpha_micros"],
            "confidence_micros": p["primary_inference"]["confidence_micros"],
        },
        "secondary": secondary_stats,
        "disposition": disposition,
        "claim_boundary": (
            "Pilot prospective association only; no universal threshold, causal claim, "
            "production guarantee, or automatic retirement rule."
        ),
    }
    if evaluation != expected_evaluation:
        raise VerificationError("evaluation cannot be independently reproduced")
    return {
        "status": "PASS",
        "protocol_sha256": hash_file(protocol_path),
        "freeze_sha256": hash_obj(freeze_doc),
        "receiver_profile_sha256": freeze_doc["receiver_profile_sha256"],
        "calibration_n": len(selected),
        "test_n": len(ids),
        "eligibility_chain_tail": hash_obj(eligibility[-1]),
        "prediction_chain_tail": hash_obj(predictions[-1]),
        "outcome_unlock_sha256": unlock_hash,
        "disposition": disposition,
        "primary_minus_best_comparator_balanced_accuracy_micros": delta,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("protocol", type=Path)
    parser.add_argument("freeze", type=Path)
    parser.add_argument("--calibration-sessions", type=Path, required=True)
    parser.add_argument("--calibration-continuations", type=Path, required=True)
    parser.add_argument("--calibration-outcomes", type=Path, required=True)
    parser.add_argument("--test-sessions", type=Path, required=True)
    parser.add_argument("--eligibility-ledger", type=Path, required=True)
    parser.add_argument("--test-continuations", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--outcome-unlock", type=Path, required=True)
    parser.add_argument("--test-outcomes", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(
            args.protocol,
            args.freeze,
            args.calibration_sessions,
            args.calibration_continuations,
            args.calibration_outcomes,
            args.test_sessions,
            args.eligibility_ledger,
            args.test_continuations,
            args.predictions,
            args.outcome_unlock,
            args.test_outcomes,
            args.evaluation,
        )
    except (
        VerificationError,
        KeyError,
        TypeError,
        ValueError,
        FileNotFoundError,
        json.JSONDecodeError,
    ) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
