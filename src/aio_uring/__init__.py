import asyncio
import io
import os
import sys
from typing import Optional, Union, List
import threading

if sys.platform == "linux":
    from .core import UringProactor

__all__ = [
    "UringFileIO",
    "UringBinaryFile",
    "UringTextFile",
    "AsyncFileContext",
    "open",
    "read_file",
    "write_file",
]


class AsyncFileContext:
    """Async context manager for opening files

    Usage:
        async with uio.open('file.txt', 'rb') as f:
            data = await f.read()
    """

    def __init__(
        self,
        uio,
        path: str,
        mode: str,
        encoding: Optional[str],
        errors: Optional[str],
        newline: Optional[str],
    ):
        self._uio = uio
        self._path = path
        self._mode = mode
        self._encoding = encoding
        self._errors = errors
        self._newline = newline
        self._file = None

    async def __aenter__(self):
        self._file = await self._uio._open_internal(
            self._path, self._mode, self._encoding, self._errors, self._newline
        )
        return self._file

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._file is not None:
            await self._file.close()
        return False


class UringBinaryFile:
    """Binary file implementation for UringFileIO."""

    def __init__(self, uio, fd: int, mode: str, path: str):
        self._uio = uio
        self._fd = fd
        self._mode = mode
        self._path = path
        self._pos = 0
        self._closed = False
        # Read-ahead buffer for efficient readline
        self._read_ahead = bytearray()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def name(self) -> str:
        return self._path

    @property
    def mode(self) -> str:
        return self._mode

    def fileno(self) -> int:
        return self._fd

    def readable(self) -> bool:
        return "r" in self._mode or "+" in self._mode

    def writable(self) -> bool:
        return (
            "w" in self._mode
            or "a" in self._mode
            or "+" in self._mode
            or "x" in self._mode
        )

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        return self._pos

    async def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to position in file."""
        if self._closed:
            raise ValueError("I/O operation on closed file")

        if whence == 0:  # SEEK_SET
            if offset < 0:
                raise OSError(22, "Invalid argument")
            self._pos = offset
        elif whence == 1:  # SEEK_CUR
            new_pos = self._pos + offset
            if new_pos < 0:
                raise OSError(22, "Invalid argument")
            self._pos = new_pos
        elif whence == 2:  # SEEK_END
            st = os.fstat(self._fd)
            self._pos = st.st_size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        return self._pos

    async def read(self, nbytes: int = -1) -> bytes:
        """Read up to nbytes from the file."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.readable():
            raise io.UnsupportedOperation("not readable")

        if nbytes == -1 or nbytes is None:
            # Read all
            chunks = []
            while True:
                chunk = await self._uio._proactor.read_file(self._fd, 65536, self._pos)
                if not chunk or len(chunk) == 0:
                    break
                self._pos += len(chunk)
                # Convert memoryview to bytes
                if isinstance(chunk, memoryview):
                    chunk = bytes(chunk)
                chunks.append(chunk)
            return b"".join(chunks)

        data = await self._uio._proactor.read_file(self._fd, nbytes, self._pos)
        # Convert memoryview to bytes
        if isinstance(data, memoryview):
            data = bytes(data)
        self._pos += len(data)
        return data

    async def readline(self, limit: int = -1) -> bytes:
        """Read a line from the file."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.readable():
            raise io.UnsupportedOperation("not readable")

        if b"\n" in self._read_ahead:
            idx = self._read_ahead.find(b"\n") + 1
            line = bytes(self._read_ahead[:idx])
            del self._read_ahead[:idx]
            return line

        while True:
            chunk = await self._uio._proactor.read_file(self._fd, 8192, self._pos)
            if not chunk or len(chunk) == 0:
                if self._read_ahead:
                    out = bytes(self._read_ahead)
                    self._read_ahead.clear()
                    return out
                return b""
            if isinstance(chunk, memoryview):
                chunk = bytes(chunk)
            self._pos += len(chunk)
            self._read_ahead.extend(chunk)
            if b"\n" in chunk:
                idx = self._read_ahead.find(b"\n") + 1
                line = bytes(self._read_ahead[:idx])
                del self._read_ahead[:idx]
                return line
            if limit > 0 and len(self._read_ahead) >= limit:
                out = bytes(self._read_ahead[:limit])
                del self._read_ahead[:limit]
                return out

    async def readlines(self, hint: int = -1) -> List[bytes]:
        """Read all lines from the file."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.readable():
            raise io.UnsupportedOperation("not readable")

        lines = []
        total = 0
        while True:
            line = await self.readline()
            if not line:
                break
            lines.append(line)
            total += len(line)
            if hint > 0 and total >= hint:
                break
        return lines

    async def write(self, data: Union[bytes, bytearray, memoryview]) -> int:
        """Write data to the file."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.writable():
            raise io.UnsupportedOperation("not writable")

        if isinstance(data, memoryview):
            data = bytes(data)
        elif isinstance(data, bytearray):
            data = bytes(data)

        res = await self._uio._proactor.write_file(self._fd, data, self._pos)
        self._pos += res
        return res

    async def writelines(self, lines: List[bytes]) -> None:
        """Write a list of lines to the file."""
        for line in lines:
            await self.write(line)

    async def truncate(self, size: Optional[int] = None) -> int:
        """Truncate the file to at most size bytes."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.writable():
            raise io.UnsupportedOperation("not writable")

        if size is None:
            size = self._pos

        os.ftruncate(self._fd, size)
        return size

    async def flush(self) -> None:
        """Flush the file to disk."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        await self._uio._proactor.fsync(self._fd)

    async def close(self) -> None:
        """Close the file."""
        if not self._closed:
            self._closed = True
            await self._uio._proactor.close_file(self._fd)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        line = await self.readline()
        if not line:
            raise StopAsyncIteration
        return line


class UringTextFile:
    """Text file implementation for UringFileIO."""

    def __init__(
        self,
        uio,
        fd: int,
        mode: str,
        path: str,
        encoding: str = "utf-8",
        errors: str = "strict",
        newline: Optional[str] = None,
    ):
        self._uio = uio
        self._fd = fd
        self._mode = mode
        self._path = path
        self._encoding = encoding
        self._errors = errors
        self._newline = newline
        self._pos = 0
        self._closed = False
        # Read-ahead buffer for readline and partial char handling
        self._read_ahead = bytearray()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def name(self) -> str:
        return self._path

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def encoding(self) -> str:
        return self._encoding

    def fileno(self) -> int:
        return self._fd

    def readable(self) -> bool:
        return "r" in self._mode or "+" in self._mode

    def writable(self) -> bool:
        return (
            "w" in self._mode
            or "a" in self._mode
            or "+" in self._mode
            or "x" in self._mode
        )

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        return self._pos

    async def seek(self, offset: int, whence: int = 0) -> int:
        """Seek to position in file (byte position)."""
        if self._closed:
            raise ValueError("I/O operation on closed file")

        if whence == 0:  # SEEK_SET
            if offset < 0:
                raise OSError(22, "Invalid argument")
            self._pos = offset
        elif whence == 1:  # SEEK_CUR
            new_pos = self._pos + offset
            if new_pos < 0:
                raise OSError(22, "Invalid argument")
            self._pos = new_pos
        elif whence == 2:  # SEEK_END
            st = os.fstat(self._fd)
            self._pos = st.st_size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

        return self._pos

    async def read(self, size: int = -1) -> str:
        """Read and decode text from the file."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.readable():
            raise io.UnsupportedOperation("not readable")

        if size == -1 or size is None:
            # Read all
            chunks = []
            while True:
                chunk = await self._uio._proactor.read_file(self._fd, 65536, self._pos)
                if not chunk or len(chunk) == 0:
                    break
                self._pos += len(chunk)
                if isinstance(chunk, memoryview):
                    chunk = bytes(chunk)
                chunks.append(chunk)
            return b"".join(chunks).decode(self._encoding, self._errors)

        chunks = []
        decoded = ""
        while True:
            chunk = await self._uio._proactor.read_file(
                self._fd, max(8192, size * 2), self._pos
            )
            if not chunk or len(chunk) == 0:
                break
            if isinstance(chunk, memoryview):
                chunk = bytes(chunk)
            self._pos += len(chunk)
            chunks.append(chunk)
            decoded = b"".join(chunks).decode(self._encoding, self._errors)
            if len(decoded) >= size:
                return decoded[:size]
        return decoded

    async def readline(self, limit: int = -1) -> str:
        """Read a line from the file."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.readable():
            raise io.UnsupportedOperation("not readable")

        # Use the read-ahead buffer similar to binary mode, then decode
        if b"\n" in self._read_ahead:
            idx = self._read_ahead.find(b"\n") + 1
            line_bytes = bytes(self._read_ahead[:idx])
            del self._read_ahead[:idx]
            return line_bytes.decode(self._encoding, self._errors)

        while True:
            chunk = await self._uio._proactor.read_file(self._fd, 8192, self._pos)
            if not chunk or len(chunk) == 0:
                if self._read_ahead:
                    out = bytes(self._read_ahead)
                    self._read_ahead.clear()
                    return out.decode(self._encoding, self._errors)
                return ""
            if isinstance(chunk, memoryview):
                chunk = bytes(chunk)
            self._pos += len(chunk)
            self._read_ahead.extend(chunk)
            if b"\n" in chunk:
                idx = self._read_ahead.find(b"\n") + 1
                line_bytes = bytes(self._read_ahead[:idx])
                del self._read_ahead[:idx]
                return line_bytes.decode(self._encoding, self._errors)
            if limit > 0 and len(self._read_ahead) >= limit:
                out = bytes(self._read_ahead[:limit])
                del self._read_ahead[:limit]
                return out.decode(self._encoding, self._errors)

    async def readlines(self, hint: int = -1) -> List[str]:
        """Read all lines from the file."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.readable():
            raise io.UnsupportedOperation("not readable")

        lines = []
        total = 0
        while True:
            line = await self.readline()
            if not line:
                break
            lines.append(line)
            total += len(line)
            if hint > 0 and total >= hint:
                break
        return lines

    async def write(self, data: str) -> int:
        """Write text to the file."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.writable():
            raise io.UnsupportedOperation("not writable")
        if not isinstance(data, str):
            raise TypeError(f"write() argument must be str, not {type(data).__name__}")

        encoded = data.encode(self._encoding, self._errors)
        res = await self._uio._proactor.write_file(self._fd, encoded, self._pos)
        self._pos += res
        return len(data)  # Return character count, not byte count

    async def writelines(self, lines: List[str]) -> None:
        """Write a list of lines to the file."""
        for line in lines:
            await self.write(line)

    async def truncate(self, size: Optional[int] = None) -> int:
        """Truncate the file to at most size bytes."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not self.writable():
            raise io.UnsupportedOperation("not writable")

        if size is None:
            size = self._pos

        os.ftruncate(self._fd, size)
        return size

    async def flush(self) -> None:
        """Flush the file to disk."""
        if self._closed:
            raise ValueError("I/O operation on closed file")
        await self._uio._proactor.fsync(self._fd)

    async def close(self) -> None:
        """Close the file."""
        if not self._closed:
            self._closed = True
            await self._uio._proactor.close_file(self._fd)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        line = await self.readline()
        if not line:
            raise StopAsyncIteration
        return line


