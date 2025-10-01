"""Redis-based storage backend implementations.

This module provides Redis implementations of storage interfaces
for distributed resilience patterns across multiple service instances.
"""

import json
import time
from typing import Any

try:
    import redis.asyncio as redis
    from redis.asyncio import Redis
except ImportError as e:
    raise ImportError(
        "redis package is required for Redis storage backend. "
        "Install with: pip install resilience_h8[redis]"
    ) from e

from ..interfaces.storage import (
    CircuitBreakerStorage,
    RateLimiterStorage,
    StorageBackend,
)


class RedisStorageBackend(StorageBackend):
    """Redis implementation of storage backend."""

    def __init__(
        self,
        redis_client: Redis | None = None,
        url: str = "redis://localhost:6379/0",
        key_prefix: str = "resilience:",
        **redis_kwargs: Any,
    ):
        """Initialize Redis storage backend.

        Args:
            redis_client: Existing Redis client (optional)
            url: Redis connection URL
            key_prefix: Prefix for all keys
            **redis_kwargs: Additional Redis client arguments
        """
        self._key_prefix = key_prefix
        self._owns_client = redis_client is None

        if redis_client:
            self._client = redis_client
        else:
            self._client = redis.from_url(url, **redis_kwargs)

    def _make_key(self, key: str) -> str:
        """Create prefixed key."""
        return f"{self._key_prefix}{key}"

    async def get(self, key: str) -> Any | None:
        """Get a value from Redis."""
        value = await self._client.get(self._make_key(key))
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value.decode() if isinstance(value, bytes) else value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Set a value in Redis."""
        serialized = json.dumps(value) if not isinstance(value, str | bytes) else value
        result = await self._client.set(
            self._make_key(key),
            serialized,
            ex=ttl,
        )
        return bool(result)

    async def delete(self, key: str) -> bool:
        """Delete a value from Redis."""
        result = await self._client.delete(self._make_key(key))
        return bool(result > 0)

    async def increment(self, key: str, amount: int = 1, ttl: int | None = None) -> int:
        """Atomically increment a counter in Redis."""
        redis_key = self._make_key(key)
        result = await self._client.incrby(redis_key, amount)

        # Set TTL if provided and key was just created
        if ttl and result == amount:
            await self._client.expire(redis_key, ttl)

        return int(result)

    async def decrement(self, key: str, amount: int = 1) -> int:
        """Atomically decrement a counter in Redis."""
        result = await self._client.decrby(self._make_key(key), amount)
        return int(result)

    async def exists(self, key: str) -> bool:
        """Check if a key exists in Redis."""
        result = await self._client.exists(self._make_key(key))
        return bool(result > 0)

    async def expire(self, key: str, ttl: int) -> bool:
        """Set expiration time for a key."""
        result = await self._client.expire(self._make_key(key), ttl)
        return bool(result)

    async def get_ttl(self, key: str) -> int | None:
        """Get remaining TTL for a key."""
        ttl = await self._client.ttl(self._make_key(key))
        if ttl < 0:  # -1 means no expiration, -2 means key doesn't exist
            return None
        return int(ttl)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._owns_client:
            await self._client.close()


class RedisRateLimiterStorage(RateLimiterStorage):
    """Redis-based rate limiter storage using sliding window log algorithm."""

    def __init__(
        self,
        redis_client: Redis | None = None,
        url: str = "redis://localhost:6379/0",
        key_prefix: str = "resilience:ratelimit:",
        **redis_kwargs: Any,
    ):
        """Initialize Redis rate limiter storage.

        Args:
            redis_client: Existing Redis client (optional)
            url: Redis connection URL
            key_prefix: Prefix for rate limiter keys
            **redis_kwargs: Additional Redis client arguments
        """
        self._key_prefix = key_prefix
        self._owns_client = redis_client is None

        if redis_client:
            self._client = redis_client
        else:
            self._client = redis.from_url(url, **redis_kwargs)

        # Lua script for atomic token bucket rate limiting
        self._token_bucket_script = """
        local key = KEYS[1]
        local limit = tonumber(ARGV[1])
        local refill_rate = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])

        local state = redis.call('HMGET', key, 'tokens', 'last_refill')
        local tokens = tonumber(state[1])
        local last_refill = tonumber(state[2])

        -- Initialize if not exists
        if not tokens then
            tokens = limit
            last_refill = now
        else
            -- Refill tokens based on elapsed time
            local elapsed = now - last_refill
            local new_tokens = elapsed * refill_rate
            tokens = math.min(limit, tokens + new_tokens)
            last_refill = now
        end

        -- Try to consume a token
        if tokens >= 1 then
            tokens = tokens - 1
            redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
            redis.call('EXPIRE', key, math.ceil(1 / refill_rate * 2))
            return {1, math.floor(tokens), 0}
        else
            -- Calculate retry_after
            local retry_after = (1 - tokens) / refill_rate
            return {0, 0, retry_after}
        end
        """

        # Lua script for fixed window rate limiting
        self._fixed_window_script = """
        local key = KEYS[1]
        local limit = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])

        local count = redis.call('GET', key)

        if not count then
            redis.call('SET', key, 1, 'EX', window)
            return {1, limit - 1, window}
        end

        count = tonumber(count)

        if count < limit then
            redis.call('INCR', key)
            local ttl = redis.call('TTL', key)
            return {1, limit - count - 1, ttl}
        else
            local ttl = redis.call('TTL', key)
            return {0, 0, ttl}
        end
        """

    def _make_key(self, key: str) -> str:
        """Create prefixed key."""
        return f"{self._key_prefix}{key}"

    async def acquire_token(
        self,
        key: str,
        limit: int,
        window_seconds: float,
    ) -> tuple[bool, dict[str, Any]]:
        """Attempt to acquire a rate limit token using token bucket algorithm."""
        redis_key = self._make_key(key)
        now = time.time()
        refill_rate = limit / window_seconds

        # Execute Lua script for atomic operation
        result = await self._client.eval(
            self._token_bucket_script,
            1,
            redis_key,
            limit,
            refill_rate,
            now,
        )

        success = bool(result[0])
        remaining = int(result[1])
        retry_after = float(result[2])

        metadata = {
            "remaining": remaining,
            "limit": limit,
            "retry_after": retry_after if not success else 0,
            "reset_at": retry_after if not success else 0,
        }

        return success, metadata

    async def acquire_token_fixed_window(
        self,
        key: str,
        limit: int,
        window_seconds: float,
    ) -> tuple[bool, dict[str, Any]]:
        """Attempt to acquire a rate limit token using fixed window algorithm."""
        redis_key = self._make_key(f"fixed:{key}")
        now = time.time()

        # Execute Lua script for atomic operation
        result = await self._client.eval(
            self._fixed_window_script,
            1,
            redis_key,
            limit,
            int(window_seconds),
            now,
        )

        success = bool(result[0])
        remaining = int(result[1])
        reset_at = float(result[2])

        metadata = {
            "remaining": remaining,
            "limit": limit,
            "retry_after": reset_at if not success else 0,
            "reset_at": reset_at,
        }

        return success, metadata

    async def get_usage(self, key: str) -> dict[str, Any]:
        """Get current rate limit usage."""
        redis_key = self._make_key(key)
        state = await self._client.hmget(redis_key, "tokens", "last_refill")

        if not state[0]:
            return {
                "tokens": None,
                "last_refill": None,
            }

        return {
            "tokens": float(state[0]) if state[0] else None,
            "last_refill": float(state[1]) if state[1] else None,
        }

    async def reset(self, key: str) -> bool:
        """Reset rate limiter state."""
        redis_key = self._make_key(key)
        result = await self._client.delete(redis_key)
        return bool(result > 0)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._owns_client:
            await self._client.close()


class RedisCircuitBreakerStorage(CircuitBreakerStorage):
    """Redis-based circuit breaker storage."""

    def __init__(
        self,
        redis_client: Redis | None = None,
        url: str = "redis://localhost:6379/0",
        key_prefix: str = "resilience:circuit:",
        **redis_kwargs: Any,
    ):
        """Initialize Redis circuit breaker storage.

        Args:
            redis_client: Existing Redis client (optional)
            url: Redis connection URL
            key_prefix: Prefix for circuit breaker keys
            **redis_kwargs: Additional Redis client arguments
        """
        self._key_prefix = key_prefix
        self._owns_client = redis_client is None

        if redis_client:
            self._client = redis_client
        else:
            self._client = redis.from_url(url, **redis_kwargs)

    def _make_key(self, key: str) -> str:
        """Create prefixed key."""
        return f"{self._key_prefix}{key}"

    async def get_state(self, key: str) -> dict[str, Any]:
        """Get circuit breaker state."""
        redis_key = self._make_key(key)
        state = await self._client.hmget(redis_key, "state", "failure_count", "last_failure_time")

        return {
            "state": state[0].decode() if state[0] else "closed",
            "failure_count": int(state[1]) if state[1] else 0,
            "last_failure_time": float(state[2]) if state[2] else 0.0,
        }

    async def record_success(self, key: str) -> dict[str, Any]:
        """Record a successful operation."""
        redis_key = self._make_key(key)

        # Reset to closed state on success
        await self._client.hset(
            redis_key,
            mapping={
                "state": "closed",
                "failure_count": 0,
                "last_failure_time": 0.0,
            },
        )

        return await self.get_state(key)

    async def record_failure(self, key: str, failure_threshold: int) -> dict[str, Any]:
        """Record a failed operation."""
        redis_key = self._make_key(key)
        now = time.time()

        # Get current state
        current_state = await self.get_state(key)
        failure_count = current_state["failure_count"] + 1

        # Determine new state
        if current_state["state"] == "closed" and failure_count >= failure_threshold:
            new_state = "open"
        elif current_state["state"] == "half_open":
            new_state = "open"
            failure_count = failure_threshold  # Reset to threshold
        else:
            new_state = current_state["state"]

        # Update state
        await self._client.hset(
            redis_key,
            mapping={
                "state": new_state,
                "failure_count": failure_count,
                "last_failure_time": now,
            },
        )

        return await self.get_state(key)

    async def transition_to_half_open(self, key: str) -> bool:
        """Transition circuit to half-open state."""
        redis_key = self._make_key(key)

        await self._client.hset(redis_key, "state", "half_open")

        return True

    async def reset(self, key: str) -> bool:
        """Reset circuit breaker to closed state."""
        redis_key = self._make_key(key)

        await self._client.hset(
            redis_key,
            mapping={
                "state": "closed",
                "failure_count": 0,
                "last_failure_time": 0.0,
            },
        )

        return True

    async def close(self) -> None:
        """Close Redis connection."""
        if self._owns_client:
            await self._client.close()
