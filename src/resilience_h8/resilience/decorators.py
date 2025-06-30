"""Resilience decorator service implementation.

This module provides a service for applying various resilience patterns
to functions using decorators with standardized configuration.
"""

from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar, cast
import asyncio

import structlog
from structlog import get_logger

from src.resilience_h8.interfaces.concurrency import TaskManager
from src.resilience_h8.interfaces.resilience import ResilienceDecorator
from src.resilience_h8.resilience.bulkhead import StandardBulkhead
from src.resilience_h8.resilience.circuit_breaker import StandardCircuitBreaker
from src.resilience_h8.resilience.retry import StandardRetryHandler

P = TypeVar("P", bound=Callable[..., Any])


class ResilienceService(ResilienceDecorator):
    """Service that combines multiple resilience patterns.

    This class provides a unified interface for applying various resilience
    patterns to functions, with sensible defaults and composable decorators.
    """

    def __init__(
        self,
        task_manager: TaskManager[Any, Any],
        logger: Optional[structlog.typing.FilteringBoundLogger] = None,
    ):
        """Initialize the resilience service.

        Args:
            task_manager: Task manager for concurrency control
            logger: Logger instance for recording events
        """
        self._task_manager = task_manager
        self._logger = logger or get_logger()

        # Initialize the individual pattern handlers
        self._retry_handler: StandardRetryHandler[Any, Any] = StandardRetryHandler(
            logger=logger,
            task_manager=task_manager,
        )

        # Circuit breaker registry
        self._circuit_breakers: dict[str, StandardCircuitBreaker[Any, Any]] = {}

        # Bulkhead registry
        self._bulkheads: dict[str, StandardBulkhead[Any]] = {}

    def with_retry(
        self,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        jitter: bool = True,
        retry_on_exceptions: Optional[List[Exception]] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Create a retry decorator.

        Args:
            max_retries: Maximum number of retry attempts
            backoff_factor: Factor to multiply delay between retries
            jitter: Whether to add randomness to backoff time
            retry_on_exceptions: List of exceptions that trigger retries

        Returns:
            Decorator function for adding retry logic
        """
        # Convert Type[Exception] to actual Exception instances if needed
        actual_exceptions: Optional[List[Exception]] = None
        if retry_on_exceptions is not None:
            actual_exceptions = []
            for exc in retry_on_exceptions:
                if isinstance(exc, type) and issubclass(exc, Exception):
                    # Create an instance of the exception with an empty message
                    actual_exceptions.append(exc(""))
                else:
                    actual_exceptions.append(exc)

        return self._retry_handler.retry(
            max_retries=max_retries,
            backoff_factor=backoff_factor,
            jitter=jitter,
            retry_on_exceptions=actual_exceptions,
        )

    def with_circuit_breaker(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        fallback: Optional[Callable[..., Any]] = None,
        name: str = "default",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Create a circuit breaker decorator.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Time in seconds before attempting recovery
            fallback: Function to call when circuit is open
            name: Name of the circuit breaker

        Returns:
            Decorator function for adding circuit breaker
        """
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = StandardCircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                logger=self._logger,
            )

        return self._circuit_breakers[name].circuit_break(fallback=fallback)

    def with_bulkhead(
        self,
        max_concurrent: int = 10,
        max_queue_size: Optional[int] = None,
        timeout: Optional[float] = None,
        name: str = "default",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Create a bulkhead decorator.

        Args:
            max_concurrent: Maximum number of concurrent operations
            max_queue_size: Maximum queue size for pending operations
            timeout: Maximum time to wait for execution slot
            name: Name of the bulkhead

        Returns:
            Decorator function for adding bulkhead
        """
        if name not in self._bulkheads:
            self._bulkheads[name] = StandardBulkhead(
                name=name,
                max_concurrent=max_concurrent,
                max_queue_size=max_queue_size,
                task_manager=self._task_manager,
                logger=self._logger,
            )

        return self._bulkheads[name].with_bulkhead(timeout=timeout)

    def with_timeout(self, timeout: float) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Create a timeout decorator.

        Args:
            timeout: Maximum time in seconds for operation

        Returns:
            Decorator function for adding timeout
        """

        def decorator(func: P) -> P:
            @wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                async def operation() -> Any:
                    result = func(*args, **kwargs)
                    if asyncio.iscoroutine(result):
                        return await result
                    return result

                return await self._task_manager.run_with_timeout(
                    operation(),
                    timeout=timeout,
                )

            return cast(P, wrapper)

        return decorator

    def with_resilience(
        self,
        retry_config: Optional[Dict[str, Any]] = None,
        circuit_config: Optional[Dict[str, Any]] = None,
        bulkhead_config: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Create a composite resilience decorator.

        Applies multiple resilience patterns in the recommended order:
        timeout -> bulkhead -> circuit_breaker -> retry

        Args:
            retry_config: Configuration for retry pattern
            circuit_config: Configuration for circuit breaker pattern
            bulkhead_config: Configuration for bulkhead pattern
            timeout: Maximum time in seconds for operation

        Returns:
            Decorator function combining multiple resilience patterns
        """

        def decorator(func: P) -> P:
            # Start with the original function
            decorated_func = func

            # Apply patterns in reverse order (inside to outside)
            if retry_config:
                decorated_func = cast(P, self.with_retry(**retry_config)(decorated_func))

            if circuit_config:
                decorated_func = cast(P, self.with_circuit_breaker(**circuit_config)(decorated_func))

            if bulkhead_config:
                decorated_func = cast(P, self.with_bulkhead(**bulkhead_config)(decorated_func))

            if timeout:
                decorated_func = cast(P, self.with_timeout(timeout)(decorated_func))

            return decorated_func

        return decorator
