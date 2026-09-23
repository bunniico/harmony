"""In-memory token buckets for per-user and per-channel rate limits."""

from __future__ import annotations

import time


class TokenBuckets:
    def __init__(self, per_minute: int, clock=time.monotonic):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self.clock = clock
        self._buckets: dict[int, tuple[float, float]] = {}

    def _level(self, key: int) -> float:
        tokens, last = self._buckets.get(key, (self.capacity, self.clock()))
        return min(self.capacity, tokens + (self.clock() - last) * self.rate)

    def available(self, key: int) -> bool:
        return self._level(key) >= 1

    def take(self, key: int) -> None:
        self._buckets[key] = (self._level(key) - 1, self.clock())


class RateLimiter:
    def __init__(self, per_user_per_minute: int, per_channel_per_minute: int, clock=time.monotonic):
        self.users = TokenBuckets(per_user_per_minute, clock)
        self.channels = TokenBuckets(per_channel_per_minute, clock)

    def allow(self, user_id: int, channel_id: int) -> bool:
        """Consumes one token from both buckets only if both have one."""
        if not (self.users.available(user_id) and self.channels.available(channel_id)):
            return False
        self.users.take(user_id)
        self.channels.take(channel_id)
        return True
