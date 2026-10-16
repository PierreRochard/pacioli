"""The Chase connector's private state directory and its single-worker lock."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

from flask import current_app


class ProfileUnavailable(Exception):
    pass


class ProfileBusy(ProfileUnavailable):
    pass


def _private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise ProfileUnavailable()
    return path


def profile_root():
    default = Path(__file__).resolve().parents[2] / '.local' / 'chase-browser'
    return _private_directory(Path(current_app.config.get(
        'CHASE_BROWSER_PROFILE_ROOT', default)))


@contextmanager
def browser_lock():
    """Also protects against stale DB jobs and overlapping worker shutdowns."""
    path = profile_root() / 'worker.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise ProfileUnavailable()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProfileBusy() from None
        yield
    finally:
        os.close(descriptor)
