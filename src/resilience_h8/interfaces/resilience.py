"""Resilience interfaces.

This module defines interface abstractions for resilience patterns
such as retry, circuit breaker, and timeout that provide standard
approaches to handling failures in distributed systems.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar

from ..custom_types.resilience import CircuitState

T = TypeVar("T")
R = TypeVar("R")
P = TypeVar("P", bound=Callable[..., Any])


class RetryHandler(Generic[T, R], ABC):
    """Interface for retry handling implementations."""

    @abstractmethod
    async def execute(
        self,
        operation: Callable[..., T],
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        jitter: bool = True,
        retry_on_exceptions: Optional[List[Exception]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> R:
        """Execute an operation with retry logic.

        Args:
            operation: Function to execute with retry logic
            max_retries: Maximum number of retry attempts
            backoff_factor: Factor to multiply delay between retries
            jitter: Whether to add randomness to backoff time
            retry_on_exceptions: List of exceptions that trigger retries
            context: Optional context information

        Returns:
            Result of the operation

        Raises:
            Exception: The last exception encountered if all retries fail
        """
        pass


class CircuitBreaker(Generic[T, R], ABC):
    """Interface for circuit breaker implementations."""

    @abstractmethod
    def get_state(self) -> CircuitState:
        """Get the current state of the circuit breaker.

        Returns:
            CircuitState: Current state (CLOSED, OPEN, HALF_OPEN)
        """
        pass

    @abstractmethod
    async def execute(
        self, operation: Callable[..., T], fallback: Optional[Callable[..., R]] = None, context: Optional[Dict[str, Any]] = None
    ) -> R:
        """Execute an operation with circuit breaker protection.

        Args:
            operation: Function to execute with circuit breaker
            fallback: Function to call when circuit is open
            context: Optional context information

        Returns:
            Result of the operation or fallback

        Raises:
            Exception: If the circuit is open and no fallback is provided
        """
        pass


class Bulkhead(Generic[T], ABC):
    """Interface for bulkhead implementations."""

    @abstractmethod
    async def execute(
        self, operation: Callable[..., T], timeout: Optional[float] = None, context: Optional[Dict[str, Any]] = None
    ) -> T:
        """Execute an operation with bulkhead protection.

        Args:
            operation: Function to execute with bulkhead
            timeout: Maximum time to wait for execution slot
            context: Optional context information

        Returns:
            Result of the operation

        Raises:
            Exception: If the bulkhead is full and timeout is reached
        """
        pass


class ResilienceDecorator(ABC):
    """Interface for combining resilience patterns into decorators."""

    @abstractmethod
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
        pass

    @abstractmethod
    def with_circuit_breaker(
        self, failure_threshold: int = 5, recovery_timeout: float = 30.0, fallback: Optional[Callable[..., Any]] = None
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Create a circuit breaker decorator.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Time in seconds before attempting recovery
            fallback: Function to call when circuit is open

        Returns:
            Decorator function for adding circuit breaker
        """
        pass

    @abstractmethod
    def with_timeout(self, timeout: float) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Create a timeout decorator.

        Args:
            timeout: Maximum time in seconds for operation

        Returns:
            Decorator function for adding timeout
        """
        pass
