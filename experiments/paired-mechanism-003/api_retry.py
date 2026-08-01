from __future__ import annotations

import email.utils
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Mapping

from common import (
    API_RETRY_AFTER_CAP_SECONDS,
    API_RETRY_BACKOFF_SECONDS,
    API_RETRY_MAX_ATTEMPTS,
    GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS,
    PAIR_JOB_INITIAL_PACING_GUARD_SECONDS,
    PERMANENT_429_CODES,
    PERMANENT_429_TYPES,
    RETRYABLE_HTTP_STATUSES,
)

_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
RATE_LIMIT_HEADER_NAMES = (
    "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens", "retry-after",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _TOKEN_RE.sub("_", text)[:128]


def sanitize_rate_limit_headers(headers: Mapping | None) -> dict[str, str]:
    if not headers:
        return {}
    lowered = {str(k).lower(): str(v).strip()[:128] for k, v in headers.items()}
    return {name: lowered[name] for name in RATE_LIMIT_HEADER_NAMES if name in lowered}


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


class GlobalRequestPacer:
    """Serial request-start gate. All attempts, including retries, pass through it."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = GLOBAL_MIN_REQUEST_START_INTERVAL_SECONDS,
        initial_delay_seconds: float = PAIR_JOB_INITIAL_PACING_GUARD_SECONDS,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
        utc_now_fn: Callable[[], str] = _utc_now,
    ):
        self.min_interval_seconds = float(min_interval_seconds)
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.utc_now_fn = utc_now_fn
        self.next_allowed = self.monotonic_fn() + float(initial_delay_seconds)
        self.start_events: list[dict] = []
        self.total_wait_seconds = 0.0

    def wait_for_start(self, *, attempt: int, not_before_monotonic: float | None = None) -> dict:
        now = self.monotonic_fn()
        target = self.next_allowed
        if not_before_monotonic is not None:
            target = max(target, float(not_before_monotonic))
        wait = max(0.0, target - now)
        if wait:
            self.sleep_fn(wait)
        started = self.monotonic_fn()
        self.next_allowed = started + self.min_interval_seconds
        self.total_wait_seconds += wait
        event = {
            "attempt": attempt,
            "wait_seconds": wait,
            "started_at_utc": self.utc_now_fn(),
            "minimum_interval_seconds": self.min_interval_seconds,
        }
        self.start_events.append(event)
        return event


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
    rate_limit_headers: dict[str, str] = field(default_factory=dict)

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
            "rate_limit_headers": dict(self.rate_limit_headers),
        }


class ResponsesAPIError(RuntimeError):
    def __init__(
        self,
        detail: APIErrorDetail,
        *,
        prior_events: list[dict] | None = None,
        transport_metrics: dict | None = None,
    ):
        super().__init__(detail.category)
        self.detail = detail
        self.prior_events = list(prior_events or [])
        self.transport_metrics = dict(transport_metrics or {})

    def public_dict(self) -> dict:
        return self.detail.public_dict()


def _extract_openai_error(body: bytes) -> tuple[str | None, str | None]:
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
    headers = sanitize_rate_limit_headers(exc.headers)
    retry_header = headers.get("retry-after")
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
        retry_after_header=retry_header,
        retry_after_seconds=retry_seconds,
        rate_limit_headers=headers,
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
        category=f"TRANSPORT_{cat}", retryable=retryable, attempt=attempt,
        timestamp_utc=_utc_now(), transport_category=cat,
    )


def backoff_seconds(failed_attempt: int, retry_after_seconds: float | None) -> float:
    scheduled = float(API_RETRY_BACKOFF_SECONDS[failed_attempt - 1])
    server = 0.0 if retry_after_seconds is None else max(0.0, float(retry_after_seconds))
    return min(max(scheduled, server), float(API_RETRY_AFTER_CAP_SECONDS))


@dataclass
class JSONTransportResult:
    obj: dict
    attempts: int
    retry_events: list[dict]
    request_start_events: list[dict]
    infrastructure_wait_seconds: float
    active_api_seconds: float
    response_headers: dict[str, str]


class RetryingJSONTransport:
    def __init__(
        self,
        *,
        urlopen_fn: Callable = urllib.request.urlopen,
        pacer: GlobalRequestPacer | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ):
        self.urlopen_fn = urlopen_fn
        self.monotonic_fn = monotonic_fn
        self.pacer = pacer or GlobalRequestPacer(monotonic_fn=monotonic_fn)

    def _metrics(self, *, active: float, starts: list[dict], scheduled_backoffs: list[float]) -> dict:
        return {
            "infrastructure_wait_seconds": sum(float(e.get("wait_seconds", 0.0)) for e in starts),
            "active_api_seconds": active,
            "request_start_events": list(starts),
            "scheduled_retry_backoff_seconds": list(scheduled_backoffs),
        }

    def request(self, req: urllib.request.Request, *, total_timeout_seconds: float) -> JSONTransportResult:
        retry_events: list[dict] = []
        starts: list[dict] = []
        scheduled_backoffs: list[float] = []
        active_elapsed = 0.0
        retry_not_before = None
        for attempt in range(1, API_RETRY_MAX_ATTEMPTS + 1):
            remaining = float(total_timeout_seconds) - active_elapsed
            if remaining <= 0:
                detail = APIErrorDetail(
                    category="RETRY_ACTIVE_DEADLINE_EXHAUSTED", retryable=False, attempt=attempt,
                    timestamp_utc=_utc_now(), transport_category="ACTIVE_DEADLINE",
                )
                raise ResponsesAPIError(detail, prior_events=retry_events, transport_metrics=self._metrics(active=active_elapsed, starts=starts, scheduled_backoffs=scheduled_backoffs))

            starts.append(self.pacer.wait_for_start(attempt=attempt, not_before_monotonic=retry_not_before))
            active_started = self.monotonic_fn()
            try:
                with self.urlopen_fn(req, timeout=max(0.001, remaining)) as resp:
                    raw = resp.read()
                    status = int(resp.status)
                    response_headers = sanitize_rate_limit_headers(resp.headers)
            except urllib.error.HTTPError as exc:
                active_elapsed += max(0.0, self.monotonic_fn() - active_started)
                detail = classify_http_error(exc, attempt)
            except Exception as exc:
                active_elapsed += max(0.0, self.monotonic_fn() - active_started)
                detail = classify_transport_error(exc, attempt)
            else:
                active_elapsed += max(0.0, self.monotonic_fn() - active_started)
                metrics = self._metrics(active=active_elapsed, starts=starts, scheduled_backoffs=scheduled_backoffs)
                if status != 200:
                    detail = APIErrorDetail(
                        category=f"HTTP_{status}_PERMANENT", retryable=False, attempt=attempt,
                        timestamp_utc=_utc_now(), http_status=status, rate_limit_headers=response_headers,
                    )
                    raise ResponsesAPIError(detail, prior_events=retry_events, transport_metrics=metrics)
                try:
                    obj = json.loads(raw)
                except Exception:
                    detail = APIErrorDetail(category="NON_JSON_RESPONSE", retryable=False, attempt=attempt, timestamp_utc=_utc_now(), http_status=status, rate_limit_headers=response_headers)
                    raise ResponsesAPIError(detail, prior_events=retry_events, transport_metrics=metrics)
                if not isinstance(obj, dict):
                    detail = APIErrorDetail(category="NON_OBJECT_JSON_RESPONSE", retryable=False, attempt=attempt, timestamp_utc=_utc_now(), http_status=status, rate_limit_headers=response_headers)
                    raise ResponsesAPIError(detail, prior_events=retry_events, transport_metrics=metrics)
                return JSONTransportResult(
                    obj=obj, attempts=attempt, retry_events=retry_events,
                    request_start_events=starts,
                    infrastructure_wait_seconds=metrics["infrastructure_wait_seconds"],
                    active_api_seconds=active_elapsed,
                    response_headers=response_headers,
                )

            event = detail.public_dict()
            retry_events.append(event)
            metrics = self._metrics(active=active_elapsed, starts=starts, scheduled_backoffs=scheduled_backoffs)
            if not detail.retryable or attempt >= API_RETRY_MAX_ATTEMPTS:
                raise ResponsesAPIError(detail, prior_events=retry_events[:-1], transport_metrics=metrics)
            delay = backoff_seconds(attempt, detail.retry_after_seconds)
            scheduled_backoffs.append(delay)
            retry_not_before = self.monotonic_fn() + delay
        raise AssertionError("unreachable")
