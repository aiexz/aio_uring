"""Test file I/O operations with io_uring."""

import asyncio
import os
import sys
import tempfile

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
def temp_file():
    """Create a temporary file for testing."""
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    yield tmp_path
    if os.path.exists(tmp_path):
        os.remove(tmp_path)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    import tempfile

    dir_path = tempfile.mkdtemp()
    yield dir_path
    import shutil

    shutil.rmtree(dir_path, ignore_errors=True)


class TestBasicFileIO:
    """Test basic file I/O operations."""

    async def test_write_and_read(self, uio, temp_file):
        """Test basic write and read operations."""
        data = b"Hello, io_uring!"

        # Test write
        async with uio.open(temp_file, "wb") as f:
            written = await f.write(data)
            assert written == len(data)
            await f.flush()

        # Test read
        async with uio.open(temp_file, "rb") as f:
            read_data = await f.read(len(data))
            if isinstance(read_data, memoryview):
                read_data = bytes(read_data)
            assert read_data == data

    async def test_file_operations_sequence(self, uio, temp_file):
        """Test a sequence of file operations."""
        data = b"Test data for sequential operations"

        # Open for writing using _open_internal to get file directly
        f = await uio._open_internal(temp_file, "wb")
        assert not f.closed
        assert f.fileno() >= 0

        # Write data
        written = await f.write(data)
        assert written == len(data)

        # Flush
        await f.flush()

        # Close
        await f.close()
        assert f.closed

        # Verify file on disk
        with open(temp_file, "rb") as std_f:
            assert std_f.read() == data

    async def test_read_nonexistent_file(self, uio):
        """Test that opening a nonexistent file raises an error."""
        with pytest.raises(OSError):
            async with uio.open("/nonexistent/path/file.txt", "r") as f:
                pass

    async def test_context_manager(self, uio, temp_file):
        """Test async context manager protocol."""
        data = b"Context manager test"

        # Write using context manager
        async with uio.open(temp_file, "wb") as f:
            await f.write(data)

        # File should be closed after exiting context
        assert f.closed

        # Read and verify
        async with uio.open(temp_file, "rb") as f:
            content = await f.read(len(data))
            assert bytes(content) == data

    async def test_seek_and_tell(self, uio, temp_file):
        """Test seek and tell operations."""
        data = b"0123456789"

        # Write test data
        async with uio.open(temp_file, "wb") as f:
            await f.write(data)

        # Test seek and tell
        async with uio.open(temp_file, "rb") as f:
            assert f.tell() == 0

            # Read first 5 bytes
            chunk1 = await f.read(5)
            assert f.tell() == 5
            assert bytes(chunk1) == b"01234"

            # Seek to position 2
            pos = await f.seek(2)
            assert pos == 2
            assert f.tell() == 2

            # Read from new position
            chunk2 = await f.read(3)
            assert bytes(chunk2) == b"234"

    async def test_append_mode(self, uio, temp_file):
        """Test append mode."""
        # Write initial data
        async with uio.open(temp_file, "wb") as f:
            await f.write(b"Hello")

        # Append more data
        async with uio.open(temp_file, "ab") as f:
            await f.write(b" World")

        # Verify
        with open(temp_file, "rb") as std_f:
            assert std_f.read() == b"Hello World"

    async def test_binary_data(self, uio, temp_file):
        """Test binary data with various byte values."""
        # Data with all possible byte values
        data = bytes(range(256))

        async with uio.open(temp_file, "wb") as f:
            await f.write(data)

        async with uio.open(temp_file, "rb") as f:
            read_data = await f.read(256)
            assert bytes(read_data) == data

    async def test_large_file(self, uio, temp_file):
        """Test reading and writing a larger file."""
        # 1MB of data
        data = b"X" * (1024 * 1024)

        async with uio.open(temp_file, "wb") as f:
            written = await f.write(data)
            assert written == len(data)

        async with uio.open(temp_file, "rb") as f:
            read_data = await f.read(len(data))
            assert len(read_data) == len(data)


