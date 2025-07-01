import asyncio
import signal
import time
import traceback
import psutil
import sys
import contextvars
import heapq
from typing import (
    Any,
    Coroutine,
    Dict,
    Generic,
    List,
    Optional,
    Set,
    TypeVar,
    cast,
    Protocol,
    ParamSpec,
    Awaitable,
    overload,
    NamedTuple,
)
from contextlib import AsyncExitStack
from collections import deque
from datetime import datetime

import structlog

from resilience_h8.interfaces import TaskManager

# Type variables for more precise typing
T = TypeVar("T", covariant=True)  # Generic result type
R = TypeVar("R")  # Return type
P = ParamSpec("P")  # Parameter specification for preserving callable signatures

# Check if TaskGroup is available (Python 3.11+)
HAS_TASK_GROUP = sys.version_info >= (3, 11)

# Define context variables for tracing and context propagation
request_id_var = contextvars.ContextVar[Optional[str]]("request_id", default=None)
trace_id_var = contextvars.ContextVar[Optional[str]]("trace_id", default=None)
span_id_var = contextvars.ContextVar[Optional[str]]("span_id", default=None)
context_data_var = contextvars.ContextVar[Dict[str, Any]]("context_data", default={})


# Define Protocol classes for task interfaces
class TaskLike(Protocol[T]):
    """Protocol defining the interface of an asyncio.Task-like object."""

    def done(self) -> bool:
        """Check if the task is done."""
        ...

    def cancel(self) -> bool:
        """Cancel the task."""
        ...

    def result(self) -> T:
        """Get the result of the task."""
        ...

    @property
    def name(self) -> Optional[str]:
        """Get the name of the task."""
        ...


class TaskResult(Protocol):
    """Protocol defining the interface for task results."""

    @property
    def success(self) -> bool:
        """Whether the task was successful."""
        ...

    @property
    def result(self) -> Optional[Any]:
        """The result of the task if successful."""
        ...

    @property
    def error(self) -> Optional[str]:
        """The error message if the task failed."""
        ...


