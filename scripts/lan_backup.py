"""Encrypted, deduplicated backups of Pacioli's private state to another LAN host.

restic keeps snapshots in a repository on another machine, reached over SFTP.
Each snapshot holds a fresh database dump, the private .local directory
(including config.json, whose keys decrypt the stored credentials) and the
untracked deploy/ files. The live PostgreSQL files, browser profiles, CI
scratch and old virtual environments are left out.

The repository password is in .local/lan-backup/password. Keep a copy outside
this machine: without it the backups cannot be decrypted.

    lan_backup.py init sftp:HOST:backups/pacioli   # once per machine
    lan_backup.py run                               # dump, back up, prune, check
    lan_backup.py snapshots
    lan_backup.py restore                           # new machine or checkout
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import local_db  # noqa: E402


STATE = local_db.LOCAL / 'lan-backup'
REPOSITORY_FILE = STATE / 'repository'
PASSWORD_FILE = STATE / 'password'
DUMP = STATE / 'pacioli.dump'
LAST_SUCCESS = STATE / 'last-success'  # read by the home page's attention list
SOURCES = (local_db.LOCAL, ROOT / 'deploy')
EXCLUDES = tuple(str(local_db.LOCAL / name) for name in (
    'postgres', 'postgres.log', 'run', 'ci', 'venv-*',
    'chase-browser', 'schwab-browser', 'lan-backup/password'))
RETENTION = ('--keep-daily', '14', '--keep-weekly', '8', '--keep-monthly', '24')
TAG = 'pacioli'
NO_REPOSITORY = 10  # restic's exit code when the repository does not exist


def restic(*arguments, **options):
    environment = dict(os.environ, RESTIC_REPOSITORY_FILE=str(REPOSITORY_FILE),
                       RESTIC_PASSWORD_FILE=str(PASSWORD_FILE))
    options.setdefault('check', True)
    return subprocess.run(['restic', *arguments], cwd=ROOT, env=environment, **options)


def require_setup():
    if not REPOSITORY_FILE.is_file() or not PASSWORD_FILE.is_file():
        raise SystemExit('Run lan_backup.py init first')


def init(repository):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if REPOSITORY_FILE.exists() and REPOSITORY_FILE.read_text().strip() != repository:
        raise SystemExit('{} names a different repository'.format(REPOSITORY_FILE))
    REPOSITORY_FILE.write_text(repository + '\n')
    if not PASSWORD_FILE.exists():
        descriptor = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as destination:
            destination.write(secrets.token_urlsafe(32) + '\n')
    probe = restic('cat', 'config', check=False, stdout=subprocess.DEVNULL,
                   stderr=subprocess.PIPE, text=True)
    if probe.returncode == NO_REPOSITORY:
        restic('init')
    elif probe.returncode:
        raise SystemExit('restic could not open the repository:\n' + probe.stderr)
    print('Repository ready. Keep a copy of {} outside this machine.'.format(PASSWORD_FILE))


def backup():
    require_setup()
    local_db.start()
    # restic compresses and deduplicates; an uncompressed dump changes less day to day.
    local_db.dump(DUMP, compress=False)
    restic('backup', '--tag', TAG, '--exclude-caches',
           *(option for pattern in EXCLUDES for option in ('--exclude', pattern)),
           *map(str, SOURCES))
    restic('forget', '--tag', TAG, *RETENTION, '--prune')
    restic('check', '--read-data-subset', '10%')
    LAST_SUCCESS.write_text(datetime.now(timezone.utc).isoformat() + '\n')


def restore():
    """Restore the latest snapshot into this checkout, then rebuild the database."""
    require_setup()
    if local_db.CONFIG.exists() or local_db.DATA.exists():
        raise SystemExit('This checkout already has a config or cluster; restore into a fresh one')
    listing = restic('snapshots', '--json', '--latest', '1', '--tag', TAG,
                     stdout=subprocess.PIPE, text=True).stdout
    snapshots = json.loads(listing)
    if not snapshots:
        raise SystemExit('The repository has no Pacioli snapshot')
    for path in snapshots[0]['paths']:
        target = ROOT / Path(path).name
        if target not in SOURCES:
            raise SystemExit('Unexpected path in the snapshot: {}'.format(path))
        restic('restore', '{}:{}'.format(snapshots[0]['id'], path), '--target', str(target))
    local_db.restore(DUMP)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('init').add_argument('repository', help='e.g. sftp:HOST:backups/pacioli')
    commands.add_parser('run')
    commands.add_parser('snapshots')
    commands.add_parser('restore')
    arguments = parser.parse_args()
    try:
        if arguments.command == 'init':
            init(arguments.repository)
        elif arguments.command == 'run':
            backup()
        elif arguments.command == 'snapshots':
            require_setup()
            restic('snapshots', '--tag', TAG, '--compact')
        else:
            restore()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode or 1)


if __name__ == '__main__':
    main()
