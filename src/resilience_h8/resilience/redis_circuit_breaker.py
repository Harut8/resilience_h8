"""Redis-based distributed circuit breaker implementation.

This module provides a Redis-backed circuit breaker that maintains
consistent state across multiple service instances.
"""

import asyncio
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, Generic, TypeVar, cast

import structlog
from structlog import get_logger

from ..custom_types.resilience import CircuitState
from ..interfaces.resilience import CircuitBreaker
from ..storage.redis_backend import RedisCircuitBreakerStorage

T = TypeVar("T")
R = TypeVar("R")
P = TypeVar("P", bound=Callable[..., Any])


class RedisCircuitBreaker(CircuitBreaker[T, R], Generic[T, R]):
    """Redis-based circuit breaker for distributed systems.

    This implementation uses Redis to maintain circuit breaker state
    across multiple service instances, ensuring consistent failure
    handling in distributed environments.
    """

    def __init__(
        self,
        name: str,
        storage: RedisCircuitBreakerStorage,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        logger: structlog.typing.FilteringBoundLogger | None = None,
    ):
        """Initialize the Redis circuit breaker.

        Args:
            name: Name of the circuit breaker for logging
            storage: Redis storage backend
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Time in seconds before attempting recovery
            logger: Optional logger for recording state changes
        """
        self._name = name
        self._storage = storage
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._logger = logger or get_logger()

    def get_state(self) -> CircuitState:
        """Get the current state of the circuit breaker.

        Note: This is a best-effort synchronous method.
        For accurate state, use get_state_async().

        Returns:
            CircuitState: Current state (CLOSED, OPEN, HALF_OPEN)
        """
        # This method needs to be async for Redis, return a placeholder
        return CircuitState.CLOSED

    async def get_state_async(self) -> CircuitState:
        """Get the current state of the circuit breaker (async version).

        Returns:
            CircuitState: Current state (CLOSED, OPEN, HALF_OPEN)
        """
        state_data = await self._storage.get_state(self._name)
        state_str = state_data.get("state", "closed")

        # Map string to CircuitState enum
        state_map = {
            "closed": CircuitState.CLOSED,
            "open": CircuitState.OPEN,
            "half_open": CircuitState.HALF_OPEN,
        }

        return state_map.get(state_str, CircuitState.CLOSED)

    async def reset(self) -> None:
        """Reset the circuit breaker to initial closed state.

        This is useful for testing and for manual intervention
        to force the circuit to close regardless of recent failures.
        """
        old_state = await self.get_state_async()

        if old_state != CircuitState.CLOSED:
            await self._storage.reset(self._name)

            if self._logger:
                self._logger.info(
                    "Circuit breaker manually reset",
                    name=self._name,
                    from_state=old_state,
                    to_state=CircuitState.CLOSED,
                )
        else:
            # Even in closed state, reset the failure count
            await self._storage.reset(self._name)

    async def _check_state_transition(self) -> CircuitState:
        """Check if the circuit state should transition based on time."""
        state_data = await self._storage.get_state(self._name)
        current_state = state_data.get("state", "closed")

        if current_state == "open":
            last_failure_time = state_data.get("last_failure_time", 0)
            elapsed = time.time() - last_failure_time

            if elapsed > self._recovery_timeout:
                await self._storage.transition_to_half_open(self._name)

                if self._logger:
                    self._logger.info(
                        "Circuit breaker state transition",
                        name=self._name,
                        from_state=CircuitState.OPEN,
                        to_state=CircuitState.HALF_OPEN,
                        recovery_timeout=self._recovery_timeout,
                    )

                return CircuitState.HALF_OPEN

        # Map string to CircuitState enum
        state_map = {
            "closed": CircuitState.CLOSED,
            "open": CircuitState.OPEN,
            "half_open": CircuitState.HALF_OPEN,
        }

        return state_map.get(current_state, CircuitState.CLOSED)

    async def _record_success(self) -> None:
        """Record a successful operation."""
        state_data = await self._storage.get_state(self._name)
        current_state = state_data.get("state", "closed")

        if current_state == "half_open":
            await self._storage.record_success(self._name)

            if self._logger:
                self._logger.info(
                    "Circuit breaker state transition",
                    name=self._name,
                    from_state=CircuitState.HALF_OPEN,
                    to_state=CircuitState.CLOSED,
                )

    async def _record_failure(self) -> None:
        """Record a failed operation."""
        state_data = await self._storage.get_state(self._name)
        current_state = state_data.get("state", "closed")

        new_state_data = await self._storage.record_failure(self._name, self._failure_threshold)

        new_state = new_state_data.get("state", "closed")

        # Log state changes
        if current_state == "closed" and new_state == "open":
            if self._logger:
                self._logger.warning(
                    "Circuit breaker opened",
                    name=self._name,
                    failures=new_state_data.get("failure_count", 0),
                    threshold=self._failure_threshold,
                )

        elif current_state == "half_open" and new_state == "open" and self._logger:
            self._logger.warning(
                "Circuit breaker reopened",
                name=self._name,
                from_state=CircuitState.HALF_OPEN,
                to_state=CircuitState.OPEN,
            )

    async def execute(
        self,
        operation: Callable[..., T],
        fallback: Callable[..., R] | None = None,
        context: dict[str, Any] | None = None,
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

        # Check if circuit is open and transition state if needed
        state = await self._check_state_transition()

        if state == CircuitState.OPEN:
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
        fallback: Callable[..., R] | None = None,
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
                fallback_callable: Callable[..., R] | None = None
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
