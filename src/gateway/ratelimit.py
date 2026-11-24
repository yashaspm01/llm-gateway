"""Token-bucket rate limiter, kept in memory."""

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Each key owns a bucket holding up to `per_minute` tokens.

    The bucket refills continuously at per_minute/60 tokens per second.
    A request takes one token, or is told how long to wait.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock  # injectable so tests can control time
        self._buckets: dict[int, _Bucket] = {}

    def check(self, key: int, per_minute: int) -> float:
        """Take one token. Returns 0.0 if allowed, otherwise seconds until one is available."""
        now = self._clock()
        capacity = float(per_minute)
        rate = per_minute / 60.0

        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=capacity, updated=now)
            self._buckets[key] = bucket
        else:
            bucket.tokens = min(capacity, bucket.tokens + (now - bucket.updated) * rate)
            bucket.updated = now

        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return 0.0
        return (1.0 - bucket.tokens) / rate
