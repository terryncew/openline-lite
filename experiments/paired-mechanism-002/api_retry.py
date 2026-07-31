from __future__ import annotations

import email.utils
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from common import (
    API_RETRY_AFTER_CAP_SECONDS,
    API_RETRY_BACKOFF_SECONDS,
    API_RETRY_MAX_ATTEMPTS,
    PERMANENT_429_CODES,
    PERMANENT_429_TYPES,
    RETRYABLE_HTTP_STATUSES,
)

_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _TOKEN_RE.sub("_", text)[:128]


def _retry_after_seconds(header: str | None, now_epoch: float | None = None) -> float | None:
    if not header:
        return None
    text = header.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now_epoch = time.time() if now_epoch is None else now_epoch
        return max(0.0, dt.timestamp() - now_epoch)
    except Exception:
        return None


@dataclass(frozen=True)
class APIErrorDetail:
    category: str
    retryable: bool
    attempt: int
    timestamp_utc: str
    http_status: int | None = None
    openai_error_type: str | None = None
    openai_error_code: str | None = None
    retry_after_header: str | None = None
    retry_after_seconds: float | None = None
    transport_category: str | None = None

    def public_dict(self) -> dict:
        return {
            "category": self.category,
            "retryable": self.retryable,
            "attempt": self.attempt,
            "timestamp_utc": self.timestamp_utc,
            "http_status": self.http_status,
            "openai_error_type": self.openai_error_type,
            "openai_error_code": self.openai_error_code,
            "retry_after_header": self.retry_after_header,
            "retry_after_seconds": self.retry_after_seconds,
            "transport_category": self.transport_category,
        }


class ResponsesAPIError(RuntimeError):
    def __init__(self, detail: APIErrorDetail, *, prior_events: list[dict] | None = None):
        super().__init__(detail.category)
        self.detail = detail
        self.prior_events = list(prior_events or [])

    def public_dict(self) -> dict:
        return self.detail.public_dict()


def _extract_openai_error(body: bytes) -> tuple[str | None, str | None]:
    # Parse only type/code. Never preserve message/body/response content.
    try:
        obj = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return None, None
    err = obj.get("error") if isinstance(obj, dict) else None
    if not isinstance(err, dict):
        return None, None
    return _token(err.get("type")), _token(err.get("code"))


def classify_http_error(exc: urllib.error.HTTPError, attempt: int) -> APIErrorDetail:
    try:
        body = exc.read()
    except Exception:
        body = b""
    err_type, err_code = _extract_openai_error(body)
    retry_header = exc.headers.get("Retry-After") if exc.headers else None
    retry_header_safe = retry_header.strip()[:128] if retry_header else None
    retry_seconds = _retry_after_seconds(retry_header)
    status = int(exc.code)
    permanent_429 = status == 429 and (
        (err_code or "").lower() in PERMANENT_429_CODES
        or (err_type or "").lower() in PERMANENT_429_TYPES
    )
    retryable = status in RETRYABLE_HTTP_STATUSES and not permanent_429
    if permanent_429:
        category = "HTTP_429_QUOTA_OR_SPEND_PERMANENT"
    elif status == 429:
        category = "HTTP_429_RATE_LIMIT_TRANSIENT"
    elif status in {500, 502, 503, 504}:
        category = f"HTTP_{status}_SERVER_TRANSIENT"
    else:
        category = f"HTTP_{status}_PERMANENT"
    return APIErrorDetail(
        category=category,
        retryable=retryable,
        attempt=attempt,
        timestamp_utc=_utc_now(),
        http_status=status,
        openai_error_type=err_type,
        openai_error_code=err_code,
        retry_after_header=retry_header_safe,
        retry_after_seconds=retry_seconds,
    )


