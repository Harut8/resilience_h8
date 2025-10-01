"""Example demonstrating Redis-based distributed resilience patterns.

This example shows how to use Redis-backed rate limiters and circuit breakers
that maintain consistent state across multiple service instances.

Requirements:
    - redis[hiredis]>=5.0.0
    - Running Redis instance on localhost:6379
"""

import asyncio
import random

import structlog
from redis.asyncio import Redis
from resilience_h8.resilience.redis_circuit_breaker import RedisCircuitBreaker
from resilience_h8.resilience.redis_rate_limiter import (
    RedisFixedWindowRateLimiter,
    RedisTokenBucketRateLimiter,
)
from resilience_h8.storage.redis_backend import (
    RedisCircuitBreakerStorage,
    RedisRateLimiterStorage,
)

# Setup logging
logger = structlog.get_logger()


async def example_redis_rate_limiter():
    """Demonstrate Redis-based token bucket rate limiter."""
    print("\n=== Example 1: Redis Token Bucket Rate Limiter ===")

    # Create Redis client
    redis_client = Redis.from_url("redis://localhost:6379/0")

    # Create rate limiter storage
    storage = RedisRateLimiterStorage(redis_client=redis_client)

    # Create rate limiter (5 requests per 10 seconds)
    limiter = RedisTokenBucketRateLimiter(
        storage=storage,
        requests_per_period=5,
        period_seconds=10.0,
        name="api_limiter",
    )

    print("Making requests with rate limiting...")

    try:
        # Make requests
        for i in range(7):
            try:

                async def make_request(req_num=i):
                    print(f"  ✓ Request {req_num + 1} successful")
                    return f"Response {req_num + 1}"

                result = await limiter.execute(make_request, wait=False)
                print(f"    Result: {result}")

                # Check remaining capacity
                capacity = await limiter.get_current_capacity_async()
                print(f"    Remaining: {capacity['remaining']}/{capacity['limit']}")

            except Exception as e:
                print(f"  ✗ Request {i + 1} failed: {e}")

            await asyncio.sleep(0.5)

    finally:
        await redis_client.close()


async def example_redis_fixed_window_limiter():
    """Demonstrate Redis-based fixed window rate limiter."""
    print("\n=== Example 2: Redis Fixed Window Rate Limiter ===")

    redis_client = Redis.from_url("redis://localhost:6379/0")
    storage = RedisRateLimiterStorage(redis_client=redis_client)

    # Create fixed window limiter (3 requests per 5 seconds)
    limiter = RedisFixedWindowRateLimiter(
        storage=storage,
        requests_per_period=3,
        period_seconds=5.0,
        name="window_limiter",
    )

    print("Testing fixed window rate limiting...")

    try:
        # First window
        print("Window 1:")
        for i in range(4):
            try:
                result = await limiter.execute(
                    lambda req_num=i: f"Request {req_num + 1}", wait=False
                )
                print(f"  ✓ {result}")
            except Exception as e:
                print(f"  ✗ Request {i + 1} blocked: {type(e).__name__}")

        # Wait for window to reset
        print("\nWaiting 5 seconds for window reset...")
        await asyncio.sleep(5.1)

        # Second window
        print("Window 2:")
        for i in range(3):
            result = await limiter.execute(lambda i=i: f"Request {i + 1}")
            print(f"  ✓ {result}")

    finally:
        await redis_client.close()


async def example_redis_circuit_breaker():
    """Demonstrate Redis-based distributed circuit breaker."""
    print("\n=== Example 3: Redis Distributed Circuit Breaker ===")

    redis_client = Redis.from_url("redis://localhost:6379/0")
    storage = RedisCircuitBreakerStorage(redis_client=redis_client)

    # Create circuit breaker
    breaker = RedisCircuitBreaker(
        name="external_api",
        storage=storage,
        failure_threshold=3,
        recovery_timeout=5.0,
        logger=logger,
    )

    # Simulated API call
    call_count = 0

    async def unstable_api_call():
        nonlocal call_count
        call_count += 1

        # Fail first 3 calls
        if call_count <= 3:
            print(f"  API call {call_count} - Simulating failure")
            raise ConnectionError("API unavailable")

        # Succeed after recovery
        print(f"  API call {call_count} - Success!")
        return {"status": "ok", "data": "response"}

    async def fallback_response():
        print("  → Using fallback response")
        return {"status": "fallback", "data": "cached"}

    try:
        # Make calls that will fail and open the circuit
        print("Making calls to unstable API...")
        for i in range(3):
            try:
                result = await breaker.execute(unstable_api_call, fallback=fallback_response)
                print(f"  Result: {result}")
            except Exception as e:
                print(f"  ✗ Call {i + 1} failed: {e}")

            state = await breaker.get_state_async()
            print(f"  Circuit state: {state}")
            await asyncio.sleep(0.5)

        # Circuit should now be OPEN
        print("\nCircuit is now OPEN - fallback will be used:")
        result = await breaker.execute(unstable_api_call, fallback=fallback_response)
        print(f"  Result: {result}")

        # Wait for recovery timeout
        print(f"\nWaiting {5} seconds for recovery...")
        await asyncio.sleep(5.1)

        # Circuit transitions to HALF_OPEN, successful call closes it
        print("\nCircuit is HALF_OPEN - testing recovery:")
        result = await breaker.execute(unstable_api_call, fallback=fallback_response)
        print(f"  Result: {result}")

        state = await breaker.get_state_async()
        print(f"  Circuit state after recovery: {state}")

    finally:
        await redis_client.close()


