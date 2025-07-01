"""
AsyncTaskManager Examples

This module demonstrates how to use the AsyncTaskManager class from the resilience_h8 package
in various scenarios including:

1. Basic task execution
2. Concurrent task execution
3. Task prioritization
4. Timeout handling
5. Context propagation
6. Performance metrics monitoring
7. Backpressure handling

The AsyncTaskManager provides a sophisticated solution for managing asynchronous tasks with
proper resource management, error handling, and graceful shutdown capabilities.
"""

import asyncio
import secrets
import time
from typing import Dict, Any
import structlog
from datetime import datetime

# Import the AsyncTaskManager and related classes
from resilience_h8 import AsyncTaskManager, BackpressureSettings, TaskPriority

# Configure structlog for better logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(),
    ]
)

logger = structlog.get_logger()


# Example 1: Basic usage - Running a simple task with semaphore control
async def example_basic_usage():
    print("\n=== Example 1: Basic Usage ===")

    # Create a task manager with default settings
    task_manager = AsyncTaskManager(
        max_concurrent_tasks=5, default_timeout=10, logger=logger
    )

    # Define a simple async task
    async def simple_task(task_id: int, duration: float):
        print(f"Task {task_id} started")
        await asyncio.sleep(duration)  # Simulate work
        print(f"Task {task_id} completed after {duration:.2f}s")
        return {"task_id": task_id, "duration": duration}

    # Run a task with semaphore control
    result = await task_manager.run_with_semaphore(simple_task(1, 2.0))

    print(f"Task result: {result}")

    # Get performance metrics
    metrics = task_manager.get_performance_metrics()
    if metrics:
        print(f"Performance metrics: {metrics}")


# Example 2: Running multiple tasks concurrently
async def example_concurrent_tasks():
    print("\n=== Example 2: Concurrent Tasks ===")

    # Create a task manager
    task_manager = AsyncTaskManager(
        max_concurrent_tasks=3,  # Limit to 3 concurrent tasks
        default_timeout=5,
        logger=logger,
    )

    # Define a task that takes a random amount of time
    async def random_duration_task(task_id: int) -> Dict[str, Any]:
        duration = secrets.randbelow(3)
        print(f"Task {task_id} started (duration: {duration:.2f}s)")
        await asyncio.sleep(duration)  # Simulate work
        print(f"Task {task_id} completed after {duration:.2f}s")
        return {"success": True, "task_id": task_id, "duration": duration}

    # Create multiple tasks
    tasks = [random_duration_task(i) for i in range(10)]

    # Execute tasks concurrently with a timeout
    start_time = time.time()
    try:
        # Use gather with semaphore control instead of execute_concurrent_tasks
        # This avoids the TaskGroup issue in Python 3.11
        results = []
        for i in range(0, len(tasks), 3):  # Process in batches of 3
            batch = tasks[i : i + 3]
            batch_results = await asyncio.gather(
                *[task_manager.run_with_semaphore(task) for task in batch]
            )
            results.extend(batch_results)
    except Exception as e:
        print(f"Error executing tasks: {e}")
        results = []

    # Display results
    total_time = time.time() - start_time
    print(f"All tasks completed in {total_time:.2f}s")
    print(f"Results count: {len(results)}")

    success_count = sum(1 for r in results if r.get("success", False))
    print(f"Success count: {success_count}")

    # Show metrics
    task_manager.log_performance_metrics()


