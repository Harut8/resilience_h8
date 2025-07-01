"""Core interfaces package.

This package contains interface definitions that decouple
implementation details from core abstractions, enabling better
testability and flexibility.
"""



__all__ = [
    # Concurrency interfaces
    "TaskManager", 
    "WorkerPool",
    
    # Resilience interfaces
    "ResilienceDecorator",
]

from .concurrency import WorkerPool, TaskManager
from .resilience import ResilienceDecorator
