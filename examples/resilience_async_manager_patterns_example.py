#!/usr/bin/env python3
"""
Example demonstrating how to use AsyncTaskManager with ResilienceService.

This example shows how to:
1. Apply bulkhead pattern for concurrency limiting
2. Use circuit breaker pattern for failing fast when services are unavailable
3. Implement retry pattern for automatic retries with configurable policies
4. Apply timeout pattern to prevent operations from hanging indefinitely
"""
import asyncio
import secrets
import signal
import time
from typing import Any, Dict

import structlog
from resilience_h8.concurrency.async_task_manager import AsyncTaskManager
from resilience_h8.resilience.decorators import ResilienceService

# Configure structured logging
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = structlog.get_logger()


# Simulating an external service with failures
class ExternalService:
    def __init__(self, failure_rate: float = 0.3, high_latency_rate: float = 0.2):
        self.failure_rate = failure_rate
        self.high_latency_rate = high_latency_rate
        self.failure_count = 0
        self.success_count = 0
        self.call_count = 0
        self.circuit_open = False

    async def reset_circuit(self):
        """Reset the circuit breaker state"""
        self.circuit_open = False
        logger.info("Circuit breaker reset")

    async def call_api(self, request_id: str) -> Dict[str, Any]:
        """Simulate an API call with potential failures and varying latency"""
        if self.circuit_open:
            logger.error("Circuit is open, fast failing", request_id=request_id)
            raise RuntimeError("Service unavailable - circuit open")

        self.call_count += 1

        # Simulate service completely down after too many failures
        if self.failure_count > 5 and self.failure_count > self.success_count:
            self.circuit_open = True
            logger.error("Service marked as unavailable", request_id=request_id)
            raise RuntimeError("Service completely unavailable")

        # Simulate random failures
        if secrets.randbelow(1) < self.failure_rate:
            self.failure_count += 1
            logger.error(
                "API call failed",
                request_id=request_id,
                failure_count=self.failure_count,
            )
            raise RuntimeError(f"API call failed for {request_id}")

        # Simulate high latency
        latency = 0.2  # Base latency
        if secrets.randbelow(3) < self.high_latency_rate:
            latency = secrets.randbelow(3)  # High latency
            logger.info("High latency request", request_id=request_id, latency=latency)
        else:
            latency = secrets.randbelow(1)  # Normal latency

        await asyncio.sleep(latency)

        self.success_count += 1
        return {
            "request_id": request_id,
            "result": f"Success for {request_id}",
            "latency": latency,
        }


# Example client that uses ResilienceService
class ResilientClient:
    def __init__(self):
        # Create task manager with concurrency control
        self.task_manager = AsyncTaskManager(
            max_concurrent_tasks=10, default_timeout=5.0, logger=logger
        )

        # Create resilience service that uses the task manager
        self.resilience = ResilienceService(
            task_manager=self.task_manager, logger=logger
        )

        self.service = ExternalService()

        # Initialize decorators
        self.timeout_decorator = self.resilience.with_timeout(
            timeout=2.0
        )  # Increased timeout
        self.retry_decorator = self.resilience.with_retry(max_retries=2)
        self.circuit_breaker_decorator = self.resilience.with_circuit_breaker(
            failure_threshold=5,  # Increased failure threshold
            recovery_timeout=3.0,  # Reduced recovery timeout
            name="example_circuit",
        )
        self.bulkhead_decorator = self.resilience.with_bulkhead(
            max_concurrent=3,  # Increased concurrency
            max_queue_size=5,
            timeout=3.0,  # Increased timeout
            name="example_bulkhead",
        )

    @property
    def metrics(self) -> Dict[str, Any]:
        """Get performance metrics from task manager"""
        return self.task_manager.get_performance_metrics()

    async def close(self):
        """Shutdown client gracefully"""
        await self.task_manager._shutdown(
            sig=signal.SIGINT
        )  # Using internal _shutdown method

    # Basic API call with no resilience patterns
    async def basic_api_call(self, request_id: str) -> Dict[str, Any]:
        """Call API with no resilience patterns"""
        return await self.service.call_api(request_id)

    # Apply timeout pattern
    async def api_with_timeout(self, request_id: str) -> Dict[str, Any]:
        """Call API with timeout pattern"""
        return await self.timeout_decorator(self.service.call_api)(request_id)

    # Apply retry pattern
    async def api_with_retry(self, request_id: str) -> Dict[str, Any]:
        """Call API with retry pattern"""
        return await self.retry_decorator(self.service.call_api)(request_id)

    # Apply circuit breaker pattern
    async def api_with_circuit_breaker(self, request_id: str) -> Dict[str, Any]:
        """Call API with circuit breaker pattern"""
        return await self.circuit_breaker_decorator(self.service.call_api)(request_id)

    # Apply bulkhead pattern
    async def api_with_bulkhead(self, request_id: str) -> Dict[str, Any]:
        """Call API with bulkhead pattern"""
        return await self.bulkhead_decorator(self.service.call_api)(request_id)

    # Combine multiple resilience patterns
    async def api_with_all_patterns(self, request_id: str) -> Dict[str, Any]:
        """Call API with all resilience patterns combined"""
        # Compose decorators
        decorated_call = self.timeout_decorator(  # Timeout first
            self.retry_decorator(  # Then retry
                self.circuit_breaker_decorator(  # Then circuit breaker
                    self.bulkhead_decorator(  # Finally bulkhead
                        self.service.call_api
                    )
                )
            )
        )
        return await decorated_call(request_id)


