"""Start Chase sync workers that the containerized web service queued.

The web container has no systemd user manager, so it leaves an empty file named
after each run's numeric ID in .local/chase-launch. The pacioli-chase-launcher
path unit runs this script on the host whenever that directory is not empty.
It removes every entry and starts a worker for each numeric one, as the web
service did before it moved into a container. A run that cannot be started is
marked failed, as before.
"""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QUEUE = ROOT / '.local' / 'chase-launch'


def queued_runs(queue):
    """Empty the queue and return the run IDs it held, oldest first."""
    entries = sorted(queue.iterdir(), key=lambda path: path.lstat().st_mtime)
    runs = []
    for path in entries:
        if path.is_dir() and not path.is_symlink():
            path.rmdir()  # never expected; a non-empty one fails loudly
        else:
            path.unlink()
        if path.name.isdigit():
            runs.append(int(path.name))
    return runs


def launch(app, run_ids):
    from pacioli.functions.chase_browser import _set_run, systemd_run
    with app.app_context():
        for run_id in run_ids:
            try:
                systemd_run(run_id)
            except (OSError, subprocess.SubprocessError):
                _set_run(run_id, 'failed', error_code='desktop_launch_failed')


def main():
    if not QUEUE.is_dir():
        return
    run_ids = queued_runs(QUEUE)
    if run_ids:
        from pacioli.wsgi import app
        launch(app, run_ids)


if __name__ == '__main__':
    main()