class UringFileIO:
    """Main class for async file I/O using io_uring.

    This class manages the io_uring proactor and provides async file operations.

    Example:
        uio = UringFileIO()
        async with uio.open('file.txt', 'rb') as f:
            data = await f.read()
        uio.close()
    """

    def __init__(self, entries: int = 256, flags: int = 0):
        """Initialize UringFileIO.

        Args:
            entries: Number of submission queue entries (default: 256)
            flags: io_uring setup flags (default: 0)
        """
        if not sys.platform == "linux":
            raise RuntimeError("io_uring is only available on Linux")

        self._proactor = UringProactor(entries, flags)
        self._loop = None
        self._closed = False

    @property
    def closed(self) -> bool:
        """Return True if the UringFileIO is closed."""
        return self._closed

    def _ensure_loop(self):
        """Ensure we have an event loop set."""
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.get_event_loop()
            self._proactor.set_loop(self._loop)

    def open(
        self,
        path: str,
        mode: str = "r",
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
        newline: Optional[str] = None,
    ) -> AsyncFileContext:
        """Open a file asynchronously.

        Returns an async context manager. Use with 'async with':

            async with uio.open('file.txt', 'rb') as f:
                data = await f.read()
        """
        if self._closed:
            raise RuntimeError("UringFileIO is closed")
        return AsyncFileContext(self, path, mode, encoding, errors, newline)

    async def _open_internal(
        self,
        path: str,
        mode: str = "r",
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
        newline: Optional[str] = None,
    ) -> Union[UringBinaryFile, UringTextFile]:
        """Internal method that actually opens the file asynchronously."""
        self._ensure_loop()

        if self._closed:
            raise RuntimeError("UringFileIO is closed")

        binary = "b" in mode
        text = "t" in mode or (not binary and "b" not in mode)

        if binary and text:
            raise ValueError(
                f"can't have text and binary mode at once in mode '{mode}'"
            )

        reading = "r" in mode
        writing = "w" in mode
        appending = "a" in mode
        creating = "x" in mode
        updating = "+" in mode

        mode_count = sum([reading, writing, appending, creating])
        if mode_count > 1:
            raise ValueError(
                f"must have exactly one of read/write/append/create mode, got '{mode}'"
            )
        if mode_count == 0:
            raise ValueError(
                f"must have exactly one of read/write/append/create mode, got '{mode}'"
            )

        # Build os flags
        flags = 0
        if reading and not updating:
            flags = os.O_RDONLY
        elif writing or creating:
            if updating:
                flags = os.O_RDWR | os.O_CREAT
            else:
                flags = os.O_WRONLY | os.O_CREAT
            if writing:
                flags |= os.O_TRUNC
            if creating:
                flags |= os.O_EXCL
        elif appending:
            if updating:
                flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
            else:
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        elif reading and updating:
            flags = os.O_RDWR

        # Open the file
        try:
            uring_file = await self._proactor.open(path, flags, 0o666)
        except OSError as e:
            # Re-raise with better error message including path
            raise OSError(e.errno, f"{e.strerror}: '{path}'") from None

        fd = uring_file.fd

        # Poll once to process the open completion
        self._proactor.select(0)

        if text:
            return UringTextFile(
                self, fd, mode, path, encoding or "utf-8", errors or "strict", newline
            )
        else:
            return UringBinaryFile(self, fd, mode, path)

    def close(self):
        """Close the UringFileIO instance."""
        if not self._closed:
            self._closed = True
            self._proactor.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()


