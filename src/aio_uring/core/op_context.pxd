# cython:language_level=3

cdef class UringOpContext:
    """
    Reusable context object for io_uring operations.
    
    Eliminates tuple allocation overhead by using a pooled cdef class.
    Uses typed fields where possible to reduce Python object overhead.
    """
    cdef public object future
    cdef public object keep_alive_obj
    cdef public int fd
    cdef public object buf
    cdef public unsigned nbytes
    cdef public unsigned long long offset
    cdef public int op_type
    cdef public char* _buf_ptr
    cdef public unsigned _buf_len
    
    cdef inline void reset(self)
