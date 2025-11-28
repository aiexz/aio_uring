# distutils:language=c++
# cython:language_level=3
# cython:boundscheck=False
# cython:wraparound=False
# cython:cdivision=True
# cython:initializedcheck=False
# cython:freelist=512


cdef class UringOpContext:
    """Reusable context object for io_uring operations.
    
    Eliminates tuple allocation overhead by using a pooled cdef class.
    """
    
    def __cinit__(self):
        """Initialize typed fields to safe defaults."""
        self.fd = -1
        self.nbytes = 0
        self.offset = 0
        self.op_type = 0
        self._buf_ptr = NULL
        self._buf_len = 0
    
    cdef inline void reset(self):
        """Reset context for reuse in the pool."""
        self.future = None
        self.keep_alive_obj = None
        self.fd = -1
        self.buf = None
        self.nbytes = 0
        self.offset = 0
        self.op_type = 0
        self._buf_ptr = NULL
        self._buf_len = 0
