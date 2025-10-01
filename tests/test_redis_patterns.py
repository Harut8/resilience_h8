"""Tests for Redis-based resilience patterns."""

import asyncio
from unittest.mock import AsyncMock

import pytest
import structlog

# Skip all tests if redis is not installed
pytest.importorskip("redis")

from resilience_h8.custom_types.resilience import CircuitState, RateLimitExceeded
from resilience_h8.resilience.redis_circuit_breaker import RedisCircuitBreaker
from resilience_h8.resilience.redis_rate_limiter import (
    RedisFixedWindowRateLimiter,
    RedisTokenBucketRateLimiter,
)
from resilience_h8.storage.redis_backend import (
    RedisCircuitBreakerStorage,
    RedisRateLimiterStorage,
)


@pytest.fixture()
def logger():
    """Create a test logger."""
    return structlog.get_logger()


@pytest.fixture()
async def mock_rate_limiter_storage():
    """Create a mock rate limiter storage."""
    storage = AsyncMock(spec=RedisRateLimiterStorage)
    storage.acquire_token = AsyncMock()
    storage.acquire_token_fixed_window = AsyncMock()
    storage.get_usage = AsyncMock()
    storage.reset = AsyncMock()
    storage.close = AsyncMock()
    return storage


@pytest.fixture()
async def mock_circuit_breaker_storage():
    """Create a mock circuit breaker storage."""
    storage = AsyncMock(spec=RedisCircuitBreakerStorage)
    storage.get_state = AsyncMock()
    storage.record_success = AsyncMock()
    storage.record_failure = AsyncMock()
    storage.transition_to_half_open = AsyncMock()
    storage.reset = AsyncMock()
    storage.close = AsyncMock()
    return storage


class TestRedisTokenBucketRateLimiter:
    """Test RedisTokenBucketRateLimiter functionality."""

    async def test_execute_success(self, mock_rate_limiter_storage):
        """Test successful execution with rate limiting."""
        mock_rate_limiter_storage.acquire_token.return_value = (
            True,
            {"remaining": 9, "limit": 10, "retry_after": 0},
        )

        limiter = RedisTokenBucketRateLimiter(
            storage=mock_rate_limiter_storage,
            requests_per_period=10,
            period_seconds=60.0,
            name="test_limiter",
        )

        async def test_operation():
            return "success"

        result = await limiter.execute(test_operation)

        assert result == "success"
        mock_rate_limiter_storage.acquire_token.assert_called_once()

    async def test_execute_rate_limit_exceeded_no_wait(self, mock_rate_limiter_storage):
        """Test rate limit exceeded with wait=False."""
        mock_rate_limiter_storage.acquire_token.return_value = (
            False,
            {"remaining": 0, "limit": 10, "retry_after": 5.0},
        )

        limiter = RedisTokenBucketRateLimiter(
            storage=mock_rate_limiter_storage,
            requests_per_period=10,
            period_seconds=60.0,
            name="test_limiter",
        )

        async def test_operation():
            return "success"

        with pytest.raises(RateLimitExceeded) as exc_info:
            await limiter.execute(test_operation, wait=False)

        assert exc_info.value.retry_after == 5.0

    async def test_execute_rate_limit_with_wait(self, mock_rate_limiter_storage):
        """Test rate limit with wait enabled."""
        # First call fails, second call succeeds
        mock_rate_limiter_storage.acquire_token.side_effect = [
            (False, {"remaining": 0, "limit": 10, "retry_after": 0.1}),
            (True, {"remaining": 9, "limit": 10, "retry_after": 0}),
        ]

        limiter = RedisTokenBucketRateLimiter(
            storage=mock_rate_limiter_storage,
            requests_per_period=10,
            period_seconds=60.0,
            name="test_limiter",
        )

        async def test_operation():
            return "success"

        result = await limiter.execute(test_operation, wait=True)

        assert result == "success"
        assert mock_rate_limiter_storage.acquire_token.call_count == 2

    async def test_execute_timeout_exceeded(self, mock_rate_limiter_storage):
        """Test timeout when waiting for rate limit."""
        mock_rate_limiter_storage.acquire_token.return_value = (
            False,
            {"remaining": 0, "limit": 10, "retry_after": 10.0},
        )

        limiter = RedisTokenBucketRateLimiter(
            storage=mock_rate_limiter_storage,
            requests_per_period=10,
            period_seconds=60.0,
            name="test_limiter",
        )

        async def test_operation():
            return "success"

        with pytest.raises(asyncio.TimeoutError):
            await limiter.execute(test_operation, wait=True, timeout=1.0)

    async def test_get_current_capacity(self, mock_rate_limiter_storage):
        """Test getting current capacity (placeholder)."""
        limiter = RedisTokenBucketRateLimiter(
            storage=mock_rate_limiter_storage,
            requests_per_period=10,
            period_seconds=60.0,
            name="test_limiter",
        )

        capacity = limiter.get_current_capacity()

        assert capacity["limit"] == 10
        assert isinstance(capacity["remaining"], int)

    async def test_get_current_capacity_async(self, mock_rate_limiter_storage):
        """Test getting current capacity async."""
        mock_rate_limiter_storage.get_usage.return_value = {
            "tokens": 7.5,
            "last_refill": 1234567890.0,
        }

        limiter = RedisTokenBucketRateLimiter(
            storage=mock_rate_limiter_storage,
            requests_per_period=10,
            period_seconds=60.0,
            name="test_limiter",
        )

        capacity = await limiter.get_current_capacity_async()

        assert capacity["remaining"] == 7
        assert capacity["limit"] == 10
        assert "reset_at" in capacity


