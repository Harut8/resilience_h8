"""Resilience H8 Library.

A library implementing resilience patterns for asynchronous operations, including:
- Bulkhead pattern - for limiting concurrent operations
- Circuit breaker pattern - for failing fast when services are unavailable
- Retry pattern - for automatic retries with configurable policies
- Timeout pattern - for preventing operations from hanging indefinitely
- Rate limiting - for controlling request rates
- Redis-based distributed patterns - for multi-instance deployments
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
    "TokenBucketRateLimiter",
    "FixedWindowRateLimiter",
    "StandardCircuitBreaker",
    "StandardRetryHandler",
    # Redis-based implementations (optional)
    "RedisStorageBackend",
    "RedisRateLimiterStorage",
    "RedisCircuitBreakerStorage",
    "RedisTokenBucketRateLimiter",
    "RedisFixedWindowRateLimiter",
    "RedisCircuitBreaker",
]

from .concurrency.async_task_manager import (
    AsyncTaskManager,
    BackpressureSettings,
    TaskPriority,
)
from .concurrency.task_manager import StandardTaskManager
from .custom_types.resilience import RetryableContext
from .interfaces.resilience import CircuitBreaker
from .resilience.bulkhead import StandardBulkhead
from .resilience.circuit_breaker import StandardCircuitBreaker
from .resilience.decorators import ResilienceService
from .resilience.rate_limiter import FixedWindowRateLimiter, TokenBucketRateLimiter
from .resilience.retry import StandardRetryHandler

# Redis-based implementations (may raise ImportError if redis not installed)
try:
    from .resilience.redis_circuit_breaker import RedisCircuitBreaker
    from .resilience.redis_rate_limiter import (
        RedisFixedWindowRateLimiter,
        RedisTokenBucketRateLimiter,
    )
    from .storage.redis_backend import (
        RedisCircuitBreakerStorage,
        RedisRateLimiterStorage,
        RedisStorageBackend,
    )
except ImportError:
    # Redis dependencies not installed
    pass
