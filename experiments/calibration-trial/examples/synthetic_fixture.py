"""Mechanism-only synthetic fixture for exercising the preregistration machinery.

Nothing emitted by this file is evidence that COLE metrics predict real handoff outcomes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from calibration_trial.trial import (
    evaluate,
    freeze,
    label,
    preregister,
    register,
    score,
    unlock_outcomes,
)


def h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def graph(version: int, changed: int) -> dict:
    claims = []
    evidence = []
    relations = []
    for i in range(6):
        value = version if i < changed else 0
        claims.append(
            {
                "id": f"c{i}",
                "content_hash": h(f"claim-{i}-v{value}"),
                "material": True,
            }
        )
        evidence.append(
            {
                "id": f"e{i}",
                "content_hash": h(f"evidence-{i}-v{value}"),
                "observed": True,
            }
        )
        relations.append({"src": f"e{i}", "dst": f"c{i}", "relation_type": "supports"})
    return {"claims": claims, "evidence": evidence, "relations": relations}


def session(session_id: str, handoff_at: str, outcome: int, index: int) -> dict:
    changed = 5 if outcome else (1 if index % 4 == 0 else 0)
    dialogue_pairs = 3 + (index % 4)
    transcript = []
    for i in range(dialogue_pairs):
        transcript.append(
            {
                "index": len(transcript),
                "role": "user",
                "text": f"work item {i}",
                "tool_name": None,
            }
        )
        transcript.append(
            {
                "index": len(transcript),
                "role": "assistant",
                "text": f"completed step {i}",
                "tool_name": None,
            }
        )
        if i < index % 3:
            transcript.append(
                {
                    "index": len(transcript),
                    "role": "tool",
                    "text": "{}",
                    "tool_name": "fixture_tool",
                }
            )
    transcript.append(
        {
            "index": len(transcript),
            "role": "assistant",
            "text": "handoff ready with bounded evidence",
            "tool_name": None,
        }
    )
    base = 500_000 + (index % 3) * 5_000
    signal = [base, base + 8_000, base + 2_000, base + 9_000, base + 3_000, base + 10_000]
    return {
        "schema": "openline.calibration-trial.session.v2",
        "session_id": session_id,
        "handoff_at_utc": handoff_at,
        "transcript": transcript,
        "measurement_input": {
            "algorithm_id": "cole-portable-core-2.1-draft",
            "signal_points_micros": signal,
            "previous_graph": graph(0, 0),
            "current_graph": graph(index + 1, changed),
        },
    }


def continuation(
    session_id: str,
    outcome: int,
    started_at_utc: str = "2026-02-01T00:00:30Z",
) -> dict:
    events = []
    turns = 2 if outcome else 8
    for i in range(turns):
        events.append(
            {
                "index": len(events),
                "role": "assistant",
                "text": f"continuation answer {i}",
                "tool_name": None,
            }
        )
        if outcome and i == 1:
            events.append(
                {
                    "index": len(events),
                    "role": "user",
                    "text": "Correction: the frozen constraint was dropped.",
                    "tool_name": None,
                }
            )
            break
        events.append(
            {
                "index": len(events),
                "role": "user",
                "text": f"continue {i}",
                "tool_name": None,
            }
        )
    return {
        "schema": "openline.calibration-trial.continuation.v1",
        "session_id": session_id,
        "started_at_utc": started_at_utc,
        "events": events,
        "ended": not outcome,
    }


def calibration_outcome_row(
    session_id: str, outcome: int, labeled_at: str, cont: dict
) -> dict:
    raw = json.dumps(
        cont, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    correction_index = 3 if outcome else None
    return {
        "schema": "openline.calibration-trial.outcome.v2",
        "phase": "calibration",
        "session_id": session_id,
        "outcome": outcome,
        "kind": "constraint" if outcome else None,
        "correction_message_index": correction_index,
        "continuation_sha256": hashlib.sha256(raw).hexdigest(),
        "window_observed_assistant_turns": 2 if outcome else 8,
        "continuation_ended": cont["ended"],
        "labeled_at_utc": labeled_at,
        "outcome_unlock_sha256": None,
        "notes": "synthetic mechanism-only calibration label",
    }


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def build(
    root: Path,
    protocol_path: Path,
    prospective_outcomes: list[int] | None = None,
) -> dict:
    cal = root / "data" / "calibration"
    test = root / "data" / "test"
    cal_cont = root / "continuations" / "calibration"
    test_cont = root / "continuations" / "test"
    labels = root / "labels"
    builddir = root / "build"
    for directory in (cal, test, cal_cont, test_cont, labels, builddir):
        directory.mkdir(parents=True, exist_ok=True)

    preregister(
        protocol_path,
        builddir / "preregistration.json",
        at="2025-12-31T23:00:00Z",
    )
    cal_out = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(20):
        y = i % 2
        t = start + timedelta(days=i)
        sid = f"cal-{i + 1:03d}"
        (cal / f"{sid}.json").write_text(
            json.dumps(session(sid, iso(t), y, i), indent=2, sort_keys=True) + "\n"
        )
        cont = continuation(sid, y, iso(t + timedelta(seconds=30)))
        (cal_cont / f"{sid}.json").write_text(
            json.dumps(cont, indent=2, sort_keys=True) + "\n"
        )
        cal_out.append(
            calibration_outcome_row(sid, y, iso(t + timedelta(hours=1)), cont)
        )
    (labels / "calibration.jsonl").write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in cal_out
        )
    )
    freeze(
        protocol_path,
        cal,
        cal_cont,
        labels / "calibration.jsonl",
        builddir / "freeze.json",
        at="2026-01-25T00:00:00Z",
    )

    test_start = datetime(2026, 1, 26, tzinfo=timezone.utc)
    eligibility = builddir / "eligibility.jsonl"
    test_rows: list[tuple[str, int, datetime, Path]] = []
    outcomes = prospective_outcomes if prospective_outcomes is not None else [i % 2 for i in range(20)]
    if len(outcomes) != 20 or any(value not in (0, 1) for value in outcomes):
        raise ValueError("prospective_outcomes must contain exactly 20 binary labels")
    for i, y in enumerate(outcomes):
        t = test_start + timedelta(days=i)
        sid = f"test-{i + 1:03d}"
        path = test / f"{sid}.json"
        path.write_text(
            json.dumps(session(sid, iso(t), y, 100 + i), indent=2, sort_keys=True)
            + "\n"
        )
        register(
            protocol_path,
            builddir / "freeze.json",
            path,
            eligibility,
            at=iso(t + timedelta(seconds=30)),
        )
        score(
            protocol_path,
            builddir / "freeze.json",
            path,
            eligibility,
            builddir / "predictions.jsonl",
            at=iso(t + timedelta(seconds=60)),
        )
        cont = continuation(sid, y, iso(t + timedelta(seconds=120)))
        cpath = test_cont / f"{sid}.json"
        cpath.write_text(json.dumps(cont, indent=2, sort_keys=True) + "\n")
        test_rows.append((sid, y, t, cpath))

    unlock_time = test_start + timedelta(days=20)
    unlock_outcomes(
        protocol_path,
        builddir / "freeze.json",
        eligibility,
        builddir / "predictions.jsonl",
        builddir / "outcome-unlock.json",
        at=iso(unlock_time),
    )

    for i, (sid, y, _t, cpath) in enumerate(test_rows):
        label(
            protocol_path,
            builddir / "freeze.json",
            eligibility,
            builddir / "predictions.jsonl",
            builddir / "outcome-unlock.json",
            cpath,
            labels / "test.jsonl",
            session_id=sid,
            outcome=y,
            kind="constraint" if y else None,
            correction_message_index=3 if y else None,
            notes="synthetic mechanism-only prospective label",
            at=iso(unlock_time + timedelta(seconds=i + 1)),
        )

    return evaluate(
        protocol_path,
        builddir / "freeze.json",
        test,
        eligibility,
        test_cont,
        builddir / "predictions.jsonl",
        builddir / "outcome-unlock.json",
        labels / "test.jsonl",
        builddir / "evaluation.json",
        at=iso(unlock_time + timedelta(hours=1)),
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("build/synthetic"))
    parser.add_argument("--protocol", type=Path, default=Path("protocol.yaml"))
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.protocol), indent=2, sort_keys=True))