class TestEdgeCases:
    """Test edge cases and error handling."""

    async def test_write_to_closed_file(self, uio, temp_file):
        """Test that writing to closed file raises error."""
        f = await uio._open_internal(temp_file, "wb")
        await f.close()

        with pytest.raises(ValueError, match="closed"):
            await f.write(b"data")

    async def test_read_from_closed_file(self, uio, temp_file):
        """Test that reading from closed file raises error."""
        # Create file first
        async with uio.open(temp_file, "wb") as f:
            await f.write(b"test")

        f = await uio._open_internal(temp_file, "rb")
        await f.close()

        with pytest.raises(ValueError, match="closed"):
            await f.read(10)

    async def test_read_from_write_only(self, uio, temp_file):
        """Test that reading from write-only file raises error."""
        import io

        async with uio.open(temp_file, "wb") as f:
            with pytest.raises(io.UnsupportedOperation, match="not readable"):
                await f.read(10)

    async def test_write_to_read_only(self, uio, temp_file):
        """Test that writing to read-only file raises error."""
        import io

        # Create file first
        with open(temp_file, "w") as f:
            f.write("test")

        async with uio.open(temp_file, "rb") as f:
            with pytest.raises(io.UnsupportedOperation, match="not writable"):
                await f.write(b"data")

    async def test_invalid_mode(self, uio, temp_file):
        """Test that invalid mode raises error."""
        with pytest.raises(ValueError):
            async with uio.open(temp_file, "z") as f:  # 'z' not supported
                pass

    async def test_empty_file(self, uio, temp_file):
        """Test reading from empty file."""
        # Create empty file
        async with uio.open(temp_file, "wb") as f:
            pass

        async with uio.open(temp_file, "rb") as f:
            data = await f.read(100)
            assert len(data) == 0


class TestFileProperties:
    """Test file object properties."""

    async def test_name_property(self, uio, temp_file):
        """Test the name property."""
        async with uio.open(temp_file, "rb") as f:
            assert f.name == temp_file

    async def test_mode_property(self, uio, temp_file):
        """Test the mode property."""
        async with uio.open(temp_file, "wb") as f:
            assert f.mode == "wb"

        # Create the file for read test
        async with uio.open(temp_file, "rb") as f:
            assert f.mode == "rb"

    async def test_fileno(self, uio, temp_file):
        """Test fileno() method."""
        async with uio.open(temp_file, "wb") as f:
            fd = f.fileno()
            assert isinstance(fd, int)
            assert fd >= 0

    async def test_readable_writable_seekable(self, uio, temp_file):
        """Test readable(), writable(), seekable() methods."""
        # Write mode
        async with uio.open(temp_file, "wb") as f:
            assert not f.readable()
            assert f.writable()
            assert f.seekable()

        # Read mode
        async with uio.open(temp_file, "rb") as f:
            assert f.readable()
            assert not f.writable()
            assert f.seekable()

        # Read/write mode
        async with uio.open(temp_file, "r+b") as f:
            assert f.readable()
            assert f.writable()
            assert f.seekable()


class TestReadlineMethods:
    """Test readline and readlines methods."""

    async def test_readline(self, uio, temp_file):
        """Test readline() method."""
        lines = b"line1\nline2\nline3\n"

        async with uio.open(temp_file, "wb") as f:
            await f.write(lines)

        async with uio.open(temp_file, "rb") as f:
            line1 = await f.readline()
            assert line1 == b"line1\n"

            line2 = await f.readline()
            assert line2 == b"line2\n"

            line3 = await f.readline()
            assert line3 == b"line3\n"

            # EOF
            line4 = await f.readline()
            assert line4 == b""

    async def test_readlines(self, uio, temp_file):
        """Test readlines() method."""
        lines = b"line1\nline2\nline3\n"

        async with uio.open(temp_file, "wb") as f:
            await f.write(lines)

        async with uio.open(temp_file, "rb") as f:
            all_lines = await f.readlines()
            assert all_lines == [b"line1\n", b"line2\n", b"line3\n"]

    async def test_async_iteration(self, uio, temp_file):
        """Test async iteration over lines."""
        lines = b"line1\nline2\nline3\n"

        async with uio.open(temp_file, "wb") as f:
            await f.write(lines)

        async with uio.open(temp_file, "rb") as f:
            collected = []
            async for line in f:
                collected.append(line)
            assert collected == [b"line1\n", b"line2\n", b"line3\n"]


