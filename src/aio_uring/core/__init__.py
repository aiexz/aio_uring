# aio_uring/core - Core Cython modules for io_uring file I/O integration

"""
Core io_uring implementation modules for file I/O.

This package contains the Cython implementation for async file I/O:

- base: Common constants and operation type enum
- fast_future: Lightweight Future implementation for asyncio integration
- op_context: Reusable operation context for io_uring submissions
- uring_file: Async file object for file I/O operations
- UringProactor: io_uring proactor for file operations

The modules are organized to minimize compilation dependencies and improve
maintainability while keeping the same high-performance characteristics.
"""

# Re-export main classes for convenience
from .UringProactor import UringProactor
from .fast_future import FastFuture
from .op_context import UringOpContext
from .uring_file import UringFile

__all__ = [
    "UringProactor",
    "FastFuture",
    "UringOpContext",
    "UringFile",
]
