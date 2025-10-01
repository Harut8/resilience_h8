"""Tests for Redis storage backends."""

from unittest.mock import AsyncMock

import pytest

# Skip all tests if redis is not installed
pytest.importorskip("redis")

from resilience_h8.storage.redis_backend import (
    RedisCircuitBreakerStorage,
    RedisRateLimiterStorage,
    RedisStorageBackend,
)


@pytest.fixture()
async def mock_redis():
    """Create a mock Redis client."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=1)
    mock.exists = AsyncMock(return_value=1)
    mock.expire = AsyncMock(return_value=True)
    mock.ttl = AsyncMock(return_value=100)
    mock.incrby = AsyncMock(return_value=1)
    mock.decrby = AsyncMock(return_value=0)
    mock.close = AsyncMock()
    return mock


@pytest.fixture()
async def redis_backend(mock_redis):
    """Create Redis storage backend with mock client."""
    backend = RedisStorageBackend(redis_client=mock_redis)
    yield backend
    await backend.close()


@pytest.fixture()
async def rate_limiter_storage(mock_redis):
    """Create Redis rate limiter storage with mock client."""
    storage = RedisRateLimiterStorage(redis_client=mock_redis)
    yield storage
    await storage.close()


@pytest.fixture()
async def circuit_breaker_storage(mock_redis):
    """Create Redis circuit breaker storage with mock client."""
    storage = RedisCircuitBreakerStorage(redis_client=mock_redis)
    yield storage
    await storage.close()


class TestRedisStorageBackend:
    """Test RedisStorageBackend functionality."""

    async def test_get_nonexistent_key(self, redis_backend, mock_redis):
        """Test getting a non-existent key returns None."""
        mock_redis.get.return_value = None
        result = await redis_backend.get("test_key")
        assert result is None

    async def test_get_string_value(self, redis_backend, mock_redis):
        """Test getting a string value."""
        mock_redis.get.return_value = b"test_value"
        result = await redis_backend.get("test_key")
        assert result == "test_value"

    async def test_get_json_value(self, redis_backend, mock_redis):
        """Test getting a JSON value."""
        import json

        test_data = {"key": "value", "number": 42}
        mock_redis.get.return_value = json.dumps(test_data).encode()
        result = await redis_backend.get("test_key")
        assert result == test_data

    async def test_set_value(self, redis_backend, mock_redis):
        """Test setting a value."""
        result = await redis_backend.set("test_key", "test_value")
        assert result is True
        mock_redis.set.assert_called_once()

    async def test_set_with_ttl(self, redis_backend, mock_redis):
        """Test setting a value with TTL."""
        await redis_backend.set("test_key", "test_value", ttl=60)
        call_args = mock_redis.set.call_args
        assert call_args.kwargs["ex"] == 60

    async def test_delete_existing_key(self, redis_backend, mock_redis):
        """Test deleting an existing key."""
        mock_redis.delete.return_value = 1
        result = await redis_backend.delete("test_key")
        assert result is True

    async def test_delete_nonexistent_key(self, redis_backend, mock_redis):
        """Test deleting a non-existent key."""
        mock_redis.delete.return_value = 0
        result = await redis_backend.delete("test_key")
        assert result is False

    async def test_increment(self, redis_backend, mock_redis):
        """Test incrementing a counter."""
        mock_redis.incrby.return_value = 5
        result = await redis_backend.increment("counter", amount=2)
        assert result == 5
        mock_redis.incrby.assert_called_once()

    async def test_increment_with_ttl(self, redis_backend, mock_redis):
        """Test incrementing with TTL on first increment."""
        mock_redis.incrby.return_value = 1  # First increment
        await redis_backend.increment("counter", amount=1, ttl=60)
        mock_redis.expire.assert_called_once()

    async def test_decrement(self, redis_backend, mock_redis):
        """Test decrementing a counter."""
        mock_redis.decrby.return_value = 3
        result = await redis_backend.decrement("counter", amount=2)
        assert result == 3

    async def test_exists_true(self, redis_backend, mock_redis):
        """Test key existence check when key exists."""
        mock_redis.exists.return_value = 1
        result = await redis_backend.exists("test_key")
        assert result is True

    async def test_exists_false(self, redis_backend, mock_redis):
        """Test key existence check when key doesn't exist."""
        mock_redis.exists.return_value = 0
        result = await redis_backend.exists("test_key")
        assert result is False

    async def test_expire(self, redis_backend, mock_redis):
        """Test setting expiration on a key."""
        mock_redis.expire.return_value = True
        result = await redis_backend.expire("test_key", 120)
        assert result is True

    async def test_get_ttl(self, redis_backend, mock_redis):
        """Test getting TTL for a key."""
        mock_redis.ttl.return_value = 100
        result = await redis_backend.get_ttl("test_key")
        assert result == 100

    async def test_get_ttl_no_expiration(self, redis_backend, mock_redis):
        """Test getting TTL for key with no expiration."""
        mock_redis.ttl.return_value = -1
        result = await redis_backend.get_ttl("test_key")
        assert result is None

    async def test_get_ttl_nonexistent_key(self, redis_backend, mock_redis):
        """Test getting TTL for non-existent key."""
        mock_redis.ttl.return_value = -2
        result = await redis_backend.get_ttl("test_key")
        assert result is None

    async def test_key_prefix(self, mock_redis):
        """Test that keys are prefixed correctly."""
        backend = RedisStorageBackend(redis_client=mock_redis, key_prefix="myapp:")
        await backend.set("test", "value")

        call_args = mock_redis.set.call_args
        assert call_args.args[0].startswith("myapp:")


