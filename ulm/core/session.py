"""One HTTP session class for the whole project.

Replaces the old RnetSession (requests-compatible shim over the `rnet`
Rust client) and the TimeoutSession/TimeoutHTTPAdapter stack with a
single small `Session` based on vanilla `requests`. It provides:

- a default timeout on every request (connect, read) so nothing hangs
- bounded exponential backoff with Retry-After support and jitter
- per-request `max_retries=0/1/...` override
- `proxies` dict with the `{"all": uri}` convenience the old core used
- `cookies` compatible with the `requests` cookie jar API services use

TLS client-hello fingerprint impersonation (curl_cffi / rnet) is NOT
built in. Services that need a fingerprint can swap the session's
transport with `session.mount` on their own — that keeps the core light.
"""
from __future__ import annotations

import logging
import math
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

import requests
from requests import HTTPError, Response

__all__ = ("Session", "DEFAULT_HEADERS", "CONNECT_TIMEOUT", "READ_TIMEOUT")

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60
DEFAULT_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

DEFAULT_HEADERS = {
    "User-Agent": "unshackle: Lightweight Movie, TV, and Music Archival Software",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate",  # no brotli: Brotli is an extra dep
}

RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_RETRIES = 5
BACKOFF_FACTOR = 0.3
MAX_BACKOFF = 30.0


class Session(requests.Session):
    """`requests.Session` with sane defaults; drop-in for service code."""

    def __init__(self, *, max_retries: int = MAX_RETRIES, **kwargs: Any) -> None:
        super().__init__()
        self.max_retries = max_retries
        self.headers.update(DEFAULT_HEADERS)
        self.verify = kwargs.get("verify", True)

    def _sleep(self, attempt: int, response: Optional[Response] = None) -> float:
        """Seconds to sleep before attempt + 1. Honors Retry-After."""
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                wait: Optional[float] = None
                try:
                    wait = float(retry_after)
                except ValueError:
                    try:
                        tgt = parsedate_to_datetime(retry_after)
                        if tgt.tzinfo is None:
                            tgt = tgt.replace(tzinfo=timezone.utc)
                        wait = (tgt - datetime.now(timezone.utc)).total_seconds()
                    except Exception:
                        wait = None
                if wait is not None and math.isfinite(wait):
                    return max(0.0, min(wait, MAX_BACKOFF))
        if attempt <= 0:
            return 0.0
        base = BACKOFF_FACTOR * (2 ** (attempt - 1))
        return min(base + random.uniform(0, base * 0.1), MAX_BACKOFF)

    def request(  # type: ignore[override]
        self,
        method: str,
        url: str,
        *,
        max_retries: Optional[int] = None,
        timeout: Any = DEFAULT_TIMEOUT,
        proxies: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> Response:
        """Send a request with retries on 429/5xx and connection errors.

        `max_retries=0` disables the retry loop entirely (one attempt,
        whatever happens). The default comes from the session
        constructor.
        """
        retries = self.max_retries if max_retries is None else max(0, int(max_retries))
        merged_proxies = dict(self.proxies)
        if proxies:
            merged_proxies.update(proxies)

        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                response = super().request(method=method, url=url, timeout=timeout, proxies=merged_proxies or None, **kwargs)
            except requests.RequestException as e:
                last_error = e
                if attempt >= retries or method.upper() not in ("GET", "HEAD", "OPTIONS"):
                    raise
            else:
                if response.status_code not in RETRY_STATUS or attempt >= retries:
                    return response
                last_error = HTTPError(f"status {response.status_code}", response=response)

            sleep = self._sleep(attempt, getattr(last_error, "response", None))
            if sleep > 0:
                time.sleep(sleep)
        raise last_error if last_error else RuntimeError(f"unreachable: request loop failed for {method} {url}")

    def get(self, url: str, **kwargs: Any) -> Response:  # type: ignore[override]
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:  # type: ignore[override]
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:  # type: ignore[override]
        return self.request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Response:  # type: ignore[override]
        return self.request("DELETE", url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> Response:  # type: ignore[override]
        return self.request("HEAD", url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> Response:  # type: ignore[override]
        return self.request("OPTIONS", url, **kwargs)
