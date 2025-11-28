# To be done
I am still in the process of writing and evaluating pros and cons for various options. Here are some of the things I plan to do:

- [ ] Figure out the pool situation
    <details>
    <summary>The problem</summary>


    Currently we have pools with 64KB and 1MB blocks. 
    ```python
            if capacity == self._read_buffer_size and len(self._read_buffer_pool) < 64:
                self._read_buffer_pool.append(buf)
            # Add a pool for 1MB buffers (simplified for your specific benchmark)
            elif capacity == 1024 * 1024 and len(self._read_buffer_pool) < 16:
                self._read_buffer_pool.append(buf)
    ```
    And when we want to use a buffer we get it from the pool, but we don't know about the pool size, so we sometimes we get a 1MB buffer when we only need 64KB. Or vice versa.
    </details>
- [ ] Figure out magic numbers
    <details>
    <summary>The problem</summary>
    There are quite a few magic numbers in the code, like 64KB and 1MB for buffer sizes, or 64 and 16 for pool sizes or 4096 for submission queue size.
    We should define them as constants or configuration parameters, so that they can be easily changed and understood.
    </details>
- [ ] Reuse data for sized reads
    <details>
    <summary>The problem</summary>
    When we do sized reads, we drop extra carecters, but we can reuse them for the next read instead of discarding them.
    </details>
- [ ] Figure our the io_uring ring on file close
    <details>
    <summary>The problem</summary>
    When we close the `UringFile`, we should also properly close the io_uring ring to avoid resource leaks.
    </details>