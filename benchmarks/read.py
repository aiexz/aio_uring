import asyncio
import time
import os
import gc
import platform
import subprocess
import statistics
import random
import aiofiles
from aio_uring import UringFileIO
import aio_uring
import errno

# Configuration
FILE_SIZE = 1024 * 1024 * 512  # 512 MB
CHUNK_SIZE = 4096  # 4KB
LARGE_CHUNK = 1024 * 64  # 64KB
WARMUP = 1
RUNS = 5
PIN_CPU = 0
FILENAME = "bench_test.dat"
RANDOM_READS = 10000


def generate_test_file():
    if not os.path.exists(FILENAME):
        print(
            f"Generating {FILE_SIZE / 1024 / 1024:.0f} MB test file... ",
            end="",
            flush=True,
        )
        # Write in chunks so we don't allocate the entire file contents in memory
        chunk = 1024 * 1024 * 4  # 4 MB chunks
        written = 0
        with open(FILENAME, "wb") as f:
            while written < FILE_SIZE:
                to_write = min(chunk, FILE_SIZE - written)
                f.write(os.urandom(to_write))
                written += to_write
        print("Done.")


def cleanup():
    if os.path.exists(FILENAME):
        os.remove(FILENAME)


def drop_cache(filename):
    """
    Tells the Linux Kernel to evict this file from RAM (Page Cache).
    This forces the next read to touch the physical disk.
    """
    try:
        # Prefer privileged drop cache on Linux if available
        if platform.system().lower() == "linux" and os.geteuid() == 0:
            try:
                subprocess.run(["sync"], check=True)
                with open("/proc/sys/vm/drop_caches", "w") as fh:
                    fh.write("3\n")
                return
            except Exception as e:
                print(f"[Warning] drop_caches failed: {e}")

        fd = os.open(filename, os.O_RDONLY)
        # 4 = POSIX_FADV_DONTNEED
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        os.close(fd)
    except OSError as e:
        print(f"[Warning] Could not drop cache: {e}")


