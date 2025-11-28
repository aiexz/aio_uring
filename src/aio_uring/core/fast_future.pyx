# distutils:language=c++
# cython:language_level=3
# cython:boundscheck=False
# cython:wraparound=False
# cython:cdivision=True
# cython:initializedcheck=False
# cython:freelist=512
import asyncio


cdef class FastFuture:
    """Lightweight awaitable compatible with asyncio Task.
    
    Uses _asyncio_future_blocking protocol to integrate with asyncio's Task.
    This is much lighter than asyncio.Future (no locks, minimal overhead).
    Optimized with __slots__-like cdef for zero dict overhead.
    Supports reset() for object pooling to reduce GC pressure.
    """
    
    def __cinit__(self):
        self._state = 0
        self._result = None
        self._exception = None
        self._callbacks = None
        self._asyncio_future_blocking = False
        self._proactor = None
    
    def __init__(self, loop, proactor=None):
        self._loop = loop
        self._proactor = proactor

    cdef void reset(self, object loop):
        """Reset the future for reuse in the pool.
        
        This allows the same FastFuture object to be reused across multiple
        I/O operations, eliminating allocation overhead.
        """
        self._state = 0  # PENDING
        self._result = None
        self._exception = None
        self._callbacks = None
        self._asyncio_future_blocking = False
        self._loop = loop

    def __await__(self):
        if self._state == 0:  # Not done yet
            self._asyncio_future_blocking = True
            yield self 
            if self._state == 0:
                raise RuntimeError("await wasn't used with future")
        if self._state == 1:  # CANCELLED
            raise asyncio.CancelledError()
        if self._exception is not None:
            raise self._exception
        return self._result
    
    def __iter__(self):
        return self.__await__()

    cdef void set_result(self, object res):
        if self._state != 0:
            return 
        self._result = res
        self._state = 2  # FINISHED
        self._schedule_callbacks()
        
    cdef void set_exception(self, object exc):
        if self._state != 0:
            return 
        self._exception = exc
        self._state = 2  # FINISHED
        self._schedule_callbacks()
    
    cdef void _schedule_callbacks(self):
        cdef list callbacks = self._callbacks
        cdef object cb
        
        if callbacks is None:
            return
            
        self._callbacks = None
        
        for cb in callbacks:
            self._loop.call_soon(cb, self)
    
    def done(self):
        return self._state != 0
    
    def cancelled(self):
        return self._state == 1
    
    def cancel(self, msg=None):
        if self._state != 0:
            return False
        self._state = 1  # CANCELLED
        return True
    
    def add_done_callback(self, fn, *, context=None):
        if self._state != 0:
            self._loop.call_soon(fn, self)
        else:
            if self._callbacks is None:
                self._callbacks = []
            self._callbacks.append(fn)
    
    def remove_done_callback(self, fn):
        cdef int count = 0
        cdef list new_callbacks = []
        if self._callbacks is None:
            return 0
        for cb in self._callbacks:
            if cb != fn:
                new_callbacks.append(cb)
            else:
                count += 1
        self._callbacks = new_callbacks if new_callbacks else None
        return count
    
    def result(self):
        if self._state == 0:
            raise asyncio.InvalidStateError("Result is not ready")
        if self._state == 1:
            raise asyncio.CancelledError()
        if self._exception is not None:
            raise self._exception
        return self._result
    
    def exception(self):
        if self._state == 0:
            raise asyncio.InvalidStateError("Exception is not ready")
        if self._state == 1:
            raise asyncio.CancelledError()
        return self._exception
    
    def get_loop(self):
        return self._loop