class TestWritelinesMethods:
    """Test writelines method."""

    async def test_writelines(self, uio, temp_file):
        """Test writelines() method."""
        lines = [b"line1\n", b"line2\n", b"line3\n"]

        async with uio.open(temp_file, "wb") as f:
            await f.writelines(lines)

        with open(temp_file, "rb") as f:
            assert f.read() == b"line1\nline2\nline3\n"


class TestTruncate:
    """Test truncate method."""

    async def test_truncate(self, uio, temp_file):
        """Test truncate() method."""
        async with uio.open(temp_file, "wb") as f:
            await f.write(b"Hello World!")

        async with uio.open(temp_file, "r+b") as f:
            size = await f.truncate(5)
            assert size == 5

        with open(temp_file, "rb") as f:
            assert f.read() == b"Hello"


class TestExclusiveCreation:
    """Test exclusive creation mode (x)."""

    async def test_exclusive_create_new_file(self, uio, temp_dir):
        """Test 'x' mode creates new file."""
        new_file = os.path.join(temp_dir, "new_file.txt")

        async with uio.open(new_file, "xb") as f:
            await f.write(b"new content")

        with open(new_file, "rb") as f:
            assert f.read() == b"new content"

    async def test_exclusive_create_existing_fails(self, uio, temp_file):
        """Test 'x' mode fails if file exists."""
        # File already exists from fixture
        with open(temp_file, "w") as f:
            f.write("existing")

        with pytest.raises(OSError):
            async with uio.open(temp_file, "xb") as f:
                pass


class TestTextMode:
    """Test text mode operations."""

    async def test_text_write_and_read(self, uio, temp_file):
        """Test text mode write and read."""
        text = "Hello, World! こんにちは"

        async with uio.open(temp_file, "w", encoding="utf-8") as f:
            written = await f.write(text)
            assert written == len(text)

        async with uio.open(temp_file, "r", encoding="utf-8") as f:
            read_text = await f.read()
            assert read_text == text

    async def test_text_readline(self, uio, temp_file):
        """Test text readline."""
        text = "line1\nline2\nline3\n"

        async with uio.open(temp_file, "w") as f:
            await f.write(text)

        async with uio.open(temp_file, "r") as f:
            line1 = await f.readline()
            assert line1 == "line1\n"

            line2 = await f.readline()
            assert line2 == "line2\n"

    async def test_text_iteration(self, uio, temp_file):
        """Test text async iteration."""
        text = "line1\nline2\nline3\n"

        async with uio.open(temp_file, "w") as f:
            await f.write(text)

        async with uio.open(temp_file, "r") as f:
            collected = []
            async for line in f:
                collected.append(line)
            assert collected == ["line1\n", "line2\n", "line3\n"]

    async def test_text_encoding_property(self, uio, temp_file):
        """Test encoding property."""
        async with uio.open(temp_file, "w", encoding="utf-8") as f:
            assert f.encoding == "utf-8"

    async def test_mixed_binary_text_mode_error(self, uio, temp_file):
        """Test that mixing b and t raises error."""
        with pytest.raises(ValueError, match="can't have text and binary mode"):
            async with uio.open(temp_file, "rbt") as f:
                pass


class TestOpenFunction:
    """Test the standalone open() function."""

    async def test_open_function_basic(self, temp_file):
        """Test using the open() function directly."""
        from aio_uring import open as aio_open

        async with aio_open(temp_file, "wb") as f:
            await f.write(b"test data")

        async with aio_open(temp_file, "rb") as f:
            data = await f.read()
            assert data == b"test data"

    async def test_open_function_text_mode(self, temp_file):
        """Test open() function with text mode."""
        from aio_uring import open as aio_open

        text = "Hello, World!"

        async with aio_open(temp_file, "w") as f:
            await f.write(text)

        async with aio_open(temp_file, "r") as f:
            read_text = await f.read()
            assert read_text == text


class TestConvenienceFunctions:
    """Test read_file and write_file convenience functions."""

    async def test_read_file_function(self, temp_file):
        """Test the read_file convenience function."""
        from aio_uring import read_file

        with open(temp_file, "wb") as f:
            f.write(b"test content")

        content = await read_file(temp_file)
        assert content == b"test content"

    async def test_write_file_function(self, temp_file):
        """Test the write_file convenience function."""
        from aio_uring import write_file

        await write_file(temp_file, b"written content")

        with open(temp_file, "rb") as f:
            assert f.read() == b"written content"