# --- 1. Standard Blocking I/O ---
def bench_sync_read(chunk_size):
    gc.collect()
    # For parity with aiofiles and io_uring, evict any cached pages so
    # this test reads from physical storage (cold cache).
    drop_cache(FILENAME)
    start = time.perf_counter()
    # Use regular buffered file I/O via Python's built-in `open()` for fairness.
    with open(FILENAME, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
    return time.perf_counter() - start


# --- 2. aiofiles (Thread Pool) ---
async def bench_aiofiles_read(chunk_size):
    gc.collect()
    drop_cache(FILENAME)  # <--- FORCE CACHE MISS
    start = time.perf_counter()
    async with aiofiles.open(FILENAME, "rb") as f:
        while True:
            data = await f.read(chunk_size)
            if not data:
                break
    return time.perf_counter() - start


# --- 3. io_uring (True Async) ---
async def bench_uring_read(chunk_size):
    gc.collect()
    drop_cache(FILENAME)  # <--- FORCE CACHE MISS
    start = time.perf_counter()
    async with aio_uring.open(FILENAME, "rb") as f:
        while True:
            data = await f.read(chunk_size)
            if not data:
                break
    return time.perf_counter() - start


# --- Random Reads ---
def bench_sync_random_read(chunk_size, n_reads=RANDOM_READS):
    """Random seek + read using blocking I/O"""
    gc.collect()
    # Use regular open() + seek+read for fairness and parity with other tests
    drop_cache(FILENAME)
    start = time.perf_counter()
    with open(FILENAME, "rb") as f:
        for _ in range(n_reads):
            idx = random.randrange(0, FILE_SIZE // chunk_size)
            offset = idx * chunk_size
            f.seek(offset)
            data = f.read(chunk_size)
            if not data:
                break
    return time.perf_counter() - start


async def bench_aiofiles_random_read(chunk_size, n_reads=RANDOM_READS):
    """Random seek + read using aiofiles (threadpool)"""
    gc.collect()
    drop_cache(FILENAME)
    start = time.perf_counter()
    async with aiofiles.open(FILENAME, "rb") as f:
        for _ in range(n_reads):
            idx = random.randrange(0, FILE_SIZE // chunk_size)
            offset = idx * chunk_size
            await f.seek(offset)
            data = await f.read(chunk_size)
            if not data:
                break
    return time.perf_counter() - start


async def bench_uring_random_read(chunk_size, n_reads=RANDOM_READS):
    """Random seek + read using io_uring (true async)"""
    gc.collect()
    drop_cache(FILENAME)
    start = time.perf_counter()
    async with aio_uring.open(FILENAME, "rb") as f:
        for _ in range(n_reads):
            idx = random.randrange(0, FILE_SIZE // chunk_size)
            offset = idx * chunk_size
            await f.seek(offset)
            data = await f.read(chunk_size)
            if not data:
                break
    return time.perf_counter() - start


async def main():
    generate_test_file()
    random.seed(0)
    print(
        f"Host: {platform.platform()} Python: {platform.python_version()}\nCPUs: {os.cpu_count()}"
    )
    if (
        platform.system().lower() == "linux"
        and PIN_CPU is not None
        and hasattr(os, "sched_setaffinity")
    ):
        try:
            os.sched_setaffinity(0, {PIN_CPU})
            print(f"Pinned to CPU {PIN_CPU}")
        except Exception:
            print("[INFO] CPU pinning failed; continuing without pinning")

    print(
        f"\n--- Benchmark: COLD CACHE / DISK I/O ({FILE_SIZE / 1024 / 1024:.0f} MB) ---"
    )
    print("Each test will force the kernel to read from physical storage.\n")

    # Test 1: Throughput (Large Chunks)
    print(f"[Test A: Throughput (64KB Chunks)]")

    # Sync
    results = []
    # Warm-up
    for _ in range(WARMUP):
        bench_sync_read(LARGE_CHUNK)
    for i in range(RUNS):
        t = bench_sync_read(LARGE_CHUNK)
        results.append(t)
        print(
            f"Sync Blocking run #{i+1}: {t:.4f}s  ({(FILE_SIZE/1024/1024)/t:.2f} MB/s)"
        )
    print(
        f"Sync Blocking: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # aiofiles
    results = []
    for _ in range(WARMUP):
        await bench_aiofiles_read(LARGE_CHUNK)
    for i in range(RUNS):
        t = await bench_aiofiles_read(LARGE_CHUNK)
        results.append(t)
        print(f"aiofiles run #{i+1}: {t:.4f}s  ({(FILE_SIZE/1024/1024)/t:.2f} MB/s)")
    print(
        f"aiofiles: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # io_uring
    results = []
    for _ in range(WARMUP):
        await bench_uring_read(LARGE_CHUNK)
    for i in range(RUNS):
        t = await bench_uring_read(LARGE_CHUNK)
        results.append(t)
        print(f"io_uring run #{i+1}: {t:.4f}s  ({(FILE_SIZE/1024/1024)/t:.2f} MB/s)")
    print(
        f"io_uring: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # Test 2: IOPS / Overhead (Small Chunks)
    print(f"\n[Test B: IOPS Stress Test (4KB Chunks)]")

    # Sync
    results = []
    for _ in range(WARMUP):
        bench_sync_read(CHUNK_SIZE)
    for i in range(RUNS):
        t = bench_sync_read(CHUNK_SIZE)
        results.append(t)
        print(f"Sync Blocking run #{i+1}: {t:.4f}s")
    print(
        f"Sync Blocking: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # aiofiles
    results = []
    for _ in range(WARMUP):
        await bench_aiofiles_read(CHUNK_SIZE)
    for i in range(RUNS):
        t = await bench_aiofiles_read(CHUNK_SIZE)
        results.append(t)
        print(f"aiofiles run #{i+1}: {t:.4f}s")
    print(
        f"aiofiles: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # io_uring
    results = []
    for _ in range(WARMUP):
        await bench_uring_read(CHUNK_SIZE)
    for i in range(RUNS):
        t = await bench_uring_read(CHUNK_SIZE)
        results.append(t)
        print(f"io_uring run #{i+1}: {t:.4f}s")
    print(
        f"io_uring: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # Test 3: Random Read (seek to random offsets and read 4KB)
    print(f"\n[Test C: Random Read (4KB Chunks) with {RANDOM_READS} seeks)]")
    # Sync
    results = []
    for _ in range(WARMUP):
        bench_sync_random_read(CHUNK_SIZE)
    for i in range(RUNS):
        t = bench_sync_random_read(CHUNK_SIZE)
        results.append(t)
        print(f"Sync Blocking run #{i+1}: {t:.4f}s")
    print(
        f"Sync Blocking: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # aiofiles
    results = []
    for _ in range(WARMUP):
        await bench_aiofiles_random_read(CHUNK_SIZE)
    for i in range(RUNS):
        t = await bench_aiofiles_random_read(CHUNK_SIZE)
        results.append(t)
        print(f"aiofiles run #{i+1}: {t:.4f}s")
    print(
        f"aiofiles: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # io_uring
    results = []
    for _ in range(WARMUP):
        await bench_uring_random_read(CHUNK_SIZE)
    for i in range(RUNS):
        t = await bench_uring_random_read(CHUNK_SIZE)
        results.append(t)
        print(f"io_uring run #{i+1}: {t:.4f}s")
    print(
        f"io_uring: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )
    cleanup()


if __name__ == "__main__":
    asyncio.run(main())
