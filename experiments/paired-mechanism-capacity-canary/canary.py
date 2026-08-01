from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

API_URL = "https://api.openai.com/v1/responses"
PINNED_MODEL = "gpt-5.5-2026-04-23"
REASONING_EFFORT = "medium"
REQUEST_COUNT = 6
MIN_INTERVAL_SECONDS = 45
MAX_OUTPUT_TOKENS = 256
# ASCII payload chosen to approximate a substantial agent-context envelope while
# keeping the canary cheap. This is a rate/capacity test, not a benchmark task.
PAYLOAD_BYTES = 80_000
SCHEMA = "openline.capacity-canary.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_bytes(obj: object) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_error(exc: urllib.error.HTTPError) -> dict:
    try:
        raw = exc.read()
        obj = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        obj = {}
    err = obj.get("error") if isinstance(obj, dict) else None
    err = err if isinstance(err, dict) else {}
    return {
        "http_status": int(exc.code),
        "openai_error_type": str(err.get("type"))[:128] if err.get("type") else None,
        "openai_error_code": str(err.get("code"))[:128] if err.get("code") else None,
        "retry_after": exc.headers.get("Retry-After") if exc.headers else None,
    }


def make_payload(index: int) -> dict:
    marker = f"CANARY-{index:02d}-"
    repeated = (marker + ("x" * 127) + "\n")
    text = (repeated * ((PAYLOAD_BYTES // len(repeated)) + 1))[:PAYLOAD_BYTES]
    return {
        "model": PINNED_MODEL,
        "reasoning": {"effort": REASONING_EFFORT},
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Capacity canary only. Do not solve a task. Reply exactly OK.\n" + text,
                    }
                ],
            }
        ],
    }


def run_canary(
    *,
    api_key: str,
    out: Path,
    urlopen_fn: Callable = urllib.request.urlopen,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    started = utc_now()
    rows: list[dict] = []
    last_start: float | None = None
    disposition = "CAPACITY_CANARY_PASS"

    for index in range(1, REQUEST_COUNT + 1):
        now = monotonic_fn()
        if last_start is not None:
            wait = max(0.0, MIN_INTERVAL_SECONDS - (now - last_start))
            if wait:
                sleep_fn(wait)
        last_start = monotonic_fn()
        payload = make_payload(index)
        body = canonical_bytes(payload)
        req = urllib.request.Request(
            API_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        row = {
            "index": index,
            "started_utc": utc_now(),
            "request_body_sha256": sha256_bytes(body),
            "input_payload_bytes": PAYLOAD_BYTES,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        }
        try:
            with urlopen_fn(req, timeout=180) as resp:
                raw = resp.read()
                status = int(resp.status)
            obj = json.loads(raw.decode("utf-8"))
            usage = obj.get("usage") if isinstance(obj, dict) else None
            usage = usage if isinstance(usage, dict) else {}
            row.update(
                {
                    "http_status": status,
                    "response_status": obj.get("status") if isinstance(obj, dict) else None,
                    "returned_model": obj.get("model") if isinstance(obj, dict) else None,
                    "usage": {
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                    },
                }
            )
            if status != 200 or row["response_status"] != "completed" or row["returned_model"] != PINNED_MODEL:
                disposition = "CAPACITY_CANARY_BLOCKED"
                row["failure_category"] = "NON_COMPLETED_OR_MODEL_MISMATCH"
                rows.append(row)
                break
        except urllib.error.HTTPError as exc:
            detail = _safe_error(exc)
            row.update(detail)
            if detail["http_status"] == 429:
                row["failure_category"] = "HTTP_429_STOP_FIRST_FAILURE"
            else:
                row["failure_category"] = "HTTP_ERROR_STOP_FIRST_FAILURE"
            rows.append(row)
            disposition = "CAPACITY_CANARY_BLOCKED"
            break
        except Exception as exc:
            row.update({"failure_category": "TRANSPORT_OR_PARSE_FAILURE", "exception_type": type(exc).__name__})
            rows.append(row)
            disposition = "CAPACITY_CANARY_BLOCKED"
            break
        rows.append(row)

    receipt = {
        "schema": SCHEMA,
        "disposition": disposition,
        "started_utc": started,
        "finished_utc": utc_now(),
        "policy": {
            "purpose": "LOW_COST_RATE_CAPACITY_DISCRIMINATOR_ONLY",
            "benchmark_or_scoring_run": False,
            "assignment_created": False,
            "unblinded": False,
            "request_count_cap": REQUEST_COUNT,
            "stop_on_first_failure": True,
            "retries": 0,
            "minimum_start_interval_seconds": MIN_INTERVAL_SECONDS,
            "input_payload_bytes_per_request": PAYLOAD_BYTES,
            "max_output_tokens_per_request": MAX_OUTPUT_TOKENS,
            "dollar_cap_guaranteed": False,
            "note": "Token bounded; provider pricing and cached-token treatment determine actual dollars.",
        },
        "requested_model": PINNED_MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "requests_started": len(rows),
        "requests_completed": sum(1 for r in rows if r.get("response_status") == "completed"),
        "failure_count": sum(1 for r in rows if r.get("failure_category")),
        "rows": rows,
        "next_use": (
            "MAY_DESIGN_SLOWER_003_CONTROLLER_NOT_CLEARED_FOR_FULL_RUN"
            if disposition == "CAPACITY_CANARY_PASS"
            else "DO_NOT_RUN_003_AT_THIS_ENVELOPE"
        ),
    }
    data = canonical_bytes(receipt)
    out.write_bytes(data)
    out.with_suffix(out.suffix + ".sha256").write_text(f"{sha256_bytes(data)}  {out.name}\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise SystemExit("OPENAI_API_KEY is required")
    receipt = run_canary(api_key=key, out=Path(args.out))
    print(json.dumps({"disposition": receipt["disposition"], "requests_started": receipt["requests_started"]}, indent=2))
    if receipt["disposition"] != "CAPACITY_CANARY_PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
