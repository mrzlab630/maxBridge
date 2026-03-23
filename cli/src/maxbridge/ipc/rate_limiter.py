"""Token bucket rate limiter for IPC methods."""

import time
from typing import Any


class TokenBucket:
    """Simple token bucket algorithm."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()

    def consume(self) -> bool:
        """Try to consume 1 token. Returns True if allowed."""
        now = time.monotonic()
        self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
        self._last = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class RateLimiter:
    """Per-client, per-method rate limiter."""

    _DEFAULT_RATE = 30.0
    _DEFAULT_BURST = 60

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._buckets: dict[str, dict[str, TokenBucket]] = {}

    def allow(self, client_id: str, method: str) -> bool:
        """Check if a request is allowed. Returns False if rate-limited."""
        if client_id not in self._buckets:
            self._buckets[client_id] = {}
        client_buckets = self._buckets[client_id]

        if method not in client_buckets:
            rate, burst = self._get_limits(method)
            client_buckets[method] = TokenBucket(rate, burst)

        return client_buckets[method].consume()

    def remove_client(self, client_id: str) -> None:
        """Clean up state for a disconnected client."""
        self._buckets.pop(client_id, None)

    def _get_limits(self, method: str) -> tuple[float, int]:
        """Get rate/burst config for a method."""
        method_cfg = self._config.get(method, self._config.get("default", {}))
        if isinstance(method_cfg, dict):
            return (
                float(method_cfg.get("rate", self._DEFAULT_RATE)),
                int(method_cfg.get("burst", self._DEFAULT_BURST)),
            )
        return self._DEFAULT_RATE, self._DEFAULT_BURST
