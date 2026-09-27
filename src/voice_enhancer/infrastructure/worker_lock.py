"""Enforce the single-worker contract on the shared Linux media volume."""

from contextlib import contextmanager
from pathlib import Path


@contextmanager
def single_worker(root: Path):
    import fcntl

    root.mkdir(parents=True, exist_ok=True)
    # Never unlink this file: a new inode would let a second worker acquire a lock.
    with (root / ".worker.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another worker is using this media volume") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