def classify_transport_error(exc: Exception, attempt: int) -> APIErrorDetail:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        cat, retryable = "TIMEOUT", True
    elif isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        cat, retryable = "CONNECTION_RESET", True
    elif isinstance(reason, socket.gaierror):
        cat, retryable = "DNS_TEMPORARY", True
    elif isinstance(reason, ConnectionRefusedError):
        cat, retryable = "CONNECTION_REFUSED", True
    elif isinstance(reason, ssl.SSLError):
        cat, retryable = "TLS_ERROR", False
    else:
        cat, retryable = f"OTHER_TRANSPORT_{type(reason).__name__}", False
    return APIErrorDetail(
        category=f"TRANSPORT_{cat}",
        retryable=retryable,
        attempt=attempt,
        timestamp_utc=_utc_now(),
        transport_category=cat,
    )


def backoff_seconds(failed_attempt: int, retry_after_seconds: float | None) -> float:
    # failed_attempt is 1-based. There is no jitter: same failure metadata -> same delay.
    scheduled = float(API_RETRY_BACKOFF_SECONDS[failed_attempt - 1])
    server = 0.0 if retry_after_seconds is None else max(0.0, float(retry_after_seconds))
    return min(max(scheduled, server), float(API_RETRY_AFTER_CAP_SECONDS))


@dataclass
class JSONTransportResult:
    obj: dict
    attempts: int
    retry_events: list[dict]


class RetryingJSONTransport:
    def __init__(
        self,
        *,
        urlopen_fn: Callable = urllib.request.urlopen,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ):
        self.urlopen_fn = urlopen_fn
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn

    def request(self, req: urllib.request.Request, *, total_timeout_seconds: float) -> JSONTransportResult:
        start = self.monotonic_fn()
        retry_events: list[dict] = []
        for attempt in range(1, API_RETRY_MAX_ATTEMPTS + 1):
            remaining = float(total_timeout_seconds) - (self.monotonic_fn() - start)
            if remaining <= 0:
                detail = APIErrorDetail(
                    category="RETRY_DEADLINE_EXHAUSTED",
                    retryable=False,
                    attempt=attempt,
                    timestamp_utc=_utc_now(),
                    transport_category="DEADLINE",
                )
                raise ResponsesAPIError(detail, prior_events=retry_events)
            try:
                with self.urlopen_fn(req, timeout=max(0.001, remaining)) as resp:
                    raw = resp.read()
                    status = int(resp.status)
            except urllib.error.HTTPError as exc:
                detail = classify_http_error(exc, attempt)
            except Exception as exc:
                detail = classify_transport_error(exc, attempt)
            else:
                if status != 200:
                    detail = APIErrorDetail(
                        category=f"HTTP_{status}_PERMANENT",
                        retryable=False,
                        attempt=attempt,
                        timestamp_utc=_utc_now(),
                        http_status=status,
                    )
                    raise ResponsesAPIError(detail, prior_events=retry_events)
                try:
                    obj = json.loads(raw)
                except Exception:
                    detail = APIErrorDetail(
                        category="NON_JSON_RESPONSE",
                        retryable=False,
                        attempt=attempt,
                        timestamp_utc=_utc_now(),
                        http_status=status,
                    )
                    raise ResponsesAPIError(detail, prior_events=retry_events)
                if not isinstance(obj, dict):
                    detail = APIErrorDetail(
                        category="NON_OBJECT_JSON_RESPONSE",
                        retryable=False,
                        attempt=attempt,
                        timestamp_utc=_utc_now(),
                        http_status=status,
                    )
                    raise ResponsesAPIError(detail, prior_events=retry_events)
                return JSONTransportResult(obj=obj, attempts=attempt, retry_events=retry_events)

            event = detail.public_dict()
            retry_events.append(event)
            if not detail.retryable or attempt >= API_RETRY_MAX_ATTEMPTS:
                raise ResponsesAPIError(detail, prior_events=retry_events[:-1])
            delay = backoff_seconds(attempt, detail.retry_after_seconds)
            remaining = float(total_timeout_seconds) - (self.monotonic_fn() - start)
            if delay >= remaining:
                exhausted = APIErrorDetail(
                    category="RETRY_DEADLINE_EXHAUSTED",
                    retryable=False,
                    attempt=attempt,
                    timestamp_utc=_utc_now(),
                    transport_category="DEADLINE",
                )
                raise ResponsesAPIError(exhausted, prior_events=retry_events)
            self.sleep_fn(delay)
        raise AssertionError("unreachable")