class TestRedisFixedWindowRateLimiter:
    """Test RedisFixedWindowRateLimiter functionality."""

    async def test_execute_success(self, mock_rate_limiter_storage):
        """Test successful execution with fixed window rate limiting."""
        mock_rate_limiter_storage.acquire_token_fixed_window.return_value = (
            True,
            {"remaining": 9, "limit": 10, "reset_at": 30.0},
        )

        limiter = RedisFixedWindowRateLimiter(
            storage=mock_rate_limiter_storage,
            requests_per_period=10,
            period_seconds=60.0,
            name="test_limiter",
        )

        async def test_operation():
            return "success"

        result = await limiter.execute(test_operation)

        assert result == "success"
        mock_rate_limiter_storage.acquire_token_fixed_window.assert_called_once()

    async def test_execute_rate_limit_exceeded(self, mock_rate_limiter_storage):
        """Test rate limit exceeded with fixed window."""
        mock_rate_limiter_storage.acquire_token_fixed_window.return_value = (
            False,
            {"remaining": 0, "limit": 10, "reset_at": 45.0, "retry_after": 45.0},
        )

        limiter = RedisFixedWindowRateLimiter(
            storage=mock_rate_limiter_storage,
            requests_per_period=10,
            period_seconds=60.0,
            name="test_limiter",
        )

        async def test_operation():
            return "success"

        with pytest.raises(RateLimitExceeded):
            await limiter.execute(test_operation, wait=False)

    async def test_execute_with_wait(self, mock_rate_limiter_storage):
        """Test waiting for window reset."""
        mock_rate_limiter_storage.acquire_token_fixed_window.side_effect = [
            (False, {"remaining": 0, "limit": 10, "reset_at": 0.1}),
            (True, {"remaining": 9, "limit": 10, "reset_at": 59.9}),
        ]

        limiter = RedisFixedWindowRateLimiter(
            storage=mock_rate_limiter_storage,
            requests_per_period=10,
            period_seconds=60.0,
            name="test_limiter",
        )

        async def test_operation():
            return "success"

        result = await limiter.execute(test_operation, wait=True)

        assert result == "success"
        assert mock_rate_limiter_storage.acquire_token_fixed_window.call_count == 2


