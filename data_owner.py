"""Protect restart recovery from another server using the same job directory."""
from contextlib import contextmanager
import fcntl


@contextmanager
def own_data_directory(root):
    with (root/'jobs/.server.lock').open('a+') as handle:
        try:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another Voice Studio server owns this DATA_DIR. Choose an isolated data directory.') from None
        yield handle
