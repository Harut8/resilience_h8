"""Task manager implementation for coordinating async tasks.

This module provides a TaskManager implementation that follows the interface
defined in the concurrency interfaces, providing a standardized way to handle
concurrent operations, task scheduling, and timeouts.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Coroutine, Dict, Generic, List, Optional, TypeVar

import structlog
from structlog.stdlib import get_logger

from ..interfaces.concurrency import TaskManager

T = TypeVar("T")
R = TypeVar("R")


class StandardTaskManager(TaskManager[T, R], Generic[T, R]):
    """Standard implementation of the TaskManager interface.

    This class provides task management capabilities including running tasks with
    timeout, parallel task execution, and controlled concurrency.
    """

    def __init__(
        self,
        max_workers: int = 10,
        thread_pool: Optional[ThreadPoolExecutor] = None,
        logger: Optional[structlog.typing.FilteringBoundLogger] = None,
    ):
        """Initialize the standard task manager.

        Args:
            max_workers: Maximum number of worker threads
            thread_pool: Optional existing thread pool executor
            logger: Logger instance for recording events
        """
        self._max_workers = max_workers
        self._thread_pool = thread_pool or ThreadPoolExecutor(max_workers=max_workers)
        self._logger = logger or get_logger()
        self._tasks: Dict[str, asyncio.Task[Any]] = {}
        self._results: Dict[str, Any] = {}

    async def run_task(self, coro: Awaitable[T]) -> T:
        """Run a coroutine as a task.

        Args:
            coro: The coroutine to run as a task

        Returns:
            The result of the coroutine
        """
        try:
            result = await coro
            return result
        except Exception as e:
            self._logger.error("Task execution failed", exception=str(e))
            raise

    async def run_with_timeout(
        self,
        coro: Coroutine[Any, Any, T],
        timeout: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> T:
        """Run a coroutine with a timeout.

        Args:
            coro: The coroutine to run
            timeout: Maximum time in seconds for the operation
            context: Optional context information for logging

        Returns:
            The result of the coroutine

        Raises:
            asyncio.TimeoutError: If the operation times out
        """
        context = context or {}
        try:
            result = await asyncio.wait_for(coro, timeout)
            return result
        except asyncio.TimeoutError:
            self._logger.warning("Task timed out", timeout=timeout, **context)
            raise
        except Exception as e:
            self._logger.error("Task execution failed", exception=str(e), **context)
            raise

    async def run_in_thread(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run a function in a separate thread.

        Args:
            func: The function to run in a thread
            *args: Positional arguments to pass to the function
            **kwargs: Keyword arguments to pass to the function

        Returns:
            The result of the function
        """
        try:
            # Use a lambda to avoid capturing self in the closure
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(self._thread_pool, lambda: func(*args, **kwargs))
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
        """
        if not tasks:
            return []

        completed: List[T] = []
        self._logger.debug("Gathering tasks", count=len(tasks))

        try:
            # Use asyncio.gather to collect all results
            completed = list(await asyncio.gather(*tasks))
            return completed
        except Exception as e:
            self._logger.error("Error in gather", exception=str(e))
            raise

    async def execute_concurrent_tasks(
        self,
        tasks: List[Coroutine[Any, Any, Dict[str, Any]]],
        timeout: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute multiple coroutines concurrently.

        Args:
            tasks: List of coroutines to execute
            timeout: Optional timeout for the operation
            context: Optional context information

        Returns:
            List of results
        """
        if not tasks:
            return []

        context = context or {}
        self._logger.debug("Executing concurrent tasks", count=len(tasks), **context)

        try:
            task_results: List[Dict[str, Any]]
            if timeout:
                # Apply timeout to the gathering operation if specified
                task_results = await asyncio.wait_for(asyncio.gather(*tasks), timeout)
            else:
                # No timeout
                task_results = await asyncio.gather(*tasks)

            results: List[Dict[str, Any]] = []
            for result in task_results:
                if result is not None:
                    # The result is already a Dict[str, Any]
                    results.append(result)

            return results
        except asyncio.TimeoutError:
            self._logger.error("Concurrent tasks execution timed out", timeout=timeout, **context)
            raise
        except Exception as e:
            self._logger.error("Failed to execute concurrent tasks", exception=str(e), **context)
            raise

    @asynccontextmanager
    async def semaphore_scope(self, max_concurrent: int) -> AsyncIterator[None]:
        """Create a semaphore-controlled context for limiting concurrency.

        Args:
            max_concurrent: Maximum number of concurrent operations
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def acquire() -> None:
            await semaphore.acquire()

        def release() -> None:
            semaphore.release()

        try:
            # Provide the semaphore to the user
            yield
        finally:
            # Ensure the semaphore is cleaned up
            pass

    async def schedule_task(self, coro: Awaitable[T], name: str) -> str:
        """Schedule a task to run in the background.

        Args:
            coro: The coroutine to schedule
            name: Name for the task

        Returns:
            ID of the scheduled task
        """
        task_id = f"{name}_{id(coro)}"

        # Create and store the task
        self._tasks[task_id] = asyncio.create_task(self._run_task(coro, task_id))

        return task_id

    async def cancel_task(self, task_id: str) -> None:
        """Cancel a task by its ID.

        Args:
            task_id: ID of the task to cancel
        """
        if task_id in self._tasks:
            self._logger.debug("Cancelling task", task_id=task_id)
            task = self._tasks.get(task_id)
            if task:
                task.cancel()
                # Remove from dict without awaiting
                _ = self._tasks.pop(task_id, None)

    async def _run_task(self, coro: Awaitable[T], task_id: str) -> None:
        """Run a task and clean up after completion.

        Args:
            coro: Coroutine to run
            task_id: ID for the task
        """
        try:
            result = await coro
            self._results[task_id] = result
            self._logger.debug("Task completed", task_id=task_id)
        except asyncio.CancelledError:
            self._logger.debug("Task cancelled", task_id=task_id)
        except Exception as e:
            self._logger.error("Task failed", task_id=task_id, exception=str(e))
        finally:
            # Remove the task from the tracking dict
            if task_id in self._tasks:
                # Get task and remove from dict without awaiting
                _ = self._tasks.pop(task_id, None)

    # Implementing required methods from abstract class to satisfy interface

    async def create_and_track_task(self, coro: Coroutine[Any, Any, R], task_name: Optional[str] = None) -> asyncio.Task[R]:
        """Create and track an asyncio task.

        Args:
            coro: Coroutine to run as a task
            task_name: Optional name for the task

        Returns:
            The created asyncio.Task
        """
        name = task_name or f"task_{id(coro)}"
        task: asyncio.Task[R] = asyncio.create_task(coro, name=name)
        return task

    def cancel_all_tasks(self) -> None:
        """Cancel all tracked tasks."""
        for task_id in list(self._tasks.keys()):
            task = self._tasks[task_id]
            if not task.done():
                task.cancel()

        self._tasks.clear()
