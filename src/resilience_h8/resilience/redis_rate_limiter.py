"""Redis-based distributed rate limiter implementation.

This module provides Redis-backed rate limiter implementations
that work across multiple service instances.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import (
    Any,
    Generic,
    TypeVar,
    overload,
)

from ..custom_types.resilience import RateLimitExceeded
from ..interfaces.resilience import RateLimiter
from ..storage.redis_backend import RedisRateLimiterStorage

T = TypeVar("T")


class RedisTokenBucketRateLimiter(RateLimiter[T], Generic[T]):
    """Redis-based token bucket rate limiter for distributed systems.

    This implementation uses Redis to maintain rate limit state across
    multiple service instances, ensuring consistent rate limiting
    in distributed environments.
    """

    def __init__(
        self,
        storage: RedisRateLimiterStorage,
        requests_per_period: int,
        period_seconds: float,
        name: str = "default",
    ):
        """Initialize the Redis rate limiter.

        Args:
            storage: Redis storage backend
            requests_per_period: Maximum number of requests allowed in the period
            period_seconds: Time period in seconds
            name: Name of this rate limiter instance
        """
        self.storage = storage
        self.requests_per_period = requests_per_period
        self.period_seconds = period_seconds
        self.name = name

    async def _acquire_token(self, wait: bool = True, timeout: float | None = None) -> bool:
        """Attempt to acquire a token.

        Args:
            wait: Whether to wait for a token to become available
            timeout: Maximum time to wait

        Returns:
            True if token was acquired, False otherwise

        Raises:
            RateLimitExceeded: If no token is available and wait is False
            TimeoutError: If timeout is reached while waiting
        """
        success, metadata = await self.storage.acquire_token(
            key=self.name,
            limit=self.requests_per_period,
            window_seconds=self.period_seconds,
        )

        if success:
            return True

        # If not waiting, fail immediately
        if not wait:
            retry_after = metadata.get("retry_after", 0)
            raise RateLimitExceeded(
                f"Rate limit exceeded: {self.name} ({self.requests_per_period}/{self.period_seconds}s)",
                retry_after=retry_after,
            )

        # Wait for token to become available
        wait_time = metadata.get("retry_after", 0)

        # Check if we'll exceed timeout
        if timeout is not None and wait_time > timeout:
            raise TimeoutError(
                f"Timeout waiting for rate limit: {self.name} would need to wait {wait_time:.2f}s but timeout is {timeout:.2f}s"
            )

        # Wait and retry
        await asyncio.sleep(wait_time)

        # Try again after waiting
        success, metadata = await self.storage.acquire_token(
            key=self.name,
            limit=self.requests_per_period,
            window_seconds=self.period_seconds,
        )

        if success:
            return True

        # Should not happen but handle it
        raise RateLimitExceeded(f"Failed to acquire token after waiting: {self.name}")

    @overload
    async def execute(
        self,
        operation: Callable[..., Awaitable[T]],
        wait: bool = True,
        timeout: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> T: ...

    @overload
    async def execute(
        self,
        operation: Callable[..., T],
        wait: bool = True,
        timeout: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> T: ...

    async def execute(
        self,
        operation: Callable[..., Awaitable[T] | T],
        wait: bool = True,
        timeout: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> T:
        """Execute an operation with rate limiting protection.

        Args:
            operation: Function to execute with rate limiting
            wait: If True, wait until execution is allowed
            timeout: Maximum time to wait for rate limit availability
            context: Optional context information

        Returns:
            Result of the operation

        Raises:
            RateLimitExceeded: If the rate limit is exceeded and wait is False
            TimeoutError: If the timeout is reached while waiting
        """
        await self._acquire_token(wait=wait, timeout=timeout)

        # After acquiring token, execute the operation
        if asyncio.iscoroutinefunction(operation) or asyncio.iscoroutine(operation):
            return await operation()  # type: ignore[no-any-return]
        return operation()  # type: ignore[return-value]

    def get_current_capacity(self) -> dict[str, int | float]:
        """Get current rate limit usage information.

        Note: This is a best-effort synchronous method.
        For accurate real-time data, use get_current_capacity_async().

        Returns:
            Dictionary containing rate limit information
        """
        # For Redis-based limiter, we need async call
        # This method returns a placeholder
        return {
            "remaining": 0,
            "limit": self.requests_per_period,
            "reset_at": 0.0,
        }

    async def get_current_capacity_async(self) -> dict[str, int | float]:
        """Get current rate limit usage information (async version).

        Returns:
            Dictionary containing rate limit information
        """
        usage = await self.storage.get_usage(self.name)

        tokens = usage.get("tokens", self.requests_per_period)
        if tokens is None:
            tokens = self.requests_per_period

        # Calculate time until full capacity
        refill_rate = self.requests_per_period / self.period_seconds
        time_to_full = (
            (self.requests_per_period - tokens) / refill_rate
            if tokens < self.requests_per_period
            else 0
        )

        return {
            "remaining": int(tokens),
            "limit": self.requests_per_period,
            "reset_at": time_to_full,
        }


class RedisFixedWindowRateLimiter(RateLimiter[T], Generic[T]):
    """Redis-based fixed window rate limiter for distributed systems.

    This implementation uses a fixed time window approach with Redis,
    ensuring consistent rate limiting across multiple service instances.
    """

    def __init__(
        self,
        storage: RedisRateLimiterStorage,
        requests_per_period: int,
        period_seconds: float,
        name: str = "default",
    ):
        """Initialize the Redis fixed window rate limiter.

        Args:
            storage: Redis storage backend
            requests_per_period: Maximum number of requests allowed in the period
            period_seconds: Time period in seconds
            name: Name of this rate limiter instance
        """
        self.storage = storage
        self.requests_per_period = requests_per_period
        self.period_seconds = period_seconds
        self.name = name

    async def _can_execute(self, wait: bool = True, timeout: float | None = None) -> bool:
        """Check if execution is allowed within rate limits.

        Args:
            wait: Whether to wait until execution is allowed
            timeout: Maximum time to wait

        Returns:
            True if execution is allowed, False otherwise

        Raises:
            RateLimitExceeded: If execution is not allowed and wait is False
            TimeoutError: If timeout is reached while waiting
        """
        success, metadata = await self.storage.acquire_token_fixed_window(
            key=self.name,
            limit=self.requests_per_period,
            window_seconds=self.period_seconds,
        )

        if success:
            return True

        # If not waiting, fail immediately
        if not wait:
            retry_after = metadata.get("retry_after", 0)
            raise RateLimitExceeded(
                f"Rate limit exceeded: {self.name} ({self.requests_per_period}/{self.period_seconds}s)",
                retry_after=retry_after,
            )

        # Wait for window to reset
        wait_time = metadata.get("reset_at", 0)

        # Check if wait time exceeds timeout
        if timeout is not None and wait_time > timeout:
            raise TimeoutError(
                f"Timeout waiting for rate limit: {self.name} would need to wait {wait_time:.2f}s but timeout is {timeout:.2f}s"
            )

        # Wait and retry
        await asyncio.sleep(wait_time)

        # After waiting, we should be in a new window
        success, metadata = await self.storage.acquire_token_fixed_window(
            key=self.name,
            limit=self.requests_per_period,
            window_seconds=self.period_seconds,
        )

        return success

    @overload
    async def execute(
        self,
        operation: Callable[..., Awaitable[T]],
        wait: bool = True,
        timeout: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> T: ...

    @overload
    async def execute(
        self,
        operation: Callable[..., T],
        wait: bool = True,
        timeout: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> T: ...

    async def execute(
        self,
        operation: Callable[..., Awaitable[T] | T],
        wait: bool = True,
        timeout: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> T:
        """Execute an operation with rate limiting protection.

        Args:
            operation: Function to execute with rate limiting
            wait: If True, wait until execution is allowed
            timeout: Maximum time to wait for rate limit availability
            context: Optional context information

        Returns:
            Result of the operation

        Raises:
            RateLimitExceeded: If the rate limit is exceeded and wait is False
            TimeoutError: If the timeout is reached while waiting
        """
        await self._can_execute(wait=wait, timeout=timeout)

        # After passing rate limit check, execute the operation
        if asyncio.iscoroutinefunction(operation) or asyncio.iscoroutine(operation):
            return await operation()  # type: ignore[no-any-return]
        return operation()  # type: ignore[return-value]

    def get_current_capacity(self) -> dict[str, int | float]:
        """Get current rate limit usage information.

        Note: This is a placeholder. Use async methods for Redis-based limiter.

        Returns:
            Dictionary containing rate limit information
        """
        return {
            "remaining": 0,
            "limit": self.requests_per_period,
            "reset_at": 0.0,
        }
