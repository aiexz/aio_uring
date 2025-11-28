"""Tests for thread safety and concurrency."""

import asyncio
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

# Skip all tests if not on Linux
pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="io_uring is only available on Linux"
)


@pytest.fixture
def uio():
    """Create a UringFileIO instance for testing."""
    from aio_uring import UringFileIO

    instance = UringFileIO()
    yield instance
    instance.close()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    dir_path = tempfile.mkdtemp()
    yield dir_path
    import shutil

    shutil.rmtree(dir_path, ignore_errors=True)


class TestConcurrentOperations:
    """Test concurrent file operations."""

    async def test_concurrent_reads_same_file(self, uio, temp_dir):
        """Test multiple concurrent reads from the same file."""
        test_file = os.path.join(temp_dir, "concurrent_read.bin")
        data = b"X" * 10000

        with open(test_file, "wb") as f:
            f.write(data)

        async def read_file():
            async with uio.open(test_file, "rb") as f:
                return await f.read()

        # Run 10 concurrent reads
        results = await asyncio.gather(*[read_file() for _ in range(10)])

        # All reads should return the same data
        for result in results:
            assert result == data

    async def test_concurrent_writes_different_files(self, uio, temp_dir):
        """Test multiple concurrent writes to different files."""
        data = b"Test data for concurrent write"

        async def write_file(idx):
            path = os.path.join(temp_dir, f"concurrent_write_{idx}.bin")
            async with uio.open(path, "wb") as f:
                await f.write(data)
            return path

        # Run 10 concurrent writes
        paths = await asyncio.gather(*[write_file(i) for i in range(10)])

        # Verify all files were written correctly
        for path in paths:
            with open(path, "rb") as f:
                assert f.read() == data

    async def test_concurrent_read_write_different_files(self, uio, temp_dir):
        """Test concurrent reads and writes to different files."""
        read_file = os.path.join(temp_dir, "read_file.bin")
        read_data = b"Read data" * 1000

        with open(read_file, "wb") as f:
            f.write(read_data)

        write_data = b"Write data" * 1000

        async def do_read():
            async with uio.open(read_file, "rb") as f:
                return await f.read()

        async def do_write(idx):
            path = os.path.join(temp_dir, f"write_{idx}.bin")
            async with uio.open(path, "wb") as f:
                await f.write(write_data)
            return path

        # Mix reads and writes
        tasks = []
        for i in range(5):
            tasks.append(do_read())
            tasks.append(do_write(i))

        results = await asyncio.gather(*tasks)

        # Verify reads
        for i in range(0, len(results), 2):
            assert results[i] == read_data

        # Verify writes
        for i in range(1, len(results), 2):
            with open(results[i], "rb") as f:
                assert f.read() == write_data

    async def test_many_concurrent_small_operations(self, uio, temp_dir):
        """Test a large number of small concurrent operations."""
        num_ops = 100
        data = b"Small data"

        async def write_and_read(idx):
            path = os.path.join(temp_dir, f"small_{idx}.bin")

            # Write
            async with uio.open(path, "wb") as f:
                await f.write(data)

            # Read back
            async with uio.open(path, "rb") as f:
                result = await f.read()

            return result

        results = await asyncio.gather(*[write_and_read(i) for i in range(num_ops)])

        for result in results:
            assert result == data


