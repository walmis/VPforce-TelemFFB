#
# This file is part of the TelemFFB distribution (https://github.com/walmis/TelemFFB).
# Copyright (c) 2023 Valmantas Palikša.
# Copyright (c) 2023 Micah Frisby
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""Cross-platform named mutex / file locking.

* **Windows** — ``NamedMutex`` wraps Win32 ``CreateMutexW`` / ``WaitForSingleObject``.
* **Linux / Unix** — ``FileLock`` uses ``fcntl.flock()`` for advisory file locking.

Both expose a uniform context-manager API::

    with FileLock('/path/to/file.xml'):
        # exclusive access here

Original ``NamedMutex`` code released under the BSD 3-clause license
(see https://github.com/benhoyt/namedmutex).
"""

import ctypes
import hashlib
import os
import sys
import time
from typing import Optional

# ── Platform detection ────────────────────────────────────────────────

if sys.platform == "win32":
    _IS_WINDOWS = True
else:
    _IS_WINDOWS = False


# ── Windows primitives ────────────────────────────────────────────────

if _IS_WINDOWS:
    from ctypes import wintypes

    _CreateMutex = ctypes.windll.kernel32.CreateMutexW  # type: ignore[union-attr]
    _CreateMutex.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]  # type: ignore[name-defined]
    _CreateMutex.restype = wintypes.HANDLE  # type: ignore[name-defined]

    _WaitForSingleObject = ctypes.windll.kernel32.WaitForSingleObject  # type: ignore[union-attr]
    _WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]  # type: ignore[name-defined]
    _WaitForSingleObject.restype = wintypes.DWORD  # type: ignore[name-defined]

    _ReleaseMutex = ctypes.windll.kernel32.ReleaseMutex  # type: ignore[union-attr]
    _ReleaseMutex.argtypes = [wintypes.HANDLE]  # type: ignore[name-defined]
    _ReleaseMutex.restype = wintypes.BOOL  # type: ignore[name-defined]

    _CloseHandle = ctypes.windll.kernel32.CloseHandle  # type: ignore[union-attr]
    _CloseHandle.argtypes = [wintypes.HANDLE]  # type: ignore[name-defined]
    _CloseHandle.restype = wintypes.BOOL  # type: ignore[name-defined]


class NamedMutex:
    """A named, system-wide Win32 mutex.

    Raises :exc:`RuntimeError` on non-Windows platforms.
    """

    def __init__(self, name: str, acquired: bool = False, timeout: Optional[float] = None) -> None:
        if not _IS_WINDOWS:
            raise RuntimeError("NamedMutex requires Windows")
        self.name = name
        self.acquired = acquired
        ret = _CreateMutex(None, False, name)  # type: ignore[possibly-undefined]
        if not ret:
            raise ctypes.WinError()
        self.handle = ret
        if acquired:
            self.acquire(timeout=timeout)

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Acquire ownership of the mutex.

        Returns ``True`` on success, ``False`` on timeout.
        Raises :exc:`OSError` on other errors.
        """
        if timeout is None:
            timeout_ms = 0xFFFFFFFF  # INFINITE
        else:
            timeout_ms = int(round(timeout * 1000))
        ret = _WaitForSingleObject(self.handle, timeout_ms)  # type: ignore[possibly-undefined]
        if ret in (0, 0x80):
            self.acquired = True
            return True
        elif ret == 0x102:
            self.acquired = False
            return False
        else:
            raise ctypes.WinError()

    def release(self) -> None:
        """Release an acquired mutex."""
        ret = _ReleaseMutex(self.handle)  # type: ignore[possibly-undefined]
        if not ret:
            raise ctypes.WinError()
        self.acquired = False

    def close(self) -> None:
        """Close the mutex handle."""
        if self.handle is None:
            return
        ret = _CloseHandle(self.handle)  # type: ignore[possibly-undefined]
        if not ret:
            raise ctypes.WinError()
        self.handle = None

    __del__ = close

    def __repr__(self) -> str:
        return '{0}({1!r}, acquired={2})'.format(
            self.__class__.__name__, self.name, self.acquired)

    __str__ = __repr__

    # Context manager
    def __enter__(self) -> "NamedMutex":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


# ── Cross-platform FileLock ───────────────────────────────────────────

class FileLock:
    """Cross-platform file-level lock using a named mutex per file path.

    * **Windows**: backed by a Win32 named mutex (SHA-256 hashed path).
      Reader-writer distinction is not supported — all locks are exclusive.
    * **Linux / Unix**: backed by ``fcntl.flock()`` on a companion .lock file.
      Shared (read) and exclusive (write) modes are fully supported.

    Example::

        # Exclusive lock (write)
        with FileLock('/path/to/userconfig_v2.xml'):
            tree = ET.parse(path)
            # ... modify ...
            tree.write(path)

        # Shared lock (read) — allows concurrent readers
        with FileLock('/path/to/userconfig_v2.xml', shared=True):
            tree = ET.parse(path)  # safe from mid-write corruption

    The lock is released automatically on exit or exception.

    Args:
        file_path: Path to the file to protect.
        timeout: Maximum seconds to wait for the lock.  ``None`` waits
            indefinitely.  On timeout the context manager raises
            :class:`TimeoutError`.
        shared: If ``True``, acquire a shared (read) lock on POSIX platforms.
            Multiple processes may hold shared locks concurrently; an
            exclusive (write) lock blocks until all shared locks are released.
            On Windows this flag is ignored (always exclusive).

    Raises:
        TimeoutError: If *timeout* elapsed before the lock was acquired.
        OSError: If the underlying OS call fails.
    """

    _MUTEX_PREFIX = "telemffb_xml_"

    def __init__(self, file_path: str, timeout: Optional[float] = 5.0, *, shared: bool = False) -> None:
        self.file_path = os.path.abspath(file_path)
        self.timeout = timeout
        self.shared = shared
        self._locked = False

        if _IS_WINDOWS:
            self._mutex: Optional[NamedMutex] = None
        else:
            import fcntl  # noqa: F811
            self._fd: Optional[int] = None
            self._fcntl = fcntl

    # ── internal helpers ─────────────────────────────────────────

    @classmethod
    def _safe_name(cls, file_path: str) -> str:
        """Derive a valid Win32 mutex name from any file path."""
        digest = hashlib.sha256(file_path.encode("utf-8")).hexdigest()[:16]
        return f"{cls._MUTEX_PREFIX}{digest}"

    # ── Windows implementation ──────────────────────────────────

    def _acquire_win(self) -> bool:
        self._mutex = NamedMutex(self._safe_name(self.file_path))
        ok = self._mutex.acquire(timeout=self.timeout)
        if not ok:
            self._mutex.close()
            self._mutex = None
        return ok

    def _release_win(self) -> None:
        if self._mutex is not None:
            try:
                self._mutex.release()
            finally:
                self._mutex.close()
                self._mutex = None

    # ── POSIX implementation ─────────────────────────────────────

    def _lock_path(self) -> str:
        """Return the companion .lock file path."""
        return self.file_path + ".lock"

    def _acquire_posix(self) -> bool:
        lock_path = self._lock_path()
        self._fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        flag = self._fcntl.LOCK_SH if self.shared else self._fcntl.LOCK_EX
        deadline = time.monotonic() + self.timeout if self.timeout is not None else None
        while True:
            try:
                self._fcntl.flock(self._fd, flag | self._fcntl.LOCK_NB)
                return True
            except (IOError, OSError):
                if deadline is not None and time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    return False
                time.sleep(0.05)

    def _release_posix(self) -> None:
        if self._fd is not None:
            try:
                self._fcntl.flock(self._fd, self._fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
            # Clean up lock file
            try:
                os.unlink(self._lock_path())
            except OSError:
                pass

    # ── public API ──────────────────────────────────────────────

    def acquire(self) -> bool:
        """Acquire the file lock, blocking up to *timeout* seconds.

        Returns ``True`` on success, ``False`` on timeout.
        """
        if self._locked:
            return True  # already held by this thread/process
        if _IS_WINDOWS:
            ok = self._acquire_win()
        else:
            ok = self._acquire_posix()
        if ok:
            self._locked = True
        return ok

    def release(self) -> None:
        """Release the file lock."""
        if not self._locked:
            return
        if _IS_WINDOWS:
            self._release_win()
        else:
            self._release_posix()
        self._locked = False

    # ── context manager ─────────────────────────────────────────

    def __enter__(self) -> "FileLock":
        if not self.acquire():
            raise TimeoutError(
                f"Could not acquire lock for {self.file_path!r} "
                f"within {self.timeout}s"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
        return None


# ── CLI smoke test ────────────────────────────────────────────────────

if __name__ == '__main__':
    if _IS_WINDOWS:
        with NamedMutex('test_mutex_123'):
            print("NamedMutex acquired & released OK")
    else:
        with FileLock('/tmp/test_filelock.txt'):
            print("FileLock (POSIX) acquired & released OK")