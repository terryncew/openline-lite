from __future__ import annotations

from datetime import datetime
from pathlib import Path

from common import (
    CAPACITY_CANARY_RECEIPT_SHA256,
    GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS,
    MAX_OUTPUT_TOKENS,
    PINNED_MODEL,
    REASONING_EFFORT,
    load_json,
    sha256_file,
)


def verify_bound_canary(root: Path) -> dict:
    path = root / "CAPACITY_CANARY_PASS_BOUND.json"
    sidecar = root / "CAPACITY_CANARY_PASS_BOUND.json.sha256"
    if not path.exists() or not sidecar.exists():
        raise ValueError("bound canary receipt or sidecar missing")
    actual = sha256_file(path)
    if actual != CAPACITY_CANARY_RECEIPT_SHA256:
        raise ValueError("bound canary receipt hash mismatch")
    if CAPACITY_CANARY_RECEIPT_SHA256 not in sidecar.read_text("utf-8"):
        raise ValueError("bound canary sidecar mismatch")
    obj = load_json(path)
    if obj.get("disposition") != "CAPACITY_CANARY_PASS":
        raise ValueError("bound canary did not pass")
    if obj.get("requested_model") != PINNED_MODEL or obj.get("reasoning_effort") != REASONING_EFFORT:
        raise ValueError("bound canary model configuration mismatch")
    policy = obj.get("policy") or {}
    if policy.get("max_output_tokens_per_request") != MAX_OUTPUT_TOKENS:
        raise ValueError("bound canary max_output_tokens mismatch")
    if policy.get("minimum_start_interval_seconds") != GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS:
        raise ValueError("bound canary interval mismatch")
    if policy.get("assignment_created") is not False or policy.get("benchmark_or_scoring_run") is not False or obj.get("requests_completed") != 6:
        raise ValueError("bound canary semantic mismatch")
    rows = obj.get("rows") or []
    if len(rows) != 6:
        raise ValueError("bound canary row count mismatch")
    starts = []
    for row in rows:
        if row.get("http_status") != 200 or row.get("response_status") != "completed":
            raise ValueError("bound canary contains failed request")
        if row.get("returned_model") != PINNED_MODEL or row.get("output_text_exact_ok") is not True:
            raise ValueError("bound canary response mismatch")
        starts.append(datetime.fromisoformat(row["started_utc"]))
    gaps = [(b-a).total_seconds() for a,b in zip(starts,starts[1:])]
    # The canary scheduled with a monotonic 45-second gate. ISO wall-clock stamps show
    # sub-millisecond clock/scheduling jitter (minimum 44.999743s), so bind with a
    # 10ms timestamp tolerance while 003 itself tests/enforces the full monotonic gate.
    timestamp_tolerance_seconds = 0.01
    if gaps and min(gaps) < GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS - timestamp_tolerance_seconds:
        raise ValueError("bound canary spacing below frozen interval beyond timestamp tolerance")
    limits = sorted({int((row.get("rate_limit_headers") or {}).get("x-ratelimit-limit-tokens", 0)) for row in rows})
    return {
        "status": "PASS",
        "receipt_sha256": actual,
        "requests_completed": len(rows),
        "minimum_observed_start_gap_seconds": min(gaps) if gaps else None,
        "timestamp_tolerance_seconds": timestamp_tolerance_seconds,
        "observed_input_tokens": sorted({int((row.get("usage") or {}).get("input_tokens", 0)) for row in rows}),
        "observed_token_limits": limits,
        "authorizes_assignment_by_itself": False,
    }