class TestThreadSafety:
    """Test thread safety of UringFileIO."""

    async def test_uio_used_from_single_thread(self, temp_dir):
        """Test that UringFileIO works correctly when used from a single thread."""
        from aio_uring import UringFileIO

        uio = UringFileIO()
        test_file = os.path.join(temp_dir, "single_thread.bin")
        data = b"Single thread test data"

        try:
            async with uio.open(test_file, "wb") as f:
                await f.write(data)

            async with uio.open(test_file, "rb") as f:
                result = await f.read()

            assert result == data
        finally:
            uio.close()

    def test_multiple_uio_instances_different_threads(self, temp_dir):
        """Test that separate UringFileIO instances work in different threads."""
        from aio_uring import UringFileIO

        results = []
        errors = []

        def thread_func(thread_id):
            try:
                uio = UringFileIO()

                async def do_io():
                    path = os.path.join(temp_dir, f"thread_{thread_id}.bin")
                    data = f"Thread {thread_id} data".encode()

                    async with uio.open(path, "wb") as f:
                        await f.write(data)

                    async with uio.open(path, "rb") as f:
                        result = await f.read()

                    return result == data

                result = asyncio.run(do_io())
                results.append(result)
                uio.close()
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            t = threading.Thread(target=thread_func, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert not errors, f"Errors occurred: {errors}"
        assert all(results), "Some thread operations failed"

    async def test_shared_uio_sequential_operations(self, uio, temp_dir):
        """Test sequential operations on a shared UringFileIO instance."""
        test_file = os.path.join(temp_dir, "sequential.bin")

        for i in range(10):
            data = f"Iteration {i}".encode()

            async with uio.open(test_file, "wb") as f:
                await f.write(data)

            async with uio.open(test_file, "rb") as f:
                result = await f.read()

            assert result == data


class TestResourceManagement:
    """Test resource management and cleanup."""

    async def test_file_closed_after_context_manager(self, uio, temp_dir):
        """Test that files are properly closed after context manager exits."""
        test_file = os.path.join(temp_dir, "close_test.bin")

        async with uio.open(test_file, "wb") as f:
            await f.write(b"test")
            fd = f.fileno()

        assert f.closed

        # The file descriptor should be closed (this is OS-dependent behavior)
        # We can't directly test fd closure, but we can verify the file state

    async def test_file_closed_on_exception(self, uio, temp_dir):
        """Test that files are closed even when exceptions occur."""
        test_file = os.path.join(temp_dir, "exception_test.bin")

        try:
            async with uio.open(test_file, "wb") as f:
                await f.write(b"test")
                raise ValueError("Intentional error")
        except ValueError:
            pass

        assert f.closed

    async def test_uio_close_releases_resources(self, temp_dir):
        """Test that closing UringFileIO releases resources."""
        from aio_uring import UringFileIO

        uio = UringFileIO()
        test_file = os.path.join(temp_dir, "resource_test.bin")

        async with uio.open(test_file, "wb") as f:
            await f.write(b"test")

        uio.close()
        assert uio.closed

        # Should not be able to use closed instance
        with pytest.raises(RuntimeError, match="closed"):
            uio.open(test_file, "rb")

    async def test_many_file_opens_closes(self, uio, temp_dir):
        """Test opening and closing many files doesn't leak resources."""
        test_file = os.path.join(temp_dir, "many_opens.bin")

        with open(test_file, "wb") as f:
            f.write(b"test data")

        # Open and close many times
        for _ in range(100):
            async with uio.open(test_file, "rb") as f:
                _ = await f.read()

        # Should still work
        async with uio.open(test_file, "rb") as f:
            data = await f.read()

        assert data == b"test data"


class TestErrorRecovery:
    """Test error handling and recovery."""

    async def test_recover_after_nonexistent_file(self, uio, temp_dir):
        """Test that operations continue after failing to open nonexistent file."""
        nonexistent = "/nonexistent/path/file.txt"
        test_file = os.path.join(temp_dir, "recovery.bin")

        # First, try to open nonexistent file (should fail)
        with pytest.raises(OSError):
            async with uio.open(nonexistent, "rb") as f:
                pass

        # Should still be able to do operations
        async with uio.open(test_file, "wb") as f:
            await f.write(b"recovery test")

        async with uio.open(test_file, "rb") as f:
            data = await f.read()

        assert data == b"recovery test"

    async def test_partial_write_recovery(self, uio, temp_dir):
        """Test behavior after partial operations."""
        test_file = os.path.join(temp_dir, "partial.bin")

        # Write some data
        async with uio.open(test_file, "wb") as f:
            await f.write(b"initial data")

        # Open for writing but don't close properly
        f = await uio._open_internal(test_file, "wb")
        await f.write(b"new data")
        await f.close()

        # Should be able to read the data
        async with uio.open(test_file, "rb") as f:
            data = await f.read()

        assert data == b"new data"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    async def test_zero_byte_write(self, uio, temp_dir):
        """Test writing zero bytes."""
        test_file = os.path.join(temp_dir, "zero_write.bin")

        async with uio.open(test_file, "wb") as f:
            written = await f.write(b"")

        assert written == 0

        with open(test_file, "rb") as f:
            assert f.read() == b""

    async def test_zero_byte_read(self, uio, temp_dir):
        """Test reading zero bytes."""
        test_file = os.path.join(temp_dir, "zero_read.bin")

        with open(test_file, "wb") as f:
            f.write(b"some data")

        async with uio.open(test_file, "rb") as f:
            data = await f.read(0)

        assert data == b""

    async def test_read_beyond_eof(self, uio, temp_dir):
        """Test reading more bytes than file contains."""
        test_file = os.path.join(temp_dir, "eof_test.bin")
        data = b"short"

        with open(test_file, "wb") as f:
            f.write(data)

        async with uio.open(test_file, "rb") as f:
            result = await f.read(1000)  # Request more than available

        assert result == data

    async def test_seek_beyond_eof(self, uio, temp_dir):
        """Test seeking beyond end of file."""
        test_file = os.path.join(temp_dir, "seek_beyond.bin")

        with open(test_file, "wb") as f:
            f.write(b"short")

        async with uio.open(test_file, "rb") as f:
            pos = await f.seek(1000)
            assert pos == 1000

            # Reading from beyond EOF should return empty
            data = await f.read(10)
            assert data == b""

    async def test_negative_seek_position(self, uio, temp_dir):
        """Test seeking to negative position (should fail)."""
        test_file = os.path.join(temp_dir, "neg_seek.bin")

        with open(test_file, "wb") as f:
            f.write(b"data")

        async with uio.open(test_file, "rb") as f:
            with pytest.raises(OSError):
                await f.seek(-10, os.SEEK_SET)

    async def test_very_long_filename(self, uio, temp_dir):
        """Test with very long filename (within OS limits)."""
        # Most filesystems support up to 255 bytes for filename
        long_name = "a" * 200 + ".bin"
        test_file = os.path.join(temp_dir, long_name)

        async with uio.open(test_file, "wb") as f:
            await f.write(b"long filename test")

        async with uio.open(test_file, "rb") as f:
            data = await f.read()

        assert data == b"long filename test"

    async def test_unicode_filename(self, uio, temp_dir):
        """Test with unicode characters in filename."""
        unicode_name = "тест_文件_🎉.bin"
        test_file = os.path.join(temp_dir, unicode_name)

        async with uio.open(test_file, "wb") as f:
            await f.write(b"unicode filename test")

        async with uio.open(test_file, "rb") as f:
            data = await f.read()

        assert data == b"unicode filename test"

    async def test_special_bytes_in_data(self, uio, temp_dir):
        """Test writing/reading data with special byte patterns."""
        test_file = os.path.join(temp_dir, "special_bytes.bin")

        # Data with null bytes, high bytes, etc.
        data = bytes(range(256)) + b"\x00" * 100 + b"\xff" * 100

        async with uio.open(test_file, "wb") as f:
            await f.write(data)

        async with uio.open(test_file, "rb") as f:
            result = await f.read()

        assert result == data


class TestStress:
    """Stress tests for reliability under load."""

    async def test_rapid_open_close(self, uio, temp_dir):
        """Test rapidly opening and closing files."""
        test_file = os.path.join(temp_dir, "rapid.bin")

        with open(test_file, "wb") as f:
            f.write(b"test")

        for _ in range(500):
            async with uio.open(test_file, "rb") as f:
                pass

    async def test_large_number_of_files(self, uio, temp_dir):
        """Test handling a large number of files."""
        num_files = 200

        # Create and write to many files
        for i in range(num_files):
            path = os.path.join(temp_dir, f"file_{i}.bin")
            async with uio.open(path, "wb") as f:
                await f.write(f"File {i} content".encode())

        # Read all files back
        for i in range(num_files):
            path = os.path.join(temp_dir, f"file_{i}.bin")
            async with uio.open(path, "rb") as f:
                data = await f.read()
                assert data == f"File {i} content".encode()

    async def test_alternating_operations(self, uio, temp_dir):
        """Test alternating between different operations."""
        files = [os.path.join(temp_dir, f"alt_{i}.bin") for i in range(5)]

        for _ in range(50):
            for i, path in enumerate(files):
                # Write
                async with uio.open(path, "wb") as f:
                    await f.write(f"Data {i}".encode())

                # Read
                async with uio.open(path, "rb") as f:
                    data = await f.read()
                    assert data == f"Data {i}".encode()

                # Append
                async with uio.open(path, "ab") as f:
                    await f.write(b" appended")

                # Read again
                async with uio.open(path, "rb") as f:
                    data = await f.read()
                    assert data == f"Data {i} appended".encode()
