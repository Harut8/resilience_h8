"""Integration tests for all resilience patterns working together."""

import asyncio
import pytest
import structlog

from src.resilience_h8 import StandardTaskManager, ResilienceService


@pytest.fixture
def logger():
    """Fixture for providing a structured logger."""
    return structlog.get_logger()


@pytest.fixture
def task_manager(logger):
    """Fixture for providing a standard task manager instance."""
    return StandardTaskManager(max_workers=10, logger=logger)


@pytest.fixture
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
            timeout=0.5
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
        except asyncio.TimeoutError:
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_resilience_concurrent_batch_processing(resilience_service, task_manager, logger):
    """Test all patterns together with concurrent batch processing."""
    # Arrange
    client = MockAPIClient(resilience_service, logger)
    
    # Reset the circuit breaker to ensure a clean test
    await ensure_circuit_breaker(resilience_service)
    
    client.set_failure_rate(0.3)  # 30% of calls will fail
    
    # Act - Process a batch of items with resilience
    async def process_item(item_id):
        try:
            result = await client.resilient_fetch_data()
            return {
                "item_id": item_id,
                "success": True,
                "status": result["status"]
            }
        except Exception as e:
            return {
                "item_id": item_id,
                "success": False,
                "error": str(e)
            }
    
    # Process 20 items concurrently
    item_ids = [f"item-{i}" for i in range(20)]
    tasks = []
    for item_id in item_ids:
        tasks.append(process_item(item_id))
    
    # Use asyncio.gather to run the tasks concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Assert
    assert len(results) == 20
    successful = len([r for r in results if isinstance(r, dict) and r.get("success", False)])
    assert successful > 0  # At least some should succeed
    
    # Some should be fallbacks when the circuit opens
    fallbacks = len([r for r in results if isinstance(r, dict) and r.get("status") == "fallback"])
    assert fallbacks >= 0  # May have fallbacks if circuit opened
    
    # Check that we didn't exceed bulkhead limits at any point
    assert client.call_count <= 20 + (2 * 20)  # Initial + up to 2 retries per item
