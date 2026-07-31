from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone

from api_retry import APIErrorDetail, ResponsesAPIError, RetryingJSONTransport
from common import (
    CAPACITY_PROBE_COUNT,
    CAPACITY_PROBE_INTERVAL_SECONDS,
    PINNED_MODEL,
    REASONING_EFFORT,
)


def run_capacity_probe(*, api_key: str, transport: RetryingJSONTransport | None = None, sleep_fn=time.sleep) -> dict:
    if not api_key:
        raise ValueError("OPENAI_API_KEY required")
    transport = transport or RetryingJSONTransport()
    attempts = retries = completed = 0
    returned_models = set()
    failure_events: list[dict] = []
    probes = []
    for index in range(1, CAPACITY_PROBE_COUNT + 1):
        body = {
            "model": PINNED_MODEL,
            "input": f"Non-scientific sustained-capacity probe {index}/{CAPACITY_PROBE_COUNT}. Reply OK.",
            "reasoning": {"effort": REASONING_EFFORT},
            "max_output_tokens": 16,
            "store": False,
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = transport.request(req, total_timeout_seconds=120)
        except ResponsesAPIError as exc:
            events = list(exc.prior_events) + [exc.public_dict()]
            attempt_events = [e for e in events if str(e.get("category", "")).startswith(("HTTP_", "TRANSPORT_"))]
            attempts += len(attempt_events)
            retries += max(0, len(attempt_events) - 1)
            failure_events.extend(events)
            raise
        attempts += result.attempts
        retries += len(result.retry_events)
        failure_events.extend(result.retry_events)
        obj = result.obj
        if obj.get("status") != "completed":
            raise ResponsesAPIError(APIErrorDetail(category="CAPACITY_RESPONSE_NOT_COMPLETED", retryable=False, attempt=result.attempts, timestamp_utc=datetime.now(timezone.utc).isoformat()))
        if obj.get("model") != PINNED_MODEL:
            raise ResponsesAPIError(APIErrorDetail(category="CAPACITY_MODEL_MISMATCH", retryable=False, attempt=result.attempts, timestamp_utc=datetime.now(timezone.utc).isoformat()))
        if not obj.get("id"):
            raise ResponsesAPIError(APIErrorDetail(category="CAPACITY_RESPONSE_ID_MISSING", retryable=False, attempt=result.attempts, timestamp_utc=datetime.now(timezone.utc).isoformat()))
        completed += 1
        returned_models.add(obj.get("model"))
        probes.append({
            "probe_index": index,
            "started_at_utc": started,
            "completed": True,
            "attempts": result.attempts,
            "retry_events": result.retry_events,
            "response_id_sha256": __import__("hashlib").sha256(str(obj.get("id")).encode()).hexdigest(),
        })
        if index < CAPACITY_PROBE_COUNT:
            sleep_fn(CAPACITY_PROBE_INTERVAL_SECONDS)
    return {
        "probe_type": "NON_SCIENTIFIC_SUSTAINED_CAPACITY",
        "probe_count": CAPACITY_PROBE_COUNT,
        "probe_interval_seconds": CAPACITY_PROBE_INTERVAL_SECONDS,
        "completed_probe_count": completed,
        "api_attempt_count": attempts,
        "retry_count": retries,
        "failure_events": failure_events,
        "returned_models": sorted(returned_models),
        "benchmark_pair_content_sent": False,
        "benchmark_model_calls": 0,
        "probes": probes,
    }
