"""Circuit breaker pattern implementation.

This module implements a standardized circuit breaker mechanism
that prevents cascading failures in distributed systems by failing fast
when a dependent service is unavailable.
"""

import asyncio
import time
from functools import wraps
from typing import Any, Callable, Dict, Generic, Optional, TypeVar, cast

import structlog
from structlog import get_logger

from src.resilience_h8 import CircuitBreaker
from src.resilience_h8.custom_types.resilience import CircuitState

T = TypeVar("T")
R = TypeVar("R")
P = TypeVar("P", bound=Callable[..., Any])


class StandardCircuitBreaker(CircuitBreaker[T, R], Generic[T, R]):
    """Standard implementation of the circuit breaker pattern.

    This class implements the circuit breaker pattern with configurable
    failure threshold and recovery timeout, helping prevent cascading failures
    by failing fast when a dependent service is unavailable.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        logger: Optional[structlog.typing.FilteringBoundLogger] = None,
    ):
        """Initialize the circuit breaker.

        Args:
            name: Name of the circuit breaker for logging
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Time in seconds before attempting recovery
            logger: Optional logger for recording state changes
        """
        self._name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._logger = logger or get_logger()

        # Circuit state
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = asyncio.Lock()

    def get_state(self) -> CircuitState:
        """Get the current state of the circuit breaker.

        Returns:
            CircuitState: Current state (CLOSED, OPEN, HALF_OPEN)
        """
        return self._state

    async def reset(self) -> None:
        """Reset the circuit breaker to initial closed state.

        This is useful for testing and for manual intervention
        to force the circuit to close regardless of recent failures.
        """
        async with self._lock:
            if self._state != CircuitState.CLOSED:
                old_state = self._state
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._last_failure_time = 0.0

                if self._logger:
                    self._logger.info(
                        "Circuit breaker manually reset",
                        name=self._name,
                        from_state=old_state,
                        to_state=CircuitState.CLOSED,
                    )
            else:
                # Even in closed state, reset the failure count
                self._failure_count = 0
                self._last_failure_time = 0.0

    def _check_state_transition(self) -> None:
        """Check if the circuit state should transition based on time."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed > self._recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                if self._logger:
                    self._logger.info(
                        "Circuit breaker state transition",
                        name=self._name,
                        from_state=CircuitState.OPEN,
                        to_state=CircuitState.HALF_OPEN,
                        recovery_timeout=self._recovery_timeout,
                    )

    async def _record_success(self) -> None:
        """Record a successful operation."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                if self._logger:
                    self._logger.info(
                        "Circuit breaker state transition",
                        name=self._name,
                        from_state=CircuitState.HALF_OPEN,
                        to_state=CircuitState.CLOSED,
                    )

    async def _record_failure(self) -> None:
        """Record a failed operation."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.CLOSED and self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN
                if self._logger:
                    self._logger.warning(
                        "Circuit breaker opened",
                        name=self._name,
                        failures=self._failure_count,
                        threshold=self._failure_threshold,
                    )

            elif self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                if self._logger:
                    self._logger.warning(
                        "Circuit breaker reopened",
                        name=self._name,
                        from_state=CircuitState.HALF_OPEN,
                        to_state=CircuitState.OPEN,
                    )

    async def execute(
        self,
        operation: Callable[..., T],
        fallback: Optional[Callable[..., R]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> R:
        """Execute an operation with circuit breaker protection.

        Args:
            operation: Function to execute with circuit breaker
            fallback: Function to call when circuit is open
            context: Optional context information for logging

        Returns:
            Result of the operation or fallback

        Raises:
            Exception: If the circuit is open and no fallback is provided
        """
        context = context or {}

        # Check if circuit is open
        self._check_state_transition()
        if self._state == CircuitState.OPEN:
            if self._logger:
                self._logger.warning(
                    "Circuit breaker is open, skipping operation",
                    name=self._name,
                    **context,
                )
            if fallback:
                if asyncio.iscoroutinefunction(fallback):
                    fallback_result = await fallback()
                else:
                    fallback_result = fallback()
                return cast(R, fallback_result)
            raise RuntimeError(f"Circuit breaker '{self._name}' is open")

        try:
            # Execute the operation
            if asyncio.iscoroutinefunction(operation):
                operation_result = await operation()
            else:
                operation_result = operation()

            # Record success
            await self._record_success()
            return cast(R, operation_result)

        except Exception as e:
            # Record failure
            await self._record_failure()

            if self._logger:
                self._logger.error(
                    "Circuit breaker operation failed",
                    name=self._name,
                    exception=str(e),
                    **context,
                )

            # Use fallback if provided
            if fallback:
                if asyncio.iscoroutinefunction(fallback):
                    fallback_result = await fallback()
                else:
                    fallback_result = fallback()
                return cast(R, fallback_result)

            raise

    def circuit_break(
        self,
        fallback: Optional[Callable[..., R]] = None,
    ) -> Callable[[P], P]:
        """Create a decorator for adding circuit breaker to a function.

        Args:
            fallback: Function to call when circuit is open

        Returns:
            Decorator function
        """

        def decorator(func: P) -> P:
            @wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> R:
                # Create a properly typed operation callable that matches the interface
                async def operation_callable() -> T:
                    result = func(*args, **kwargs)
                    if asyncio.iscoroutine(result):
                        return cast(T, await result)
                    return cast(T, result)

                # Properly type the fallback if provided
                fallback_callable: Optional[Callable[..., R]] = None
                if fallback is not None:
                    async def fallback_wrapper() -> R:
                        result = fallback(*args, **kwargs)
                        if asyncio.iscoroutine(result):
                            return cast(R, await result)
                        return result
                    fallback_callable = cast(Callable[..., R], fallback_wrapper)

                context = {"function": func.__name__}
                return await self.execute(
                    cast(Callable[..., T], operation_callable),
                    fallback=fallback_callable,
                    context=context,
                )

            return cast(P, wrapper)

        return decorator
