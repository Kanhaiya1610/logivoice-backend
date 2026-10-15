"""Small process-local rate limiter for sensitive API operations.

This protects a single backend process without adding a service dependency. A
multi-instance production deployment should also enforce limits at the edge or
replace this store with a shared Redis-backed limiter.
"""

from __future__ import annotations

from collections import defaultdict, deque
from functools import wraps
from threading import Lock
from time import monotonic

from flask import request

from .errors import APIError


class ProcessRateLimiter:
    def __init__(self) -> None:
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: tuple[str, str], limit: int, window_seconds: int) -> bool:
        now = monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


limiter = ProcessRateLimiter()


def rate_limit(name: str, limit: int, window_seconds: int):
    """Reject requests above a bounded process-local threshold."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            # ProxyFix has already normalized the trusted proxy address into
            # request.remote_addr; do not let callers spoof the bucket key.
            client = request.remote_addr or "unknown"
            if not limiter.allow((name, client), limit, window_seconds):
                raise APIError(
                    "rate_limited",
                    "Too many requests. Please retry later.",
                    429,
                    {"retry_after_seconds": window_seconds},
                )
            return view(*args, **kwargs)

        return wrapped

    return decorator
