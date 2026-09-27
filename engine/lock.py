"""
Prevents two concurrent `run.py` invocations from writing to the same
workdir/scoreboard at once.

This is built on the OS's own advisory file locking (fcntl.flock), not a
manually-tracked PID, and that choice is deliberate: a PID recorded in a
lock file is unreliable to check for liveness across a container restart.
Docker resets PID numbering each time a container's process tree starts
fresh, so a low PID (1, 7, 12...) recorded by a previous run is very likely
to coincidentally belong to some unrelated, genuinely-alive process shortly
after the container comes back up - producing a false "already running"
error that has nothing to do with an actual concurrent run.

flock() sidesteps this entirely: the lock is tied to an open file
descriptor held by the actual live process. The kernel releases it
automatically the instant that process exits for ANY reason - clean exit,
an uncaught exception, `docker stop`, an OOM-kill, a hard `kill -9` - with
no atexit handler and no PID bookkeeping required. This is the same
mechanism most Unix daemons and package managers (apt, dpkg) use for
exactly this reason.
"""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO


class AlreadyRunningError(Exception):
    pass


class RunLock:
    """Use as a context manager:

        with RunLock(workdir):
            orchestrator.run()

    or call acquire()/release() directly if you need the lock held across a
    wider scope than a single `with` block.
    """

    def __init__(self, workdir: Path | str):
        self.lock_path = Path(workdir) / "run.lock"
        self._fh: IO | None = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise AlreadyRunningError(
                f"A run is already in progress against this workdir "
                f"({self.lock_path}). Wait for it to finish. If you're sure "
                "nothing is actually running (e.g. the host itself was hard "
                "reset while a process held this lock), it's safe to delete "
                "that file by hand and try again."
            )
        self._fh = fh

    def release(self) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()