class TestRedisRateLimiterStorage:
    """Test RedisRateLimiterStorage functionality."""

    async def test_acquire_token_success(self, rate_limiter_storage, mock_redis):
        """Test successful token acquisition."""
        # Mock Lua script execution returning success
        mock_redis.eval.return_value = [1, 9, 0]

        success, metadata = await rate_limiter_storage.acquire_token(
            key="test_limiter", limit=10, window_seconds=60.0
        )

        assert success is True
        assert metadata["remaining"] == 9
        assert metadata["retry_after"] == 0

    async def test_acquire_token_failure(self, rate_limiter_storage, mock_redis):
        """Test failed token acquisition."""
        # Mock Lua script execution returning failure
        mock_redis.eval.return_value = [0, 0, 5.5]

        success, metadata = await rate_limiter_storage.acquire_token(
            key="test_limiter", limit=10, window_seconds=60.0
        )

        assert success is False
        assert metadata["remaining"] == 0
        assert metadata["retry_after"] == 5.5

    async def test_acquire_token_fixed_window_success(self, rate_limiter_storage, mock_redis):
        """Test successful token acquisition with fixed window."""
        mock_redis.eval.return_value = [1, 5, 30]

        success, metadata = await rate_limiter_storage.acquire_token_fixed_window(
            key="test_limiter", limit=10, window_seconds=60.0
        )

        assert success is True
        assert metadata["remaining"] == 5
        assert metadata["reset_at"] == 30

    async def test_acquire_token_fixed_window_failure(self, rate_limiter_storage, mock_redis):
        """Test failed token acquisition with fixed window."""
        mock_redis.eval.return_value = [0, 0, 45]

        success, metadata = await rate_limiter_storage.acquire_token_fixed_window(
            key="test_limiter", limit=10, window_seconds=60.0
        )

        assert success is False
        assert metadata["remaining"] == 0
        assert metadata["reset_at"] == 45

    async def test_get_usage(self, rate_limiter_storage, mock_redis):
        """Test getting rate limiter usage."""
        mock_redis.hmget.return_value = [b"7.5", b"1234567890.5"]

        usage = await rate_limiter_storage.get_usage("test_limiter")

        assert usage["tokens"] == 7.5
        assert usage["last_refill"] == 1234567890.5

    async def test_get_usage_no_data(self, rate_limiter_storage, mock_redis):
        """Test getting usage when no data exists."""
        mock_redis.hmget.return_value = [None, None]

        usage = await rate_limiter_storage.get_usage("test_limiter")

        assert usage["tokens"] is None
        assert usage["last_refill"] is None

    async def test_reset(self, rate_limiter_storage, mock_redis):
        """Test resetting rate limiter."""
        mock_redis.delete.return_value = 1

        result = await rate_limiter_storage.reset("test_limiter")

        assert result is True


