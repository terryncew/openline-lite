from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from datetime import datetime, timezone

from api_retry import ResponsesAPIError, RetryingJSONTransport
from common import (
    CAPACITY_PROBE_COUNT,
    CAPACITY_PROBE_INTERVAL_SECONDS,
    MAX_OUTPUT_TOKENS,
    PINNED_MODEL,
    REASONING_EFFORT,
)

_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def _safe_token(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _SAFE_TOKEN_RE.sub("_", text)[:128]


def _safe_nonnegative_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _safe_usage(obj: dict) -> dict:
    """Keep numeric billing/capacity telemetry only; never retain model output content."""
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        return {}
    safe: dict[str, object] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = _safe_nonnegative_int(usage.get(key))
        if value is not None:
            safe[key] = value
    input_details = usage.get("input_tokens_details")
    if isinstance(input_details, dict):
        cached = _safe_nonnegative_int(input_details.get("cached_tokens"))
        if cached is not None:
            safe["input_tokens_details"] = {"cached_tokens": cached}
    output_details = usage.get("output_tokens_details")
    if isinstance(output_details, dict):
        reasoning = _safe_nonnegative_int(output_details.get("reasoning_tokens"))
        if reasoning is not None:
            safe["output_tokens_details"] = {"reasoning_tokens": reasoning}
    return safe


def _response_id_sha256(obj: dict) -> str | None:
    response_id = obj.get("id")
    if not response_id:
        return None
    return hashlib.sha256(str(response_id).encode("utf-8")).hexdigest()


def _safe_response_context(
    obj: dict,
    *,
    attempts: int,
    retry_events: list[dict],
    completed_before_failure: int,
) -> dict:
    incomplete_details = obj.get("incomplete_details")
    incomplete_reason = None
    if isinstance(incomplete_details, dict):
        incomplete_reason = _safe_token(incomplete_details.get("reason"))
    return {
        "response_status": _safe_token(obj.get("status")),
        "incomplete_reason": incomplete_reason,
        "returned_model": _safe_token(obj.get("model")),
        "response_id_sha256": _response_id_sha256(obj),
        "usage": _safe_usage(obj),
        "requested_max_output_tokens": MAX_OUTPUT_TOKENS,
        "api_attempt_count_for_failed_probe": attempts,
        "retry_count_for_failed_probe": len(retry_events),
        "prior_retry_events": list(retry_events),
        "completed_probe_count_before_failure": completed_before_failure,
    }


class CapacityProbeResponseError(RuntimeError):
    """Fail-closed application-response error with strictly sanitized public context."""

    def __init__(self, category: str, public_detail: dict):
        super().__init__(category)
        self.category = category
        self.public_detail = public_detail


def run_capacity_probe(
    *,
    api_key: str,
    transport: RetryingJSONTransport | None = None,
    sleep_fn=time.sleep,
) -> dict:
    if not api_key:
        raise ValueError("OPENAI_API_KEY required")
    transport = transport or RetryingJSONTransport()
    attempts = retries = completed = 0
    returned_models = set()
    failure_events: list[dict] = []
    probes = []
    for index in range(1, CAPACITY_PROBE_COUNT + 1):
        # This is a non-scientific capacity check, but it deliberately uses the
        # benchmark's frozen output-token ceiling. A 16-token ceiling can be
        # consumed by hidden reasoning before visible output is produced.
        body = {
            "model": PINNED_MODEL,
            "input": f"Non-scientific sustained-capacity probe {index}/{CAPACITY_PROBE_COUNT}. Reply OK.",
            "reasoning": {"effort": REASONING_EFFORT},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
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
            attempt_events = [
                event
                for event in events
                if str(event.get("category", "")).startswith(("HTTP_", "TRANSPORT_"))
            ]
            attempts += len(attempt_events)
            retries += max(0, len(attempt_events) - 1)
            failure_events.extend(events)
            raise
        attempts += result.attempts
        retries += len(result.retry_events)
        failure_events.extend(result.retry_events)
        obj = result.obj
        context = _safe_response_context(
            obj,
            attempts=result.attempts,
            retry_events=result.retry_events,
            completed_before_failure=completed,
        )
        if obj.get("status") != "completed":
            raise CapacityProbeResponseError("CAPACITY_RESPONSE_NOT_COMPLETED", context)
        if obj.get("model") != PINNED_MODEL:
            raise CapacityProbeResponseError("CAPACITY_MODEL_MISMATCH", context)
        if not obj.get("id"):
            raise CapacityProbeResponseError("CAPACITY_RESPONSE_ID_MISSING", context)
        completed += 1
        returned_models.add(obj.get("model"))
        probes.append(
            {
                "probe_index": index,
                "started_at_utc": started,
                "completed": True,
                "attempts": result.attempts,
                "retry_events": result.retry_events,
                "response_id_sha256": context["response_id_sha256"],
                "usage": context["usage"],
            }
        )
        if index < CAPACITY_PROBE_COUNT:
            sleep_fn(CAPACITY_PROBE_INTERVAL_SECONDS)
    return {
        "probe_type": "NON_SCIENTIFIC_SUSTAINED_CAPACITY",
        "probe_count": CAPACITY_PROBE_COUNT,
        "probe_interval_seconds": CAPACITY_PROBE_INTERVAL_SECONDS,
        "requested_max_output_tokens": MAX_OUTPUT_TOKENS,
        "completed_probe_count": completed,
        "api_attempt_count": attempts,
        "retry_count": retries,
        "failure_events": failure_events,
        "returned_models": sorted(returned_models),
        "benchmark_pair_content_sent": False,
        "benchmark_model_calls": 0,
        "probes": probes,
    }