# Example 3: Task prioritization
async def example_task_prioritization():
    print("\n=== Example 3: Task Prioritization ===")

    # Create a task manager with priority queue enabled
    backpressure_settings = BackpressureSettings(
        enable_priority_queue=True, max_queue_size=100
    )

    task_manager = AsyncTaskManager(
        max_concurrent_tasks=2,  # Very limited concurrency to demonstrate prioritization
        default_timeout=10,
        logger=logger,
        backpressure_settings=backpressure_settings,
    )

    # Define tasks with different priorities
    async def priority_task(task_id: int, priority_name: str, duration: float):
        print(f"Task {task_id} ({priority_name}) started")
        await asyncio.sleep(duration)  # Simulate work
        print(f"Task {task_id} ({priority_name}) completed after {duration:.2f}s")
        return {"task_id": task_id, "priority": priority_name}

    # Schedule tasks with different priorities
    # Higher priority tasks should complete first despite being scheduled later

    # Submit low priority tasks
    print("Scheduling low priority tasks...")
    low_tasks = []
    for i in range(3):
        task = await task_manager.schedule_task_with_priority(
            priority_task(i, "LOW", 1.0), priority=TaskPriority.LOW
        )
        low_tasks.append(task)

    # Submit a high priority task (should jump ahead in the queue)
    print("Scheduling high priority task...")
    high_task = await task_manager.schedule_task_with_priority(
        priority_task(100, "HIGH", 1.0), priority=TaskPriority.HIGH
    )

    # Submit a critical priority task (should execute first)
    print("Scheduling critical priority task...")
    critical_task = await task_manager.schedule_task_with_priority(
        priority_task(200, "CRITICAL", 1.0), priority=TaskPriority.CRITICAL
    )

    # Wait for all tasks to complete
    all_tasks = [high_task, critical_task] + low_tasks
    print("Waiting for all tasks to complete...")

    results = await asyncio.gather(*all_tasks, return_exceptions=True)
    print("All tasks completed")

    # Display results
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"Task {i} failed: {result}")
        else:
            print(f"Task {i} result: {result}")


# Example 4: Timeout handling
async def example_timeout_handling():
    print("\n=== Example 4: Timeout Handling ===")

    task_manager = AsyncTaskManager(
        max_concurrent_tasks=5,
        default_timeout=2.0,  # Short default timeout
        logger=logger,
    )

    # Define tasks that might time out
    async def timeout_task(task_id: int, duration: float):
        print(f"Task {task_id} started (duration: {duration:.2f}s)")
        try:
            await asyncio.sleep(duration)
            print(f"Task {task_id} completed after {duration:.2f}s")
            return {"task_id": task_id, "completed": True}
        except asyncio.CancelledError:
            print(f"Task {task_id} was cancelled")
            raise

    # Run tasks with different timeouts
    try:
        # This should complete normally
        result1 = await task_manager.run_with_timeout(timeout_task(1, 1.0), timeout=2.0)
        print(f"Task 1 result: {result1}")
    except asyncio.TimeoutError:
        print("Task 1 timed out (unexpected)")

    try:
        # This should time out
        result2 = await task_manager.run_with_timeout(timeout_task(2, 3.0), timeout=1.5)
        print(f"Task 2 result: {result2} (unexpected)")
    except asyncio.TimeoutError:
        print("Task 2 timed out (expected)")


# Example 5: Context propagation
async def example_context_propagation():
    print("\n=== Example 5: Context Propagation ===")

    task_manager = AsyncTaskManager(max_concurrent_tasks=5, logger=logger)

    # Define a task that uses context
    async def context_aware_task(task_name: str):
        # Get current context
        current_context = task_manager.get_current_context()

        print(f"Task '{task_name}' running with context:")
        print(f"  Request ID: {current_context.get('request_id')}")
        print(f"  Trace ID: {current_context.get('trace_id')}")
        print(f"  Context Data: {current_context.get('context_data')}")

        # Simulate some work
        await asyncio.sleep(0.5)

        return {"task_name": task_name, "context_used": current_context}

    # Create context variables
    context_vars = {
        "request_id": f"req-{int(time.time())}",
        "trace_id": f"trace-{secrets.randbelow(9999)}",
        "context_data": {
            "user_id": "user-123",
            "operation": "context_example",
            "timestamp": datetime.now().isoformat(),
        },
    }

    # Run task with context propagation
    result = await task_manager.run_with_semaphore(
        context_aware_task("context-demo"), context_vars=context_vars
    )

    print(f"Task result: {result}")


# Example 6: Performance metrics monitoring
async def example_performance_metrics():
    print("\n=== Example 6: Performance Metrics ===")

    task_manager = AsyncTaskManager(
        max_concurrent_tasks=10,
        default_timeout=5.0,
        collect_metrics=True,
        logger=logger,
    )

    # Define a simple task
    async def metric_task(task_id: int, duration: float):
        await asyncio.sleep(duration)
        return {"task_id": task_id}

    # Run several tasks to generate metrics
    tasks = []
    for i in range(15):
        duration = secrets.randbelow(1)
        tasks.append(metric_task(i, duration))

    # Run tasks concurrently to generate metrics
    try:
        # Use gather with semaphore control instead of execute_concurrent_tasks
        for task in tasks:
            await task_manager.run_with_semaphore(task)
    except Exception as e:
        print(f"Error executing tasks: {e}")

    # Display metrics
    metrics = task_manager.get_performance_metrics()
    print("Performance metrics:")
    for key, value in metrics.items():
        print(f"  {key}: {value}")

    # Reset metrics
    print("\nResetting metrics...")
    task_manager.reset_metrics()

    # Run a few more tasks
    for i in range(5):
        await task_manager.run_with_semaphore(metric_task(i, 0.2))

    # Display metrics after reset
    metrics = task_manager.get_performance_metrics()
    print("\nMetrics after reset:")
    for key, value in metrics.items():
        print(f"  {key}: {value}")


