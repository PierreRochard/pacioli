"""Run one supervised Chase export from a transient desktop user service."""

import signal
import sys

from pacioli.functions.chase_browser import ChaseBrowserCancelled, run_worker


def _cancel_worker(signum, frame):
    # systemd stop must unwind browser contexts and release the BiDi session.
    raise ChaseBrowserCancelled()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not argv[0].isdigit():
        raise SystemExit('Expected one numeric run ID')
    from pacioli.wsgi import app
    previous_handler = signal.signal(signal.SIGTERM, _cancel_worker)
    try:
        with app.app_context():
            run_worker(int(argv[0]))
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == '__main__':
    main()
