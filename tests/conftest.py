"""Pytest configuration for resilience library tests."""

import asyncio
import sys
import pytest
import structlog

# Configure structlog for testing
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger("INFO"),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=False,
)


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session.
    
    This overrides the default fixture to use the same event loop for all tests.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
