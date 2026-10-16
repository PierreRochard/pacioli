"""Manage a persistent, private PostgreSQL cluster for this checkout."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import tempfile
from urllib.parse import quote, urlencode


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
DATA = LOCAL / 'postgres'
SOCKET = LOCAL / 'run'
LOG = LOCAL / 'postgres.log'
CONFIG = LOCAL / 'config.json'
PORT = 55432
DATABASE = 'pacioli'
MIN_AVAILABLE_KIB = 10 * 1024 * 1024


def run(*command, capture=False):
    return subprocess.run(command, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None)


def pg_bin(name):
    directory = Path(run('pg_config', '--bindir', capture=True).stdout.strip())
    binary = directory / name
    if not binary.is_file():
        raise SystemExit('PostgreSQL server binary is missing: {}'.format(binary))
    return str(binary)


def check_memory():
    available = next(
        int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
        if line.startswith('MemAvailable:')
    )
    if available < MIN_AVAILABLE_KIB:
        raise SystemExit('MemAvailable is below 10 GB; PostgreSQL was not started')


def cluster_exists():
    return (DATA / 'PG_VERSION').is_file()


def running():
    if not cluster_exists():
        return False
    return subprocess.run([pg_bin('pg_ctl'), '-D', str(DATA), 'status'],
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def ensure_local_directories():
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    LOCAL.chmod(0o700)
    SOCKET.mkdir(mode=0o700, exist_ok=True)
    SOCKET.chmod(0o700)


def database_url():
    username = pwd.getpwuid(os.geteuid()).pw_name
    query = urlencode({'host': str(SOCKET), 'port': PORT})
    return 'postgresql+psycopg2://{}@/{}?{}'.format(
        quote(username, safe=''), DATABASE, query)


def write_config():
    # Imported here so that restoring onto a new machine needs only the stdlib.
    from cryptography.fernet import Fernet
    values = {
        'DATABASE_URL': database_url(),
        'SECRET_KEY': secrets.token_urlsafe(48),
        'SECURITY_PASSWORD_SALT': secrets.token_urlsafe(48),
        'PACIOLI_CREDENTIAL_KEY': Fernet.generate_key().decode('ascii'),
    }
    descriptor = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as destination:
        json.dump(values, destination, indent=2)
        destination.write('\n')


def initialize_cluster():
    if DATA.exists() and not cluster_exists():
        raise SystemExit('{} exists but is not a PostgreSQL cluster'.format(DATA))
    if cluster_exists():
        if not CONFIG.is_file():
            raise SystemExit('Existing cluster has no config; do not replace its encryption key')
        return
    if CONFIG.exists():
        raise SystemExit('Existing config has no cluster; inspect it before initializing')
    initdb()
    write_config()


def initdb():
    check_memory()
    ensure_local_directories()
    username = pwd.getpwuid(os.geteuid()).pw_name
    run(pg_bin('initdb'), '--no-instructions', '--auth-local=peer',
        '--auth-host=reject', '--encoding=UTF8', '--locale=C.UTF-8',
        '-U', username, '-D', str(DATA))


def start():
    if not cluster_exists() or not CONFIG.is_file():
        raise SystemExit('Run make db-init to initialize the local database')
    if running():
        print('Local PostgreSQL is already running')
        return
    check_memory()
    ensure_local_directories()
    options = "-h '' -k {} -p {}".format(SOCKET, PORT)
    run(pg_bin('pg_ctl'), '-D', str(DATA), '-o', options,
        '-l', str(LOG), 'start')
    print('Local PostgreSQL started')


def create_database():
    username = pwd.getpwuid(os.geteuid()).pw_name
    connection = ('-h', str(SOCKET), '-p', str(PORT), '-U', username)
    exists = run('psql', *connection, '-d', 'postgres', '-Atc',
                 "SELECT 1 FROM pg_database WHERE datname='pacioli'",
                 capture=True).stdout.strip()
    if not exists:
        run('createdb', *connection, DATABASE)
        run('psql', *connection, '-d', 'postgres', '-c',
            'ALTER DATABASE pacioli SET timezone TO UTC')
        print('Created local Pacioli database')
    else:
        print('Local Pacioli database already exists')


def backup():
    check_memory()
    start()
    target_directory = Path(os.environ.get('PACIOLI_BACKUP_DIR') or
                            LOCAL / 'backups')
    target_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destination = target_directory / 'pacioli-{}.dump'.format(stamp)
    if destination.exists():
        raise SystemExit('Backup already exists: {}'.format(destination))
    dump(destination)
    print('Readable local backup archive: {}'.format(destination))


def dump(destination, compress=True):
    """Write a custom-format dump that pg_restore can read, or nothing at all."""
    with tempfile.NamedTemporaryFile(prefix='.pacioli-', suffix='.partial',
                                     dir=destination.parent, delete=False) as source:
        partial = Path(source.name)
    try:
        username = pwd.getpwuid(os.geteuid()).pw_name
        run('pg_dump', '--format=custom', '--no-password', '--host', str(SOCKET),
            '--port', str(PORT), '--username', username,
            *(() if compress else ('--compress=0',)),
            '--file', str(partial), DATABASE)
        run('pg_restore', '--list', str(partial), capture=True)
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


def restore(dump_path):
    """Rebuild the cluster on a new machine from a dump and its restored config.

    The restored config keeps the original encryption keys; only its database
    URL is pointed at this checkout's socket.
    """
    if cluster_exists() or DATA.exists():
        raise SystemExit('{} already exists; restore only into a checkout without a cluster'.format(DATA))
    if not CONFIG.is_file():
        raise SystemExit('Restore .local/config.json first: its keys decrypt the restored data')
    run('pg_restore', '--list', str(dump_path), capture=True)
    config = json.loads(CONFIG.read_text(encoding='utf-8'))
    config['DATABASE_URL'] = database_url()
    replacement = CONFIG.with_name('.config.json.partial')
    descriptor = os.open(replacement, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as destination:
        json.dump(config, destination, indent=2)
        destination.write('\n')
    replacement.replace(CONFIG)
    initdb()
    start()
    create_database()
    username = pwd.getpwuid(os.geteuid()).pw_name
    run('pg_restore', '--no-owner', '--exit-on-error', '--no-password',
        '--host', str(SOCKET), '--port', str(PORT), '--username', username,
        '--dbname', DATABASE, str(dump_path))
    print('Restored the Pacioli database from {}'.format(dump_path))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'start', 'stop', 'status', 'backup', 'restore'))
    parser.add_argument('dump', nargs='?', type=Path,
                        help='custom-format dump to restore (restore only)')
    arguments = parser.parse_args()
    action = arguments.action
    if (action == 'restore') != (arguments.dump is not None):
        parser.error('a dump path is required for restore, and only for restore')
    if action == 'init':
        initialize_cluster()
        start()
        create_database()
    elif action == 'start':
        start()
    elif action == 'stop':
        if running():
            run(pg_bin('pg_ctl'), '-D', str(DATA), 'stop', '-m', 'fast')
        else:
            print('Local PostgreSQL is not running')
    elif action == 'status':
        print('Local PostgreSQL: {}'.format('running' if running() else 'stopped'))
        print('Data directory: {}'.format(DATA))
        print('Configuration: {}'.format('present' if CONFIG.is_file() else 'absent'))
    elif action == 'backup':
        backup()
    elif action == 'restore':
        restore(arguments.dump.resolve())


if __name__ == '__main__':
    main()
