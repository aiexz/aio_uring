import asyncio
import mmap
import time
import os
import gc
import platform
import subprocess
import statistics
import errno
import aiofiles
import aiofiles.os
from aio_uring import UringFileIO

# Configuration
FILE_SIZE = 1024 * 1024 * 512  # 512 MB
CHUNK_SIZE = 1024 * 64  # 64 KB chunks
FILENAME = "bench_write.dat"
WARMUP = 1
RUNS = 5
PIN_CPU = 0


def cleanup():
    if os.path.exists(FILENAME):
        os.remove(FILENAME)


# --- 1. Standard Blocking Write ---
def bench_sync_write(fsync_every_chunk=False):
    cleanup()
    gc.collect()
    mem = mmap.mmap(-1, CHUNK_SIZE)
    mem.write(b"X" * CHUNK_SIZE)
    mem.seek(0)
    data = mem
    chunks = FILE_SIZE // CHUNK_SIZE

    start = time.perf_counter()
    with open(FILENAME, "wb") as f:
        for _ in range(chunks):
            f.write(data)
            if fsync_every_chunk:
                f.flush()
                os.fsync(f.fileno())

    # Ensure disk flush at the end regardless
    if not fsync_every_chunk:
        with open(FILENAME, "ab") as f:
            os.fsync(f.fileno())

    return time.perf_counter() - start


def bench_direct_write(fsync_every_chunk=False, use_direct=False):
    cleanup()
    gc.collect()
    mem = mmap.mmap(-1, CHUNK_SIZE)
    mem.write(b"X" * CHUNK_SIZE)
    mem.seek(0)
    data = mem
    chunks = FILE_SIZE // CHUNK_SIZE

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    use_odirect = use_direct and hasattr(os, "O_DIRECT")
    if use_odirect:
        flags |= os.O_DIRECT
    if fsync_every_chunk:
        if hasattr(os, "O_SYNC"):
            flags |= os.O_SYNC

    start = time.perf_counter()
    fd = os.open(FILENAME, flags, 0o644)
    try:
        for i in range(chunks):
            try:
                # os.write expects bytes - O_DIRECT may require aligned buffers
                os.write(fd, data)
            except OSError as e:
                # If O_DIRECT fails with EINVAL, retry without O_DIRECT
                if use_odirect and e.errno == errno.EINVAL:
                    print(
                        "[INFO] O_DIRECT failed (EINVAL); falling back to non-direct writes"
                    )
                    os.close(fd)
                    # Build fallback flags: remove O_DIRECT and do not truncate existing data
                    fallback_flags = os.O_WRONLY | os.O_CREAT
                    if fsync_every_chunk and hasattr(os, "O_SYNC"):
                        fallback_flags |= os.O_SYNC
                    fd = os.open(FILENAME, fallback_flags, 0o644)
                    # Seek to the current file position and write the failed chunk
                    os.lseek(fd, i * CHUNK_SIZE, os.SEEK_SET)
                    os.write(fd, data)
                else:
                    raise
            if fsync_every_chunk and not hasattr(os, "O_SYNC"):
                os.fsync(fd)
    finally:
        if not fsync_every_chunk:
            os.fsync(fd)
        os.close(fd)
    return time.perf_counter() - start


# --- 2. aiofiles Write ---
async def bench_aiofiles_write(fsync_every_chunk=False):
    cleanup()
    gc.collect()
    mem = mmap.mmap(-1, CHUNK_SIZE)
    mem.write(b"X" * CHUNK_SIZE)
    mem.seek(0)
    data = mem
    chunks = FILE_SIZE // CHUNK_SIZE

    async def _async_fsync(fd):
        # aiofiles does not have fsync, so we use to_thread
        return await asyncio.to_thread(os.fsync, fd)

    start = time.perf_counter()
    async with aiofiles.open(FILENAME, "wb") as f:
        for _ in range(chunks):
            await f.write(data)
            if fsync_every_chunk:
                await f.flush()
                await _async_fsync(f.fileno())

    # Final flush
    if not fsync_every_chunk:
        async with aiofiles.open(FILENAME, "rb") as f:
            await _async_fsync(f.fileno())

    return time.perf_counter() - start


# --- 3. io_uring Write ---
async def bench_uring_write(fsync_every_chunk=False):
    cleanup()
    gc.collect()
    mem = mmap.mmap(-1, CHUNK_SIZE)
    mem.write(b"X" * CHUNK_SIZE)
    mem.seek(0)
    data = mem
    chunks = FILE_SIZE // CHUNK_SIZE

    uio = UringFileIO()
    start = time.perf_counter()

    async with uio.open(FILENAME, "wb") as f:
        for _ in range(chunks):
            # UringFileIO expects bytes/bytearray/memoryview; convert mmap to memoryview
            to_write = memoryview(data) if isinstance(data, mmap.mmap) else data
            await f.write(to_write)
            if fsync_every_chunk:
                await f.flush()

    # Final flush logic included in close/exit if needed,
    # but let's be explicit for benchmark parity
    if not fsync_every_chunk:
        async with uio.open(FILENAME, "ab") as f:
            await f.flush()

    uio.close()
    return time.perf_counter() - start