class TestRedisCircuitBreaker:
    """Test RedisCircuitBreaker functionality."""

    async def test_execute_closed_circuit_success(self, mock_circuit_breaker_storage, logger):
        """Test successful execution with closed circuit."""
        mock_circuit_breaker_storage.get_state.return_value = {
            "state": "closed",
            "failure_count": 0,
            "last_failure_time": 0.0,
        }

        breaker = RedisCircuitBreaker(
            name="test_breaker",
            storage=mock_circuit_breaker_storage,
            failure_threshold=5,
            recovery_timeout=30.0,
            logger=logger,
        )

        async def test_operation():
            return "success"

        result = await breaker.execute(test_operation)

        assert result == "success"
        # In closed state, record_success is not called (only in half_open state)
        mock_circuit_breaker_storage.record_success.assert_not_called()

    async def test_execute_closed_circuit_failure(self, mock_circuit_breaker_storage, logger):
        """Test failed execution with closed circuit."""
        mock_circuit_breaker_storage.get_state.return_value = {
            "state": "closed",
            "failure_count": 0,
            "last_failure_time": 0.0,
        }
        mock_circuit_breaker_storage.record_failure.return_value = {
            "state": "closed",
            "failure_count": 1,
            "last_failure_time": 1234567890.0,
        }

        breaker = RedisCircuitBreaker(
            name="test_breaker",
            storage=mock_circuit_breaker_storage,
            failure_threshold=5,
            recovery_timeout=30.0,
            logger=logger,
        )

        async def test_operation():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await breaker.execute(test_operation)

        mock_circuit_breaker_storage.record_failure.assert_called_once()

    async def test_execute_open_circuit_no_fallback(self, mock_circuit_breaker_storage, logger):
        """Test execution with open circuit and no fallback."""
        import time

        # Need to return state for both _check_state_transition and the open circuit check
        # Use current time so circuit stays open (recovery_timeout not yet passed)
        mock_circuit_breaker_storage.get_state.return_value = {
            "state": "open",
            "failure_count": 5,
            "last_failure_time": time.time(),  # Current time so circuit stays open
        }

        breaker = RedisCircuitBreaker(
            name="test_breaker",
            storage=mock_circuit_breaker_storage,
            failure_threshold=5,
            recovery_timeout=30.0,
            logger=logger,
        )

        async def test_operation():
            return "success"

        with pytest.raises(RuntimeError) as exc_info:
            await breaker.execute(test_operation)

        assert "open" in str(exc_info.value).lower()

    async def test_execute_open_circuit_with_fallback(self, mock_circuit_breaker_storage, logger):
        """Test execution with open circuit and fallback."""
        import time

        # Circuit is open, so fallback should be used
        # Use current time so circuit stays open (recovery_timeout not yet passed)
        mock_circuit_breaker_storage.get_state.return_value = {
            "state": "open",
            "failure_count": 5,
            "last_failure_time": time.time(),  # Current time so circuit stays open
        }

        breaker = RedisCircuitBreaker(
            name="test_breaker",
            storage=mock_circuit_breaker_storage,
            failure_threshold=5,
            recovery_timeout=30.0,
            logger=logger,
        )

        async def test_operation():
            return "success"

        async def fallback_operation():
            return "fallback"

        result = await breaker.execute(test_operation, fallback=fallback_operation)

        assert result == "fallback"

    async def test_half_open_success_closes_circuit(self, mock_circuit_breaker_storage, logger):
        """Test successful execution in half-open state closes circuit."""
        mock_circuit_breaker_storage.get_state.return_value = {
            "state": "half_open",
            "failure_count": 0,
            "last_failure_time": 1234567890.0,
        }
        mock_circuit_breaker_storage.record_success.return_value = {
            "state": "closed",
            "failure_count": 0,
            "last_failure_time": 0.0,
        }

        breaker = RedisCircuitBreaker(
            name="test_breaker",
            storage=mock_circuit_breaker_storage,
            failure_threshold=5,
            recovery_timeout=30.0,
            logger=logger,
        )

        async def test_operation():
            return "success"

        result = await breaker.execute(test_operation)

        assert result == "success"
        mock_circuit_breaker_storage.record_success.assert_called_once()

    async def test_half_open_failure_reopens_circuit(self, mock_circuit_breaker_storage, logger):
        """Test failure in half-open state reopens circuit."""
        mock_circuit_breaker_storage.get_state.return_value = {
            "state": "half_open",
            "failure_count": 0,
            "last_failure_time": 1234567890.0,
        }
        mock_circuit_breaker_storage.record_failure.return_value = {
            "state": "open",
            "failure_count": 5,
            "last_failure_time": 1234567900.0,
        }

        breaker = RedisCircuitBreaker(
            name="test_breaker",
            storage=mock_circuit_breaker_storage,
            failure_threshold=5,
            recovery_timeout=30.0,
            logger=logger,
        )

        async def test_operation():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await breaker.execute(test_operation)

        mock_circuit_breaker_storage.record_failure.assert_called_once()

    async def test_get_state_async(self, mock_circuit_breaker_storage, logger):
        """Test getting circuit state asynchronously."""
        mock_circuit_breaker_storage.get_state.return_value = {
            "state": "open",
            "failure_count": 5,
            "last_failure_time": 1234567890.0,
        }

        breaker = RedisCircuitBreaker(
            name="test_breaker",
            storage=mock_circuit_breaker_storage,
            failure_threshold=5,
            recovery_timeout=30.0,
            logger=logger,
        )

        state = await breaker.get_state_async()

        assert state == CircuitState.OPEN

    async def test_reset(self, mock_circuit_breaker_storage, logger):
        """Test resetting circuit breaker."""
        mock_circuit_breaker_storage.get_state.side_effect = [
            {"state": "open", "failure_count": 5, "last_failure_time": 1234567890.0},
            {"state": "closed", "failure_count": 0, "last_failure_time": 0.0},
        ]
        mock_circuit_breaker_storage.reset.return_value = True

        breaker = RedisCircuitBreaker(
            name="test_breaker",
            storage=mock_circuit_breaker_storage,
            failure_threshold=5,
            recovery_timeout=30.0,
            logger=logger,
        )

        await breaker.reset()

        mock_circuit_breaker_storage.reset.assert_called_once()

    async def test_circuit_break_decorator(self, mock_circuit_breaker_storage, logger):
        """Test circuit breaker as a decorator."""
        mock_circuit_breaker_storage.get_state.return_value = {
            "state": "closed",
            "failure_count": 0,
            "last_failure_time": 0.0,
        }
        mock_circuit_breaker_storage.record_success.return_value = {
            "state": "closed",
            "failure_count": 0,
            "last_failure_time": 0.0,
        }

        breaker = RedisCircuitBreaker(
            name="test_breaker",
            storage=mock_circuit_breaker_storage,
            failure_threshold=5,
            recovery_timeout=30.0,
            logger=logger,
        )

        @breaker.circuit_break()
        async def decorated_function(value: str):
            return f"result: {value}"

        result = await decorated_function("test")

        assert result == "result: test"


