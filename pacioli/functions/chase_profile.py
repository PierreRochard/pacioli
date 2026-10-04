"""Private, app-owned Chromium profiles; never use the user's daily profile."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import shutil
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


def profile_path(key):
    if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{32}', key):
        raise ProfileUnavailable()
    return _private_directory(profile_root() / key)


def remove_profile(key):
    """Caller holds browser_lock. Refuse paths outside this app's namespace."""
    if key is None:
        return
    path = profile_path(key)
    shutil.rmtree(path)


def remove_unused_profiles(keep):
    """Caller holds browser_lock; reap test profiles left by a killed worker."""
    for path in profile_root().iterdir():
        if re.fullmatch('[a-f0-9]{32}', path.name) and path.name not in keep:
            if path.is_symlink():
                path.unlink()
            else:
                remove_profile(path.name)


def discard_history(profile):
    """Keep auth/provider state, discard only this app profile's history.

    Chromium 153 on this workstation crashes on a second download after profile
    reuse. Removing History while the browser is closed fixes the offline
    reproducer. Related upstream report: microsoft/playwright#42506.
    """
    default = profile / 'Default'
    if default.is_symlink():
        raise ProfileUnavailable()
    for name in ('History', 'History-journal', 'History-wal', 'History-shm'):
        (default / name).unlink(missing_ok=True)


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
