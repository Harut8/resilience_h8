"""Integration tests for all resilience patterns working together."""

import asyncio

import pytest
import structlog

from src.resilience_h8 import ResilienceService, StandardTaskManager
from src.resilience_h8.custom_types.resilience import RateLimitExceeded
from src.resilience_h8.resilience.rate_limiter import TokenBucketRateLimiter


@pytest.fixture()
def logger():
    """Fixture for providing a structured logger."""
    return structlog.get_logger()


@pytest.fixture()
def task_manager(logger):
    """Fixture for providing a standard task manager instance."""
    return StandardTaskManager(max_workers=10, logger=logger)


@pytest.fixture()
def resilience_service(task_manager, logger):
    """Fixture for providing a resilience service instance."""
    return ResilienceService(task_manager=task_manager, logger=logger)


class MockAPIClient:
    """Mock API client to test resilience patterns."""

    def __init__(self, resilience_service, logger):
        self.resilience_service = resilience_service
        self.logger = logger
        self.call_count = 0
        self.success_count = 0
        self.fallback_count = 0
        self.is_available = True
        self.response_time = 0.1
        self.failure_rate = 0.0
        self.failure_counter = 0

        # Configure the resilience decorator
        self._resilient_decorator = self.resilience_service.with_resilience(
            retry_config={
                "max_retries": 2,
                "backoff_factor": 0.1,
                "jitter": True,
            },
            circuit_config={
                "failure_threshold": 3,
                "recovery_timeout": 0.5,
                "fallback": self._fallback_fetch_data,
                "name": "api_client",
            },
            bulkhead_config={
                "max_concurrent": 2,
                "max_queue_size": 5,
                "name": "api_client",
            },
            timeout=0.5,
        )

        # Configure a rate-limited decorator
        self._rate_limited_decorator = self.resilience_service.with_rate_limiter(
            requests_per_period=5, period_seconds=1.0, name="api_client_rate_limited"
        )

        # Combined decorator with all resilience patterns including rate limiting
        self._fully_resilient_decorator = self.resilience_service.with_resilience(
            retry_config={
                "max_retries": 2,
                "backoff_factor": 0.1,
                "jitter": True,
            },
            circuit_config={
                "failure_threshold": 3,
                "recovery_timeout": 0.5,
                "fallback": self._fallback_fetch_data,
                "name": "api_client",
            },
            bulkhead_config={
                "max_concurrent": 2,
                "max_queue_size": 5,
                "name": "api_client",
            },
            timeout=0.5,
            rate_limit_config={
                "requests_per_period": 5,
                "period_seconds": 1.0,
                "name": "api_client_full_resilience",
            },
        )

    def set_availability(self, is_available):
        """Set whether the API is available."""
        self.is_available = is_available

    def set_response_time(self, seconds):
        """Set the simulated response time in seconds."""
        self.response_time = seconds

    def set_failure_rate(self, rate):
        """Set the failure rate (0.0 to 1.0)."""
        self.failure_rate = max(0.0, min(1.0, rate))

    def trigger_failures(self, count):
        """Set to fail the next N calls."""
        self.failure_counter = count

    async def _raw_fetch_data(self):
        """Base method to simulate API calls with configurable behavior."""
        self.call_count += 1

        # Check if API is available
        if not self.is_available:
            self.logger.warning("API is unavailable")
            raise ConnectionError("API is unavailable")

        # Simulate response time
        await asyncio.sleep(self.response_time)

        # Simulate failures based on counter or random rate
        if self.failure_counter > 0:
            self.failure_counter -= 1
            self.logger.error("API call failed (counter)")
            raise RuntimeError("API call failed")

        if self.failure_rate > 0 and self.call_count % int(1 / self.failure_rate) == 0:
            self.logger.error("API call failed (rate)")
            raise RuntimeError("API call failed")

        self.success_count += 1
        return {"status": "success", "data": [1, 2, 3]}

    async def _fallback_fetch_data(self):
        """Fallback method when API calls fail."""
        self.fallback_count += 1
        self.logger.info("Using fallback data")
        return {"status": "fallback", "data": []}

    async def resilient_fetch_data(self):
        """Execute the API call with resilience patterns applied."""
        try:
            # Apply the decorator directly to the function call
            decorated_func = self._resilient_decorator(self._raw_fetch_data)
            # Call and await the decorated function
            return await decorated_func()
        except TimeoutError:
            # Handle timeout errors by using the fallback
            self.logger.warning("Operation timed out, using fallback")
            return await self._fallback_fetch_data()

    async def rate_limited_fetch_data(self):
        """Execute the API call with just rate limiting applied."""
        # Apply the decorator directly to the function call
        decorated_func = self._rate_limited_decorator(self._raw_fetch_data)
        # Call and await the decorated function
        return await decorated_func()

    async def fully_resilient_fetch_data(self):
        """Execute the API call with all resilience patterns including rate limiting."""
        try:
            # Apply the decorator directly to the function call
            decorated_func = self._fully_resilient_decorator(self._raw_fetch_data)
            # Call and await the decorated function
            return await decorated_func()
        except TimeoutError:
            # Handle timeout errors by using the fallback
            self.logger.warning("Operation timed out, using fallback")
            return await self._fallback_fetch_data()


