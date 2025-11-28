# cython:language_level=3

cdef class FastFuture:
    """
    Lightweight awaitable compatible with asyncio Task.

    Uses _asyncio_future_blocking protocol to integrate with asyncio's Task.
    This is much lighter than asyncio.Future (no locks, minimal overhead).
    Optimized with __slots__-like cdef for zero dict overhead.
    """
    cdef:
        object _loop
        object _result
        object _exception
        object _proactor
        int _state  # 0=PENDING, 1=CANCELLED, 2=FINISHED
        list _callbacks
        public bint _asyncio_future_blocking  # Required for asyncio Task integration
    
    cdef void set_result(self, object res)
    cdef void set_exception(self, object exc)
    cdef void _schedule_callbacks(self)
    cdef void reset(self, object loop)
