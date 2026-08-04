from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict
from typing import Any

import numpy as np

from .action_parser import observation_is_error, parse_action
from .csd import rolling_csd_summary
from .curvature import curvature_series_micros, to_micros
from .nebius import BlindRecord

PREFIXES = (0.25, 0.50, 0.75, 1.00)
CATEGORIES = ("search", "read", "edit", "exec", "verify", "submit", "other")
CHANNELS = (
    "error_rate",
    "edit_revisit_rate",
    "verify_failure_rate",
    "exploration_breadth_rate",
    "action_switch_rate",
)


def _action_observations(record: BlindRecord) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for row in record.trajectory:
        if row["role"] == "ai":
            parsed = parse_action(row["text"])
            pending = {**asdict(parsed), "ai_chars": len(row["text"]), "observation": "", "observation_chars": 0, "is_error": False}
            events.append(pending)
        elif row["role"] == "user" and pending is not None and not pending["observation"]:
            pending["observation"] = row["text"]
            pending["observation_chars"] = len(row["text"])
            pending["is_error"] = observation_is_error(row["text"])
            pending = None
    return events


def _trajectory_channels(events: list[dict[str, Any]]) -> dict[str, list[float]]:
    errors = 0
    edits = 0
    repeated_edits = 0
    verifies = 0
    verify_failures = 0
    unique_targets: set[str] = set()
    edited_targets: Counter[str] = Counter()
    switches = 0
    previous_category: str | None = None
    output = {name: [] for name in CHANNELS}
    for index, event in enumerate(events, start=1):
        category = event["category"]
        errors += int(event["is_error"])
        if category == "edit":
            edits += 1
            targets = event["targets"]
            if any(edited_targets[target] > 0 for target in targets):
                repeated_edits += 1
            for target in targets:
                edited_targets[target] += 1
        if category == "verify":
            verifies += 1
            verify_failures += int(event["is_error"])
        unique_targets.update(event["targets"])
        if previous_category is not None and category != previous_category:
            switches += 1
        previous_category = category
        output["error_rate"].append(errors / index)
        output["edit_revisit_rate"].append(repeated_edits / edits if edits else 0.0)
        output["verify_failure_rate"].append(verify_failures / verifies if verifies else 0.0)
        output["exploration_breadth_rate"].append(len(unique_targets) / index)
        output["action_switch_rate"].append(switches / (index - 1) if index > 1 else 0.0)
    return output


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _extract_from_events(record: BlindRecord, prefix: float, all_events: list[dict[str, Any]]) -> dict[str, Any]:
    if not all_events:
        events: list[dict[str, Any]] = []
    else:
        count = max(1, math.ceil(len(all_events) * prefix))
        events = all_events[:count]
    categories = Counter(event["category"] for event in events)
    errors = sum(int(event["is_error"]) for event in events)
    commands = [event["command_hash"] for event in events if event["command"]]
    repeated_commands = len(commands) - len(set(commands))
    targets = [target for event in events for target in event["targets"]]
    unique_targets = set(targets)
    edited_targets = [target for event in events if event["category"] == "edit" for target in event["targets"]]
    repeated_edit_targets = len(edited_targets) - len(set(edited_targets))
    action_switches = sum(1 for left, right in zip(events, events[1:]) if left["category"] != right["category"])
    row: dict[str, Any] = {
        "trajectory_id": record.trajectory_id,
        "instance_id": record.instance_id,
        "repository": record.repository,
        "model_name": record.model_name,
        "prefix": prefix,
        "action_count": len(events),
        "ai_chars": sum(event["ai_chars"] for event in events),
        "observation_chars": sum(event["observation_chars"] for event in events),
        "observable_chars": sum(event["ai_chars"] + event["observation_chars"] for event in events),
        "error_count": errors,
        "error_rate": _safe_ratio(errors, len(events)),
        "repeated_command_count": repeated_commands,
        "repeated_command_rate": _safe_ratio(repeated_commands, len(commands)),
        "unique_target_count": len(unique_targets),
        "target_touch_count": len(targets),
        "target_revisit_rate": _safe_ratio(len(targets) - len(unique_targets), len(targets)),
        "repeated_edit_target_count": repeated_edit_targets,
        "action_switch_count": action_switches,
        "action_switch_rate": _safe_ratio(action_switches, max(0, len(events) - 1)),
        "explicit_action_parse_rate": _safe_ratio(sum(event["parse_confidence"] != "unavailable" for event in events), len(events)),
        "logprob_entropy_status": "UNAVAILABLE_NOT_IN_PUBLIC_SCHEMA",
        "latency_status": "UNAVAILABLE_NOT_IN_PUBLIC_SCHEMA",
        "synchrony_status": "UNAVAILABLE_NO_FROZEN_PHASE_MAPPING",
        "phi_star_status": "UNAVAILABLE_NO_SUBSTRATE_CALIBRATION",
        "vkd_status": "UNAVAILABLE_NO_EXTERNAL_KAPPA_STAR_AND_PHI_FLOOR",
        "frozen_003_mapper_status": "UNAVAILABLE_NO_VERIFIED_DEPENDENCY_EDGE_OR_STATE_FIELD_MAPPING",
    }
    for category in CATEGORIES:
        row[f"{category}_count"] = categories[category]
        row[f"{category}_rate"] = _safe_ratio(categories[category], len(events))

    channels = _trajectory_channels(events)
    for channel_name, values in channels.items():
        signal = [to_micros(value) for value in values]
        kappa = curvature_series_micros(signal)
        row[f"{channel_name}_kappa_max"] = max(kappa) if kappa else math.nan
        row[f"{channel_name}_kappa_last"] = kappa[-1] if kappa else math.nan
        for window in (10, 32):
            for key, value in rolling_csd_summary(kappa, window).items():
                row[f"{channel_name}_{key}"] = value
    return row


def extract_prefix(record: BlindRecord, prefix: float) -> dict[str, Any]:
    return _extract_from_events(record, prefix, _action_observations(record))


def extract_all_prefixes(record: BlindRecord) -> list[dict[str, Any]]:
    # Parse each large trajectory once, then reuse the condition-blind event list.
    events = _action_observations(record)
    return [_extract_from_events(record, prefix, events) for prefix in PREFIXES]
