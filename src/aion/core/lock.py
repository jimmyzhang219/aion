"""Cross-platform file locking.

Provides a ``FileLock`` class that wraps ``fcntl.flock`` on Unix
and ``msvcrt.locking`` on Windows.
"""

import sys

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class FileLock:
    """Context-free file lock (explicit acquire/release).

    Usage::

        lock = FileLock(f)
        lock.acquire_exclusive()
        try:
            f.write(data)
        finally:
            lock.release()
    """

    def __init__(self, file):
        self._file = file

    def acquire_shared(self) -> None:
        """Acquire a shared (read) lock, non-blocking."""
        if sys.platform == "win32":
            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)

    def acquire_exclusive(self) -> None:
        """Acquire an exclusive (write) lock, non-blocking."""
        if sys.platform == "win32":
            msvcrt.locking(self._file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self) -> None:
        """Release the lock."""
        if sys.platform == "win32":
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