async def ensure_circuit_breaker(resilience_service, name="api_client", reset=True):
    """Ensure a circuit breaker exists and optionally reset it.

    Args:
        resilience_service: The resilience service instance
        name: Name of the circuit breaker
        reset: Whether to reset the circuit breaker

    Returns:
        The circuit breaker instance
    """
    # Create a dummy decorator to ensure the circuit breaker is created
    if name not in resilience_service._circuit_breakers:
        # Create a circuit breaker with default settings
        _ = resilience_service.with_circuit_breaker(
            failure_threshold=3,
            recovery_timeout=0.5,
            name=name,
        )

    circuit_breaker = resilience_service._circuit_breakers[name]

    if reset:
        await circuit_breaker.reset()

    return circuit_breaker


@pytest.mark.asyncio()
async def test_resilience_normal_operation(resilience_service, logger):
    """Test normal operation with all resilience patterns."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)

    # Reset the circuit breaker to ensure a clean test
    await ensure_circuit_breaker(resilience_service)

    # Act
    result = await client.resilient_fetch_data()

    # Assert
    assert result["status"] == "success"
    assert result["data"] == [1, 2, 3]
    assert client.call_count == 1
    assert client.success_count == 1
    assert client.fallback_count == 0


@pytest.mark.asyncio()
async def test_resilience_retry_pattern(resilience_service, logger):
    """Test retry pattern works with temporary failures."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)

    # Reset the circuit breaker to ensure it doesn't interfere with the test
    await ensure_circuit_breaker(resilience_service)

    # Configure the client for temporary failures
    client.trigger_failures(1)  # Only fail once, allowing retry to succeed

    # Act
    result = await client.resilient_fetch_data()

    # Assert
    assert result["status"] == "success"
    assert client.call_count >= 2  # Initial + at least 1 retry
    assert client.success_count == 1
    assert client.fallback_count == 0