class PerformanceMetrics:
    """Class for tracking performance metrics of task execution."""

    def __init__(self, max_history: int = 100):
        """Initialize performance metrics.

        Args:
            max_history: Maximum number of execution times to keep in history
        """
        self.total_tasks_completed: int = 0
        self.total_tasks_failed: int = 0
        self.total_tasks_timed_out: int = 0
        self.execution_times: deque[int] = deque(
            maxlen=max_history
        )  # Store recent execution times
        self.wait_times: deque[int] = deque(
            maxlen=max_history
        )  # Store recent wait times for semaphore
        self.last_reset_time: datetime = datetime.now()

    def record_task_completion(self, execution_time_ms: int) -> None:
        """Record a completed task.

        Args:
            execution_time_ms: Execution time in milliseconds
        """
        self.total_tasks_completed += 1
        self.execution_times.append(execution_time_ms)

    def record_task_failure(self) -> None:
        """Record a failed task."""
        self.total_tasks_failed += 1

    def record_task_timeout(self) -> None:
        """Record a timed-out task."""
        self.total_tasks_timed_out += 1

    def record_wait_time(self, wait_time_ms: int) -> None:
        """Record wait time for semaphore acquisition.

        Args:
            wait_time_ms: Wait time in milliseconds
        """
        self.wait_times.append(wait_time_ms)

    def get_average_execution_time(self) -> float:
        """Get average execution time in milliseconds."""
        if not self.execution_times:
            return 0.0
        return sum(self.execution_times) / len(self.execution_times)

    def get_average_wait_time(self) -> float:
        """Get average wait time in milliseconds."""
        if not self.wait_times:
            return 0.0
        return sum(self.wait_times) / len(self.wait_times)

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get a summary of all metrics."""
        return {
            "total_tasks_completed": self.total_tasks_completed,
            "total_tasks_failed": self.total_tasks_failed,
            "total_tasks_timed_out": self.total_tasks_timed_out,
            "average_execution_time_ms": self.get_average_execution_time(),
            "average_wait_time_ms": self.get_average_wait_time(),
            "current_execution_time_samples": len(self.execution_times),
            "metrics_since": self.last_reset_time.isoformat(),
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self.total_tasks_completed = 0
        self.total_tasks_failed = 0
        self.total_tasks_timed_out = 0
        self.execution_times.clear()
        self.wait_times.clear()
        self.last_reset_time = datetime.now()


# Improved type definitions for context
ContextDict = Dict[str, Any]
TraceContext = Dict[str, Optional[str]]


# Priority levels for task execution
class TaskPriority:
    """Task priority levels for the backpressure mechanism."""

    # Higher number means higher priority
    LOW = 0
    NORMAL = 50
    HIGH = 100
    CRITICAL = 200


class PrioritizedItem(NamedTuple):
    """A prioritized item for the priority queue."""

    priority: int
    creation_time: float  # Used as a tiebreaker for items with the same priority
    item: Any


class BackpressureSettings:
    """Settings for the backpressure mechanism."""

    def __init__(
        self,
        enable_priority_queue: bool = False,
        enable_rate_limiting: bool = False,
        max_queue_size: int = 1000,
        rate_limit_threshold: float = 0.8,  # CPU threshold for rate limiting
        low_priority_rejection_threshold: float = 0.9,  # CPU threshold for rejecting low priority tasks
        target_success_rate: float = 0.95,  # Target success rate for adaptive rate limiting
    ):
        """Initialize backpressure settings.

        Args:
            enable_priority_queue: Whether to enable priority-based task scheduling
            enable_rate_limiting: Whether to enable adaptive rate limiting
            max_queue_size: Maximum size of the task queue
            rate_limit_threshold: CPU threshold for rate limiting
            low_priority_rejection_threshold: CPU threshold for rejecting low priority tasks
            target_success_rate: Target success rate for adaptive rate limiting
        """
        self.enable_priority_queue = enable_priority_queue
        self.enable_rate_limiting = enable_rate_limiting
        self.max_queue_size = max_queue_size
        self.rate_limit_threshold = rate_limit_threshold
        self.low_priority_rejection_threshold = low_priority_rejection_threshold
        self.target_success_rate = target_success_rate


class AsyncTaskManager(TaskManager[Any, Any], Generic[T]):
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
    - Enhanced backpressure mechanisms
    """

    def __init__(
        self,
        max_concurrent_tasks: int = 10,
        default_timeout: float = 30,
        logger: Optional[structlog.typing.FilteringBoundLogger] = None,
        register_signal_handlers: bool = True,
        adaptive_concurrency: bool = False,
        cpu_threshold: float = 0.8,
        min_concurrent_tasks: int = 2,
        collect_metrics: bool = True,
        backpressure_settings: Optional[BackpressureSettings] = None,
    ):
        """
        Initialize the AsyncTaskManager.

        Args:
            max_concurrent_tasks: Maximum number of tasks to run concurrently
            default_timeout: Default timeout in seconds for task execution
            logger: Logger instance for recording events
            register_signal_handlers: Whether to register signal handlers for graceful shutdown
            adaptive_concurrency: Whether to dynamically adjust concurrency based on system load
            cpu_threshold: CPU usage threshold for adjusting concurrency (0.0 to 1.0)
            min_concurrent_tasks: Minimum number of concurrent tasks regardless of system load
            collect_metrics: Whether to collect performance metrics
            backpressure_settings: Settings for the backpressure mechanism
        """
        self._max_concurrent_tasks = max_concurrent_tasks
        self._default_timeout = default_timeout
        self._logger = logger
        self._adaptive_concurrency = adaptive_concurrency
        self._cpu_threshold = cpu_threshold
        self._min_concurrent_tasks = min_concurrent_tasks
        self._initial_max_tasks = max_concurrent_tasks
        self._collect_metrics = collect_metrics

        # Performance metrics
        self._metrics: Optional[PerformanceMetrics] = (
            PerformanceMetrics() if collect_metrics else None
        )

        # Concurrency control
        self._task_semaphore = asyncio.Semaphore(self._max_concurrent_tasks)

        # Task tracking for proper cleanup
        self._active_tasks: Set[asyncio.Task[Any]] = set()

        # For resource monitoring
        self._last_resource_check: float = 0.0
        self._resource_check_interval = 5  # Check resources every 5 seconds

        # Backpressure mechanism
        self._backpressure_settings = backpressure_settings or BackpressureSettings()
        self._task_queue: List[PrioritizedItem] = []  # Priority queue for pending tasks
        self._queue_processor_task: Optional[asyncio.Task[None]] = None
        self._queue_not_empty = (
            asyncio.Event()
        )  # Event to signal when queue is not empty
        self._task_success_window: deque[bool] = deque(
            maxlen=100
        )  # For adaptive rate limiting
        self._current_rate_limit = max_concurrent_tasks  # Current rate limit
        self._queue_processor_running = False

        # Setup signal handlers for graceful shutdown if requested
        if register_signal_handlers:
            self._setup_signal_handlers()

        # Start queue processor if priority queue is enabled
        if self._backpressure_settings.enable_priority_queue:
            self._start_queue_processor()

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

        # Cancel the queue processor task if running
        if self._queue_processor_task and not self._queue_processor_task.done():
            self._queue_processor_task.cancel()
            try:
                await self._queue_processor_task
            except asyncio.CancelledError:
                pass

        # Clear the task queue
        self._task_queue.clear()
        self._queue_processor_running = False

        # Cancel all active tasks
        tasks = [t for t in self._active_tasks if not t.done()]
        if tasks:
            if self._logger:
                self._logger.info(f"Cancelling {len(tasks)} active tasks")

            # Improved task cancellation: Cancel all tasks at once
            # and gather with return_exceptions to avoid unhandled exceptions
            for task in tasks:
                task.cancel()

            # Wait for all cancellations to complete with proper exception handling
            try:
                # Using gather with return_exceptions=True ensures we don't lose control
                # if some tasks raise exceptions during cancellation
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                if self._logger:
                    self._logger.error("Error during shutdown", exception=str(e))

        # Log final shutdown status
        if self._logger:
            active_count = sum(1 for t in self._active_tasks if not t.done())
            self._logger.info("Shutdown complete", remaining_active_tasks=active_count)

    async def _check_and_adjust_concurrency(self) -> None:
        """
        Check system resources and adjust concurrency limits if needed.
        This implements adaptive concurrency based on system load.
        """
        # Only check resources periodically to avoid overhead
        current_time = time.monotonic()
        if (
            current_time - self._last_resource_check < self._resource_check_interval
            or not self._adaptive_concurrency
        ):
            return

        self._last_resource_check = current_time

        try:
            # Get current CPU usage
            cpu_usage = psutil.cpu_percent(interval=0.1) / 100.0

            # Calculate new concurrency limit based on CPU usage
            if cpu_usage > self._cpu_threshold:
                # Reduce concurrency when CPU usage is high
                new_limit = max(
                    self._min_concurrent_tasks,
                    int(
                        self._initial_max_tasks
                        * (1 - (cpu_usage - self._cpu_threshold))
                    ),
                )
            else:
                # Gradually increase concurrency when CPU usage is low
                new_limit = min(
                    self._initial_max_tasks,
                    int(self._max_concurrent_tasks * 1.1),  # Increase by 10%
                )

            # Only log and adjust if there's a significant change
            if new_limit != self._max_concurrent_tasks:
                old_limit = self._max_concurrent_tasks
                self._max_concurrent_tasks = new_limit

                # Create a new semaphore with the adjusted limit
                # Note: We can't change the limit of an existing semaphore
                _ = self._task_semaphore
                self._task_semaphore = asyncio.Semaphore(new_limit)

                if self._logger:
                    self._logger.info(
                        "Adjusted concurrency limit",
                        old_limit=old_limit,
                        new_limit=new_limit,
                        cpu_usage=cpu_usage,
                    )
            else:
                if self._logger:
                    self._logger.debug(
                        "No change in concurrency limit",
                        cpu_usage=cpu_usage,
                    )

        except Exception as e:
            if self._logger:
                self._logger.error(
                    "Failed to adjust concurrency",
                    exception=str(e),
                )

    def create_and_track_task(
        self, coro: Coroutine[Any, Any, R], task_name: Optional[str] = None
    ) -> asyncio.Task[R]:
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

    async def _run_with_context(
        self, coro: Coroutine[Any, Any, R], context_vars: Optional[ContextDict] = None
    ) -> R:
        """
        Run a coroutine with the provided context variables.

        This ensures that context is properly propagated across async boundaries,
        which is essential for distributed tracing and logging.

        Args:
            coro: The coroutine to run
            context_vars: Optional dictionary of context variables to set

        Returns:
            The result of the coroutine
        """
        # Save the current context values to restore them later
        saved_tokens: Dict[str, Any] = {}

        try:
            # Set context variables if provided
            if context_vars:
                # Set each context var and save the token to restore later
                if (
                    "request_id" in context_vars
                    and context_vars["request_id"] is not None
                ):
                    saved_tokens["request_id"] = request_id_var.set(
                        context_vars["request_id"]
                    )

                if "trace_id" in context_vars and context_vars["trace_id"] is not None:
                    saved_tokens["trace_id"] = trace_id_var.set(
                        context_vars["trace_id"]
                    )

                if "span_id" in context_vars and context_vars["span_id"] is not None:
                    saved_tokens["span_id"] = span_id_var.set(context_vars["span_id"])

                # Set additional context data if available
                if "context_data" in context_vars and isinstance(
                    context_vars["context_data"], dict
                ):
                    # Merge with existing context data
                    existing_data = context_data_var.get()
                    new_data = {**existing_data, **context_vars["context_data"]}
                    saved_tokens["context_data"] = context_data_var.set(new_data)

            # Run the coroutine with the set context
            result = await coro
            return result

        finally:
            # Restore previous context values
            for var_name, token in saved_tokens.items():
                if var_name == "request_id":
                    request_id_var.reset(token)
                elif var_name == "trace_id":
                    trace_id_var.reset(token)
                elif var_name == "span_id":
                    span_id_var.reset(token)
                elif var_name == "context_data":
                    context_data_var.reset(
                        cast(contextvars.Token[Dict[str, Any]], token)
                    )

    def get_current_context(self) -> ContextDict:
        """
        Get the current context variables.

        Returns:
            A dictionary with the current context variables
        """
        return {
            "request_id": request_id_var.get(),
            "trace_id": trace_id_var.get(),
            "span_id": span_id_var.get(),
            "context_data": context_data_var.get(),
        }

    @overload
    async def run_with_semaphore(
        self,
        coro: Coroutine[Any, Any, R],
        timeout: Optional[float] = None,
        context_vars: Optional[ContextDict] = None,
    ) -> R:
        ...

    @overload
    async def run_with_semaphore(
        self,
        coro: Awaitable[R],
        timeout: Optional[float] = None,
        context_vars: Optional[ContextDict] = None,
    ) -> R:
        ...

    async def run_with_semaphore(
        self,
        coro: Awaitable[R],
        timeout: Optional[float] = None,
        context_vars: Optional[ContextDict] = None,
    ) -> R:
        """
        Run a coroutine with semaphore control, timeout, and context propagation.

        Args:
            coro: Coroutine to run
            timeout: Optional timeout in seconds
            context_vars: Optional context variables to propagate

        Returns:
            The result of the coroutine

        Raises:
            asyncio.TimeoutError: If the operation times out
        """
        # Check and adjust concurrency limits based on system load
        await self._check_and_adjust_concurrency()

        # Use existing context if none provided
        if context_vars is None:
            context_vars = self.get_current_context()

        timeout = timeout or self._default_timeout
        start_time = time.monotonic()

        # Measure semaphore acquisition time
        semaphore_wait_start = time.monotonic()

        try:
            async with self._task_semaphore:
                # Record semaphore wait time for metrics
                if self._collect_metrics and self._metrics:
                    wait_time = time.monotonic() - semaphore_wait_start
                    self._metrics.record_wait_time(int(wait_time * 1000))

                # Adjust timeout if one was provided
                if timeout:
                    elapsed = time.monotonic() - start_time
                    if elapsed >= timeout:
                        if self._collect_metrics and self._metrics:
                            self._metrics.record_task_timeout()
                        raise asyncio.TimeoutError("Timeout waiting for semaphore")
                    adjusted_timeout = timeout - elapsed
                else:
                    adjusted_timeout = None

                execution_start = time.monotonic()
                try:
                    # Run with timeout and context propagation
                    if adjusted_timeout:
                        # Wrap the coroutine in a context-preserving function
                        result: R = await asyncio.wait_for(
                            self._run_with_context(
                                cast(Coroutine[Any, Any, R], coro), context_vars
                            ),
                            timeout=adjusted_timeout,
                        )
                    else:
                        # Run with context propagation but no timeout
                        result = await self._run_with_context(
                            cast(Coroutine[Any, Any, R], coro), context_vars
                        )

                    # Record successful execution time
                    if self._collect_metrics and self._metrics:
                        execution_time = time.monotonic() - execution_start
                        self._metrics.record_task_completion(int(execution_time * 1000))

                    return result
                except asyncio.TimeoutError:
                    if self._collect_metrics and self._metrics:
                        self._metrics.record_task_timeout()
                    raise
                except Exception:
                    if self._collect_metrics and self._metrics:
                        self._metrics.record_task_failure()
                    raise
        except asyncio.TimeoutError:
            if self._collect_metrics and self._metrics:
                self._metrics.record_task_timeout()
            raise
        except Exception:
            if self._collect_metrics and self._metrics:
                self._metrics.record_task_failure()
            raise

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
            context: Optional context information for logging and tracing

        Returns:
            List of results, each containing success status and result or error
        """
        # Capture current context to propagate to each task
        current_context = self.get_current_context()
        if context:
            # If context is provided, merge it with current context
            if "context_data" in current_context and isinstance(
                current_context["context_data"], dict
            ):
                # Avoid modifying the original context
                merged_context = current_context.copy()
                if "context_data" not in merged_context:
                    merged_context["context_data"] = {}
                merged_context["context_data"].update(context)
                context_to_use = merged_context
            else:
                # Use provided context directly
                context_to_use = context
        else:
            # Use current context
            context_to_use = current_context

        timeout = timeout or self._default_timeout
        results: List[Dict[str, Any]] = []

        # Use Python 3.11's TaskGroup if available for better structured concurrency
        if HAS_TASK_GROUP and len(tasks) > 0:
            try:
                # Start timeout guard
                _ = time.monotonic()

                # Create a task group for structured concurrency
                # This gives better error propagation and cleaner task lifecycle management
                tg = asyncio.TaskGroup()
                task_futures = []

                # Create tasks with the task group
                for i, task_coro in enumerate(tasks):
                    # Still use our semaphore for concurrency control and context propagation
                    wrapped_coro = self.run_with_semaphore(
                        task_coro, timeout, context_to_use
                    )
                    task = tg.create_task(wrapped_coro, name=f"concurrent_task_{i}")
                    task_futures.append(task)

                # Use an exit stack to ensure proper cleanup if timeout occurs
                async with AsyncExitStack() as stack:
                    # Add the task group to the exit stack
                    await stack.enter_async_context(tg)

                    # Set up timeout if needed
                    if timeout:
                        # Create a task that will cancel everything after timeout
                        async def timeout_guard() -> None:
                            await asyncio.sleep(timeout)
                            # If we reach here, timeout has occurred
                            if self._logger:
                                self._logger.error(
                                    "Operation timed out",
                                    message="Operation timed out",
                                    timeout=timeout,
                                    **(context or {}),
                                )
                            # This will propagate to the task group and cancel all tasks
                            raise asyncio.TimeoutError("Operation timeout")

                        _ = tg.create_task(timeout_guard(), name="timeout_guard")

                    # Wait for all tasks to complete
                    # The TaskGroup context manager ensures all tasks are awaited or cancelled
                    # No need for manual task tracking

                # Process results after exiting the context
                for task in task_futures:
                    try:
                        result = task.result()
                        if result.get("success", False):
                            results.append({"success": True, "result": result})
                        else:
                            results.append({"success": False, "error": result})
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        results.append(
                            {"success": False, "error": "Task cancelled or timed out"}
                        )
                    except Exception as e:
                        if self._logger:
                            self._logger.error(
                                "Task execution error",
                                message="Task execution error",
                                exception=str(e),
                                **(context or {}),
                            )
                        results.append({"success": False, "error": str(e)})

            except (asyncio.CancelledError, asyncio.TimeoutError):
                if self._logger:
                    self._logger.error(
                        "Operation timed out or cancelled",
                        timeout=timeout,
                        **(context or {}),
                    )
                # The TaskGroup context manager ensures proper cancellation and cleanup

            except Exception as e:
                if self._logger:
                    self._logger.error(
                        "Error executing concurrent tasks",
                        exception=str(e),
                        traceback=traceback.format_exc(),
                        **(context or {}),
                    )

            return results

        # Fall back to original implementation for Python 3.10 and below
        tracked_tasks: List[asyncio.Task[Dict[str, Any]]] = []

        # Create and track tasks
        for i, task_coro in enumerate(tasks):
            task = self.create_and_track_task(
                self.run_with_semaphore(task_coro, timeout, context_to_use),
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
                        self._logger.warning("Task was cancelled", **(context or {}))
                    results.append({"success": False, "error": "Task cancelled"})
                except Exception as e:
                    if self._logger:
                        self._logger.error(
                            "Task execution error",
                            message="Task execution error",
                            exception=str(e),
                            traceback=traceback.format_exc(),
                            **(context or {}),
                        )
                    results.append({"success": False, "error": str(e)})
        except asyncio.TimeoutError:
            # Handle timeout for the entire operation
            if self._logger:
                self._logger.error(
                    "Operation timed out",
                    message="Operation timed out",
                    timeout=timeout,
                    **(context or {}),
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
                    **(context or {}),
                )

            return result

        except asyncio.TimeoutError:
            if self._logger:
                self._logger.error(
                    "Operation timed out",
                    message="Operation timed out",
                    timeout=timeout,
                    **(context or {}),
                )
            raise
        except asyncio.CancelledError:
            if self._logger:
                self._logger.warning("Task was cancelled", **(context or {}))
            raise
        except Exception as e:
            if self._logger:
                self._logger.error(
                    "Task execution error",
                    message="Task execution error",
                    exception=str(e),
                    traceback=traceback.format_exc(),
                    **(context or {}),
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

    def get_performance_metrics(self) -> Optional[Dict[str, Any]]:
        """
        Get a summary of performance metrics.

        Returns:
            Dictionary with performance metrics or None if metrics collection is disabled
        """
        if not self._collect_metrics or not self._metrics:
            return None

        metrics = self._metrics.get_metrics_summary()
        metrics["active_tasks"] = self.active_task_count
        metrics["max_concurrent_tasks"] = self._max_concurrent_tasks

        return metrics

    def reset_metrics(self) -> None:
        """Reset all performance metrics."""
        if self._metrics:
            self._metrics.reset()

    def log_performance_metrics(self) -> None:
        """Log current performance metrics."""
        if self._collect_metrics and self._metrics and self._logger:
            metrics = self.get_performance_metrics()
            if metrics:
                self._logger.info("Task manager performance metrics", **metrics)

    def is_task_done(self, task: TaskLike[Any]) -> bool:
        """
        Check if a task is done.

        Args:
            task: The task to check

        Returns:
            True if the task is done, False otherwise
        """
        return task.done()

    def _start_queue_processor(self) -> None:
        """Start the queue processor task."""
        if not self._queue_processor_running:
            self._queue_processor_running = True
            self._queue_processor_task = asyncio.create_task(
                self._process_task_queue(), name="task_queue_processor"
            )

    async def _process_task_queue(self) -> None:
        """Process tasks from the priority queue based on their priority."""
        try:
            while True:
                # Wait until there are tasks in the queue
                if not self._task_queue:
                    self._queue_not_empty.clear()
                    await self._queue_not_empty.wait()

                # Check system load for adaptive rate limiting
                if self._backpressure_settings.enable_rate_limiting:
                    await self._adjust_rate_limit()

                # Get the highest priority task from the queue
                if not self._task_queue:
                    continue

                # Check if we can process more tasks based on the current rate limit
                if len(self._active_tasks) >= self._current_rate_limit:
                    # Wait a bit before checking again
                    await asyncio.sleep(0.1)
                    continue

                # Get the highest priority task
                priority_item = heapq.heappop(self._task_queue)
                priority, _, (coro, timeout, context_vars) = priority_item

                # Execute the task
                try:
                    task = self.create_and_track_task(
                        self.run_with_semaphore(coro, timeout, context_vars),
                        task_name=f"prioritized_task_{id(coro)}",
                    )

                    # Add a callback to track task success/failure for adaptive rate limiting
                    task.add_done_callback(self._track_task_result)

                except Exception as e:
                    if self._logger:
                        self._logger.error(
                            "Failed to start prioritized task",
                            priority=priority,
                            exception=str(e),
                        )

                # Small delay to prevent CPU hogging
                await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            if self._logger:
                self._logger.info("Task queue processor cancelled")
        except Exception as e:
            if self._logger:
                self._logger.error(
                    "Task queue processor error",
                    exception=str(e),
                    traceback=traceback.format_exc(),
                )

    def _track_task_result(self, task: asyncio.Task[Any]) -> None:
        """Track task result for adaptive rate limiting."""
        try:
            # Check if task completed successfully
            success = not task.cancelled() and not task.exception()
            self._task_success_window.append(success)
        except Exception:  # nosec
            # Ignore errors in callback
            pass

    async def _adjust_rate_limit(self) -> None:
        """Adjust rate limit based on system load and task success rate."""
        # Only check periodically
        current_time = time.monotonic()
        if current_time - self._last_resource_check < self._resource_check_interval:
            return

        self._last_resource_check = current_time

        try:
            # Get current CPU usage
            cpu_usage = psutil.cpu_percent(interval=0.1) / 100.0

            # Calculate success rate
            if self._task_success_window:
                success_rate = sum(
                    1 for result in self._task_success_window if result
                ) / len(self._task_success_window)
            else:
                success_rate = 1.0

            # Adjust rate limit based on CPU usage and success rate
            if cpu_usage > self._backpressure_settings.rate_limit_threshold:
                # High CPU usage: reduce rate limit
                reduction_factor = 1.0 - (
                    cpu_usage - self._backpressure_settings.rate_limit_threshold
                )
                new_limit = max(
                    self._min_concurrent_tasks,
                    int(self._current_rate_limit * reduction_factor),
                )
            elif success_rate < self._backpressure_settings.target_success_rate:
                # Low success rate: reduce rate limit
                reduction_factor = (
                    success_rate / self._backpressure_settings.target_success_rate
                )
                new_limit = max(
                    self._min_concurrent_tasks,
                    int(self._current_rate_limit * reduction_factor),
                )
            else:
                # Normal operation: gradually increase rate limit
                new_limit = min(
                    self._initial_max_tasks,
                    int(self._current_rate_limit * 1.05),  # Increase by 5%
                )

            # Only log and adjust if there's a significant change
            if new_limit != self._current_rate_limit:
                old_limit = self._current_rate_limit
                self._current_rate_limit = new_limit

                if self._logger:
                    self._logger.info(
                        "Adjusted rate limit",
                        old_limit=old_limit,
                        new_limit=new_limit,
                        cpu_usage=cpu_usage,
                        success_rate=success_rate,
                    )

        except Exception as e:
            if self._logger:
                self._logger.error("Failed to adjust rate limit", exception=str(e))

    async def schedule_task_with_priority(
        self,
        coro: Coroutine[Any, Any, R],
        priority: int = TaskPriority.NORMAL,
        timeout: Optional[float] = None,
        context_vars: Optional[ContextDict] = None,
    ) -> asyncio.Task[R]:
        """
        Schedule a task with a specific priority.

        Args:
            coro: Coroutine to run
            priority: Priority level (higher number = higher priority)
            timeout: Optional timeout in seconds
            context_vars: Optional context variables to propagate

        Returns:
            A task that will complete when the scheduled task is picked from the queue and executed

        Raises:
            asyncio.CancelledError: If the task is rejected due to backpressure
        """
        # Start queue processor if not already running
        if not self._queue_processor_running:
            self._start_queue_processor()

        # Check if we should reject the task due to system load and priority
        if self._backpressure_settings.enable_rate_limiting:
            cpu_usage = psutil.cpu_percent(interval=0.1) / 100.0

            # Reject low priority tasks when system is under heavy load
            if (
                cpu_usage > self._backpressure_settings.low_priority_rejection_threshold
                and priority < TaskPriority.HIGH
            ):
                if self._logger:
                    self._logger.warning(
                        "Rejected low priority task due to system load",
                        cpu_usage=cpu_usage,
                        priority=priority,
                    )
                # Create a cancelled task to return
                result_task: asyncio.Task[R] = asyncio.create_task(coro)
                result_task.cancel()
                return result_task

            # Check queue size limit
            if (
                len(self._task_queue) >= self._backpressure_settings.max_queue_size
                and priority < TaskPriority.CRITICAL
            ):
                if self._logger:
                    self._logger.warning(
                        "Rejected task due to queue size limit",
                        queue_size=len(self._task_queue),
                        priority=priority,
                    )
                # Create a cancelled task to return
                result_task = asyncio.create_task(coro)
                result_task.cancel()
                return result_task

        # Create a future that will be resolved when the task is executed
        future: asyncio.Future[R] = asyncio.Future()

        # Create a wrapper coroutine that will execute the original coroutine and set the future result
        async def wrapper_coro() -> R:
            try:
                result = await coro
                if not future.done():
                    future.set_result(result)
                return result
            except Exception as e:
                if not future.done():
                    future.set_exception(e)
                raise

        # Add the task to the priority queue
        # Note: We negate the priority because heapq is a min heap, but we want higher priority numbers to come first
        heapq.heappush(
            self._task_queue,
            PrioritizedItem(
                priority=-priority,  # Negate priority for correct heap ordering
                creation_time=time.monotonic(),
                item=(wrapper_coro(), timeout, context_vars),
            ),
        )

        # Signal that the queue is not empty
        self._queue_not_empty.set()

        # Return a task that will complete when the future is resolved
        return asyncio.create_task(
            self._await_future(future), name=f"priority_task_{priority}_{id(coro)}"
        )

    async def _await_future(self, future: asyncio.Future[R]) -> R:
        """Helper to await a future and handle cancellation."""
        try:
            return await future
        except asyncio.CancelledError:
            # If this wrapper task is cancelled, cancel the future too if not already done
            if not future.done():
                future.cancel()
            raise

    def add_task_to_queue(
        self, task: Coroutine[Any, Any, R], priority: int = TaskPriority.NORMAL
    ) -> None:
        """
        Add a task to the priority queue.

        This is a simple version that doesn't return a Task object.
        For more control, use schedule_task_with_priority instead.

        Args:
            task: Coroutine to run
            priority: Priority level (higher number = higher priority)
        """
        if self._backpressure_settings.enable_priority_queue:
            # Create a prioritized item for the task
            heapq.heappush(
                self._task_queue,
                PrioritizedItem(
                    priority=-priority,  # Negate priority for correct heap ordering
                    creation_time=time.monotonic(),
                    item=(task, None, None),
                ),
            )

            # Signal that the queue is not empty
            self._queue_not_empty.set()

            # Start the queue processor if it's not already running
            self._start_queue_processor()

    def get_queue_size(self) -> int:
        """Get the current size of the task queue."""
        return len(self._task_queue)

    def get_backpressure_metrics(self) -> Dict[str, Any]:
        """Get metrics about the backpressure mechanism."""
        metrics: Dict[str, Any] = {
            "task_queue_size": len(self._task_queue),
            "active_tasks": len(self._active_tasks),
            "current_rate_limit": self._current_rate_limit,
        }

        # Calculate success rate
        if self._task_success_window:
            success_rate = sum(
                1 for result in self._task_success_window if result
            ) / len(self._task_success_window)
            metrics["task_success_rate"] = float(success_rate)

        return metrics
