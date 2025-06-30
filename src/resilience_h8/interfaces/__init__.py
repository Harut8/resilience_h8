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

from src.resilience_h8.interfaces.concurrency import WorkerPool, TaskManager
from src.resilience_h8.interfaces.resilience import ResilienceDecorator
