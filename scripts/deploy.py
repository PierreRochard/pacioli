"""Deploy this checkout to the local pacioli-web service.

The web service runs in a container (compose.yml) built from the committed
tree plus the private wheels in .local/wheels, never from uncommitted files.
A deploy backs up the database, makes .venv match the hashed lock (it still
runs migrations, backups, CI and the Chase worker on the host), builds the
SPARK OFX program, builds the image, applies pending Alembic migrations,
restarts the service and waits for its login page.
"""

import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv' / 'bin' / 'python'
SERVICE = 'pacioli-web.service'
LOGIN_URL = 'http://127.0.0.1:8001/login/'
START_TIMEOUT_SECONDS = 60
IMAGE = 'pacioli-web'
KEEP_IMAGES = 3  # the current build and two to roll back to
# Bind-mount sources in compose.yml. Docker would create a missing one as a
# root-owned directory, which the container cannot use.
MOUNTED_FILES = ('config.json', 'decoy_tickers.txt', 'lan-backup/last-success')
PRIVATE_DIRECTORIES = ('chase-browser', 'chase-launch', 'schwab')
# Built from the committed sources by `make ofx`; the host worker and the
# image run the same static binary.
OFX_PROGRAM = Path('ofx', 'build', 'bin', 'pacioli-ofx')


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


def committed_revision():
    changed = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'],
                             cwd=ROOT, check=True, capture_output=True, text=True).stdout
    if changed.strip():
        raise SystemExit('Commit tracked changes first: the image is built from HEAD')
    return subprocess.run(['git', 'rev-parse', '--short=12', 'HEAD'], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def check_mounts():
    local = ROOT / '.local'
    missing = [name for name in MOUNTED_FILES if not (local / name).is_file()]
    if missing:
        raise SystemExit('Missing private files the container mounts: {}'.format(
            ', '.join('.local/' + name for name in missing)))
    for name in PRIVATE_DIRECTORIES:
        (local / name).mkdir(mode=0o700, exist_ok=True)


def build_image(revision):
    """Build from the committed tree and the locked private wheels, nothing else."""
    archive = subprocess.run(['git', 'archive', 'HEAD'], cwd=ROOT, check=True,
                             capture_output=True).stdout
    scratch = ROOT / '.local' / 'ci'
    scratch.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pacioli-image-', dir=scratch) as context:
        context = Path(context)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
            tree.extractall(context, filter='data')
        wheels = context / '.local' / 'wheels'
        wheels.mkdir(parents=True)
        lock = (ROOT / 'requirements.txt').read_text()
        for name in re.findall(r'^\./\.local/wheels/(\S+\.whl)', lock, re.MULTILINE):
            shutil.copy2(ROOT / '.local' / 'wheels' / name, wheels / name)
        (context / OFX_PROGRAM).parent.mkdir(parents=True)
        shutil.copy2(ROOT / OFX_PROGRAM, context / OFX_PROGRAM)
        environment = dict(os.environ)
        # Ubuntu ships BuildKit separately (docker-buildx); fall back to the
        # classic builder without it.
        buildx = subprocess.run(['docker', 'buildx', 'version'], capture_output=True)
        environment['DOCKER_BUILDKIT'] = '1' if buildx.returncode == 0 else '0'
        print('+ docker build', IMAGE, revision, flush=True)
        subprocess.run(['docker', 'build', '--quiet', '--tag', '{}:{}'.format(IMAGE, revision),
                        '--tag', IMAGE + ':current', str(context)],
                       check=True, env=environment, stdout=subprocess.DEVNULL)


def prune_images(revision):
    listed = subprocess.run(['docker', 'image', 'ls', IMAGE, '--format', '{{.Tag}}'],
                            check=True, capture_output=True, text=True).stdout.split()
    builds = [tag for tag in listed if re.fullmatch(r'[0-9a-f]{12}', tag)]
    # docker image ls lists the newest first.
    stale = [tag for tag in builds if tag != revision][KEEP_IMAGES - 1:]
    for tag in stale:
        subprocess.run(['docker', 'image', 'rm', '{}:{}'.format(IMAGE, tag)],
                       check=True, stdout=subprocess.DEVNULL)
    subprocess.run(['docker', 'image', 'prune', '--force', '--filter',
                    'label=org.opencontainers.image.title=' + IMAGE],
                   check=True, stdout=subprocess.DEVNULL)


def main():
    if not PYTHON.exists():
        raise SystemExit('No .venv; run `make env` first')
    if not shutil.which('uv'):
        raise SystemExit('uv is required to sync .venv with the lock')
    if not shutil.which('docker'):
        raise SystemExit('docker is required to build the web service image')
    if not shutil.which('alr'):
        raise SystemExit('Alire (alr) is required to build the OFX program; see ofx/README.md')
    revision = committed_revision()
    check_mounts()
    run(PYTHON, 'scripts/local_db.py', 'backup')
    # .venv runs migrations, backups, local CI and the Chase worker, so it
    # follows the development lock.
    run('uv', 'pip', 'sync', '--python', PYTHON, '--require-hashes', 'requirements-dev.txt')
    run('make', 'ofx')
    build_image(revision)
    run(PYTHON, '-W', 'ignore', 'manage.py', 'db', 'upgrade')
    run('systemctl', '--user', 'restart', SERVICE)
    wait_for_login_page()
    prune_images(revision)
    print('{} is serving {} from {}:{}'.format(SERVICE, LOGIN_URL, IMAGE, revision), flush=True)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
