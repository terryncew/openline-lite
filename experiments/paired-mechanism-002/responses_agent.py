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
            # The final failed attempt is not in prior_events; count it explicitly.
            events = list(exc.prior_events) + [exc.public_dict()]
            attempt_events = [e for e in events if str(e.get("category", "")).startswith(("HTTP_", "TRANSPORT_"))]
            self.api_attempt_count += len(attempt_events)
            self.retry_count += max(0, len(attempt_events) - 1)
            self.failure_events.extend(events)
            raise
        self.api_attempt_count += result.attempts
        self.retry_count += len(result.retry_events)
        self.failure_events.extend(result.retry_events)
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
