"""Resilience-related type definitions.

This module defines types related to resilience patterns such as retries,
circuit breakers, and bulkheads used in the application.
"""

from enum import StrEnum
from typing import Any, TypedDict


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, requests flow through
    OPEN = "open"  # Failing state, requests are blocked
    HALF_OPEN = "half_open"  # Testing state, limited requests allowed


class RetryableContext(TypedDict, total=False):
    """Context for retryable operations."""

    max_retries: int
    retry_count: int
    last_exception: Exception | None
    backoff_factor: float
    jitter: float
    operation_name: str
    start_time: float
    metadata: dict[str, Any] | None


class RateLimiterException(Exception):
    """Base exception for rate limiter errors."""

    pass


class RateLimitExceeded(RateLimiterException):
    """Exception raised when rate limit is exceeded."""

    def __init__(self, message: str, retry_after: float | None = None):
        """Initialize the exception.

        Args:
            message: Error message
            retry_after: Seconds to wait before retrying (if known)
        """
        super().__init__(message)
        self.retry_after = retry_after


class RateLimitContext(TypedDict, total=False):
    """Context for rate-limited operations."""

    operation_name: str
    requests_per_period: int
    period_seconds: float
    current_window_start: float
    current_request_count: int
    metadata: dict[str, Any] | None
