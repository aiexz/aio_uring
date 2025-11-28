"""Setup script for building Cython extensions with liburing."""

import os
import sys
import platform
from setuptools import setup
from Cython.Build import cythonize
from setuptools.extension import Extension


def get_extensions():
    """Build list of Cython extensions."""

    # Check if we're on Linux (io_uring is Linux-only)
    if sys.platform != "linux":
        print("WARNING: io_uring is only available on Linux.")
        print("Building stub module for development/documentation purposes.")
        # Return empty list on non-Linux platforms
        # The package will still install but the Cython extension won't be built
        return []

    def make_compile_args():
        """Return a conservative set of arch-aware compile flags.

        - Default to `-O3` and `-mtune=generic` for portability.
        - Prefer an explicit arch target instead of `-march=native`.
        - Allow overrides via `AIO_URING_EXTRA_CFLAGS` or the `CFLAGS` env var.
        """
        arch = platform.machine().lower()
        flags = ["-O3"]
        if arch in ("x86_64", "amd64"):
            flags += ["-march=x86-64", "-mtune=generic"]
        elif arch in ("aarch64", "arm64"):
            flags += ["-march=armv8-a", "-mtune=generic"]
        else:
            flags += ["-mtune=generic"]

        # Environment override (user-specified)
        env_flags = os.environ.get("AIO_URING_EXTRA_CFLAGS") or os.environ.get("CFLAGS")
        if env_flags:
            # Basic split; users should quote values correctly in CI or envs
            flags += env_flags.split()
        return flags

    _extra_compile_args = make_compile_args()

    extensions = [
        Extension(
            "aio_uring.core.fast_future",
            sources=["src/aio_uring/core/fast_future.pyx"],
            include_dirs=["src/aio_uring/core"],
            language="c++",
            extra_compile_args=_extra_compile_args,
        ),
        Extension(
            "aio_uring.core.op_context",
            sources=["src/aio_uring/core/op_context.pyx"],
            include_dirs=["src/aio_uring/core"],
            language="c++",
            extra_compile_args=_extra_compile_args,
        ),
        Extension(
            "aio_uring.core.uring_file",
            sources=["src/aio_uring/core/uring_file.pyx"],
            include_dirs=["src/aio_uring/core"],
            language="c++",
            extra_compile_args=_extra_compile_args,
        ),
        Extension(
            "aio_uring.core.UringProactor",
            sources=["src/aio_uring/core/UringProactor.pyx"],
            include_dirs=["src/aio_uring/core"],
            libraries=["uring"],
            language="c++",
            extra_compile_args=_extra_compile_args,
            extra_link_args=["-luring"],
        ),
    ]

    return cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "initializedcheck": False,
        },
    )


setup(
    ext_modules=get_extensions(),
)