# Example 7: Backpressure handling
async def example_backpressure():
    print("\n=== Example 7: Backpressure Handling ===")

    # Configure backpressure settings
    backpressure_settings = BackpressureSettings(
        enable_priority_queue=True,
        enable_rate_limiting=True,
        max_queue_size=50,
        rate_limit_threshold=0.7,  # CPU threshold for rate limiting
        low_priority_rejection_threshold=0.8,  # CPU threshold for rejecting low priority tasks
    )

    task_manager = AsyncTaskManager(
        max_concurrent_tasks=5,
        default_timeout=5.0,
        adaptive_concurrency=True,
        cpu_threshold=0.7,
        min_concurrent_tasks=2,
        backpressure_settings=backpressure_settings,
        logger=logger,
    )

    # Define a CPU-intensive task
    async def cpu_intensive_task(task_id: int, iterations: int):
        print(f"Task {task_id} started (iterations: {iterations})")
        start_time = time.time()

        # Simulate CPU-intensive work
        result = 0
        for i in range(iterations):
            # Simple but CPU-intensive calculation
            result += sum(i * j for j in range(1000))

            # Allow other tasks to run occasionally
            if i % 10000 == 0:
                await asyncio.sleep(0)

        duration = time.time() - start_time
        print(f"Task {task_id} completed after {duration:.2f}s")
        return {"task_id": task_id, "duration": duration}

    # Schedule tasks with different priorities under load
    tasks = []

    # Print initial backpressure metrics
    print("Initial backpressure metrics:")
    print(task_manager.get_backpressure_metrics())

    # Schedule a mix of tasks with different priorities
    print("\nScheduling tasks with various priorities...")
    for i in range(20):
        priority = secrets.choice(
            [
                TaskPriority.LOW,
                TaskPriority.NORMAL,
                TaskPriority.HIGH,
                TaskPriority.CRITICAL,
            ]
        )

        # Determine priority name for display
        priority_name = "UNKNOWN"
        if priority == TaskPriority.LOW:
            priority_name = "LOW"
        elif priority == TaskPriority.NORMAL:
            priority_name = "NORMAL"
        elif priority == TaskPriority.HIGH:
            priority_name = "HIGH"
        elif priority == TaskPriority.CRITICAL:
            priority_name = "CRITICAL"

        # More intensive work for demonstration
        iterations = secrets.randbelow(1000)

        try:
            task = await task_manager.schedule_task_with_priority(
                cpu_intensive_task(i, iterations), priority=priority, timeout=10.0
            )
            tasks.append((task, priority_name))
            print(f"Scheduled task {i} with {priority_name} priority")
        except Exception as e:
            print(f"Failed to schedule task {i}: {e}")

    # Monitor queue size as tasks are processed
    for _ in range(5):
        metrics = task_manager.get_backpressure_metrics()
        print(f"\nCurrent backpressure metrics: {metrics}")
        await asyncio.sleep(1)

    # Wait for all tasks to complete or fail
    results = []
    for i, (task, priority) in enumerate(tasks):
        try:
            result = await task
            results.append((i, priority, "SUCCESS", result))
        except Exception as e:
            results.append((i, priority, "FAILED", str(e)))

    # Print final results
    print("\nTask completion results:")
    for task_id, priority, status, result in results:
        print(f"Task {task_id} ({priority}): {status}")

    # Print final metrics
    print("\nFinal backpressure metrics:")
    print(task_manager.get_backpressure_metrics())


# Main function to run all examples
async def main():
    print("Starting AsyncTaskManager examples...")

    await example_basic_usage()
    await example_concurrent_tasks()
    await example_task_prioritization()
    await example_timeout_handling()
    await example_context_propagation()
    await example_performance_metrics()
    await example_backpressure()

    print("\nAll examples completed.")


if __name__ == "__main__":
    # Run the main coroutine
    asyncio.run(main())
