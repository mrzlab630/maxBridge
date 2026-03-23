"""Tests for rate limiter."""

import time

from maxbridge.ipc.rate_limiter import RateLimiter, TokenBucket


class TestTokenBucket:
    def test_burst_allowed(self):
        b = TokenBucket(rate=10.0, burst=5)
        for _ in range(5):
            assert b.consume()
        assert not b.consume()

    def test_refill(self):
        b = TokenBucket(rate=1000.0, burst=1)
        assert b.consume()
        assert not b.consume()
        time.sleep(0.01)
        assert b.consume()


class TestRateLimiter:
    def test_default_limits(self):
        rl = RateLimiter({})
        for _ in range(60):
            assert rl.allow("c1", "ping")
        assert not rl.allow("c1", "ping")

    def test_custom_limits(self):
        rl = RateLimiter({"send_message": {"rate": 2, "burst": 2}})
        assert rl.allow("c1", "send_message")
        assert rl.allow("c1", "send_message")
        assert not rl.allow("c1", "send_message")

    def test_per_client_isolation(self):
        rl = RateLimiter({"default": {"rate": 1, "burst": 1}})
        assert rl.allow("c1", "x")
        assert rl.allow("c2", "x")
        assert not rl.allow("c1", "x")

    def test_remove_client(self):
        rl = RateLimiter({"default": {"rate": 1, "burst": 1}})
        rl.allow("c1", "x")
        assert not rl.allow("c1", "x")
        rl.remove_client("c1")
        assert rl.allow("c1", "x")
