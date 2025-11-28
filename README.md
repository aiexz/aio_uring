# aio-uring
High-performance async file I/O using Linux io_uring.

## Features

- **High Performance**: Uses Linux io_uring for efficient async file I/O
- **Any Event Loop**: Works with any asyncio event loop (including uvloop)
- **Simple API**: Familiar file-like interface with async/await support

## Requirements

- **Linux** (io_uring is a Linux-specific kernel interface)
- **Linux kernel 5.1+** (for basic io_uring support)
- **liburing** (development library)
- **Python 3.12+**

## Who should use aio-uring?
- **You have High Concurrency**: If you are building a server handling 1,000 concurrent uploads/downloads.
The lack of threads will save you gigabytes of RAM and CPU context-switching time (aio-uring works great with uvloop too).
- **You are Reading Files**: The Memory Pool and Optimistic Completion we found make it faster than almost anything else.
- **You want Stable Latency**: No thread pool jitter means your p99 latency will be much flatter.
### But you are better off with regular open if:
- You need a lot of writes
- Your workload is mostly sequential reads

## Installation

### From Source

```bash
# Install liburing development package
# Debian/Ubuntu:
sudo apt-get install liburing-dev

# Fedora/RHEL:
sudo dnf install liburing-devel

# Then install aio-uring
pip install .
```

### Using Docker (for development on non-Linux systems)

```bash

# Or use docker-compose
docker-compose run --rm test
```

## Quick Start

```python
import asyncio
from aio_uring import UringFileIO
import aio_uring

async def main():
    uio = UringFileIO()
    
    # Write to a file
    async with uio.open('example.txt', 'w') as f:
        await f.write(b"Hello, io_uring!")
    
    # or without even using `uio`
    async with aio_uring.open('example2.txt', 'w') as f:
        await f.write(b"Hello, aio_uring!")
    
    # Read from a file
    async with uio.open('example.txt', 'r') as f:
        data = await f.read(1024)
        print(data)
    
    uio.close()

asyncio.run(main())
```

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
