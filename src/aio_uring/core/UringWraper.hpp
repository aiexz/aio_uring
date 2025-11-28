#include <liburing.h>
#include <string.h>

class UringWraper {
private:
  struct io_uring ring;

public:
  UringWraper() {}
  
  ~UringWraper() {
    if (ring.ring_fd) {
      io_uring_queue_exit(&ring);
    }
  }
  
  inline int queue_init(unsigned entries, unsigned flags) {
    // Enable COOP_TASKRUN for reduced context switches (kernel 5.19+)
    // Note: DEFER_TASKRUN and SINGLE_ISSUER are NOT used because they
    // prevent immediate eventfd notifications for asyncio integration
    unsigned enhanced_flags = flags;
#ifdef IORING_SETUP_COOP_TASKRUN
    enhanced_flags |= IORING_SETUP_COOP_TASKRUN;
#endif
    
    int ret = io_uring_queue_init(entries, &ring, enhanced_flags);
    if (ret < 0 && enhanced_flags != flags) {
      // Fall back to basic flags if enhanced features not supported
      ret = io_uring_queue_init(entries, &ring, flags);
    }
    
    return ret;
  }

  inline int submit() {
    unsigned ready = io_uring_sq_ready(&ring);
    if (ready > 0) {
      return io_uring_submit(&ring);
    }
    return 0;
  }

  inline unsigned peek_batch_cqe(io_uring_cqe **cqes, unsigned count) {
    return io_uring_peek_batch_cqe(&ring, cqes, count);
  }

  inline void advance_cq(unsigned count) {
    io_uring_cq_advance(&ring, count);
  }

  inline io_uring_sqe* get_sqe_safe() {
    // Try to get SQE without submission
    io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    if (__builtin_expect(!sqe, 0)) {
      // SQ full - submit pending operations to free space
      io_uring_submit(&ring);
      sqe = io_uring_get_sqe(&ring);
    }
    return sqe;
  }

  inline int wait_cqe(io_uring_cqe **cqe_ptr, double timeout_sec) {
    // If timeout is negative, wait indefinitely (standard io_uring_wait_cqe)
    if (timeout_sec < 0) {
      return io_uring_wait_cqe(&ring, cqe_ptr);
    }

    // Otherwise use the timeout version
    __kernel_timespec ts;
    ts.tv_sec = timeout_sec;
    ts.tv_nsec = (timeout_sec - ts.tv_sec) * 1e9;
    return io_uring_wait_cqe_timeout(&ring, cqe_ptr, &ts);
  }
  
  inline int peek_cqe(io_uring_cqe **cqe_ptr) {
    return io_uring_peek_cqe(&ring, cqe_ptr);
  }
  
  inline void cqe_seen(io_uring_cqe *cqe) { io_uring_cqe_seen(&ring, cqe); }

  // File I/O operations
  
  inline int do_read(int fd, char *buf, unsigned nbytes, __u64 offset, __u64 user_data) {
    io_uring_sqe *sqe = get_sqe_safe();
    if (__builtin_expect(!sqe, 0)) return -1;
    io_uring_prep_read(sqe, fd, buf, nbytes, offset);
    sqe->user_data = user_data;
    return 0;
  }
  
  inline int do_write(int fd, char *buf, unsigned nbytes, __u64 offset, __u64 user_data) {
    io_uring_sqe *sqe = get_sqe_safe();
    if (__builtin_expect(!sqe, 0)) return -1;
    io_uring_prep_write(sqe, fd, buf, nbytes, offset);
    sqe->user_data = user_data;
    return 0;
  }
  
  inline int do_openat(int dfd, const char *path, int flags, mode_t mode, __u64 user_data) {
    io_uring_sqe *sqe = get_sqe_safe();
    if (!sqe) return -1;
    io_uring_prep_openat(sqe, dfd, path, flags, mode);
    sqe->user_data = user_data;
    return 0;
  }
  
  inline int do_close(int fd, __u64 user_data) {
    io_uring_sqe *sqe = get_sqe_safe();
    if (!sqe) return -1;
    io_uring_prep_close(sqe, fd);
    sqe->user_data = user_data;
    return 0;
  }
  
  inline int do_fsync(int fd, unsigned flags, __u64 user_data) {
    io_uring_sqe *sqe = get_sqe_safe();
    if (!sqe) return -1;
    io_uring_prep_fsync(sqe, fd, flags);
    sqe->user_data = user_data;
    return 0;
  }

  inline int register_eventfd(int fd) {
    return io_uring_register_eventfd(&ring, fd);
  }

  inline int unregister_eventfd() {
    return io_uring_unregister_eventfd(&ring);
  }

  inline void queue_exit() {
    if (ring.ring_fd > 0) {
      io_uring_queue_exit(&ring);
      ring.ring_fd = 0;
    }
  }
};
