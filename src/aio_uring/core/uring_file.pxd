# cython:language_level=3

cdef class UringFile:
    """
    Async file object backed by UringProactor.
    
    Provides async context manager protocol and async read/write operations.
    """
    cdef object _proactor
    cdef int _fd
    cdef unsigned long long _pos