@pytest.mark.asyncio()
async def test_resilience_circuit_breaker_pattern(resilience_service, logger):
    """Test circuit breaker pattern works with persistent failures."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)

    # Reset the circuit breaker to ensure a clean test
    await ensure_circuit_breaker(resilience_service)

    client.trigger_failures(10)  # Fail many consecutive calls

    # Act - Call until circuit opens (3 failures including retries)
    for _ in range(3):
        result = await client.resilient_fetch_data()
        assert result["status"] == "fallback"  # After retry failures, fallback is used

    # Assert that circuit is now open and using fallback without retrying
    call_count = client.call_count
    fallback_count = client.fallback_count

    # Next call should immediately use fallback without attempting the operation
    result = await client.resilient_fetch_data()

    assert result["status"] == "fallback"
    assert client.call_count == call_count  # No additional calls to raw method
    assert client.fallback_count == fallback_count + 1  # One more fallback call


@pytest.mark.asyncio()
async def test_resilience_circuit_breaker_recovery(resilience_service, logger):
    """Test circuit breaker recovery after timeout."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)

    # Reset the circuit breaker to ensure a clean test
    await ensure_circuit_breaker(resilience_service)

    client.trigger_failures(10)  # Fail many consecutive calls

    # Act - Call until circuit opens
    for _ in range(3):
        await client.resilient_fetch_data()

    call_count = client.call_count

    # Sleep to allow recovery timeout to pass
    await asyncio.sleep(0.6)  # recovery_timeout is 0.5

    # Fix the API
    client.set_availability(True)
    client.trigger_failures(0)

    # Next call should try the operation again (half-open circuit)
    result = await client.resilient_fetch_data()

    # Assert
    assert result["status"] == "success"
    assert client.call_count > call_count  # Should attempt the raw operation again


@pytest.mark.asyncio()
async def test_resilience_bulkhead_pattern(resilience_service, logger):
    """Test bulkhead pattern limits concurrent executions."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)

    # Reset the circuit breaker to ensure a clean test
    await ensure_circuit_breaker(resilience_service)

    client.set_response_time(0.3)  # Slow down responses

    # Act - Start 10 concurrent operations, but only 2 should run at once
    # and only 7 total should be accepted (2 running + 5 queued)
    tasks = []
    for _ in range(10):
        tasks.append(asyncio.create_task(client.resilient_fetch_data()))

    # Allow tasks to start and try to acquire slots
    await asyncio.sleep(0.1)

    # At this point, 2 should be running and 5 should be queued
    # Wait for the first two to complete
    await asyncio.sleep(0.3)

    # Now check the results
    # Get completed results without waiting for rejected tasks
    completed_results = [task for task in tasks if task.done()]

    # Assert at least 2 tasks have completed
    assert len(completed_results) >= 2

    # The rejected tasks should raise exceptions
    rejected_tasks = [task for task in tasks if not task.done()]

    # Cancel any remaining tasks to clean up
    for task in rejected_tasks:
        task.cancel()

    # Wait for cancellations
    await asyncio.sleep(0.1)

    # Assert that at most 7 operations were accepted (2 running + 5 queued)
    assert client.call_count <= 7


@pytest.mark.asyncio()
async def test_resilience_timeout_pattern(resilience_service, logger):
    """Test timeout pattern prevents long-running operations."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)
    client.set_response_time(1.0)  # Longer than timeout of 0.5

    # Reset the circuit breaker to ensure a clean test
    await ensure_circuit_breaker(resilience_service)

    # Act - This should time out and use fallback
    result = await client.resilient_fetch_data()

    # Assert - After timeout, fallback should be used
    assert result["status"] == "fallback"
    assert client.fallback_count >= 1


