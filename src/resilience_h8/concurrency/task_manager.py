"""Task manager implementation for coordinating async tasks.

This module provides a TaskManager implementation that follows the interface
defined in the concurrency interfaces, providing a standardized way to handle
concurrent operations, task scheduling, and timeouts.
"""

import asyncio
import signal
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack
from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Dict,
    Generic,
    List,
    Optional,
    Set,
    TypeVar,
    cast,
)
import structlog
from structlog.stdlib import get_logger

from ..interfaces.concurrency import TaskManager

T = TypeVar("T")
R = TypeVar("R")
D = TypeVar("D", bound=Dict[str, Any])


class StandardTaskManager(TaskManager[T, R], Generic[T, R]):
    """Advanced implementation of the TaskManager interface.

    This class provides task management capabilities including running tasks with
    timeout, parallel task execution, and controlled concurrency with proper
    resource management and error handling.

    Example:
        ```python
        async with StandardTaskManager(max_workers=10) as manager:
            # Run a single task with timeout
            result = await manager.run_with_timeout(my_coro(), timeout=5.0)

            # Run multiple tasks concurrently
            tasks = [task1(), task2(), task3()]
            results = await manager.execute_concurrent_tasks(tasks)
        ```
    """

    def __init__(
        self,
        max_workers: int = 10,
        thread_pool: Optional[ThreadPoolExecutor] = None,
        logger: Optional[structlog.typing.FilteringBoundLogger] = None,
    ):
        """Initialize the standard task manager.

        Args:
            max_workers: Maximum number of worker threads/concurrent tasks
            thread_pool: Optional existing thread pool executor
            logger: Logger instance for recording events
        """
        self._max_workers = max_workers
        self._thread_pool = thread_pool or ThreadPoolExecutor(max_workers=max_workers)
        self._logger = logger or get_logger()
        self._tasks: Set[asyncio.Task[Any]] = set()
        self._results: Dict[str, Any] = {}
        self._semaphore = asyncio.Semaphore(max_workers)
        self._exit_stack = AsyncExitStack()
        self._shutting_down = False

    async def __aenter__(self) -> "StandardTaskManager[T, R]":
        """Enter async context, setting up resources."""
        await self._exit_stack.__aenter__()
        self._setup_signal_handlers()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit async context, cleaning up resources."""
        self._shutting_down = True
        self.cancel_all_tasks()  # Synchronous call
        await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
        self._thread_pool.shutdown(wait=True)

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):

                def create_handler(s: signal.Signals = sig) -> Callable[[], None]:
                    def handler() -> None:
                        asyncio.create_task(self._shutdown(s))

                    return handler

                asyncio.get_event_loop().add_signal_handler(sig, create_handler())
        except NotImplementedError:
            # Signal handlers not supported on this platform
            self._logger.warning("Signal handlers not supported on this platform")

    async def _shutdown(self, sig: signal.Signals) -> None:
        """Handle graceful shutdown on receiving a signal.

        Args:
            sig: The signal received
        """
        self._logger.info("Received shutdown signal", signal=sig.name)
        self._shutting_down = True
        self.cancel_all_tasks()  # Synchronous call

    async def run_task(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run a coroutine as a task with proper tracking and error handling.

        Args:
            coro: The coroutine to run as a task

        Returns:
            The result of the coroutine

        Raises:
            Exception: Any exception that occurred during task execution
        """
        if self._shutting_down:
            raise RuntimeError("TaskManager is shutting down")

        task: asyncio.Task[T] = asyncio.create_task(coro)
        self._tasks.add(task)
        try:
            result: T = await task
            return result
        except Exception as e:
            self._logger.error("Task execution failed", exception=str(e))
            raise
        finally:
            self._tasks.discard(task)

    async def run_with_timeout(
        self,
        coro: Coroutine[Any, Any, T],
        timeout: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> T:
        """Run a coroutine with a timeout and proper error handling.

        Args:
            coro: The coroutine to run
            timeout: Optional timeout in seconds for the operation
            context: Optional context information for logging

        Returns:
            The result of the coroutine

        Raises:
            asyncio.TimeoutError: If the operation times out
            Exception: Any other exception that occurred
        """
        context = context or {}
        try:
            async with self._semaphore:
                result: T = await asyncio.wait_for(self.run_task(coro), timeout)
                return result
        except asyncio.TimeoutError:
            self._logger.warning("Task timed out", timeout=timeout, **context)
            raise
        except Exception as e:
            self._logger.error("Task execution failed", exception=str(e), **context)
            raise

    async def run_in_thread(
        self, func: Callable[..., T], *args: Any, **kwargs: Any
    ) -> T:
        """Run a function in a separate thread using the thread pool.

        Args:
            func: The function to run in a thread
            *args: Positional arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function

        Returns:
            The result of the function

        Raises:
            Exception: Any exception that occurred during execution
        """
        try:
            loop = asyncio.get_event_loop()
            result: T = await loop.run_in_executor(
                self._thread_pool, lambda: func(*args, **kwargs)
            )
            return result
        except Exception as e:
            self._logger.error("Thread execution failed", exception=str(e))
            raise

    async def gather(self, tasks: List[Awaitable[T]]) -> List[T]:
        """Gather multiple coroutines and wait for their completion.

        Args:
            tasks: List of coroutines to gather

        Returns:
            List of results from the coroutines

        Raises:
            Exception: If any task fails
        """
        if not tasks:
            return []

        self._logger.debug("Gathering tasks", count=len(tasks))
        try:
            results = await asyncio.gather(
                *[self.run_task(cast(Coroutine[Any, Any, T], task)) for task in tasks]
            )
            return list(results)
        except Exception as e:
            self._logger.error("Error in gather", exception=str(e))
            raise

    async def execute_concurrent_tasks(
        self,
        tasks: List[Coroutine[Any, Any, Dict[str, Any]]],
        timeout: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute multiple coroutines concurrently with proper resource management.

        Args:
            tasks: List of coroutines to execute
            timeout: Optional timeout for the entire operation
            context: Optional context information

        Returns:
            List of results from the tasks

        Raises:
            asyncio.TimeoutError: If the operation times out
            Exception: If any task fails
        """
        if not tasks:
            return []

        context = context or {}
        self._logger.debug("Executing concurrent tasks", count=len(tasks), **context)

        # Create a specialized run_task for Dict[str, Any] return type
        async def run_task_dict(
            coro: Coroutine[Any, Any, Dict[str, Any]]
        ) -> Dict[str, Any]:
            return await self.run_task(coro)  # type: ignore

        async def run_with_semaphore(
            coro: Coroutine[Any, Any, Dict[str, Any]]
        ) -> Dict[str, Any]:
            async with self._semaphore:
                return await run_task_dict(coro)

        try:
            if timeout:
                async with asyncio.timeout(timeout):
                    results = await asyncio.gather(
                        *[run_with_semaphore(task) for task in tasks]
                    )
            else:
                results = await asyncio.gather(
                    *[run_with_semaphore(task) for task in tasks]
                )
            return [r for r in results if r is not None]
        except asyncio.TimeoutError:
            self._logger.error(
                "Concurrent tasks execution timed out", timeout=timeout, **context
            )
            raise
        except Exception as e:
            self._logger.error(
                "Failed to execute concurrent tasks", exception=str(e), **context
            )
            raise

    def cancel_all_tasks(self) -> None:
        """Cancel all tracked tasks."""
        if not self._tasks:
            return

        self._logger.info("Cancelling all tasks", task_count=len(self._tasks))
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Clear the tasks set
        self._tasks.clear()

    def get_active_task_count(self) -> int:
        """Get the number of currently active tasks.

        Returns:
            The number of active tasks
        """
        return len(self._tasks)

    def create_and_track_task(
        self, coro: Coroutine[Any, Any, R], task_name: Optional[str] = None
    ) -> asyncio.Task[R]:
        """Create and track an asyncio task for proper cleanup.

        This method creates a new asyncio task and adds it to the internal task tracking set
        to ensure proper cleanup when the manager is shut down.

        Args:
            coro: Coroutine to run as a task
            task_name: Optional name for the task for better debugging

        Returns:
            The created asyncio.Task

        Example:
            ```python
            async def my_coro():
                return await some_async_operation()

            task = manager.create_and_track_task(my_coro())
            result = await task
            ```
        """
        if self._shutting_down:
            raise RuntimeError("TaskManager is shutting down")

        task: asyncio.Task[R] = asyncio.create_task(
            coro, name=task_name or f"task_{id(coro)}"
        )
        self._tasks.add(task)

        # Add callback to remove task from tracking set when done
        task.add_done_callback(lambda t: self._tasks.discard(t))

        return task
