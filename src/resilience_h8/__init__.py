"""Resilience H8 Library.

A library implementing resilience patterns for asynchronous operations, including:
- Bulkhead pattern - for limiting concurrent operations
- Circuit breaker pattern - for failing fast when services are unavailable
- Retry pattern - for automatic retries with configurable policies
- Timeout pattern - for preventing operations from hanging indefinitely
"""


__all__ = [
    "ResilienceService",
    "StandardTaskManager",
    "StandardBulkhead",
    "CircuitBreaker",
    "RetryableContext",
    "AsyncTaskManager",
    "BackpressureSettings",
    "TaskPriority",
]

from .concurrency.task_manager import StandardTaskManager
from .concurrency.async_task_manager import (
    AsyncTaskManager,
    BackpressureSettings,
    TaskPriority,
)
from .custom_types.resilience import RetryableContext
from .interfaces.resilience import CircuitBreaker
from .resilience.bulkhead import StandardBulkhead
from .resilience.decorators import ResilienceService