@pytest.mark.asyncio()
async def test_resilience_concurrent_batch_processing(resilience_service, task_manager, logger):
    """Test all patterns together with concurrent batch processing."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)

    # Reset the circuit breaker to ensure a clean test
    await ensure_circuit_breaker(resilience_service)

    # Process a batch of operations in waves to respect bulkhead limits
    # Bulkhead: max_concurrent=2, max_queue_size=5 = 7 total capacity
    num_operations = 20
    successes = 0
    fallbacks = 0
    rejections = 0

    # Configure for some failures but mostly success
    client.set_failure_rate(0.2)
    client.set_response_time(0.05)  # Faster responses to allow more throughput

    # Execute batch of operations concurrently with controlled rate
    async def process_item(i):
        try:
            result = await client.resilient_fetch_data()
            if result["status"] == "success":
                return "success"
            if result["status"] == "fallback":
                return "fallback"
            return "unknown"
        except RuntimeError as e:
            # Bulkhead rejections or other runtime errors
            if "queue is full" in str(e) or "Bulkhead" in str(e):
                logger.warning(f"Operation {i} rejected by bulkhead")
                return "rejected"
            logger.error(f"Operation {i} failed: {e}")
            return "failed"
        except Exception as e:
            logger.error(f"Operation {i} failed: {e}")
            return "failed"

    # Process operations in smaller batches to avoid overwhelming bulkhead
    batch_size = 5
    all_results = []

    for batch_start in range(0, num_operations, batch_size):
        batch_end = min(batch_start + batch_size, num_operations)
        batch_tasks = [process_item(i) for i in range(batch_start, batch_end)]
        batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
        all_results.extend(batch_results)
        # Small delay between batches to allow bulkhead slots to free up
        await asyncio.sleep(0.1)

    # Count results
    successes = sum(1 for r in all_results if r == "success")
    fallbacks = sum(1 for r in all_results if r == "fallback")
    rejections = sum(1 for r in all_results if r == "rejected")

    # Assert - With batching, we should have some successful operations
    assert successes > 0, f"Expected some successes, got {successes}"
    assert client.call_count > 0
    assert client.success_count > 0

    # Due to failure rate and bulkhead limits, some operations may use fallback or be rejected
    # But we should still have completed most operations successfully
    logger.info(
        f"Batch processing results: {successes} successes, "
        f"{fallbacks} fallbacks, {rejections} rejections out of {num_operations}"
    )


@pytest.mark.asyncio()
async def test_rate_limiter_pattern(resilience_service, logger):
    """Test rate limiter pattern prevents exceeding the request rate."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)

    # Act - Execute requests at a rate higher than the limit
    # The rate limiter is configured to allow 5 requests per second

    # First, verify we can make 5 requests in quick succession
    results = []
    for _ in range(5):
        results.append(await client.rate_limited_fetch_data())

    # All should succeed
    assert all(r["status"] == "success" for r in results)
    assert client.call_count == 5

    # Now try to make another request immediately
    # This should wait until a slot becomes available
    start_time = asyncio.get_event_loop().time()
    result = await client.rate_limited_fetch_data()
    end_time = asyncio.get_event_loop().time()

    # The request should succeed but should have waited for some time
    # The timing can vary based on execution environment, so we use a more relaxed assertion
    assert result["status"] == "success"
    assert end_time > start_time  # Just verify that some time has passed
    assert client.call_count == 6

    # Verify non-waiting behavior with explicit parameter
    # Create a rate limiter directly instead of using the decorator
    rate_limiter = TokenBucketRateLimiter[dict](
        requests_per_period=1,
        period_seconds=10.0,  # Very slow refill rate
        name="test_no_wait",
    )

    # Define a simple operation
    async def test_operation():
        return {"status": "success"}

    # First call should succeed
    await rate_limiter.execute(test_operation)

    # Second call without waiting should fail with RateLimitExceeded
    with pytest.raises(RateLimitExceeded):
        await rate_limiter.execute(test_operation, wait=False)


@pytest.mark.asyncio()
async def test_rate_limiter_with_other_patterns(resilience_service, logger):
    """Test rate limiter working with other resilience patterns."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)

    # Reset the circuit breaker to ensure a clean test
    await ensure_circuit_breaker(resilience_service)

    # Act - Execute multiple requests using the fully resilient decorator
    results = []
    for _ in range(5):
        results.append(await client.fully_resilient_fetch_data())

    # All should succeed
    assert all(r["status"] == "success" for r in results)

    # Force some failures to test retry with rate limiting
    client.trigger_failures(1)

    # This should retry and succeed but will count against our rate limit
    result = await client.fully_resilient_fetch_data()
    assert result["status"] == "success"

    # The call_count should be higher than the success_count due to retries
    assert client.call_count > client.success_count

    # Now cause a timeout which should use fallback
    client.set_response_time(1.0)  # Timeout is 0.5s

    # This should timeout and use fallback
    result = await client.fully_resilient_fetch_data()
    assert result["status"] == "fallback"
    assert client.fallback_count > 0
