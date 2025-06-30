"""Concurrency interfaces.

This module defines interface abstractions for concurrency operations,
providing a standardized way to manage asynchronous tasks across the application.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine, Dict, Generic, List, Optional, TypeVar

T = TypeVar("T")
R = TypeVar("R")


class TaskManager(Generic[T, R], ABC):
    """Interface for task management implementations."""

    @abstractmethod
    def create_and_track_task(self, coro: Coroutine[Any, Any, R], task_name: Optional[str] = None) -> Any:
        """Create and track an asyncio task for proper cleanup.

        Args:
            coro: Coroutine to run as a task
            task_name: Optional name for the task

        Returns:
            The created task
        """
        pass

    @abstractmethod
    async def run_with_timeout(
        self, coro: Coroutine[Any, Any, T], timeout: Optional[float] = None, context: Optional[Dict[str, Any]] = None
    ) -> T:
        """Run a coroutine with timeout and proper error handling.

        Args:
            coro: Coroutine to run
            timeout: Optional timeout in seconds
            context: Optional context information

        Returns:
            The result of the coroutine

        Raises:
            TimeoutError: If the operation times out
        """
        pass

    @abstractmethod
    async def execute_concurrent_tasks(
        self,
        tasks: List[Coroutine[Any, Any, Dict[str, Any]]],
        timeout: Optional[float] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute multiple coroutines concurrently with resource management.

        Args:
            tasks: List of coroutines to execute
            timeout: Optional timeout for the entire operation
            context: Optional context information

        Returns:
            List of results, each containing success status and result or error
        """
        pass

    @abstractmethod
    def cancel_all_tasks(self) -> None:
        """Cancel all tracked tasks."""
        pass


class WorkerPool(ABC):
    """Interface for worker pool implementations."""

    @abstractmethod
    async def submit(self, task: Callable[..., Coroutine[Any, Any, R]], *args: Any, **kwargs: Any) -> R:
        """Submit a task to the worker pool.

        Args:
            task: Coroutine function to execute
            *args: Positional arguments to pass to the task
            **kwargs: Keyword arguments to pass to the task

        Returns:
            The result of the task
        """
        pass

    @abstractmethod
    async def map(self, func: Callable[[T], Coroutine[Any, Any, R]], items: List[T]) -> List[R]:
        """Apply a function to each item in a list using the worker pool.

        Args:
            func: Coroutine function to apply
            items: List of items to process

        Returns:
            List of results
        """
        pass

    @abstractmethod
    async def shutdown(self, wait: bool = True) -> None:
        """Shutdown the worker pool.

        Args:
            wait: Whether to wait for pending tasks to complete
        """
        pass
