# distutils:language=c++
# cython:language_level=3
# cython:boundscheck=False
# cython:wraparound=False
# cython:cdivision=True
# cython:initializedcheck=False

cdef class UringFile:
    """
    Async file object backed by UringProactor.
    
    Provides async context manager protocol and async read/write operations.
    """
    
    def __cinit__(self):
        self._fd = -1
        self._pos = 0
    
    def __init__(self, proactor, int fd):
        self._proactor = proactor
        self._fd = fd
        self._pos = 0

    @property
    def fd(self):
        return self._fd

    async def read(self, nbytes):
        """Read up to nbytes from the file."""
        data = await self._proactor.read_file(self._fd, nbytes, self._pos)
        self._pos += len(data)
        return data

    async def write(self, data):
        """Write data to the file."""
        cdef int res = await self._proactor.write_file(self._fd, data, self._pos)
        self._pos += res
        return res

    async def close(self):
        """Close the file."""
        await self._proactor.close_file(self._fd)

    async def fsync(self):
        """Flush the file to disk."""
        await self._proactor.fsync(self._fd)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
