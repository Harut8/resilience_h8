"""Retry pattern implementation.

This module implements a standardized retry mechanism that integrates
with the concurrency framework for handling transient failures.
"""

import asyncio
import secrets
import time
from functools import wraps
from typing import Any, Callable, Dict, Generic, List, Optional, Protocol, Type, TypeVar, Union, cast

import structlog
from structlog import get_logger

from ..custom_types.resilience import RetryableContext
from ..interfaces.concurrency import TaskManager
from ..interfaces.resilience import RetryHandler

T = TypeVar("T")
R = TypeVar("R")
P = TypeVar("P", bound=Callable[..., Any])

# Define ExceptionType as a Union type that can accept either Exception instances or Exception classes
ExceptionType = Union[Type[Exception], Exception]


class Retryable(Protocol):
    """Protocol for objects that can be called."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        ...


class StandardRetryHandler(RetryHandler[T, R], Generic[T, R]):
    """Standard implementation of the retry pattern.

    This class provides a standardized retry mechanism for handling transient
    failures, with configurable backoff, jitter, and exception filtering.
    """

    def __init__(
        self,
        logger: Optional[structlog.typing.FilteringBoundLogger] = None,
        task_manager: Optional[TaskManager[Any, Any]] = None,
    ):
        """Initialize the StandardRetryHandler.

        Args:
            logger: Logger instance for recording retry attempts
            task_manager: Optional task manager for timeout handling
        """
        self._logger = logger or get_logger()
        self._task_manager = task_manager

    async def execute(  # noqa: C901
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
        # Default to retry on any exception if not specified
        exception_types = [Exception] if retry_on_exceptions is None else retry_on_exceptions
        context = context or {}
        retries = 0
        start_time = time.monotonic()

        retry_context: RetryableContext = {
            "max_retries": max_retries,
            "retry_count": 0,
            "last_exception": None,
            "start_time": start_time,
            "metadata": context,
        }

        while True:
            try:
                if asyncio.iscoroutinefunction(operation):
                    result = await operation()
                elif callable(operation):
                    result = operation()
                else:
                    raise TypeError(f"Operation {operation} is not callable")
                return cast(R, result)

            except Exception as e:
                # Check if the caught exception is of a type we should retry on
                should_retry = False
                for exc_type in exception_types:
                    if isinstance(e, type(exc_type) if not isinstance(exc_type, type) else exc_type):
                        should_retry = True
                        break

                if not should_retry:
                    raise

                retries += 1
                retry_context["retry_count"] = retries
                retry_context["last_exception"] = e

                if retries >= max_retries:
                    if self._logger:
                        self._logger.error(
                            "Max retries exceeded",
                            retries=retries,
                            max_retries=max_retries,
                            exception=str(e),
                            **context,
                        )
                    raise

                # Calculate backoff with optional jitter
                delay = backoff_factor * (2 ** (retries - 1))
                if jitter:
                    delay *= 0.5 + (secrets.randbelow(100) / 100)

                if self._logger:
                    self._logger.info(
                        "Retrying operation",
                        retry=retries,
                        max_retries=max_retries,
                        delay=delay,
                        exception=str(e),
                        **context,
                    )

                await asyncio.sleep(delay)

    def retry(
        self,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        jitter: bool = True,
        retry_on_exceptions: Optional[List[Exception]] = None,
    ) -> Callable[[P], P]:
        """Create a decorator for adding retry logic to a function.

        Args:
            max_retries: Maximum number of retry attempts
            backoff_factor: Factor to multiply delay between retries
            jitter: Whether to add randomness to backoff time
            retry_on_exceptions: List of exceptions that trigger retries

        Returns:
            Decorator function
        """

        def decorator(func: P) -> P:
            @wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                # Create a properly typed operation callable that matches the interface
                async def operation_callable() -> T:
                    result = func(*args, **kwargs)
                    if asyncio.iscoroutine(result):
                        return cast(T, await result)
                    return cast(T, result)

                context = {"function": func.__name__}
                return await self.execute(
                    cast(Callable[..., T], operation_callable),
                    max_retries=max_retries,
                    backoff_factor=backoff_factor,
                    jitter=jitter,
                    retry_on_exceptions=retry_on_exceptions,
                    context=context,
                )

            return cast(P, wrapper)

        return decorator
