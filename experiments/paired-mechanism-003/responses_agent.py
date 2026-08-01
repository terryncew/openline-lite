from __future__ import annotations

import copy
import json
import urllib.request
from datetime import datetime, timezone

from api_retry import APIErrorDetail, ResponsesAPIError, RetryingJSONTransport
from common import MAX_OUTPUT_TOKENS, PINNED_MODEL, REASONING_EFFORT

TOOL_DEFS = [
    {
        "type": "function",
        "name": "read_file",
        "description": "read UTF-8 repository file text by relative path",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function",
        "name": "list_tree",
        "description": "list repository paths without file contents",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "max_entries": {"type": "integer", "minimum": 1, "maximum": 10000}}, "additionalProperties": False},
        "strict": False,
    },
    {
        "type": "function",
        "name": "search_text",
        "description": "literal/regex text search within repository",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}, "regex": {"type": "boolean"}, "max_matches": {"type": "integer", "minimum": 1, "maximum": 1000}}, "required": ["pattern"], "additionalProperties": False},
        "strict": False,
    },
    {
        "type": "function",
        "name": "apply_patch",
        "description": "modify repository files with an explicit patch",
        "parameters": {"type": "object", "properties": {"patch": {"type": "string"}}, "required": ["patch"], "additionalProperties": False},
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_shell",
        "description": "run a local shell command rooted in the repository; network unavailable",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"], "additionalProperties": False},
        "strict": True,
    },
]


class ResponsesClient:
    def __init__(self, api_key: str, *, transport: RetryingJSONTransport | None = None):
        if not api_key:
            raise ValueError("api_key required")
        self.api_key = api_key
        self.transport = transport or RetryingJSONTransport()
        self.api_attempt_count = 0
        self.completed_response_count = 0
        self.retry_count = 0
        self.returned_models: set[str] = set()
        self.failure_events: list[dict] = []
        self.infrastructure_wait_seconds = 0.0
        self.active_api_seconds = 0.0
        self.request_start_events: list[dict] = []
        self.response_rate_limit_headers: list[dict] = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.cached_input_tokens = 0

    @property
    def call_count(self) -> int:
        # Compatibility field: model call count means HTTP request attempts.
        return self.api_attempt_count

    def metrics(self) -> dict:
        return {
            "api_attempt_count": self.api_attempt_count,
            "completed_response_count": self.completed_response_count,
            "retry_count": self.retry_count,
            "failure_events": list(self.failure_events),
            "returned_models": sorted(self.returned_models),
            "infrastructure_wait_seconds": self.infrastructure_wait_seconds,
            "active_api_seconds": self.active_api_seconds,
            "request_start_events": list(self.request_start_events),
            "response_rate_limit_headers": list(self.response_rate_limit_headers),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": self.cached_input_tokens,
        }

    def create(self, *, instructions: str, history: list, timeout: float) -> dict:
        body = {
            "model": PINNED_MODEL,
            "instructions": instructions,
            "input": history,
            "tools": TOOL_DEFS,
            "reasoning": {"effort": REASONING_EFFORT},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "include": ["reasoning.encrypted_content"],
            "store": False,
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            result = self.transport.request(req, total_timeout_seconds=timeout)
        except ResponsesAPIError as exc:
            events = list(exc.prior_events) + [exc.public_dict()]
            attempt_events = [e for e in events if str(e.get("category", "")).startswith(("HTTP_", "TRANSPORT_"))]
            self.api_attempt_count += len(attempt_events)
            self.retry_count += max(0, len(attempt_events) - 1)
            self.failure_events.extend(events)
            tm = exc.transport_metrics
            self.infrastructure_wait_seconds += float(tm.get("infrastructure_wait_seconds", 0.0))
            self.active_api_seconds += float(tm.get("active_api_seconds", 0.0))
            self.request_start_events.extend(tm.get("request_start_events", []))
            raise
        self.api_attempt_count += result.attempts
        self.retry_count += len(result.retry_events)
        self.failure_events.extend(result.retry_events)
        self.infrastructure_wait_seconds += result.infrastructure_wait_seconds
        self.active_api_seconds += result.active_api_seconds
        self.request_start_events.extend(result.request_start_events)
        if result.response_headers:
            self.response_rate_limit_headers.append(dict(result.response_headers))
        obj = result.obj
        if obj.get("status") != "completed":
            detail = APIErrorDetail(
                category="RESPONSE_NOT_COMPLETED",
                retryable=False,
                attempt=result.attempts,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.failure_events.append(detail.public_dict())
            raise ResponsesAPIError(detail)
        returned = obj.get("model")
        if returned != PINNED_MODEL:
            detail = APIErrorDetail(
                category="MODEL_MISMATCH",
                retryable=False,
                attempt=result.attempts,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.failure_events.append(detail.public_dict())
            raise ResponsesAPIError(detail)
        if not isinstance(obj.get("output"), list):
            detail = APIErrorDetail(
                category="MISSING_OUTPUT",
                retryable=False,
                attempt=result.attempts,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
            )
            self.failure_events.append(detail.public_dict())
            raise ResponsesAPIError(detail)
        usage = obj.get("usage") or {}
        if isinstance(usage, dict):
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
            self.total_tokens += int(usage.get("total_tokens") or 0)
            details = usage.get("input_tokens_details") or {}
            if isinstance(details, dict):
                self.cached_input_tokens += int(details.get("cached_tokens") or 0)
        self.completed_response_count += 1
        self.returned_models.add(returned)
        return obj


def initial_history(task_prompt: str) -> list:
    return [{"role": "user", "content": [{"type": "input_text", "text": task_prompt}]}]


def append_response_output(history: list, response: dict) -> list:
    out = copy.deepcopy(history)
    out.extend(copy.deepcopy(response.get("output", [])))
    return out


def function_calls(response: dict) -> list[dict]:
    return [copy.deepcopy(item) for item in response.get("output", []) if item.get("type") == "function_call"]


def function_output_item(call_id: str, output: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": output}