async def example_distributed_coordination():
    """Demonstrate distributed rate limiting across multiple workers."""
    print("\n=== Example 4: Distributed Rate Limiting (Multi-Worker) ===")

    redis_client = Redis.from_url("redis://localhost:6379/0")
    storage = RedisRateLimiterStorage(redis_client=redis_client)

    # Shared rate limiter across all workers
    limiter = RedisTokenBucketRateLimiter(
        storage=storage,
        requests_per_period=10,
        period_seconds=5.0,
        name="shared_api_limiter",
    )

    async def worker(worker_id: int, num_requests: int):
        """Simulate a worker making API requests."""
        print(f"Worker {worker_id} starting...")
        successful = 0

        for i in range(num_requests):
            try:

                async def api_call(req_num=i):
                    # Simulate API call
                    await asyncio.sleep(0.01)
                    return f"Worker {worker_id} - Request {req_num + 1}"

                result = await limiter.execute(api_call, wait=False)
                successful += 1
                print(f"  ✓ {result}")

            except Exception:
                print(f"  ✗ Worker {worker_id} - Request {i + 1} rate limited")

            await asyncio.sleep(random.uniform(0.1, 0.3))

        print(f"Worker {worker_id} completed: {successful}/{num_requests} successful")
        return successful

    try:
        # Simulate 3 workers competing for the same rate limit
        print("Starting 3 workers with shared rate limit of 10 req/5s...")

        workers = [
            worker(1, 5),
            worker(2, 5),
            worker(3, 5),
        ]

        results = await asyncio.gather(*workers)
        total_successful = sum(results)

        print(f"\nTotal successful requests: {total_successful}/15")
        print("(Should be close to the rate limit of 10)")

    finally:
        await redis_client.close()


async def example_combined_patterns():
    """Demonstrate combining Redis rate limiter with circuit breaker."""
    print("\n=== Example 5: Combined Redis Patterns ===")

    redis_client = Redis.from_url("redis://localhost:6379/0")

    # Setup storage backends
    rate_storage = RedisRateLimiterStorage(redis_client=redis_client)
    circuit_storage = RedisCircuitBreakerStorage(redis_client=redis_client)

    # Create rate limiter and circuit breaker
    limiter = RedisTokenBucketRateLimiter(
        storage=rate_storage,
        requests_per_period=5,
        period_seconds=10.0,
        name="combined_limiter",
    )

    breaker = RedisCircuitBreaker(
        name="combined_api",
        storage=circuit_storage,
        failure_threshold=3,
        recovery_timeout=5.0,
        logger=logger,
    )

    failure_count = 0

    async def protected_api_call():
        """API call protected by both rate limiting and circuit breaking."""
        nonlocal failure_count

        # Simulate some failures
        if failure_count < 2:
            failure_count += 1
            raise ValueError("Temporary API error")

        await asyncio.sleep(0.1)
        return {"status": "success"}

    async def combined_call():
        """Execute with both rate limiting and circuit breaking."""

        # First apply rate limiting
        async def rate_limited_call():
            # Then apply circuit breaking
            return await breaker.execute(protected_api_call)

        return await limiter.execute(rate_limited_call)

    try:
        print("Making protected API calls with rate limiting + circuit breaking...")

        for i in range(5):
            try:
                result = await combined_call()
                print(f"  ✓ Call {i + 1}: {result}")
            except Exception as e:
                print(f"  ✗ Call {i + 1}: {type(e).__name__} - {e}")

            circuit_state = await breaker.get_state_async()
            rate_capacity = await limiter.get_current_capacity_async()

            print(
                f"    Circuit: {circuit_state}, "
                f"Rate limit: {rate_capacity['remaining']}/{rate_capacity['limit']}"
            )

            await asyncio.sleep(0.5)

    finally:
        await redis_client.close()


async def main():
    """Run all Redis examples."""
    print("╔══════════════════════════════════════════════════════════╗")
    print("║     Redis-Based Distributed Resilience Patterns         ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print("\nMake sure Redis is running on localhost:6379")
    print("Start with: docker run -d -p 6379:6379 redis:7-alpine\n")

    try:
        await example_redis_rate_limiter()
        await example_redis_fixed_window_limiter()
        await example_redis_circuit_breaker()
        await example_distributed_coordination()
        await example_combined_patterns()

        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)

    except ConnectionError as e:
        print("\n✗ Error: Could not connect to Redis")
        print("  Make sure Redis is running on localhost:6379")
        print("  Start with: docker run -d -p 6379:6379 redis:7-alpine")
        print(f"\n  Details: {e}")
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
