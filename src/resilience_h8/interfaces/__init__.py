"""Core interfaces package.

This package contains interface definitions that decouple
implementation details from core abstractions, enabling better
testability and flexibility.
"""

__all__ = [
    # Concurrency interfaces
    "TaskManager",
    "WorkerPool",
    # Resilience interfaces
    "ResilienceDecorator",
    # Storage interfaces
    "StorageBackend",
    "RateLimiterStorage",
    "CircuitBreakerStorage",
]

from .concurrency import TaskManager, WorkerPool
from .resilience import ResilienceDecorator
from .storage import CircuitBreakerStorage, RateLimiterStorage, StorageBackend