async def main():
    global FILE_SIZE
    print(f"--- Benchmark: WRITE ({FILE_SIZE / 1024 / 1024:.0f} MB) ---")
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

    # TEST A: Buffered Writes (Kernel Cache)
    print(f"\n[Test A: Buffered Writes (64KB Chunks, No Fsync)]")
    print("Writing to Page Cache. Fast, but unsafe.")

    # Sync
    results = []
    for _ in range(WARMUP):
        bench_sync_write(False)
    for i in range(RUNS):
        t = bench_sync_write(False)
        results.append(t)
        print(
            f"Sync Blocking run #{i+1}:  {t:.4f}s  ({(FILE_SIZE/1024/1024)/t:.2f} MB/s)"
        )
    print(
        f"Sync Blocking: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # aiofiles
    results = []
    for _ in range(WARMUP):
        await bench_aiofiles_write(False)
    for i in range(RUNS):
        t = await bench_aiofiles_write(False)
        results.append(t)
        print(
            f"aiofiles run #{i+1}:       {t:.4f}s  ({(FILE_SIZE/1024/1024)/t:.2f} MB/s)"
        )
    print(
        f"aiofiles: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # io_uring
    results = []
    for _ in range(WARMUP):
        await bench_uring_write(False)
    for i in range(RUNS):
        t = await bench_uring_write(False)
        results.append(t)
        print(
            f"io_uring run #{i+1}:       {t:.4f}s  ({(FILE_SIZE/1024/1024)/t:.2f} MB/s)"
        )
    print(
        f"io_uring: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # TEST B: Durable Writes (Fsync)
    print(f"\n[Test B: DURABLE Writes (64KB Chunks + Fsync)]")
    print("Simulating a database implementation (Write-Ahead Log).")
    print("WARNING: This will be slow.")

    # Using a smaller file for fsync test to save time
    OLD_SIZE = FILE_SIZE
    FILE_SIZE = 1024 * 1024 * 10  # 10 MB only
    print(f"Reduced file size to 10MB for fsync test.")

    results = []
    for _ in range(WARMUP):
        bench_sync_write(True)
    for i in range(RUNS):
        t = bench_sync_write(True)
        results.append(t)
        print(f"Sync Blocking run #{i+1} (fsync):  {t:.4f}s")
    print(
        f"Sync Blocking (fsync): median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    results = []
    for _ in range(WARMUP):
        await bench_aiofiles_write(True)
    for i in range(RUNS):
        t = await bench_aiofiles_write(True)
        results.append(t)
        print(f"aiofiles run #{i+1} (fsync):       {t:.4f}s")
    print(
        f"aiofiles (fsync): median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    results = []
    for _ in range(WARMUP):
        await bench_uring_write(True)
    for i in range(RUNS):
        t = await bench_uring_write(True)
        results.append(t)
        print(f"io_uring run #{i+1} (fsync):       {t:.4f}s")
    print(
        f"io_uring (fsync): median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    FILE_SIZE = OLD_SIZE  # Restore
    cleanup()

    # TEST C: Direct / Sync Writes (O_DIRECT if available, or O_SYNC)
    print(f"\n[Test C: Direct Writes (O_DIRECT/O_SYNC)]")
    # normal direct-like writes (with O_DIRECT if possible)
    results = []
    for _ in range(WARMUP):
        bench_direct_write(False, use_direct=True)
    for i in range(RUNS):
        try:
            t = bench_direct_write(False, use_direct=True)
        except OSError as e:
            print(
                f"O_DIRECT not supported or failed: {e}. Falling back to O_SYNC variant."
            )
            t = bench_direct_write(False, use_direct=False)
        results.append(t)
        print(f"Direct run #{i+1}: {t:.4f}s  ({(FILE_SIZE/1024/1024)/t:.2f} MB/s)")
    print(
        f"Direct Writes: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )

    # Direct with fsync
    print(f"\n[Test C2: Direct Writes + fsync (O_SYNC / fallback)]")
    results = []
    for _ in range(WARMUP):
        bench_direct_write(True, use_direct=False)
    for i in range(RUNS):
        t = bench_direct_write(True, use_direct=False)
        results.append(t)
        print(f"Direct fsync run #{i+1}: {t:.4f}s")
    print(
        f"Direct Writes w/fsync: median={statistics.median(results):.4f}s mean={statistics.mean(results):.4f}s stdev={statistics.pstdev(results):.4f}s"
    )


if __name__ == "__main__":
    asyncio.run(main())
