# Dockerfile for building and testing aio-uring
# io_uring is Linux-only, so this container provides the build environment

FROM python:3.12-slim-bookworm

LABEL maintainer="Alex <aiexz@yandex.ru>"
LABEL description="Build environment for aio-uring (Linux io_uring async file I/O)"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    liburing-dev \
    git \
    wrk \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml setup.py README.md ./
COPY src/ ./src/
COPY tests/ ./tests/
COPY benchmarks/ ./benchmarks/

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir build wheel Cython pytest pytest-asyncio aiohttp aiofiles aiofile

# Build the extension
RUN pip install --no-cache-dir -e .

# Default command runs tests
CMD ["pytest", "-v", "tests/"]
