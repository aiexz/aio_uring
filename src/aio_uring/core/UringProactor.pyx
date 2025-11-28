# distutils:language=c++
# cython:language_level=3
# cython:boundscheck=False
# cython:wraparound=False
# cython:cdivision=True
# cython:initializedcheck=False

import asyncio
import os

from cpython.ref cimport Py_INCREF, Py_DECREF
from cpython.object cimport PyObject
from cpython.bytes cimport PyBytes_AsString

# Python buffer protocol
cdef extern from "Python.h":
    ctypedef struct Py_buffer:
        void *buf
        Py_ssize_t len
    int PyObject_GetBuffer(object obj, Py_buffer *view, int flags) except -1
    void PyBuffer_Release(Py_buffer *view)
    int PyBUF_SIMPLE

# eventfd for asyncio integration
cdef extern from "sys/eventfd.h":
    int eventfd(unsigned int initval, int flags)
    int EFD_NONBLOCK
    int EFD_CLOEXEC

cdef extern from "unistd.h":
    ssize_t read(int fd, void *buf, size_t count) nogil
    int close(int fd)

# fstat for getting file size
cdef extern from "sys/stat.h":
    ctypedef long long off_t
    struct stat:
        off_t st_size
    int fstat(int fd, stat *buf) nogil

from aio_uring.core.base cimport (
    OpType, OP_READ, OP_WRITE, OP_OPENAT, OP_CLOSE, OP_FSYNC,
)

from aio_uring.core.uring_wrapper cimport UringWraper, io_uring_cqe

from aio_uring.core.fast_future cimport FastFuture
from aio_uring.core.fast_future import FastFuture

from aio_uring.core.op_context cimport UringOpContext
from aio_uring.core.op_context import UringOpContext

from aio_uring.core.uring_file cimport UringFile
from aio_uring.core.uring_file import UringFile

AT_FDCWD = getattr(os, 'AT_FDCWD', -100)

# Function type for completion handlers
ctypedef object (*completion_handler_t)(UringOpContext ctx, int res, object proactor)

# Handler implementations - these are called from the dispatch array
cdef object _handle_read(UringOpContext ctx, int res, object proactor):
    if res < 0:
        raise OSError(-res, "read failed")
    # Access the pooled buffer and COPY data out to ensure safety.
    # This prevents use-after-free if the pooled buffer is reused.
    cdef bytearray buf = <bytearray>ctx.buf
    cdef bytes result = bytes(buf[:res])
    # Return the pooled buffer to the proactor's pool now that we've copied
    try:
        (<UringProactor>proactor)._return_read_buffer(buf)
    except Exception:
        pass
    return result

cdef object _handle_write(UringOpContext ctx, int res, object proactor):
    if res < 0:
        raise OSError(-res, "write failed")
    return res

cdef object _handle_open(UringOpContext ctx, int res, object proactor):
    if res < 0:
        raise OSError(-res, "open failed")
    return UringFile(proactor, res)

cdef object _handle_close(UringOpContext ctx, int res, object proactor):
    if res < 0:
        raise OSError(-res, "close failed")
    return res

cdef object _handle_fsync(UringOpContext ctx, int res, object proactor):
    if res < 0:
        raise OSError(-res, "fsync failed")
    return res

cdef object _handle_default(UringOpContext ctx, int res, object proactor):
    """Default handler for unknown op types."""
    return res

# Max op type value + 1 for array size
# Maybe we should seprate these handlers into their own module?
DEF MAX_OP_TYPE = 7

cdef completion_handler_t _handlers[MAX_OP_TYPE]

# Initialize handler dispatch table at module load time
cdef void _init_handlers() noexcept:
    _handlers[0] = _handle_default  # Unused index 0
    _handlers[1] = _handle_read     # OP_READ
    _handlers[2] = _handle_write    # OP_WRITE
    _handlers[3] = _handle_default  # Unused (was OP_POLL_ADD)
    _handlers[4] = _handle_open     # OP_OPENAT
    _handlers[5] = _handle_close    # OP_CLOSE
    _handlers[6] = _handle_fsync    # OP_FSYNC
