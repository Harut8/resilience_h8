# Resilience_H8

A robust Python library for implementing resilience patterns in microservices architectures with concurrency control.

## Features

- **Bulkhead Pattern**: Isolate failures and prevent system-wide cascading failures by limiting concurrent operations
- **Circuit Breaker Pattern**: Fail fast and apply backpressure when systems are overloaded
- **Retry Pattern**: Automatically retry failed operations with configurable backoff and jitter
- **Timeout Pattern**: Set maximum execution times for operations to prevent resource exhaustion
- **Concurrency Control**: Built-in task management for safe async operations
- **Decorator API**: Simple function decorators for all resilience patterns
- **Composable Patterns**: Combine multiple resilience patterns with proper execution order
- **Type Safety**: Full typing support with generics for better IDE integration

## Installation

```bash
# From PyPI (recommended for production)
pip install resilience_h8

# From source (for development)
git clone https://github.com/yourusername/resilience_h8.git
cd resilience_h8
pip install -e .
```

## Quick Start

```python
import asyncio
import structlog
from httpx import AsyncClient, RequestError, TimeoutException

from resilience_h8 import ResilienceService, StandardTaskManager

# Setup logging and task manager
logger = structlog.get_logger()
task_manager = StandardTaskManager(max_workers=10, logger=logger)

# Create resilience service
resilience = ResilienceService(task_manager=task_manager, logger=logger)

# Apply resilience patterns to an async function
@resilience.with_retry(max_retries=3, jitter=True)
@resilience.with_circuit_breaker(failure_threshold=5, name="api_client")
@resilience.with_timeout(timeout=5.0)
async def fetch_data(client: AsyncClient, url: str):
    response = await client.get(url)
    response.raise_for_status()
    return response.json()

# Or use the combined decorator
@resilience.with_resilience(
    retry_config={
        "max_retries": 3,
        "backoff_factor": 1.0,
        "jitter": True,
        "retry_on_exceptions": [TimeoutException, RequestError]
    },
    circuit_config={
        "failure_threshold": 5,
        "recovery_timeout": 30.0,
        "name": "api_client"
    },
    timeout=5.0
)
async def fetch_data_combined(client: AsyncClient, url: str):
    response = await client.get(url)
    response.raise_for_status()
    return response.json()

# Example usage
async def main():
    client = AsyncClient()
    try:
        data = await fetch_data(client, "https://api.example.com/data")
        print(f"Received data: {data}")
    finally:
        await client.aclose()
        task_manager.cancel_all_tasks()

if __name__ == "__main__":
    asyncio.run(main())
```

## Resilience Patterns

### Bulkhead Pattern

Limits the number of concurrent operations to prevent resource exhaustion.

```python
from resilience_h8 import StandardBulkhead

# Create a bulkhead
bulkhead = StandardBulkhead(
    name="api_client",
    max_concurrent=10,
    max_queue_size=20,
    logger=logger
)

# Use with decorator
@bulkhead.with_bulkhead(timeout=5.0)
async def my_function():
    # Your code here
    pass

# Or directly
result = await bulkhead.execute(my_function, timeout=5.0)
```

### Circuit Breaker Pattern

Prevents cascading failures by failing fast when a dependent service is unavailable.

```python
from resilience_h8 import CircuitBreaker, StandardCircuitBreaker

# Create a circuit breaker
circuit_breaker = StandardCircuitBreaker(
    name="api_client",
    failure_threshold=5,
    recovery_timeout=30.0,
    logger=logger
)

# Use with decorator
@circuit_breaker.circuit_break(fallback=fallback_function)
async def my_function():
    # Your code here
    pass

# Or directly
result = await circuit_breaker.execute(my_function, fallback=fallback_function)
```

### Retry Pattern

Automatically retries failed operations with configurable backoff and jitter.

```python
from resilience_h8 import RetryHandler, StandardRetryHandler

# Create a retry handler
retry_handler = StandardRetryHandler(logger=logger)

# Use with decorator
@retry_handler.retry(
    max_retries=3,
    backoff_factor=1.0,
    jitter=True,
    retry_on_exceptions=[ConnectionError, TimeoutError]
)
async def my_function():
    # Your code here
    pass

# Or directly
result = await retry_handler.execute(
    my_function,
    max_retries=3,
    backoff_factor=1.0,
    jitter=True
)
```

## Development

### Setting up the Development Environment

```bash
# Clone the repository
git clone https://github.com/yourusername/resilience_h8.git
cd resilience_h8

# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run type checking
mypy --config-file=mypy.ini
```

### Project Structure

```
resilience_h8/
├── src/
│   └── resilience_h8/
│       ├── concurrency/      # Concurrency management tools
│       ├── custom_types/     # Custom type definitions
│       ├── interfaces/       # Interface definitions
│       └── resilience/       # Resilience pattern implementations
├── tests/                    # Test suite
├── pyproject.toml           # Package configuration
└── README.md                # This file
```

## License

MIT
