"""Rate limiter implementation.

This module provides an implementation of the rate limiter interface
to control the frequency of operations such as API calls.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import (
    Any,
    Generic,
    TypeVar,
    overload,
)

from ..custom_types.resilience import RateLimitExceeded
from ..interfaces.resilience import RateLimiter

T = TypeVar("T")


class TokenBucketRateLimiter(RateLimiter[T], Generic[T]):
    """Token bucket implementation of rate limiter.

    This implementation uses the token bucket algorithm to control request rates.
    It works by maintaining a "bucket" of tokens that refill at a constant rate.
    Each request consumes a token, and if no tokens are available, the request
    must wait or be rejected.
    """

    def __init__(
        self,
        requests_per_period: int,
        period_seconds: float,
        name: str = "default",
    ):
        """Initialize the rate limiter.

        Args:
            requests_per_period: Maximum number of requests allowed in the period
            period_seconds: Time period in seconds
            name: Name of this rate limiter instance
        """
        self.requests_per_period = requests_per_period
        self.period_seconds = period_seconds
        self.name = name

        # Calculate token refill rate (tokens per second)
        self.refill_rate = requests_per_period / period_seconds

        # Initial state
        self.tokens = float(requests_per_period)
        self.last_refill_time = time.monotonic()
        self._lock = asyncio.Lock()

    async def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill_time

        # Calculate new tokens to add based on elapsed time
        new_tokens = elapsed * self.refill_rate

        # Update tokens (not exceeding capacity)
        self.tokens = min(self.requests_per_period, self.tokens + new_tokens)
        self.last_refill_time = now

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
        start_time = time.monotonic()

        async with self._lock:
            await self._refill()

            # If we have a token, consume it immediately
            if self.tokens >= 1:
                self.tokens -= 1
                return True

            # If not waiting, fail immediately
            if not wait:
                retry_after = 1.0 / self.refill_rate
                raise RateLimitExceeded(
                    f"Rate limit exceeded: {self.name} ({self.requests_per_period}/{self.period_seconds}s)",
                    retry_after=retry_after,
                )

            # Calculate time until next token
            wait_time = (1.0 - self.tokens) / self.refill_rate

            # Check if we'll exceed timeout
            if timeout is not None and wait_time > timeout:
                raise TimeoutError(
                    f"Timeout waiting for rate limit: {self.name} would need to wait {wait_time:.2f}s but timeout is {timeout:.2f}s"
                )

        # Release lock while waiting
        await asyncio.sleep(wait_time)

        # Reacquire lock and try again
        async with self._lock:
            await self._refill()

            # Check timeout again after waiting
            if timeout is not None and time.monotonic() - start_time >= timeout:
                raise TimeoutError(f"Timeout waiting for rate limit: {self.name}")

            # Now we should have a token
            if self.tokens >= 1:
                self.tokens -= 1
                return True

            # Should not happen but just in case
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

        Returns:
            Dictionary containing rate limit information
        """
        # Create a snapshot of current state
        tokens = self.tokens
        now = time.monotonic()
        elapsed = now - self.last_refill_time
        current_tokens = min(self.requests_per_period, tokens + (elapsed * self.refill_rate))

        # Calculate time until full capacity
        time_to_full = (
            (self.requests_per_period - current_tokens) / self.refill_rate
            if current_tokens < self.requests_per_period
            else 0
        )

        return {
            "remaining": int(current_tokens),  # Round down to be conservative
            "limit": self.requests_per_period,
            "reset_at": time_to_full,
        }


class FixedWindowRateLimiter(RateLimiter[T], Generic[T]):
    """Fixed window implementation of rate limiter.

    This implementation uses a fixed time window approach to rate limiting.
    It counts requests in a rolling time window and rejects or delays requests
    that would exceed the limit in the current window.
    """

    def __init__(
        self,
        requests_per_period: int,
        period_seconds: float,
        name: str = "default",
    ):
        """Initialize the rate limiter.

        Args:
            requests_per_period: Maximum number of requests allowed in the period
            period_seconds: Time period in seconds
            name: Name of this rate limiter instance
        """
        self.requests_per_period = requests_per_period
        self.period_seconds = period_seconds
        self.name = name

        # Initialize the current window
        self.window_start = time.monotonic()
        self.request_count = 0
        self._lock = asyncio.Lock()

    async def _check_and_update_window(self) -> None:
        """Check if current window has expired and reset if needed."""
        now = time.monotonic()
        if now - self.window_start >= self.period_seconds:
            # Window has expired, reset
            self.window_start = now
            self.request_count = 0

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
        start_time = time.monotonic()

        async with self._lock:
            await self._check_and_update_window()

            # If we haven't reached the limit, allow execution
            if self.request_count < self.requests_per_period:
                self.request_count += 1
                return True

            # If not waiting, fail immediately
            if not wait:
                time_remaining = self.period_seconds - (time.monotonic() - self.window_start)
                raise RateLimitExceeded(
                    f"Rate limit exceeded: {self.name} ({self.requests_per_period}/{self.period_seconds}s)",
                    retry_after=time_remaining,
                )

            # Calculate wait time until window resets
            wait_time = self.period_seconds - (time.monotonic() - self.window_start)

            # Check if wait time exceeds timeout
            if timeout is not None and wait_time > timeout:
                raise TimeoutError(
                    f"Timeout waiting for rate limit: {self.name} would need to wait {wait_time:.2f}s but timeout is {timeout:.2f}s"
                )

        # Release lock while waiting
        await asyncio.sleep(wait_time)

        # After waiting, try again
        async with self._lock:
            await self._check_and_update_window()

            # Check timeout again after waiting
            if timeout is not None and time.monotonic() - start_time >= timeout:
                raise TimeoutError(f"Timeout waiting for rate limit: {self.name}")

            # After waiting, we should be in a new window
            self.request_count += 1
            return True

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

        Returns:
            Dictionary containing rate limit information
        """
        now = time.monotonic()
        elapsed = now - self.window_start

        # If window has expired, we're at full capacity
        if elapsed >= self.period_seconds:
            return {
                "remaining": self.requests_per_period,
                "limit": self.requests_per_period,
                "reset_at": 0.0,
            }

        # Otherwise calculate remaining capacity
        remaining = max(0, self.requests_per_period - self.request_count)
        time_to_reset = self.period_seconds - elapsed

        return {
            "remaining": remaining,
            "limit": self.requests_per_period,
            "reset_at": time_to_reset,
        }
