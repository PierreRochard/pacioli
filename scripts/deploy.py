"""Deploy this checkout to the local pacioli-web service.

The service runs straight from this checkout and its .venv, so a deploy backs
up the database, makes .venv match the hashed lock exactly, applies the
idempotent schema updates in `manage.py createdb`, restarts the service and
waits for its login page. Connector migrations such as
`manage.py migrate_chase_browser` stay separate and explicit.
"""

from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv' / 'bin' / 'python'
SERVICE = 'pacioli-web.service'
LOGIN_URL = 'http://127.0.0.1:8001/login/'
START_TIMEOUT_SECONDS = 60


def run(*command):
    print('+', ' '.join(str(part) for part in command), flush=True)
    subprocess.run(command, check=True, cwd=ROOT)


def wait_for_login_page():
    opener = build_opener(ProxyHandler({}))  # Loopback only; never a proxy.
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while True:
        try:
            with opener.open(LOGIN_URL, timeout=5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise SystemExit('{} did not serve {} within {} seconds; see '
                             'journalctl --user -u {}'.format(
                                 SERVICE, LOGIN_URL, START_TIMEOUT_SECONDS, SERVICE))
        time.sleep(1)


def main():
    if not PYTHON.exists():
        raise SystemExit('No .venv; run `make env` first')
    if not shutil.which('uv'):
        raise SystemExit('uv is required to sync .venv with the lock')
    run(PYTHON, 'scripts/local_db.py', 'backup')
    # The service's .venv also runs local CI, so it follows the development lock.
    run('uv', 'pip', 'sync', '--python', PYTHON, '--require-hashes', 'requirements-dev.txt')
    run(PYTHON, '-W', 'ignore', 'manage.py', 'createdb')
    run('systemctl', '--user', 'restart', SERVICE)
    wait_for_login_page()
    print('{} is serving {}'.format(SERVICE, LOGIN_URL), flush=True)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