# Example functions to demonstrate each pattern
async def demonstrate_basic_api_call(client: ResilientClient):
    print("\n=== Example 1: Basic API Call (No Resilience) ===")

    # Make API calls without resilience patterns
    results = []
    for i in range(5):
        request_id = f"basic-{i}"
        try:
            start_time = time.time()
            result = await client.basic_api_call(request_id)
            elapsed = time.time() - start_time
            print(f"Request {request_id} succeeded after {elapsed:.2f}s: {result}")
            results.append(
                {"status": "success", "request_id": request_id, "result": result}
            )
        except Exception as e:
            print(f"Request {request_id} failed: {e}")
            results.append(
                {"status": "failure", "request_id": request_id, "error": str(e)}
            )

    print(
        f"Success rate: {sum(1 for r in results if r['status'] == 'success')} / {len(results)}"
    )
    print(f"Performance metrics: {client.metrics}")


async def demonstrate_timeout_pattern(client: ResilientClient):
    print("\n=== Example 2: Timeout Pattern ===")

    # Make API calls with timeout
    results = []
    for i in range(5):
        request_id = f"timeout-{i}"
        try:
            start_time = time.time()
            result = await client.api_with_timeout(request_id)
            elapsed = time.time() - start_time
            print(f"Request {request_id} succeeded after {elapsed:.2f}s: {result}")
            results.append(
                {"status": "success", "request_id": request_id, "time": elapsed}
            )
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            print(f"Request {request_id} timed out after {elapsed:.2f}s (as expected)")
            results.append(
                {"status": "timeout", "request_id": request_id, "time": elapsed}
            )
        except Exception as e:
            print(f"Request {request_id} failed with unexpected error: {e}")
            results.append(
                {"status": "failure", "request_id": request_id, "error": str(e)}
            )

    # Print summary
    timeouts = sum(1 for r in results if r["status"] == "timeout")
    successes = sum(1 for r in results if r["status"] == "success")
    print(f"Results: {successes} successful, {timeouts} timed out")
    print(f"Performance metrics: {client.metrics}")


async def demonstrate_retry_pattern(client: ResilientClient):
    print("\n=== Example 3: Retry Pattern ===")

    # Make API calls with retry
    results = []
    for i in range(5):
        request_id = f"retry-{i}"
        try:
            start_time = time.time()
            result = await client.api_with_retry(request_id)
            elapsed = time.time() - start_time
            print(f"Request {request_id} succeeded after {elapsed:.2f}s: {result}")
            results.append(
                {"status": "success", "request_id": request_id, "time": elapsed}
            )
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"Request {request_id} failed after retries in {elapsed:.2f}s: {e}")
            results.append(
                {"status": "failure", "request_id": request_id, "error": str(e)}
            )

    # Print summary
    successes = sum(1 for r in results if r["status"] == "success")
    print(f"Success rate with retry: {successes} / {len(results)}")
    print(f"Performance metrics: {client.metrics}")

    # Reset service state before next example
    await client.service.reset_circuit()


