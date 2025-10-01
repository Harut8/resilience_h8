"""Storage backend interfaces for distributed resilience patterns.

This module defines abstract storage interfaces that enable
resilience patterns to work across distributed systems using
shared state backends like Redis, Memcached, etc.
"""

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    """Abstract interface for distributed storage backends."""

    @abstractmethod
    async def get(self, key: str) -> Any | None:
        """Get a value from storage.

        Args:
            key: Storage key

        Returns:
            Value if exists, None otherwise
        """
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Set a value in storage.

        Args:
            key: Storage key
            value: Value to store
            ttl: Time-to-live in seconds (optional)

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete a value from storage.

        Args:
            key: Storage key

        Returns:
            True if deleted, False if key didn't exist
        """
        pass

    @abstractmethod
    async def increment(self, key: str, amount: int = 1, ttl: int | None = None) -> int:
        """Atomically increment a counter.

        Args:
            key: Storage key
            amount: Amount to increment by
            ttl: Time-to-live in seconds (optional)

        Returns:
            New value after increment
        """
        pass

    @abstractmethod
    async def decrement(self, key: str, amount: int = 1) -> int:
        """Atomically decrement a counter.

        Args:
            key: Storage key
            amount: Amount to decrement by

        Returns:
            New value after decrement
        """
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if a key exists in storage.

        Args:
            key: Storage key

        Returns:
            True if key exists, False otherwise
        """
        pass

    @abstractmethod
    async def expire(self, key: str, ttl: int) -> bool:
        """Set expiration time for a key.

        Args:
            key: Storage key
            ttl: Time-to-live in seconds

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def get_ttl(self, key: str) -> int | None:
        """Get remaining TTL for a key.

        Args:
            key: Storage key

        Returns:
            Remaining TTL in seconds, None if no expiration or key doesn't exist
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close storage connection and cleanup resources."""
        pass


class RateLimiterStorage(ABC):
    """Specialized storage interface for rate limiting."""

    @abstractmethod
    async def acquire_token(
        self,
        key: str,
        limit: int,
        window_seconds: float,
    ) -> tuple[bool, dict[str, Any]]:
        """Attempt to acquire a rate limit token.

        Args:
            key: Rate limiter key
            limit: Maximum requests per window
            window_seconds: Time window in seconds

        Returns:
            Tuple of (success, metadata) where metadata contains:
                - remaining: tokens remaining
                - reset_at: seconds until window resets
                - retry_after: seconds to wait (if denied)
        """
        pass

    @abstractmethod
    async def get_usage(self, key: str) -> dict[str, Any]:
        """Get current rate limit usage.

        Args:
            key: Rate limiter key

        Returns:
            Dictionary with usage information
        """
        pass

    @abstractmethod
    async def reset(self, key: str) -> bool:
        """Reset rate limiter state.

        Args:
            key: Rate limiter key

        Returns:
            True if successful
        """
        pass


class CircuitBreakerStorage(ABC):
    """Specialized storage interface for circuit breaker state."""

    @abstractmethod
    async def get_state(self, key: str) -> dict[str, Any]:
        """Get circuit breaker state.

        Args:
            key: Circuit breaker key

        Returns:
            Dictionary containing:
                - state: current state (closed/open/half_open)
                - failure_count: number of consecutive failures
                - last_failure_time: timestamp of last failure
        """
        pass

    @abstractmethod
    async def record_success(self, key: str) -> dict[str, Any]:
        """Record a successful operation.

        Args:
            key: Circuit breaker key

        Returns:
            Updated state dictionary
        """
        pass

    @abstractmethod
    async def record_failure(self, key: str, failure_threshold: int) -> dict[str, Any]:
        """Record a failed operation.

        Args:
            key: Circuit breaker key
            failure_threshold: Threshold before opening circuit

        Returns:
            Updated state dictionary
        """
        pass

    @abstractmethod
    async def transition_to_half_open(self, key: str) -> bool:
        """Transition circuit to half-open state.

        Args:
            key: Circuit breaker key

        Returns:
            True if transition successful
        """
        pass

    @abstractmethod
    async def reset(self, key: str) -> bool:
        """Reset circuit breaker to closed state.

        Args:
            key: Circuit breaker key

        Returns:
            True if successful
        """
        pass
