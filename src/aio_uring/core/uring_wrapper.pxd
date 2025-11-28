# cython:language_level=3


cdef extern from "UringWraper.hpp":
    # C++ io_uring wrapper declarations for file I/O operations.
    cdef struct io_uring_cqe:
        unsigned long long user_data
        signed res
        unsigned flags

    cdef cppclass UringWraper:
        signed queue_init(unsigned entries, unsigned flags)
        signed submit()

        signed wait_cqe(io_uring_cqe ** cqe_ptr, double timeout_sec) nogil
        signed peek_cqe(io_uring_cqe ** cqe_ptr)
        unsigned peek_batch_cqe(io_uring_cqe ** cqes, unsigned count)
        void cqe_seen(io_uring_cqe * cqe)
        void advance_cq(unsigned count)

        # File I/O operations
        signed do_read(signed fd, char * buf, unsigned nbytes, unsigned long long offset, unsigned long long user_data)
        signed do_write(signed fd, char * buf, unsigned nbytes, unsigned long long offset, unsigned long long user_data)
        signed do_openat(signed dfd, const char * path, signed flags, unsigned mode, unsigned long long user_data)
        signed do_close(signed fd, unsigned long long user_data)
        signed do_fsync(signed fd, unsigned flags, unsigned long long user_data)

        # Event fd for asyncio integration
        signed register_eventfd(signed fd)
        signed unregister_eventfd()

        void queue_exit()
