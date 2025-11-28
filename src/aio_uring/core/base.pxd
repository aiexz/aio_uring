# cython:language_level=3
# Operation type enum - avoids lambda allocation
cpdef enum OpType:
    OP_READ = 1
    OP_WRITE = 2
    OP_OPENAT = 4
    OP_CLOSE = 5
    OP_FSYNC = 6