@pytest.mark.integration()
class TestRedisIntegrationPatterns:
    """Integration tests with real Redis for resilience patterns."""

    async def test_rate_limiter_full_flow(self, redis_available, logger):
        """Test complete rate limiter flow with Redis."""
        if not redis_available:
            pytest.skip("Requires --redis flag")
        from redis.asyncio import Redis

        redis_client = Redis.from_url("redis://localhost:6379/15")
        storage = RedisRateLimiterStorage(redis_client=redis_client)

        limiter = RedisTokenBucketRateLimiter(
            storage=storage,
            requests_per_period=3,
            period_seconds=1.0,
            name="test_integration",
        )

        try:
            # First 3 requests should succeed
            for i in range(3):
                result = await limiter.execute(lambda: f"request_{i}")
                assert result == f"request_{i}"

            # 4th request should fail or wait
            with pytest.raises(RateLimitExceeded):
                await limiter.execute(lambda: "request_4", wait=False)

        finally:
            await redis_client.flushdb()
            await redis_client.aclose()

    async def test_circuit_breaker_full_flow(self, redis_available, logger):
        """Test complete circuit breaker flow with Redis."""
        if not redis_available:
            pytest.skip("Requires --redis flag")
        from redis.asyncio import Redis

        redis_client = Redis.from_url("redis://localhost:6379/15")
        storage = RedisCircuitBreakerStorage(redis_client=redis_client)

        breaker = RedisCircuitBreaker(
            name="test_integration",
            storage=storage,
            failure_threshold=3,
            recovery_timeout=1.0,
            logger=logger,
        )

        try:
            # Record failures until circuit opens
            for i in range(3):
                try:
                    await breaker.execute(lambda: 1 / 0)  # Force error
                except ZeroDivisionError:
                    pass

            # Circuit should be open now
            state = await breaker.get_state_async()
            assert state == CircuitState.OPEN

            # Execution should fail
            with pytest.raises(RuntimeError):
                await breaker.execute(lambda: "success")

        finally:
            await redis_client.flushdb()
            await redis_client.aclose()
