import asyncio
import signal
import time
import traceback
from typing import Any, Coroutine, Dict, Generic, List, Optional, Set, TypeVar, cast

import structlog

T = TypeVar("T")
R = TypeVar("R")


class AsyncTaskManager(Generic[T]):
    """
    A utility class for managing concurrent asyncio tasks with proper resource management,
    error handling, and graceful shutdown capabilities.

    Features:
    - Task tracking for proper cleanup
    - Concurrency limiting with semaphores
    - Timeout management
    - Signal handling for graceful shutdown
    - Performance metrics collection
    - Structured error handling
    """

    def __init__(
        self,
        max_concurrent_tasks: int = 10,
        default_timeout: float = 30,
        logger: Optional[structlog.typing.FilteringBoundLogger] = None,
        register_signal_handlers: bool = True,
    ):
        """
        Initialize the AsyncTaskManager.

        Args:
            max_concurrent_tasks: Maximum number of tasks to run concurrently
            default_timeout: Default timeout in seconds for task execution
            logger: Logger instance for recording events
            register_signal_handlers: Whether to register signal handlers for graceful shutdown
        """
        self._max_concurrent_tasks = max_concurrent_tasks
        self._default_timeout = default_timeout
        self._logger = logger

        # Concurrency control
        self._task_semaphore = asyncio.Semaphore(self._max_concurrent_tasks)

        # Task tracking for proper cleanup
        self._active_tasks: Set[asyncio.Task[Any]] = set()

        # Setup signal handlers for graceful shutdown if requested
        if register_signal_handlers:
            self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown"""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                # Add type annotation for the lambda function
                shutdown_handler = lambda s: asyncio.create_task(self._shutdown(s))  # noqa: E731
                shutdown_handler.__annotations__ = {"s": signal.Signals, "return": None}
                asyncio.get_event_loop().add_signal_handler(sig, shutdown_handler, None)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

    async def _shutdown(self, sig: signal.Signals) -> None:
        """Gracefully shutdown all active tasks"""
        if self._logger:
            self._logger.info("Shutdown signal received", signal=sig)

        # Cancel all active tasks
        tasks = [t for t in self._active_tasks if not t.done()]
        if tasks:
            if self._logger:
                self._logger.info(f"Cancelling {len(tasks)} active tasks")
            for task in tasks:
                task.cancel()

            # Wait for all tasks to be cancelled
            await asyncio.gather(*tasks, return_exceptions=True)

        if self._logger:
            self._logger.info("Shutdown complete")

    def create_and_track_task(self, coro: Coroutine[Any, Any, R], task_name: Optional[str] = None) -> asyncio.Task[R]:
        """
        Create and track an asyncio task for proper cleanup.

        Args:
            coro: Coroutine to run as a task
            task_name: Optional name for the task for better debugging

        Returns:
            The created asyncio.Task
        """
        task: asyncio.Task[R] = asyncio.create_task(coro, name=task_name)
        self._active_tasks.add(cast(asyncio.Task[Any], task))

        # Setup callback to remove task when done
        task.add_done_callback(lambda t: self._active_tasks.discard(t))
        return task

    async def run_with_semaphore(self, coro: Coroutine[Any, Any, R], timeout: Optional[float] = None) -> R:
        """
        Run a coroutine with semaphore control and timeout.

        Args:
            coro: Coroutine to run
            timeout: Optional timeout in seconds

        Returns:
            The result of the coroutine

        Raises:
            asyncio.TimeoutError: If the operation times out
        """
        timeout = timeout or self._default_timeout
        start_time = time.monotonic()

        async with self._task_semaphore:
            # Adjust timeout if one was provided
            if timeout:
                elapsed = time.monotonic() - start_time
                if elapsed >= timeout:
                    raise asyncio.TimeoutError("Timeout waiting for semaphore")
                adjusted_timeout = timeout - elapsed
            else:
                adjusted_timeout = None

            # Run with timeout
            if adjusted_timeout:
                return await asyncio.wait_for(coro, timeout=adjusted_timeout)
            else:
                return await coro

    async def execute_concurrent_tasks(  # noqa: C901
        self,
        tasks: List[Coroutine[Any, Any, Dict[str, Any]]],
        timeout: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute multiple coroutines concurrently with resource management.

        Args:
            tasks: List of coroutines to execute
            timeout: Optional timeout for the entire operation
            context: Optional context information for logging

        Returns:
            List of results, each containing success status and result or error
        """
        timeout = timeout or self._default_timeout
        context = context or {}
        tracked_tasks: List[asyncio.Task[Dict[str, Any]]] = []
        results: List[Dict[str, Any]] = []

        # Create and track tasks
        for i, task_coro in enumerate(tasks):
            task = self.create_and_track_task(
                self.run_with_semaphore(task_coro, timeout),
                task_name=f"concurrent_task_{i}",
            )
            tracked_tasks.append(task)

        # Use as_completed for better responsiveness
        try:
            for future in asyncio.as_completed(tracked_tasks, timeout=timeout):
                try:
                    result = await future
                    if result.get("success", False):
                        results.append({"success": True, "result": result})
                    else:
                        results.append({"success": False, "error": result})
                except asyncio.CancelledError:
                    # Handle cancellation explicitly
                    if self._logger:
                        self._logger.warning("Task was cancelled", **context)
                    results.append({"success": False, "error": "Task cancelled"})
                except Exception as e:
                    if self._logger:
                        self._logger.error(
                            "Task execution error",
                            message="Task execution error",
                            exception=str(e),
                            traceback=traceback.format_exc(),
                            **context,
                        )
                    results.append({"success": False, "error": str(e)})
        except asyncio.TimeoutError:
            # Handle timeout for the entire operation
            if self._logger:
                self._logger.error(
                    "Operation timed out",
                    message="Operation timed out",
                    timeout=timeout,
                    **context,
                )

            # Cancel any pending tasks
            for task in tracked_tasks:
                if not task.done():
                    task.cancel()

            # Wait for cancellations to complete
            await asyncio.gather(*tracked_tasks, return_exceptions=True)

            # Add timeout results for pending tasks
            for task in tracked_tasks:
                if task.cancelled():
                    results.append({"success": False, "error": "Operation timeout"})

        return results

    async def run_with_timeout(
        self,
        coro: Coroutine[Any, Any, T],
        timeout: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> T:
        """
        Run a single coroutine with timeout and proper error handling.

        Args:
            coro: Coroutine to run
            timeout: Optional timeout in seconds
            context: Optional context information for logging

        Returns:
            The result of the coroutine

        Raises:
            Exception: Re-raises any exception from the coroutine
            asyncio.TimeoutError: If the operation times out
        """
        timeout = timeout or self._default_timeout
        context = context or {}

        # Record start time for metrics
        start_time = time.monotonic()

        try:
            result = await asyncio.wait_for(coro, timeout=timeout)

            # Record execution time for metrics
            if self._logger:
                execution_time = time.monotonic() - start_time
                self._logger.debug(
                    "Task execution completed",
                    message="Task execution completed",
                    execution_time_ms=int(execution_time * 1000),
                    **context,
                )

            return result

        except asyncio.TimeoutError:
            if self._logger:
                self._logger.error(
                    "Operation timed out",
                    message="Operation timed out",
                    timeout=timeout,
                    **context,
                )
            raise
        except asyncio.CancelledError:
            if self._logger:
                self._logger.warning("Task was cancelled", **context)
            raise
        except Exception as e:
            if self._logger:
                self._logger.error(
                    "Task execution error",
                    message="Task execution error",
                    exception=str(e),
                    traceback=traceback.format_exc(),
                    **context,
                )
            raise

    def cancel_all_tasks(self) -> None:
        """Cancel all tracked tasks"""
        for task in self._active_tasks:
            if not task.done():
                task.cancel()

    @property
    def active_task_count(self) -> int:
        """Get the number of active tasks"""
        return len([t for t in self._active_tasks if not t.done()])