class TestRedisCircuitBreakerStorage:
    """Test RedisCircuitBreakerStorage functionality."""

    async def test_get_state_closed(self, circuit_breaker_storage, mock_redis):
        """Test getting closed circuit state."""
        mock_redis.hmget.return_value = [b"closed", b"0", b"0.0"]

        state = await circuit_breaker_storage.get_state("test_circuit")

        assert state["state"] == "closed"
        assert state["failure_count"] == 0
        assert state["last_failure_time"] == 0.0

    async def test_get_state_open(self, circuit_breaker_storage, mock_redis):
        """Test getting open circuit state."""
        mock_redis.hmget.return_value = [b"open", b"5", b"1234567890.5"]

        state = await circuit_breaker_storage.get_state("test_circuit")

        assert state["state"] == "open"
        assert state["failure_count"] == 5
        assert state["last_failure_time"] == 1234567890.5

    async def test_get_state_default(self, circuit_breaker_storage, mock_redis):
        """Test getting state when no data exists."""
        mock_redis.hmget.return_value = [None, None, None]

        state = await circuit_breaker_storage.get_state("test_circuit")

        assert state["state"] == "closed"
        assert state["failure_count"] == 0
        assert state["last_failure_time"] == 0.0

    async def test_record_success(self, circuit_breaker_storage, mock_redis):
        """Test recording a successful operation."""
        mock_redis.hset = AsyncMock()
        mock_redis.hmget.return_value = [b"closed", b"0", b"0.0"]

        state = await circuit_breaker_storage.record_success("test_circuit")

        assert state["state"] == "closed"
        assert state["failure_count"] == 0
        mock_redis.hset.assert_called_once()

    async def test_record_failure_opens_circuit(self, circuit_breaker_storage, mock_redis):
        """Test recording failure that opens the circuit."""
        # First call: get current state (closed with 4 failures)
        # Second call: get new state (open with 5 failures)
        mock_redis.hmget.side_effect = [
            [b"closed", b"4", b"1234567890.0"],
            [b"open", b"5", None],
        ]
        mock_redis.hset = AsyncMock()

        state = await circuit_breaker_storage.record_failure("test_circuit", 5)

        assert state["state"] == "open"
        assert state["failure_count"] == 5
        mock_redis.hset.assert_called_once()

    async def test_record_failure_half_open_to_open(self, circuit_breaker_storage, mock_redis):
        """Test recording failure in half-open state transitions to open."""
        mock_redis.hmget.side_effect = [
            [b"half_open", b"0", b"1234567890.0"],
            [b"open", b"5", None],
        ]
        mock_redis.hset = AsyncMock()

        state = await circuit_breaker_storage.record_failure("test_circuit", 5)

        assert state["state"] == "open"
        mock_redis.hset.assert_called_once()

    async def test_transition_to_half_open(self, circuit_breaker_storage, mock_redis):
        """Test transitioning circuit to half-open state."""
        mock_redis.hset = AsyncMock()

        result = await circuit_breaker_storage.transition_to_half_open("test_circuit")

        assert result is True
        mock_redis.hset.assert_called_once_with(
            "resilience:circuit:test_circuit", "state", "half_open"
        )

    async def test_reset(self, circuit_breaker_storage, mock_redis):
        """Test resetting circuit breaker."""
        mock_redis.hset = AsyncMock()

        result = await circuit_breaker_storage.reset("test_circuit")

        assert result is True
        mock_redis.hset.assert_called_once()

    async def test_key_prefix(self, mock_redis):
        """Test custom key prefix."""
        storage = RedisCircuitBreakerStorage(redis_client=mock_redis, key_prefix="myapp:cb:")
        mock_redis.hmget.return_value = [None, None, None]

        await storage.get_state("test")

        call_args = mock_redis.hmget.call_args
        assert call_args.args[0].startswith("myapp:cb:")


@pytest.mark.integration()
class TestRedisIntegration:
    """Integration tests requiring a real Redis instance."""

    async def test_real_redis_connection(self, redis_available):
        """Test connection to real Redis instance."""
        if not redis_available:
            pytest.skip("Requires --redis flag")
        from redis.asyncio import Redis

        redis_client = Redis.from_url("redis://localhost:6379/15")

        try:
            # Test basic operations
            await redis_client.set("test_key", "test_value")
            value = await redis_client.get("test_key")
            assert value == b"test_value"

            await redis_client.delete("test_key")
        finally:
            await redis_client.aclose()

    async def test_rate_limiter_integration(self, redis_available):
        """Test rate limiter with real Redis."""
        if not redis_available:
            pytest.skip("Requires --redis flag")
        from redis.asyncio import Redis

        redis_client = Redis.from_url("redis://localhost:6379/15")
        storage = RedisRateLimiterStorage(redis_client=redis_client)

        try:
            # Test token acquisition
            success1, meta1 = await storage.acquire_token(
                "test_limiter", limit=5, window_seconds=10.0
            )
            assert success1 is True
            assert meta1["remaining"] == 4

            success2, meta2 = await storage.acquire_token(
                "test_limiter", limit=5, window_seconds=10.0
            )
            assert success2 is True
            assert meta2["remaining"] == 3

            # Reset
            await storage.reset("test_limiter")

        finally:
            await redis_client.flushdb()
            await redis_client.aclose()

    async def test_circuit_breaker_integration(self, redis_available):
        """Test circuit breaker with real Redis."""
        if not redis_available:
            pytest.skip("Requires --redis flag")
        from redis.asyncio import Redis

        redis_client = Redis.from_url("redis://localhost:6379/15")
        storage = RedisCircuitBreakerStorage(redis_client=redis_client)

        try:
            # Initial state should be closed
            state = await storage.get_state("test_circuit")
            assert state["state"] == "closed"

            # Record failures until circuit opens
            for i in range(5):
                state = await storage.record_failure("test_circuit", 5)

            assert state["state"] == "open"

            # Reset circuit
            await storage.reset("test_circuit")
            state = await storage.get_state("test_circuit")
            assert state["state"] == "closed"

        finally:
            await redis_client.flushdb()
            await redis_client.aclose()
