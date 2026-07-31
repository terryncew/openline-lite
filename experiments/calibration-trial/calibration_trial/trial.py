from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from .common import (
    ELIGIBILITY_SCHEMA,
    EVALUATION_SCHEMA,
    FREEZE_SCHEMA,
    OUTCOME_SCHEMA,
    OUTCOME_UNLOCK_SCHEMA,
    PREDICTION_SCHEMA,
    PREREG_SCHEMA,
    SCALE,
    TrialError,
    append_jsonl_no_overwrite,
    load_json,
    load_jsonl,
    now_utc,
    parse_utc,
    protocol,
    sha256_file,
    sha256_obj,
    validate_continuation,
    validate_outcome,
    validate_outcome_against_continuation,
    validate_session,
    write_json,
)
from .metrics import compute_metrics, require_complete_primary, transcript_features


def _load_sessions(directory: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(directory.glob("*.json")):
        session = validate_session(load_json(path))
        rows.append({**session, "_path": str(path), "_sha256": sha256_file(path)})
    ids = [row["session_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise TrialError("duplicate session ids")
    return rows


def _load_continuations(directory: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        row = validate_continuation(load_json(path))
        sid = row["session_id"]
        if sid in result:
            raise TrialError(f"duplicate continuation for {sid}")
        result[sid] = {**row, "_path": str(path), "_sha256": sha256_obj(row)}
    return result


def _load_outcomes(
    path: Path,
    p: dict[str, Any],
    continuations: dict[str, dict[str, Any]] | None = None,
    *,
    expected_phase: str,
    outcome_unlock_sha256: str | None = None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in load_jsonl(path):
        validate_outcome(row, p, expected_phase=expected_phase)
        sid = row["session_id"]
        if sid in result:
            raise TrialError(f"duplicate outcome for {sid}")
        if outcome_unlock_sha256 is not None and row["outcome_unlock_sha256"] != outcome_unlock_sha256:
            raise TrialError(f"outcome unlock binding mismatch for {sid}")
        if continuations is not None:
            if sid not in continuations:
                raise TrialError(f"missing continuation for {sid}")
            clean = {k: v for k, v in continuations[sid].items() if not k.startswith("_")}
            validate_outcome_against_continuation(row, clean, p)
        result[sid] = row
    return result


def _classification(y: list[int], pred: list[int]) -> dict[str, int]:
    tp = sum(a == 1 and b == 1 for a, b in zip(y, pred))
    tn = sum(a == 0 and b == 0 for a, b in zip(y, pred))
    fp = sum(a == 0 and b == 1 for a, b in zip(y, pred))
    fn = sum(a == 1 and b == 0 for a, b in zip(y, pred))
    pos, neg = tp + fn, tn + fp
    sens = 0 if pos == 0 else tp * SCALE // pos
    spec = 0 if neg == 0 else tn * SCALE // neg
    ba = (sens + spec) // 2
    acc = (tp + tn) * SCALE // len(y) if y else 0
    return {
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "sensitivity_micros": sens,
        "specificity_micros": spec,
        "balanced_accuracy_micros": ba,
        "accuracy_micros": acc,
    }


def _candidate_thresholds(values: list[int]) -> list[int]:
    uniq = sorted(set(values))
    if not uniq:
        raise TrialError("cannot fit threshold without values")
    return sorted(set([uniq[0] - 1, *uniq, uniq[-1] + 1]))


def _pred(values: list[int], threshold: int, direction: str) -> list[int]:
    if direction == "high":
        return [int(value >= threshold) for value in values]
    if direction == "low":
        return [int(value <= threshold) for value in values]
    raise TrialError(f"unknown threshold direction {direction!r}")


def _fit_threshold(values: list[int], y: list[int], direction: str) -> dict[str, Any]:
    candidates = []
    for threshold in _candidate_thresholds(values):
        stats = _classification(y, _pred(values, threshold, direction))
        candidates.append((stats, threshold))
    best_stats, best_threshold = max(
        candidates,
        key=lambda item: (
            item[0]["balanced_accuracy_micros"],
            item[0]["specificity_micros"],
            item[0]["sensitivity_micros"],
            -item[1] if direction == "high" else item[1],
        ),
    )
    return {"threshold": best_threshold, "direction": direction, "calibration": best_stats}


def preregister(protocol_path: Path, out: Path, at: str | None = None) -> dict[str, Any]:
    p = protocol(protocol_path)
    result = {
        "schema": PREREG_SCHEMA,
        "protocol_sha256": sha256_file(protocol_path),
        "trial_id": p["trial_id"],
        "generated_at_utc": at or now_utc(),
        "claim": "Protocol bytes frozen before prospective held-out scoring.",
    }
    write_json(out, result)
    return result


def freeze(
    protocol_path: Path,
    sessions_dir: Path,
    continuations_dir: Path,
    outcomes_path: Path,
    out: Path,
    at: str | None = None,
) -> dict[str, Any]:
    p = protocol(protocol_path)
    receiver_profile = p["measurement_contract"]["receiver_profile"]
    all_sessions = _load_sessions(sessions_dir)
    target_n = p["sample"]["calibration_n"]
    if len(all_sessions) < target_n:
        raise TrialError(f"need at least {target_n} eligible pre-freeze session files")
    sessions = sorted(
        all_sessions,
        key=lambda s: (parse_utc(s["handoff_at_utc"]), s["session_id"]),
    )[-target_n:]
    continuations = _load_continuations(continuations_dir)
    outcomes = _load_outcomes(
        outcomes_path,
        p,
        continuations,
        expected_phase="calibration",
    )
    expected_ids = {s["session_id"] for s in sessions}
    if set(outcomes) != expected_ids or set(continuations) != expected_ids:
        raise TrialError("calibration sessions, continuations, and outcomes must exactly align")
    freeze_time = at or now_utc()
    freeze_dt = parse_utc(freeze_time)
    if any(parse_utc(s["handoff_at_utc"]) >= freeze_dt for s in sessions):
        raise TrialError("every calibration handoff must predate freeze")
    if any(parse_utc(outcomes[s["session_id"]]["labeled_at_utc"]) > freeze_dt for s in sessions):
        raise TrialError("calibration labels must exist no later than freeze")
    for session in sessions:
        continuation = continuations[session["session_id"]]
        if parse_utc(continuation["started_at_utc"]) <= parse_utc(session["handoff_at_utc"]):
            raise TrialError("calibration continuation must start strictly after its handoff")
    y = [outcomes[s["session_id"]]["outcome"] for s in sessions]
    per_class = p["sample"]["minimum_calibration_per_class"]
    if y.count(0) < per_class or y.count(1) < per_class:
        raise TrialError("calibration class minimum not met")
    rows = []
    for session in sessions:
        metrics = compute_metrics(session, receiver_profile)
        require_complete_primary(metrics)
        features = transcript_features(
            session,
            p["comparators"]["keyword_heuristic"]["case_insensitive_terms"],
        )
        rows.append(
            {
                "session_id": session["session_id"],
                "handoff_at_utc": session["handoff_at_utc"],
                "session_sha256": session["_sha256"],
                "metrics": metrics,
                "features": features,
                "outcome": outcomes[session["session_id"]]["outcome"],
            }
        )
    primary = _fit_threshold(
        [row["metrics"]["delta_hol_micros"] for row in rows], y, "high"
    )
    secondary: dict[str, Any] = {}
    for item in p["measurement_contract"]["secondary_metrics"]:
        values = [row["metrics"][item["name"]] for row in rows]
        if any(value is None for value in values):
            secondary[item["name"]] = {"status": "unavailable"}
        else:
            secondary[item["name"]] = {
                "status": "frozen",
                **_fit_threshold(values, y, item["direction"]),
            }
    comparators = {
        "always_safe": {
            "type": "fixed",
            "prediction": 0,
            "calibration": _classification(y, [0] * len(y)),
        },
        "keyword_heuristic": {
            "type": "fixed",
            "calibration": _classification(
                y, [row["features"]["keyword_heuristic"] for row in rows]
            ),
        },
        "session_length": {
            "type": "threshold",
            **_fit_threshold(
                [row["features"]["session_length"] for row in rows], y, "high"
            ),
        },
        "tool_call_count": {
            "type": "threshold",
            **_fit_threshold(
                [row["features"]["tool_call_count"] for row in rows], y, "high"
            ),
        },
    }
    manifest = [
        {
            "session_id": row["session_id"],
            "session_sha256": row["session_sha256"],
            "continuation_sha256": continuations[row["session_id"]]["_sha256"],
            "outcome": row["outcome"],
        }
        for row in rows
    ]
    result = {
        "schema": FREEZE_SCHEMA,
        "trial_id": p["trial_id"],
        "protocol_sha256": sha256_file(protocol_path),
        "generated_at_utc": freeze_time,
        "calibration_max_handoff_at_utc": max(row["handoff_at_utc"] for row in rows),
        "calibration_n": len(rows),
        "calibration_selection": {
            "rule": "most_recent_pre_freeze",
            "input_session_count": len(all_sessions),
            "selected_session_ids": [row["session_id"] for row in rows],
        },
        "calibration_manifest_sha256": sha256_obj(manifest),
        "measurement_contract_sha256": sha256_obj(p["measurement_contract"]),
        "receiver_profile_sha256": sha256_obj(receiver_profile),
        "primary": {"metric": "delta_hol_micros", **primary},
        "secondary": secondary,
        "comparators": comparators,
        "claim_boundary": (
            "Thresholds are fitted only to pre-freeze labeled sessions; the receiver-owned "
            "measurement profile and test inference rule are protocol-bound before held-out scoring."
        ),
    }
    write_json(out, result)
    return result


def _validate_eligibility_ledger(
    protocol_path: Path,
    freeze_doc: dict[str, Any],
    ledger_path: Path,
    sessions: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    p = protocol(protocol_path)
    rows = load_jsonl(ledger_path) if ledger_path.exists() else []
    previous = None
    last_key = None
    seen: set[str] = set()
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
        if not isinstance(row, dict) or set(row) != fields or row.get("schema") != ELIGIBILITY_SCHEMA:
            raise TrialError("invalid eligibility ledger row")
        if row.get("trial_id") != p["trial_id"] or row.get("protocol_sha256") != sha256_file(protocol_path):
            raise TrialError("eligibility ledger protocol mismatch")
        if row.get("freeze_sha256") != sha256_obj(freeze_doc):
            raise TrialError("eligibility ledger freeze mismatch")
        if row.get("eligibility_index") != index:
            raise TrialError("eligibility ledger index mismatch")
        sid = row.get("session_id")
        if not isinstance(sid, str) or not sid or sid in seen:
            raise TrialError("duplicate/invalid eligibility session id")
        seen.add(sid)
        handoff = parse_utc(row["handoff_at_utc"])
        registered = parse_utc(row["registered_at_utc"])
        freeze_dt = parse_utc(freeze_doc["generated_at_utc"])
        if handoff <= freeze_dt:
            raise TrialError("eligibility handoff must occur strictly after freeze")
        max_lag = p["split"]["max_prediction_lag_seconds"]
        if registered < handoff or registered > handoff + timedelta(seconds=max_lag):
            raise TrialError("eligibility registration missed the preregistered window")
        key = (handoff, sid)
        if last_key is not None and key <= last_key:
            raise TrialError("eligibility ledger is not strictly ordered by handoff/session id")
        last_key = key
        if row.get("previous_eligibility_hash") != previous:
            raise TrialError("eligibility ledger chain mismatch")
        base = {k: v for k, v in row.items() if k != "eligibility_hash"}
        if row.get("eligibility_hash") != sha256_obj(base):
            raise TrialError("eligibility row hash mismatch")
        if sessions is not None:
            if sid not in sessions:
                raise TrialError(f"eligibility ledger session missing from test directory: {sid}")
            session = sessions[sid]
            if (
                row["handoff_at_utc"] != session["handoff_at_utc"]
                or row["session_sha256"] != session["_sha256"]
            ):
                raise TrialError(f"eligibility/session binding mismatch: {sid}")
        previous = sha256_obj(row)
    return rows


def register(
    protocol_path: Path,
    freeze_path: Path,
    session_path: Path,
    eligibility_path: Path,
    at: str | None = None,
) -> dict[str, Any]:
    p = protocol(protocol_path)
    freeze_doc = load_json(freeze_path)
    if (
        freeze_doc.get("schema") != FREEZE_SCHEMA
        or freeze_doc.get("protocol_sha256") != sha256_file(protocol_path)
        or freeze_doc.get("receiver_profile_sha256")
        != sha256_obj(p["measurement_contract"]["receiver_profile"])
    ):
        raise TrialError("invalid freeze/protocol/receiver-profile binding")
    prior = _validate_eligibility_ledger(protocol_path, freeze_doc, eligibility_path)
    if len(prior) >= p["sample"]["test_n"]:
        raise TrialError("fixed prospective test N already registered; do not extend the trial")
    session = validate_session(load_json(session_path))
    sid = session["session_id"]
    if any(row["session_id"] == sid for row in prior):
        raise TrialError(f"refusing duplicate eligibility session_id={sid!r}")
    handoff = parse_utc(session["handoff_at_utc"])
    registered_at = at or now_utc()
    registered_dt = parse_utc(registered_at)
    freeze_dt = parse_utc(freeze_doc["generated_at_utc"])
    max_lag = p["split"]["max_prediction_lag_seconds"]
    if handoff <= freeze_dt:
        raise TrialError("test handoff must occur strictly after freeze")
    if registered_dt < handoff or registered_dt > handoff + timedelta(seconds=max_lag):
        raise TrialError(f"eligibility must be registered between handoff and +{max_lag}s")
    key = (handoff, sid)
    if prior:
        prev_key = (
            parse_utc(prior[-1]["handoff_at_utc"]),
            prior[-1]["session_id"],
        )
        if key <= prev_key:
            raise TrialError("late discovery of an earlier handoff is a protocol deviation; trial must hold")
    base = {
        "schema": ELIGIBILITY_SCHEMA,
        "trial_id": p["trial_id"],
        "eligibility_index": len(prior) + 1,
        "session_id": sid,
        "handoff_at_utc": session["handoff_at_utc"],
        "registered_at_utc": registered_at,
        "protocol_sha256": freeze_doc["protocol_sha256"],
        "freeze_sha256": sha256_obj(freeze_doc),
        "session_sha256": sha256_file(session_path),
        "previous_eligibility_hash": sha256_obj(prior[-1]) if prior else None,
    }
    row = {**base, "eligibility_hash": sha256_obj(base)}
    append_jsonl_no_overwrite(eligibility_path, row, ("session_id", sid))
    return row


def _prediction_payload(
    protocol_path: Path,
    p: dict[str, Any],
    freeze_doc: dict[str, Any],
    session: dict[str, Any],
    session_path: Path,
    predicted_at: str,
) -> dict[str, Any]:
    if freeze_doc.get("protocol_sha256") != sha256_file(protocol_path):
        raise TrialError("protocol bytes changed after freeze")
    receiver_profile = p["measurement_contract"]["receiver_profile"]
    if freeze_doc.get("receiver_profile_sha256") != sha256_obj(receiver_profile):
        raise TrialError("receiver-owned measurement profile changed after freeze")
    handoff = parse_utc(session["handoff_at_utc"])
    freeze_dt = parse_utc(freeze_doc["generated_at_utc"])
    predicted_dt = parse_utc(predicted_at)
    if handoff <= freeze_dt:
        raise TrialError("test handoff must occur strictly after freeze")
    max_lag = p["split"]["max_prediction_lag_seconds"]
    if predicted_dt < handoff or predicted_dt > handoff + timedelta(seconds=max_lag):
        raise TrialError(f"prediction must be sealed between handoff and +{max_lag}s")
    metrics = compute_metrics(session, receiver_profile)
    require_complete_primary(metrics)
    features = transcript_features(
        session,
        p["comparators"]["keyword_heuristic"]["case_insensitive_terms"],
    )
    primary = freeze_doc["primary"]
    primary_pred = _pred(
        [metrics[primary["metric"]]], primary["threshold"], primary["direction"]
    )[0]
    comparators = {
        "always_safe": 0,
        "keyword_heuristic": features["keyword_heuristic"],
        "session_length": _pred(
            [features["session_length"]],
            freeze_doc["comparators"]["session_length"]["threshold"],
            "high",
        )[0],
        "tool_call_count": _pred(
            [features["tool_call_count"]],
            freeze_doc["comparators"]["tool_call_count"]["threshold"],
            "high",
        )[0],
    }
    secondary = {}
    for name, cfg in freeze_doc["secondary"].items():
        if cfg.get("status") == "frozen" and metrics.get(name) is not None:
            secondary[name] = _pred(
                [metrics[name]], cfg["threshold"], cfg["direction"]
            )[0]
        else:
            secondary[name] = None
    return {
        "schema": PREDICTION_SCHEMA,
        "trial_id": p["trial_id"],
        "session_id": session["session_id"],
        "handoff_at_utc": session["handoff_at_utc"],
        "predicted_at_utc": predicted_at,
        "protocol_sha256": freeze_doc["protocol_sha256"],
        "freeze_sha256": sha256_obj(freeze_doc),
        "receiver_profile_sha256": freeze_doc["receiver_profile_sha256"],
        "session_sha256": sha256_file(session_path),
        "metrics": metrics,
        "features": features,
        "primary_prediction": primary_pred,
        "secondary_predictions": secondary,
        "comparator_predictions": comparators,
    }


def _validate_prediction_chain(
    protocol_path: Path,
    freeze_doc: dict[str, Any],
    eligibility: list[dict[str, Any]],
    predictions_path: Path,
) -> list[dict[str, Any]]:
    p = protocol(protocol_path)
    rows = load_jsonl(predictions_path) if predictions_path.exists() else []
    if len(rows) > len(eligibility):
        raise TrialError("predictions exceed eligibility ledger")
    seen: set[str] = set()
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
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or set(row) != fields or row.get("schema") != PREDICTION_SCHEMA:
            raise TrialError("invalid prediction row")
        entry = eligibility[index - 1]
        sid = row.get("session_id")
        if not isinstance(sid, str) or sid in seen:
            raise TrialError("duplicate/invalid prediction session id")
        seen.add(sid)
        if sid != entry["session_id"]:
            raise TrialError("predictions skip/reorder an eligible handoff")
        if row.get("trial_id") != p["trial_id"]:
            raise TrialError("prediction trial mismatch")
        if row.get("protocol_sha256") != sha256_file(protocol_path):
            raise TrialError("prediction protocol mismatch")
        if row.get("freeze_sha256") != sha256_obj(freeze_doc):
            raise TrialError("prediction freeze mismatch")
        if row.get("receiver_profile_sha256") != freeze_doc.get("receiver_profile_sha256"):
            raise TrialError("prediction receiver-profile mismatch")
        if (
            row.get("session_sha256") != entry["session_sha256"]
            or row.get("handoff_at_utc") != entry["handoff_at_utc"]
            or row.get("eligibility_index") != entry["eligibility_index"]
            or row.get("eligibility_hash") != entry["eligibility_hash"]
        ):
            raise TrialError("prediction eligibility binding mismatch")
        handoff = parse_utc(row["handoff_at_utc"])
        predicted = parse_utc(row["predicted_at_utc"])
        registered = parse_utc(entry["registered_at_utc"])
        max_lag = p["split"]["max_prediction_lag_seconds"]
        if predicted < registered or predicted < handoff or predicted > handoff + timedelta(seconds=max_lag):
            raise TrialError("prediction missed the preregistered prospective window")
        if row.get("previous_prediction_hash") != previous:
            raise TrialError("prediction chain mismatch")
        base = {k: v for k, v in row.items() if k != "prediction_hash"}
        if row.get("prediction_hash") != sha256_obj(base):
            raise TrialError("prediction row hash mismatch")
        previous = sha256_obj(row)
    return rows


def score(
    protocol_path: Path,
    freeze_path: Path,
    session_path: Path,
    eligibility_path: Path,
    predictions_path: Path,
    at: str | None = None,
) -> dict[str, Any]:
    p = protocol(protocol_path)
    freeze_doc = load_json(freeze_path)
    if freeze_doc.get("schema") != FREEZE_SCHEMA:
        raise TrialError("invalid freeze")
    session = validate_session(load_json(session_path))
    eligibility = _validate_eligibility_ledger(protocol_path, freeze_doc, eligibility_path)
    prior = _validate_prediction_chain(
        protocol_path, freeze_doc, eligibility, predictions_path
    )
    if len(prior) >= p["sample"]["test_n"]:
        raise TrialError("fixed prospective test N already reached; do not extend after outcomes are observable")
    next_index = len(prior) + 1
    if next_index > len(eligibility):
        raise TrialError("session must be registered in the eligibility ledger before scoring")
    entry = eligibility[next_index - 1]
    if entry["session_id"] != session["session_id"]:
        raise TrialError("cannot skip an earlier eligible handoff")
    if (
        entry["session_sha256"] != sha256_file(session_path)
        or entry["handoff_at_utc"] != session["handoff_at_utc"]
    ):
        raise TrialError("scored session does not match its eligibility receipt")
    predicted_at = at or now_utc()
    if parse_utc(predicted_at) < parse_utc(entry["registered_at_utc"]):
        raise TrialError("prediction cannot predate eligibility registration")
    row = _prediction_payload(
        protocol_path, p, freeze_doc, session, session_path, predicted_at
    )
    row["eligibility_index"] = entry["eligibility_index"]
    row["eligibility_hash"] = entry["eligibility_hash"]
    row["previous_prediction_hash"] = sha256_obj(prior[-1]) if prior else None
    row["prediction_hash"] = sha256_obj(
        {k: v for k, v in row.items() if k != "prediction_hash"}
    )
    append_jsonl_no_overwrite(
        predictions_path, row, ("session_id", session["session_id"])
    )
    return row


def unlock_outcomes(
    protocol_path: Path,
    freeze_path: Path,
    eligibility_path: Path,
    predictions_path: Path,
    out: Path,
    at: str | None = None,
) -> dict[str, Any]:
    if out.exists():
        raise TrialError("refusing to overwrite an existing outcome unlock receipt")
    p = protocol(protocol_path)
    freeze_doc = load_json(freeze_path)
    eligibility = _validate_eligibility_ledger(protocol_path, freeze_doc, eligibility_path)
    predictions = _validate_prediction_chain(
        protocol_path, freeze_doc, eligibility, predictions_path
    )
    target_n = p["sample"]["test_n"]
    if len(eligibility) != target_n or len(predictions) != target_n:
        raise TrialError(
            f"outcome labeling remains blacked out until all {target_n} eligible predictions are sealed"
        )
    unlocked_at = at or now_utc()
    unlocked_dt = parse_utc(unlocked_at)
    if unlocked_dt < max(parse_utc(row["predicted_at_utc"]) for row in predictions):
        raise TrialError("outcome unlock cannot predate the final sealed prediction")
    result = {
        "schema": OUTCOME_UNLOCK_SCHEMA,
        "trial_id": p["trial_id"],
        "generated_at_utc": unlocked_at,
        "protocol_sha256": sha256_file(protocol_path),
        "freeze_sha256": sha256_obj(freeze_doc),
        "eligibility_ledger_sha256": sha256_obj(eligibility),
        "eligibility_chain_tail": sha256_obj(eligibility[-1]),
        "predictions_sha256": sha256_obj(predictions),
        "prediction_chain_tail": sha256_obj(predictions[-1]),
        "test_n": target_n,
        "claim": (
            "Outcome labeling unlocked only after every preregistered prospective prediction was sealed."
        ),
    }
    write_json(out, result)
    return result


def _validate_outcome_unlock(
    protocol_path: Path,
    freeze_doc: dict[str, Any],
    eligibility_path: Path,
    predictions_path: Path,
    unlock_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    p = protocol(protocol_path)
    eligibility = _validate_eligibility_ledger(protocol_path, freeze_doc, eligibility_path)
    predictions = _validate_prediction_chain(
        protocol_path, freeze_doc, eligibility, predictions_path
    )
    target_n = p["sample"]["test_n"]
    if len(eligibility) != target_n or len(predictions) != target_n:
        raise TrialError("outcome unlock requires the complete fixed prospective sample")
    unlock = load_json(unlock_path)
    expected_fields = {
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
    if not isinstance(unlock, dict) or set(unlock) != expected_fields or unlock.get("schema") != OUTCOME_UNLOCK_SCHEMA:
        raise TrialError("invalid outcome unlock receipt")
    if unlock.get("trial_id") != p["trial_id"] or unlock.get("protocol_sha256") != sha256_file(protocol_path):
        raise TrialError("outcome unlock protocol mismatch")
    if unlock.get("freeze_sha256") != sha256_obj(freeze_doc):
        raise TrialError("outcome unlock freeze mismatch")
    if (
        unlock.get("eligibility_ledger_sha256") != sha256_obj(eligibility)
        or unlock.get("eligibility_chain_tail") != sha256_obj(eligibility[-1])
        or unlock.get("predictions_sha256") != sha256_obj(predictions)
        or unlock.get("prediction_chain_tail") != sha256_obj(predictions[-1])
        or unlock.get("test_n") != target_n
    ):
        raise TrialError("outcome unlock does not bind the complete prediction set")
    if parse_utc(unlock["generated_at_utc"]) < max(
        parse_utc(row["predicted_at_utc"]) for row in predictions
    ):
        raise TrialError("outcome unlock predates the final prediction")
    return unlock, eligibility, predictions


def label(
    protocol_path: Path,
    freeze_path: Path,
    eligibility_path: Path,
    predictions_path: Path,
    outcome_unlock_path: Path,
    continuation_path: Path,
    out: Path,
    *,
    session_id: str,
    outcome: int,
    kind: str | None,
    correction_message_index: int | None,
    notes: str,
    at: str | None = None,
) -> dict[str, Any]:
    p = protocol(protocol_path)
    freeze_doc = load_json(freeze_path)
    unlock, _, prediction_rows = _validate_outcome_unlock(
        protocol_path,
        freeze_doc,
        eligibility_path,
        predictions_path,
        outcome_unlock_path,
    )
    predictions = {row["session_id"]: row for row in prediction_rows}
    if session_id not in predictions:
        raise TrialError("cannot label a session without a sealed prediction")
    continuation = validate_continuation(load_json(continuation_path))
    if continuation["session_id"] != session_id:
        raise TrialError("continuation does not match session")
    if parse_utc(predictions[session_id]["predicted_at_utc"]) >= parse_utc(
        continuation["started_at_utc"]
    ):
        raise TrialError("prediction was sealed after the continuation began")
    labeled_at = at or now_utc()
    labeled_dt = parse_utc(labeled_at)
    if labeled_dt < parse_utc(unlock["generated_at_utc"]):
        raise TrialError("prospective outcome labels are blacked out until the outcome unlock")
    if labeled_dt <= parse_utc(predictions[session_id]["predicted_at_utc"]):
        raise TrialError("outcome label must postdate prediction")
    window = p["outcome"]["window_assistant_turns"]
    if outcome == 1:
        if (
            correction_message_index is None
            or correction_message_index < 0
            or correction_message_index >= len(continuation["events"])
        ):
            raise TrialError("positive outcome requires a valid correction message index")
        if continuation["events"][correction_message_index]["role"] != "user":
            raise TrialError("correction message must be a human/user event")
        observed = sum(
            1
            for event in continuation["events"][:correction_message_index]
            if event["role"] == "assistant"
        )
    else:
        observed = min(
            window,
            sum(1 for event in continuation["events"] if event["role"] == "assistant"),
        )
    row = {
        "schema": OUTCOME_SCHEMA,
        "phase": "prospective",
        "session_id": session_id,
        "outcome": outcome,
        "kind": kind,
        "correction_message_index": correction_message_index,
        "continuation_sha256": sha256_obj(continuation),
        "window_observed_assistant_turns": observed,
        "continuation_ended": continuation["ended"],
        "labeled_at_utc": labeled_at,
        "outcome_unlock_sha256": sha256_obj(unlock),
        "notes": notes,
    }
    validate_outcome(row, p, expected_phase="prospective")
    validate_outcome_against_continuation(row, continuation, p)
    append_jsonl_no_overwrite(out, row, ("session_id", session_id))
    return row


def _delta_ba_fraction(
    y: list[int], primary: list[int], comparator: list[int]
) -> tuple[int, int]:
    pos = y.count(1)
    neg = y.count(0)
    if pos == 0 or neg == 0:
        raise TrialError("balanced-accuracy inference requires both outcome classes")
    numerator = 0
    for label_value, primary_pred, comparator_pred in zip(y, primary, comparator):
        primary_correct = int(primary_pred == label_value)
        comparator_correct = int(comparator_pred == label_value)
        class_weight = neg if label_value == 1 else pos
        numerator += class_weight * (primary_correct - comparator_correct)
    return numerator, 2 * pos * neg


def _ceil_ratio(numerator: int, denominator: int) -> int:
    return -((-numerator) // denominator)


def _exact_paired_randomization(
    y: list[int], primary: list[int], comparator: list[int], alpha_micros: int
) -> dict[str, Any]:
    pos = y.count(1)
    neg = y.count(0)
    observed_num, observed_den = _delta_ba_fraction(y, primary, comparator)
    discordant_weights = []
    for label_value, primary_pred, comparator_pred in zip(y, primary, comparator):
        if int(primary_pred == label_value) != int(comparator_pred == label_value):
            discordant_weights.append(neg if label_value == 1 else pos)
    distribution: dict[int, int] = {0: 1}
    for weight in discordant_weights:
        next_distribution: defaultdict[int, int] = defaultdict(int)
        for value, count in distribution.items():
            next_distribution[value + weight] += count
            next_distribution[value - weight] += count
        distribution = dict(next_distribution)
    permutation_count = 1 << len(discordant_weights)
    extreme_count = sum(
        count for value, count in distribution.items() if value >= observed_num
    )
    p_micros_ceil = _ceil_ratio(extreme_count * SCALE, permutation_count)
    significant = (
        observed_num > 0
        and extreme_count * SCALE <= alpha_micros * permutation_count
    )
    return {
        "method": "exact_paired_randomization_balanced_accuracy",
        "alternative": "primary_greater",
        "discordant_n": len(discordant_weights),
        "observed_delta_numerator": observed_num,
        "observed_delta_denominator": observed_den,
        "observed_delta_balanced_accuracy_micros": observed_num * SCALE // observed_den,
        "p_value_numerator": extreme_count,
        "p_value_denominator": permutation_count,
        "p_value_micros_ceil": p_micros_ceil,
        "alpha_micros": alpha_micros,
        "significant": significant,
    }


def _bootstrap_sum_distribution(values: list[int], draws: int) -> dict[int, int]:
    multiplicities = Counter(values)
    distribution: dict[int, int] = {0: 1}
    for _ in range(draws):
        next_distribution: defaultdict[int, int] = defaultdict(int)
        for current_sum, count in distribution.items():
            for value, multiplicity in multiplicities.items():
                next_distribution[current_sum + value] += count * multiplicity
        distribution = dict(next_distribution)
    return distribution


def _distribution_quantile(
    distribution: dict[int, int], total: int, q_micros: int
) -> int:
    rank = max(1, _ceil_ratio(q_micros * total, SCALE))
    cumulative = 0
    for value in sorted(distribution):
        cumulative += distribution[value]
        if cumulative >= rank:
            return value
    return max(distribution)


def _exact_stratified_bootstrap_ci(
    y: list[int],
    primary: list[int],
    comparator: list[int],
    confidence_micros: int,
) -> dict[str, Any]:
    pos_diffs = []
    neg_diffs = []
    for label_value, primary_pred, comparator_pred in zip(y, primary, comparator):
        diff = int(primary_pred == label_value) - int(comparator_pred == label_value)
        (pos_diffs if label_value == 1 else neg_diffs).append(diff)
    pos = len(pos_diffs)
    neg = len(neg_diffs)
    if pos == 0 or neg == 0:
        raise TrialError("bootstrap interval requires both outcome classes")
    pos_dist = _bootstrap_sum_distribution(pos_diffs, pos)
    neg_dist = _bootstrap_sum_distribution(neg_diffs, neg)
    combined: defaultdict[int, int] = defaultdict(int)
    for pos_sum, pos_count in pos_dist.items():
        for neg_sum, neg_count in neg_dist.items():
            numerator = pos_sum * neg + neg_sum * pos
            combined[numerator] += pos_count * neg_count
    total = pos**pos * neg**neg
    tail = (SCALE - confidence_micros) // 2
    lower_num = _distribution_quantile(dict(combined), total, tail)
    upper_num = _distribution_quantile(dict(combined), total, SCALE - tail)
    denominator = 2 * pos * neg
    return {
        "method": "exact_enumerated_stratified_bootstrap_percentile",
        "confidence_micros": confidence_micros,
        "quantile_rule": "smallest support value with cumulative mass >= requested quantile",
        "resample_space_size": total,
        "common_denominator": denominator,
        "lower_numerator": lower_num,
        "upper_numerator": upper_num,
        "lower_delta_balanced_accuracy_micros_floor": lower_num * SCALE // denominator,
        "upper_delta_balanced_accuracy_micros_ceil": _ceil_ratio(
            upper_num * SCALE, denominator
        ),
    }


def _paired_inference(
    y: list[int],
    primary: list[int],
    comparator: list[int],
    p: dict[str, Any],
) -> dict[str, Any]:
    inference = p["primary_inference"]
    return {
        "exact_randomization": _exact_paired_randomization(
            y, primary, comparator, inference["alpha_micros"]
        ),
        "bootstrap_interval": _exact_stratified_bootstrap_ci(
            y, primary, comparator, inference["confidence_micros"]
        ),
    }


def evaluate(
    protocol_path: Path,
    freeze_path: Path,
    sessions_dir: Path,
    eligibility_path: Path,
    continuations_dir: Path,
    predictions_path: Path,
    outcome_unlock_path: Path,
    outcomes_path: Path,
    out: Path,
    at: str | None = None,
) -> dict[str, Any]:
    p = protocol(protocol_path)
    freeze_doc = load_json(freeze_path)
    if freeze_doc.get("protocol_sha256") != sha256_file(protocol_path):
        raise TrialError("protocol bytes changed after freeze")
    sessions = {s["session_id"]: s for s in _load_sessions(sessions_dir)}
    eligibility = _validate_eligibility_ledger(
        protocol_path, freeze_doc, eligibility_path, sessions
    )
    target_n = p["sample"]["test_n"]
    if len(eligibility) != target_n:
        raise TrialError(
            f"evaluate only once after exactly {target_n} registered prospective handoffs"
        )
    eligibility_ids = [row["session_id"] for row in eligibility]
    if set(sessions) != set(eligibility_ids):
        raise TrialError(
            "test session directory must exactly match the eligibility ledger; omitted/extra handoff detected"
        )
    unlock, _, pred_rows = _validate_outcome_unlock(
        protocol_path,
        freeze_doc,
        eligibility_path,
        predictions_path,
        outcome_unlock_path,
    )
    predictions = {row["session_id"]: row for row in pred_rows}
    continuations = _load_continuations(continuations_dir)
    unlock_hash = sha256_obj(unlock)
    outcomes = _load_outcomes(
        outcomes_path,
        p,
        continuations,
        expected_phase="prospective",
        outcome_unlock_sha256=unlock_hash,
    )
    if [row["session_id"] for row in pred_rows] != eligibility_ids:
        raise TrialError("predictions must cover the eligibility ledger in exact order with no skips")
    if (
        set(predictions) != set(outcomes)
        or set(predictions) != set(sessions)
        or set(predictions) != set(continuations)
    ):
        raise TrialError(
            "test eligibility/predictions/outcomes/sessions/continuations do not align"
        )
    for sid in predictions:
        if parse_utc(predictions[sid]["predicted_at_utc"]) >= parse_utc(
            continuations[sid]["started_at_utc"]
        ):
            raise TrialError("a held-out prediction was sealed after its continuation began")
        if parse_utc(outcomes[sid]["labeled_at_utc"]) < parse_utc(
            unlock["generated_at_utc"]
        ):
            raise TrialError("a prospective label predates the all-predictions outcome unlock")
    ids = eligibility_ids
    y = [outcomes[sid]["outcome"] for sid in ids]
    min_class = p["sample"]["minimum_test_per_class"]
    primary_predictions = [predictions[sid]["primary_prediction"] for sid in ids]
    primary_stats = _classification(y, primary_predictions)
    comparator_stats = {
        name: _classification(
            y, [predictions[sid]["comparator_predictions"][name] for sid in ids]
        )
        for name in p["comparators"]
    }
    eligible = y.count(0) >= min_class and y.count(1) >= min_class
    if eligible:
        inference = {
            name: _paired_inference(
                y,
                primary_predictions,
                [predictions[sid]["comparator_predictions"][name] for sid in ids],
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
        values = [predictions[sid]["secondary_predictions"].get(name) for sid in ids]
        secondary_stats[name] = (
            None if any(value is None for value in values) else _classification(y, values)
        )
    best_name = sorted(
        comparator_stats,
        key=lambda name: (
            -comparator_stats[name]["balanced_accuracy_micros"],
            name,
        ),
    )[0]
    best = comparator_stats[best_name]
    delta = (
        primary_stats["balanced_accuracy_micros"]
        - best["balanced_accuracy_micros"]
    )
    strictly_beats_all = all(
        primary_stats["balanced_accuracy_micros"]
        > stats["balanced_accuracy_micros"]
        for stats in comparator_stats.values()
    )
    significant_against_all = eligible and all(
        result["exact_randomization"]["significant"]
        for result in inference.values()
    )
    if not eligible:
        disposition = "INSUFFICIENT_SAMPLE"
    elif strictly_beats_all and significant_against_all:
        disposition = "PRIMARY_SIGNAL_CLEARS_PREREGISTERED_GATE"
    else:
        disposition = "DOES_NOT_CLEAR_PREREGISTERED_GATE"
    result = {
        "schema": EVALUATION_SCHEMA,
        "trial_id": p["trial_id"],
        "generated_at_utc": at or now_utc(),
        "protocol_sha256": sha256_file(protocol_path),
        "freeze_sha256": sha256_obj(freeze_doc),
        "receiver_profile_sha256": freeze_doc["receiver_profile_sha256"],
        "eligibility_ledger_sha256": sha256_obj(eligibility),
        "eligibility_chain_tail": sha256_obj(eligibility[-1]),
        "prediction_chain_tail": sha256_obj(pred_rows[-1]),
        "outcome_unlock_sha256": unlock_hash,
        "test_outcomes_sha256": sha256_obj([outcomes[sid] for sid in ids]),
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
    write_json(out, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m calibration_trial")
    sub = parser.add_subparsers(dest="cmd", required=True)

    command = sub.add_parser("preregister")
    command.add_argument("protocol", type=Path)
    command.add_argument("--out", type=Path, required=True)

    command = sub.add_parser("freeze")
    command.add_argument("protocol", type=Path)
    command.add_argument("--sessions", type=Path, required=True)
    command.add_argument("--continuations", type=Path, required=True)
    command.add_argument("--outcomes", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)

    command = sub.add_parser("register")
    command.add_argument("protocol", type=Path)
    command.add_argument("freeze", type=Path)
    command.add_argument("session", type=Path)
    command.add_argument("--eligibility-ledger", type=Path, required=True)

    command = sub.add_parser("score")
    command.add_argument("protocol", type=Path)
    command.add_argument("freeze", type=Path)
    command.add_argument("session", type=Path)
    command.add_argument("--eligibility-ledger", type=Path, required=True)
    command.add_argument("--predictions", type=Path, required=True)

    command = sub.add_parser("unlock-outcomes")
    command.add_argument("protocol", type=Path)
    command.add_argument("freeze", type=Path)
    command.add_argument("--eligibility-ledger", type=Path, required=True)
    command.add_argument("--predictions", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)

    command = sub.add_parser("label")
    command.add_argument("protocol", type=Path)
    command.add_argument("freeze", type=Path)
    command.add_argument("--eligibility-ledger", type=Path, required=True)
    command.add_argument("--predictions", type=Path, required=True)
    command.add_argument("--outcome-unlock", type=Path, required=True)
    command.add_argument("--continuation", type=Path, required=True)
    command.add_argument("--session-id", required=True)
    command.add_argument("--outcome", type=int, choices=[0, 1], required=True)
    command.add_argument("--kind")
    command.add_argument("--correction-message-index", type=int)
    command.add_argument("--notes", default="")
    command.add_argument("--out", type=Path, required=True)

    command = sub.add_parser("evaluate")
    command.add_argument("protocol", type=Path)
    command.add_argument("freeze", type=Path)
    command.add_argument("--sessions", type=Path, required=True)
    command.add_argument("--eligibility-ledger", type=Path, required=True)
    command.add_argument("--continuations", type=Path, required=True)
    command.add_argument("--predictions", type=Path, required=True)
    command.add_argument("--outcome-unlock", type=Path, required=True)
    command.add_argument("--outcomes", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "preregister":
            result = preregister(args.protocol, args.out)
        elif args.cmd == "freeze":
            result = freeze(
                args.protocol,
                args.sessions,
                args.continuations,
                args.outcomes,
                args.out,
            )
        elif args.cmd == "register":
            result = register(
                args.protocol, args.freeze, args.session, args.eligibility_ledger
            )
        elif args.cmd == "score":
            result = score(
                args.protocol,
                args.freeze,
                args.session,
                args.eligibility_ledger,
                args.predictions,
            )
        elif args.cmd == "unlock-outcomes":
            result = unlock_outcomes(
                args.protocol,
                args.freeze,
                args.eligibility_ledger,
                args.predictions,
                args.out,
            )
        elif args.cmd == "label":
            result = label(
                args.protocol,
                args.freeze,
                args.eligibility_ledger,
                args.predictions,
                args.outcome_unlock,
                args.continuation,
                args.out,
                session_id=args.session_id,
                outcome=args.outcome,
                kind=args.kind,
                correction_message_index=args.correction_message_index,
                notes=args.notes,
            )
        else:
            result = evaluate(
                args.protocol,
                args.freeze,
                args.sessions,
                args.eligibility_ledger,
                args.continuations,
                args.predictions,
                args.outcome_unlock,
                args.outcomes,
                args.out,
            )
    except TrialError as exc:
        parser.error(str(exc))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
