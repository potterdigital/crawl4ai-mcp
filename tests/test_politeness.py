"""Unit tests for politeness delay parameter on batch tools.

Tests cover:
- crawl_many, deep_crawl, crawl_sitemap accept `delay` parameter
- RateLimiter constructs with fixed delay tuple
- SemaphoreDispatcher accepts rate_limiter kwarg
"""

import inspect

from crawl4ai.async_dispatcher import RateLimiter, SemaphoreDispatcher

from crawl4ai_mcp.server import crawl_many, crawl_sitemap, deep_crawl


# ---------------------------------------------------------------------------
# Parameter acceptance
# ---------------------------------------------------------------------------


class TestDelayParameterAccepted:
    def test_crawl_many_accepts_delay(self) -> None:
        """crawl_many has a `delay` parameter with default 0."""
        sig = inspect.signature(crawl_many)
        assert "delay" in sig.parameters
        assert sig.parameters["delay"].default == 0

    def test_deep_crawl_accepts_delay(self) -> None:
        """deep_crawl has a `delay` parameter with default 0."""
        sig = inspect.signature(deep_crawl)
        assert "delay" in sig.parameters
        assert sig.parameters["delay"].default == 0

    def test_crawl_sitemap_accepts_delay(self) -> None:
        """crawl_sitemap has a `delay` parameter with default 0."""
        sig = inspect.signature(crawl_sitemap)
        assert "delay" in sig.parameters
        assert sig.parameters["delay"].default == 0


# ---------------------------------------------------------------------------
# RateLimiter construction
# ---------------------------------------------------------------------------


class TestRateLimiterConstruction:
    def test_rate_limiter_fixed_delay(self) -> None:
        """RateLimiter(base_delay=(1.0, 1.0)) constructs without error."""
        rl = RateLimiter(base_delay=(1.0, 1.0))
        assert rl is not None
        assert rl.base_delay == (1.0, 1.0)

    def test_rate_limiter_zero_delay(self) -> None:
        """RateLimiter with zero delay is valid (no-op pacing)."""
        rl = RateLimiter(base_delay=(0, 0))
        assert rl is not None


# ---------------------------------------------------------------------------
# SemaphoreDispatcher accepts rate_limiter
# ---------------------------------------------------------------------------


class TestDispatcherRateLimiter:
    def test_dispatcher_accepts_rate_limiter(self) -> None:
        """SemaphoreDispatcher(rate_limiter=...) accepts a RateLimiter."""
        rl = RateLimiter(base_delay=(0.5, 0.5))
        dispatcher = SemaphoreDispatcher(semaphore_count=5, rate_limiter=rl)
        assert dispatcher is not None

    def test_dispatcher_none_rate_limiter(self) -> None:
        """SemaphoreDispatcher(rate_limiter=None) is valid (default)."""
        dispatcher = SemaphoreDispatcher(semaphore_count=5, rate_limiter=None)
        assert dispatcher is not None
