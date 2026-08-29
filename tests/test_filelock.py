"""Tests for telemffb.namedmutex.FileLock.

Validates cross-process/cross-thread mutual exclusion semantics:
readers and writers must never overlap, regardless of platform.
"""
import os
import sys
import threading
import time

import pytest

from telemffb.namedmutex import FileLock

IS_WINDOWS = sys.platform == "win32"


@pytest.fixture
def lock_target(tmp_path):
    target = tmp_path / "file.xml"
    target.write_text("<root/>")
    return str(target)


class TestLockFileLifecycle:
    @pytest.mark.skipif(IS_WINDOWS, reason="POSIX-only companion .lock file")
    def test_lock_file_not_removed_on_release(self, lock_target):
        """The .lock file must survive release — a process blocked on the
        old inode would otherwise race a new acquirer on a new inode."""
        lock_path = lock_target + ".lock"
        with FileLock(lock_target, shared=True):
            assert os.path.exists(lock_path)
        # released, but file must NOT be unlinked
        assert os.path.exists(lock_path)
        # and re-acquisition still works
        with FileLock(lock_target):
            pass
        assert os.path.exists(lock_path)


class TestConcurrency:
    def _hold_in_thread(self, target, shared, timeout, ready, done):
        lock = FileLock(target, timeout=timeout, shared=shared)
        assert lock.acquire()
        ready.set()
        time.sleep(0.5)  # hold the lock
        lock.release()
        done.set()

    def test_writer_blocks_reader(self, lock_target):
        ready = threading.Event()
        done = threading.Event()
        t = threading.Thread(
            target=self._hold_in_thread,
            args=(lock_target, False, 10.0, ready, done))
        t.start()
        ready.wait(5)
        try:
            # reader must time out while writer holds the lock
            assert FileLock(lock_target, timeout=0.3, shared=True).acquire() is False
        finally:
            t.join()

    def test_reader_blocks_writer(self, lock_target):
        ready = threading.Event()
        done = threading.Event()
        t = threading.Thread(
            target=self._hold_in_thread,
            args=(lock_target, True, 10.0, ready, done))
        t.start()
        ready.wait(5)
        try:
            # writer must time out while reader holds the lock
            assert FileLock(lock_target, timeout=0.3).acquire() is False
        finally:
            t.join()

    @pytest.mark.skipif(IS_WINDOWS, reason="POSIX flock allows concurrent readers")
    def test_concurrent_readers_allowed(self, lock_target):
        results = []

        def read(target, ready, done):
            lock = FileLock(target, timeout=5.0, shared=True)
            ok = lock.acquire()
            ready.set()
            time.sleep(0.3)
            lock.release()
            done.set()
            results.append(ok)

        r1, d1 = threading.Event(), threading.Event()
        r2, d2 = threading.Event(), threading.Event()
        t1 = threading.Thread(target=read, args=(lock_target, r1, d1))
        t2 = threading.Thread(target=read, args=(lock_target, r2, d2))
        t1.start()
        r1.wait(5)
        t2.start()  # second reader must not block while first holds
        r2.wait(5)
        t1.join()
        t2.join()
        assert results == [True, True]

    def test_context_manager_timeout_raises(self, lock_target):
        ready = threading.Event()
        done = threading.Event()
        t = threading.Thread(
            target=self._hold_in_thread,
            args=(lock_target, False, 10.0, ready, done))
        t.start()
        ready.wait(5)
        try:
            with pytest.raises(TimeoutError):
                with FileLock(lock_target, timeout=0.3, shared=True):
                    pass
        finally:
            t.join()
