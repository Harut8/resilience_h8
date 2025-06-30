"""Example usage of resilience patterns.

This module demonstrates how to use the resilience patterns
in a real-world application, showing the integration between
concurrency management and resilience strategies.
"""

from typing import Any, Dict

import structlog
from httpx import AsyncClient, RequestError, TimeoutException

from src.resilience_h8 import ResilienceService, StandardTaskManager
from src.resilience_h8.resilience.bulkhead import StandardBulkhead

logger = structlog.get_logger()

TIMEOUT = 10.0
BASE_URL = "https://example.com/api"
CLIENT = AsyncClient(base_url=BASE_URL, timeout=TIMEOUT)


async def _get_data(endpoint: str = "/data") -> Dict[str, Any]:
    try:
        response = await CLIENT.get(endpoint)
        response.raise_for_status()
        return dict(response.json())
    except Exception as e:
        logger.error(
            "API request failed",
            endpoint=endpoint,
            exception=str(e),
        )
        raise


bulkhead: StandardBulkhead[Any] = StandardBulkhead(
            name="api_data",
            max_concurrent=10,
            max_queue_size=20,
            task_manager=None,
            logger=logger,
)

task_manager: StandardTaskManager[Any, Any] = StandardTaskManager(
        max_workers=10,
        logger=logger
)

resilience_service = ResilienceService(
        task_manager=task_manager,
        logger=logger,
)

with_retry = resilience_service.with_retry(
    max_retries=3,
    backoff_factor=1.0,
    jitter=True,
    retry_on_exceptions=[TimeoutException("Timeout occurred"), RequestError("Request failed")],
    )(_get_data)

with_circuit_breaker = resilience_service.with_circuit_breaker(
    failure_threshold=5,
    recovery_timeout=30.0,
    name="api_client",
    fallback=lambda *args: logger.warning(f"Circuit breaker is open for {args[0]}"),
    )(_get_data)

with_bulkhead = resilience_service.with_bulkhead(
    max_concurrent=10,
    max_queue_size=20,
    timeout=5.0,
    name="api_data",
    )(_get_data)


with_resilience = resilience_service.with_resilience(
    retry_config={
        "max_retries": 3,
        "backoff_factor": 1.0,
        "jitter": True,
        "retry_on_exceptions": [TimeoutException("Timeout occurred"), RequestError("Request failed")],
    },
    circuit_config={
        "failure_threshold": 2,
        "recovery_timeout": 30.0,
        "fallback": lambda *args: logger.warning(f"Circuit breaker is open for {args[0]}"),
        "name": "api_client",
    },
    bulkhead_config={
        "max_concurrent": 10,
        "max_queue_size": 20,
        "timeout": 5.0,
        "name": "api_data",
    },
    timeout=5.0,
    )(_get_data)


async def main() -> None:
    # await with_retry("/data")
    # await with_circuit_breaker("/data")
    # await with_bulkhead("/data")
    await with_resilience("/data")

if __name__ == "__main__":
    import asyncio

    asyncio.run(main())