# Use thread-local storage for the global UringFileIO instance to avoid
# races and accidental sharing across threads.
# Maybe there is a better way to do this?
_local = threading.local()


def _get_global_uio() -> UringFileIO:
    """Get or create a thread-local UringFileIO instance.

    Ensures each thread gets its own UringFileIO to avoid cross-thread
    usage of io_uring proactors.
    """
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    uio = getattr(_local, "uio", None)
    uio_loop = getattr(_local, "uio_loop", None)

    if (
        uio is None
        or uio._closed
        or (current_loop is not None and uio_loop is not current_loop)
    ):
        if uio is not None and not uio._closed:
            uio.close()
        uio = UringFileIO()
        _local.uio = uio
        _local.uio_loop = current_loop

    return uio


def open(
    path: str,
    mode: str = "r",
    encoding: Optional[str] = None,
    errors: Optional[str] = None,
    newline: Optional[str] = None,
) -> AsyncFileContext:
    """Open a file asynchronously using io_uring.

    This is a convenience function that uses a thread-local UringFileIO instance.

    Example:
        async with open('file.txt', 'rb') as f:
            data = await f.read()
    """
    uio = _get_global_uio()
    return uio.open(path, mode, encoding, errors, newline)


async def read_file(path: str, binary: bool = True) -> Union[bytes, str]:
    """Read entire file contents asynchronously.

    Example:
        content = await read_file('file.txt')
    """
    mode = "rb" if binary else "r"
    async with open(path, mode) as f:
        return await f.read()


async def write_file(path: str, data: Union[bytes, str], binary: bool = True) -> int:
    """Write data to a file asynchronously.

    Example:
        await write_file('file.txt', b'Hello, World!')
    """
    mode = "wb" if binary else "w"
    async with open(path, mode) as f:
        return await f.write(data)  # type: ignore[arg-type]