# Add OP_TEE, OP_RENAMEAT, OP_MKDIR, etc. as needed

# Call initialization at module load
_init_handlers()


cdef class UringProactor:
    cdef UringWraper _ring
    cdef object _loop
    cdef list _ctx_pool       # Object pool for UringOpContext reuse
    cdef list _future_pool    # Object pool for FastFuture reuse
    cdef int _eventfd         # eventfd for asyncio integration
    cdef bint _reader_added   # Whether we've added the eventfd reader to loop
    
    cdef list _read_buffer_pool  # Pool of bytearray objects
    cdef unsigned _read_buffer_size  # Size of each buffer in pool

    def __init__(self, entries: int = 256, flags: int = 0):
        cdef int ret = self._ring.queue_init(entries, flags)
        if ret < 0:
            raise OSError(-ret, "io_uring_queue_init failed")
        self._ctx_pool = []
        self._future_pool = []
        self._read_buffer_pool = []
        self._read_buffer_size = 65536  # 64KB default read buffer
        self._reader_added = False
        
        # Create eventfd for asyncio integration
        self._eventfd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC)
        if self._eventfd < 0:
            raise OSError(-self._eventfd, "eventfd creation failed")
        
        # Register eventfd with io_uring
        ret = self._ring.register_eventfd(self._eventfd)
        if ret < 0:
            close(self._eventfd)
            raise OSError(-ret, "io_uring_register_eventfd failed")

    def set_loop(self, loop: asyncio.BaseEventLoop):
        self._loop = loop
        # Register eventfd reader with the event loop
        if not self._reader_added and self._eventfd >= 0:
            self._loop.add_reader(self._eventfd, self._on_eventfd_ready)
            self._reader_added = True
    
    def _on_eventfd_ready(self):
        """Called by asyncio when eventfd is readable (io_uring has completions)."""
        cdef unsigned long long buf = 0
        read(self._eventfd, &buf, sizeof(buf))
        self._poll(0)
    
    cdef inline UringOpContext _get_context(self):
        """Get a context from the pool or create a new one."""
        if self._ctx_pool:
            return self._ctx_pool.pop()
        return UringOpContext()
    
    cdef inline FastFuture _get_future(self):
        """Get a future (pooling disabled - Cython freelist provides similar benefits)."""
        return FastFuture(self._loop)
    
    cdef inline bytearray _get_read_buffer(self, unsigned size):
        """Get a read buffer from the pool or create a new one."""
        cdef bytearray buf
        if size <= self._read_buffer_size and self._read_buffer_pool:
            buf = self._read_buffer_pool.pop()
            return buf
        return bytearray(size if size > self._read_buffer_size else self._read_buffer_size)
    
    cdef inline void _return_read_buffer(self, bytearray buf):
        """Return a read buffer to the pool for reuse."""
        cdef size_t capacity = len(buf)
        
        # We should remake this to use multiple pools for different sizes.
        if capacity == self._read_buffer_size and len(self._read_buffer_pool) < 64:
            self._read_buffer_pool.append(buf)
        elif capacity == 1024 * 1024 and len(self._read_buffer_pool) < 16:
             self._read_buffer_pool.append(buf)

    def open(self, path, flags: int, mode: int = 0o666):
        if isinstance(path, str):
            path = path.encode()
        
        cdef UringOpContext ctx = self._get_context()
        ctx.future = self._get_future()
        ctx.keep_alive_obj = path  # Keep path alive during operation
        ctx.fd = AT_FDCWD
        ctx.buf = path
        ctx.nbytes = flags
        ctx.offset = mode
        ctx.op_type = OP_OPENAT
        
        return self._submit_op_native(ctx, OP_OPENAT)

    def close_file(self, fd: int):
        cdef UringOpContext ctx = self._get_context()
        ctx.future = self._get_future()
        ctx.keep_alive_obj = None
        ctx.fd = fd
        ctx.op_type = OP_CLOSE
        
        return self._submit_op_native(ctx, OP_CLOSE)

    def read_file(self, fd: int, nbytes: int, offset: int = 0):
        """Read from a file using pooled buffers."""
        cdef bytearray buf = self._get_read_buffer(nbytes)
        
        cdef UringOpContext ctx = self._get_context()
        ctx.future = self._get_future()
        ctx.keep_alive_obj = buf
        ctx.fd = fd
        ctx.buf = buf
        ctx.nbytes = nbytes
        ctx.offset = offset
        ctx.op_type = OP_READ
        
        return self._submit_op_native(ctx, OP_READ)

    def read_file_all(self, int fd):
        """Read entire file contents in one io_uring operation.
        
        Uses fstat to determine file size and reads everything at once.
        Much faster than multiple read() calls for whole-file reads.
        """
        cdef stat st
        cdef int ret
        
        # Get file size using fstat (fast syscall)
        with nogil:
            ret = fstat(fd, &st)
        if ret < 0:
            raise OSError(-ret, "fstat failed")
        
        cdef unsigned long long file_size = st.st_size
        if file_size == 0:
            return b''
        
        # Allocate buffer for entire file
        cdef bytearray buf = bytearray(file_size)
        
        cdef UringOpContext ctx = self._get_context()
        ctx.future = self._get_future()
        ctx.keep_alive_obj = buf
        ctx.fd = fd
        ctx.buf = buf
        ctx.nbytes = file_size
        ctx.offset = 0
        ctx.op_type = OP_READ
        
        return self._submit_op_native(ctx, OP_READ)

    def write_file(self, fd: int, data: bytes, offset: int = 0):
        cdef UringOpContext ctx = self._get_context()
        ctx.future = self._get_future()
        ctx.keep_alive_obj = data
        ctx.fd = fd
        ctx.buf = data
        ctx.nbytes = len(data)
        ctx.offset = offset
        ctx.op_type = OP_WRITE
        
        return self._submit_op_native(ctx, OP_WRITE)

    def fsync(self, fd: int):
        cdef UringOpContext ctx = self._get_context()
        ctx.future = self._get_future()
        ctx.keep_alive_obj = None
        ctx.fd = fd
        ctx.op_type = OP_FSYNC
        
        return self._submit_op_native(ctx, OP_FSYNC)

    cdef object _submit_op_native(self, UringOpContext ctx, int op_type):
        """Submit an operation using native enum dispatch (no lambda allocation)."""
        Py_INCREF(ctx)
        cdef unsigned long long user_data = <unsigned long long><PyObject*>ctx

        cdef int ret
        cdef char* buf_ptr = NULL
        cdef Py_buffer pybuf
        cdef int has_pybuf = 0

        try:
            if ctx.buf is not None and op_type in (OP_READ, OP_WRITE):
                PyObject_GetBuffer(ctx.buf, &pybuf, PyBUF_SIMPLE)
                buf_ptr = <char*>pybuf.buf
                has_pybuf = 1
            elif ctx.buf is not None and op_type == OP_OPENAT:
                buf_ptr = PyBytes_AsString(ctx.buf)

            if op_type == OP_READ:
                ret = self._ring.do_read(ctx.fd, buf_ptr, ctx.nbytes, ctx.offset, user_data)
            elif op_type == OP_WRITE:
                ret = self._ring.do_write(ctx.fd, buf_ptr, ctx.nbytes, ctx.offset, user_data)
            elif op_type == OP_OPENAT:
                ret = self._ring.do_openat(ctx.fd, buf_ptr, ctx.nbytes, ctx.offset, user_data)
            elif op_type == OP_CLOSE:
                ret = self._ring.do_close(ctx.fd, user_data)
            elif op_type == OP_FSYNC:
                ret = self._ring.do_fsync(ctx.fd, 0, user_data)
            else:
                if has_pybuf:
                    PyBuffer_Release(&pybuf)
                Py_DECREF(ctx)
                raise ValueError(f"Unknown operation type: {op_type}")

            if has_pybuf:
                PyBuffer_Release(&pybuf)

            if ret < 0:
                Py_DECREF(ctx)
                raise MemoryError("Submission Queue Full")

            self._ring.submit()
        except:
            # Undo the INCREF if anything failed during preparation/submission
            Py_DECREF(ctx)
            raise
        
        # Optimistic completion avoids a full event loop round-trip for fast operations
        cdef io_uring_cqe *cqe
        cdef int res
        cdef object value
        cdef completion_handler_t handler
        cdef FastFuture fast_fut
        cdef bytearray read_buf
        cdef object future_to_return
        
        if self._ring.peek_cqe(&cqe) == 0:
            if cqe.user_data == user_data:
                res = cqe.res
                self._ring.cqe_seen(cqe)
                
                fast_fut = <FastFuture>ctx.future
                future_to_return = ctx.future
                
                try:
                    if op_type > 0 and op_type < MAX_OP_TYPE:
                        handler = _handlers[op_type]
                        value = handler(ctx, res, self)
                    else:
                        value = res
                except OSError as exc:
                    fast_fut.set_exception(exc)
                except Exception as exc:
                    fast_fut.set_exception(exc)
                else:
                    fast_fut.set_result(value)
                
                
                ctx.reset()
                if len(self._ctx_pool) < 512:
                    self._ctx_pool.append(ctx)
                
                Py_DECREF(ctx)
                
                return future_to_return
        
        return ctx.future

    cdef void _poll(self, double timeout):
        cdef io_uring_cqe *cqes[512]  # TODO: Figure out a good batch size
        cdef io_uring_cqe *cqe
        cdef unsigned count
        cdef unsigned i
        cdef int ret
        cdef int res
        cdef UringOpContext ctx
        cdef FastFuture fast_fut
        cdef object value
        cdef int op_type
        cdef completion_handler_t handler
        cdef bytearray read_buf
        
        ret = self._ring.submit()
        
        # peek for ready completions without syscall
        count = self._ring.peek_batch_cqe(cqes, 512)
        
        # Wait for completions if none ready and we have a timeout
        if count == 0:
            # If timeout is 0 (poll only), we are done
            if timeout == 0:
                return
            
            with nogil:
                ret = self._ring.wait_cqe(&cqe, timeout)
            
            # Handle interrupted system call like ctrl-c
            if ret == -4:  # -EINTR
                return
            
            if ret == 0:
                # After wait, use batch peek to get all available
                count = self._ring.peek_batch_cqe(cqes, 512)

        while count > 0:
            for i in range(count):
                cqe = cqes[i]
                
                if cqe.user_data == 0:
                    continue
                
                ctx = <UringOpContext><PyObject*>cqe.user_data
                res = cqe.res
                
                op_type = ctx.op_type
                
                fast_fut = <FastFuture>ctx.future
                if fast_fut._state == 0:  # PENDING
                    try:
                        if op_type > 0 and op_type < MAX_OP_TYPE:
                            handler = _handlers[op_type]
                            value = handler(ctx, res, self)
                        else:
                            # Unknown op_type - should not happen
                            value = res
                    except OSError as exc:
                        fast_fut.set_exception(exc)
                    except Exception as exc:
                        fast_fut.set_exception(exc)
                    else:
                        fast_fut.set_result(value)
                
                ctx.reset()
                if len(self._ctx_pool) < 512:
                    self._ctx_pool.append(ctx)
                
                # Decrement reference count to match Py_INCREF in _submit_op_native
                Py_DECREF(ctx)

            self._ring.advance_cq(count)

            # Check for more completions that arrived during processing
            count = self._ring.peek_batch_cqe(cqes, 512)

    def select(self, timeout: float = None):
        if timeout is None:
            self._poll(-1.0)  # Wait indefinitely
        else:
            self._poll(timeout)
        return []

    def close(self):
        if self._reader_added and self._loop is not None:
            try:
                self._loop.remove_reader(self._eventfd)
            except Exception:
                # Add warning log here?
                pass
            self._reader_added = False
        
        if self._eventfd >= 0:
            self._ring.unregister_eventfd()
            close(self._eventfd)
            self._eventfd = -1

    def __del__(self):
        self.close()