async def demonstrate_circuit_breaker_pattern(client: ResilientClient):
    print("\n=== Example 4: Circuit Breaker Pattern ===")

    # Make API calls until circuit opens
    results = []
    for i in range(10):  # More calls to ensure circuit opens
        request_id = f"circuit-{i}"
        try:
            start_time = time.time()
            result = await client.api_with_circuit_breaker(request_id)
            elapsed = time.time() - start_time
            print(f"Request {request_id} succeeded after {elapsed:.2f}s: {result}")
            results.append({"status": "success", "request_id": request_id})
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"Request {request_id} failed after {elapsed:.2f}s: {e}")
            if "circuit open" in str(e).lower():
                results.append({"status": "circuit_open", "request_id": request_id})
            else:
                results.append({"status": "failure", "request_id": request_id})

    # Print summary
    successes = sum(1 for r in results if r["status"] == "success")
    circuit_opens = sum(1 for r in results if r["status"] == "circuit_open")
    failures = sum(1 for r in results if r["status"] == "failure")
    print(
        f"Results: {successes} successful, {failures} failures, {circuit_opens} circuit open fast fails"
    )
    print(f"Performance metrics: {client.metrics}")

    # Reset service state before next example
    await client.service.reset_circuit()


async def demonstrate_bulkhead_pattern(client: ResilientClient):
    print("\n=== Example 5: Bulkhead Pattern ===")

    # Make concurrent API calls to test bulkhead (limited to 2 concurrent)
    start_time = time.time()

    # Create tasks for concurrent execution
    tasks = []
    for i in range(6):  # Create more tasks than the bulkhead allows
        tasks.append(asyncio.create_task(client.api_with_bulkhead(f"bulkhead-{i}")))

    # Wait for all tasks to complete
    results = []
    for i, task in enumerate(tasks):
        try:
            result = await task
            print(f"Bulkhead request {i} succeeded: {result}")
            results.append({"status": "success", "result": result})
        except Exception as e:
            print(f"Bulkhead request {i} failed: {e}")
            results.append({"status": "failure", "error": str(e)})

    elapsed = time.time() - start_time
    print(f"All bulkhead tasks completed in {elapsed:.2f}s")
    print(
        f"Bulkhead success rate: {sum(1 for r in results if r['status'] == 'success')} / {len(results)}"
    )
    print(f"Performance metrics: {client.metrics}")

    # Reset service state before next example
    await client.service.reset_circuit()


async def demonstrate_combined_patterns(client: ResilientClient):
    print("\n=== Example 6: Combined Resilience Patterns ===")

    # Test with all resilience patterns combined
    results = []
    for i in range(8):
        request_id = f"combined-{i}"
        try:
            start_time = time.time()
            result = await client.api_with_all_patterns(request_id)
            elapsed = time.time() - start_time
            print(f"Request {request_id} succeeded after {elapsed:.2f}s: {result}")
            results.append(
                {"status": "success", "request_id": request_id, "time": elapsed}
            )
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            print(f"Request {request_id} timed out after {elapsed:.2f}s")
            results.append({"status": "timeout", "request_id": request_id})
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"Request {request_id} failed after {elapsed:.2f}s: {e}")
            results.append(
                {"status": "failure", "request_id": request_id, "error": str(e)}
            )

    # Print summary
    successes = sum(1 for r in results if r["status"] == "success")
    timeouts = sum(1 for r in results if r["status"] == "timeout")
    failures = sum(1 for r in results if r["status"] == "failure")
    print(
        f"Combined pattern results: {successes} successful, {timeouts} timeouts, {failures} failures"
    )
    print(f"Final performance metrics: {client.metrics}")


async def main():
    print("Starting Resilience Patterns Examples...")

    # Create client
    client = ResilientClient()

    try:
        # Run each demonstration
        await demonstrate_basic_api_call(client)
        await demonstrate_timeout_pattern(client)
        await demonstrate_retry_pattern(client)
        await demonstrate_circuit_breaker_pattern(client)
        await demonstrate_bulkhead_pattern(client)
        await demonstrate_combined_patterns(client)
    finally:
        # Ensure client is closed properly
        await client.close()

    print("\nAll resilience pattern examples completed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Examples interrupted by user")
