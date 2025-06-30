"""Bulkhead pattern implementation.

This module implements a standardized bulkhead mechanism that limits
concurrent operations to prevent resource exhaustion and cascading failures.
"""

import asyncio
from functools import wraps
from typing import Any, Callable, Dict, Generic, Optional, TypeVar, cast

import structlog
from structlog import get_logger

from src.resilience_h8 import Bulkhead
from src.resilience_h8.interfaces.concurrency import TaskManager

T = TypeVar("T")
P = TypeVar("P", bound=Callable[..., Any])


class StandardBulkhead(Bulkhead[T], Generic[T]):
    """Standard implementation of the bulkhead pattern.

    This class implements the bulkhead pattern with configurable concurrency limits,
    preventing resource exhaustion by limiting concurrent operations.
    """

    def __init__(
        self,
        name: str,
        max_concurrent: int,
        max_queue_size: Optional[int] = None,
        task_manager: Optional[TaskManager[Any, Any]] = None,
        logger: Optional[structlog.typing.FilteringBoundLogger] = None,
    ):
        """Initialize the bulkhead.

        Args:
            name: Name of the bulkhead for logging
            max_concurrent: Maximum number of concurrent operations
            max_queue_size: Maximum size of the pending operations queue
            task_manager: Optional task manager for timeout handling
            logger: Optional logger for recording events
        """
        self._name = name
        self._max_concurrent = max_concurrent
        self._max_queue_size = max_queue_size
        self._task_manager = task_manager
        self._logger = logger or get_logger()

        # Concurrency control
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue_size = 0
        self._queue_semaphore = asyncio.Semaphore(max_queue_size) if max_queue_size is not None else None

    async def execute(  # noqa: C901
        self,
        operation: Callable[..., T],
        timeout: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> T:
        """Execute an operation with bulkhead protection.

        Args:
            operation: Function to execute with bulkhead
            timeout: Maximum time to wait for execution slot
            context: Optional context information

        Returns:
            Result of the operation

        Raises:
            asyncio.TimeoutError: If timeout is reached before execution
            BulkheadFullError: If the queue is full
        """
        context = context or {}

        # Check if queue is full
        queue_acquired = False
        if self._queue_semaphore:
            try:
                if timeout:
                    # Try to acquire with timeout
                    queue_acquired = await asyncio.wait_for(self._queue_semaphore.acquire(), timeout)
                else:
                    # Try to acquire immediately
                    if self._queue_semaphore._value <= 0:
                        if self._logger:
                            self._logger.warning(
                                "Bulkhead queue is full",
                                name=self._name,
                                max_queue_size=self._max_queue_size,
                                **context,
                            )
                        raise RuntimeError(f"Bulkhead '{self._name}' queue is full")

                    # Try to acquire the semaphore without blocking
                    await self._queue_semaphore.acquire()
                    queue_acquired = True

                self._queue_size += 1

            except (asyncio.TimeoutError, RuntimeError) as e:
                if self._logger:
                    self._logger.warning(
                        "Bulkhead rejection",
                        name=self._name,
                        reason=str(e),
                        **context,
                    )
                raise

        try:
            # Acquire semaphore with timeout if specified
            if timeout:
                try:
                    await asyncio.wait_for(self._semaphore.acquire(), timeout)
                except asyncio.TimeoutError:
                    if self._logger:
                        self._logger.warning(
                            "Bulkhead timeout",
                            name=self._name,
                            timeout=timeout,
                            **context,
                        )
                    raise
            else:
                await self._semaphore.acquire()

            if self._logger:
                self._logger.debug(
                    "Bulkhead executing operation",
                    name=self._name,
                    **context,
                )

            # Execute the operation
            try:
                op_result: T
                if asyncio.iscoroutinefunction(operation):
                    op_result = await operation()
                else:
                    op_result = operation()
                return op_result

            finally:
                # Release semaphore
                self._semaphore.release()
                if self._logger:
                    self._logger.debug(
                        "Bulkhead released semaphore",
                        name=self._name,
                        **context,
                    )

        finally:
            # Release queue semaphore if acquired
            if self._queue_semaphore and queue_acquired:
                self._queue_size -= 1
                self._queue_semaphore.release()

    def with_bulkhead(self, timeout: Optional[float] = None) -> Callable[[P], P]:
        """Create a decorator for adding bulkhead to a function.

        Args:
            timeout: Maximum time to wait for execution slot

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
                    timeout=timeout,
                    context=context,
                )

            return cast(P, wrapper)

        return decorator
