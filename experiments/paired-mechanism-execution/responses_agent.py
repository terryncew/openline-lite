from __future__ import annotations

import copy
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from common import MAX_OUTPUT_TOKENS, PINNED_MODEL, REASONING_EFFORT


TOOL_DEFS = [
    {
        "type": "function",
        "name": "read_file",
        "description": "read UTF-8 repository file text by relative path",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "list_tree",
        "description": "list repository paths without file contents",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 10000},
            },
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "search_text",
        "description": "literal/regex text search within repository",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "pattern": {"type": "string"},
                "regex": {"type": "boolean"},
                "max_matches": {"type": "integer", "minimum": 1, "maximum": 1000},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        "strict": False,
    },
    {
        "type": "function",
        "name": "apply_patch",
        "description": "modify repository files with an explicit patch",
        "parameters": {
            "type": "object",
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_shell",
        "description": "run a local shell command rooted in the repository; network unavailable",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class ResponsesAPIError(RuntimeError):
    pass


class ResponsesClient:
    def __init__(self, api_key: str, *, timeout: int = 300):
        if not api_key:
            raise ValueError("api_key required")
        self.api_key = api_key
        self.timeout = timeout
        self.call_count = 0
        self.returned_models: set[str] = set()

    def create(self, *, instructions: str, history: list, timeout: int | None = None) -> dict:
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
        self.call_count += 1
        try:
            with urllib.request.urlopen(req, timeout=(timeout if timeout is not None else self.timeout)) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:2000]
            raise ResponsesAPIError(f"HTTP_{e.code}:{detail}") from None
        except Exception as e:
            raise ResponsesAPIError(f"TRANSPORT:{type(e).__name__}:{e}") from None
        if status != 200:
            raise ResponsesAPIError(f"HTTP_{status}")
        try:
            obj = json.loads(raw)
        except Exception as e:
            raise ResponsesAPIError(f"NON_JSON:{type(e).__name__}") from None
        if obj.get("status") != "completed":
            raise ResponsesAPIError(f"RESPONSE_NOT_COMPLETED:{obj.get('status')}:{obj.get('incomplete_details')}")
        returned = obj.get("model")
        if returned != PINNED_MODEL:
            raise ResponsesAPIError(f"MODEL_MISMATCH:{returned}")
        self.returned_models.add(returned)
        if not isinstance(obj.get("output"), list):
            raise ResponsesAPIError("MISSING_OUTPUT")
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


def has_final_message(response: dict) -> bool:
    return any(item.get("type") == "message" and item.get("status") == "completed" for item in response.get("output", []))
