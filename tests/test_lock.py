from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.lock import AlreadyRunningError, RunLock  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _spawn_holder(workdir: Path, hold_seconds: float) -> subprocess.Popen:
    """A real separate process that acquires the lock and holds it, so tests
    exercise actual cross-process contention rather than in-process mocks.
    """
    script = (
        f"import sys, time\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        f"from engine.lock import RunLock\n"
        f"lock = RunLock({str(workdir)!r})\n"
        f"lock.acquire()\n"
        f"print('locked', flush=True)\n"
        f"time.sleep({hold_seconds})\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, text=True,
    )
    # wait for the child to actually confirm it holds the lock before
    # the test proceeds, rather than guessing with a sleep
    line = proc.stdout.readline()
    assert line.strip() == "locked", f"holder process failed to acquire: {line!r}"
    return proc


def test_acquire_then_release_allows_reacquire(tmp_path: Path):
    lock = RunLock(tmp_path)
    lock.acquire()
    lock.release()
    # should be acquirable again with no error
    lock2 = RunLock(tmp_path)
    lock2.acquire()
    lock2.release()


def test_blocks_while_a_real_process_holds_it(tmp_path: Path):
    holder = _spawn_holder(tmp_path, hold_seconds=5)
    try:
        with pytest.raises(AlreadyRunningError):
            RunLock(tmp_path).acquire()
    finally:
        holder.kill()
        holder.wait()


def test_released_immediately_after_holder_is_sigkilled(tmp_path: Path):
    """This is the exact scenario the old PID-liveness lock couldn't handle
    safely across a container restart: the holder dies WITHOUT a chance to
    run any cleanup code (no atexit, no finally block - SIGKILL can't be
    caught). A correct lock must still release, immediately, with no
    dependency on the dead process having run any code at all.
    """
    holder = _spawn_holder(tmp_path, hold_seconds=30)
    holder.send_signal(signal.SIGKILL)
    holder.wait()

    # no sleep/backoff needed - the kernel releases flock() the instant the
    # file descriptor's owning process dies, so this should succeed right away
    lock = RunLock(tmp_path)
    lock.acquire()  # should NOT raise
    lock.release()


def test_context_manager_releases_on_exception(tmp_path: Path):
    class Boom(Exception):
        pass

    try:
        with RunLock(tmp_path):
            raise Boom()
    except Boom:
        pass

    # lock should be free again despite the exception
    lock = RunLock(tmp_path)
    lock.acquire()
    lock.release()
