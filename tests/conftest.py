"""Pytest configuration for aio_uring tests."""

import sys
import pytest


def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line(
        "markers", "linux_only: mark test as Linux-only (deselected on other platforms)"
    )


def pytest_collection_modifyitems(config, items):
    """Skip Linux-only tests on non-Linux platforms."""
    if sys.platform == "linux":
        return

    skip_linux = pytest.mark.skip(reason="io_uring is only available on Linux")
    for item in items:
        if "linux_only" in item.keywords:
            item.add_marker(skip_linux)
