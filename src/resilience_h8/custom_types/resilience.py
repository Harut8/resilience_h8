"""Resilience-related type definitions.

This module defines types related to resilience patterns such as retries,
circuit breakers, and bulkheads used in the application.
"""

from enum import StrEnum
from typing import Any, Dict, Optional, TypedDict


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, requests flow through
    OPEN = "open"  # Failing state, requests are blocked
    HALF_OPEN = "half_open"  # Testing state, limited requests allowed


class RetryableContext(TypedDict, total=False):
    """Context for retryable operations."""

    max_retries: int
    retry_count: int
    last_exception: Optional[Exception]
    backoff_factor: float
    jitter: float
    operation_name: str
    start_time: float
    metadata: Optional[Dict[str, Any]]
