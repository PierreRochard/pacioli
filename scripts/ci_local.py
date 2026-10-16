"""Run Pacioli's checks against a disposable local PostgreSQL cluster."""

import ast
import getpass
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode


ROOT = Path(__file__).resolve().parents[1]
POSTGRES_PORT = 55439
MIN_AVAILABLE_KIB = 10 * 1024 * 1024


def run(*command, env=None):
    subprocess.run(command, check=True, cwd=ROOT, env=env)


def check_resources(scratch):
    run('free', '-h')
    run('df', '-hT', '/', str(scratch))
    available = next(
        int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
        if line.startswith('MemAvailable:')
    )
    if available < MIN_AVAILABLE_KIB:
        raise SystemExit('MemAvailable is below 10 GB; local CI was not started')
    if shutil.disk_usage('/').free < 10 * 1024**3:
        raise SystemExit('The root filesystem has less than 10 GB free')


def check_python_syntax():
    files = [ROOT / 'install.py', ROOT / 'manage.py']
    for directory in ('pacioli', 'tests', 'scripts'):
        files.extend((ROOT / directory).rglob('*.py'))
    for path in files:
        ast.parse(path.read_text(), filename=str(path))
    print('Python syntax:', len(files), 'files parsed', flush=True)


def main():
    # Not TMPDIR: it may point at bulk storage. On a busy shingled HDD, browser
    # profile writes stalled Firefox startup past the browser tests' timeouts.
    scratch = Path(os.environ.get('PACIOLI_CI_SCRATCH') or ROOT / '.local' / 'ci').resolve()
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    check_resources(scratch)

    bindir = Path(subprocess.check_output(['pg_config', '--bindir'], text=True).strip())
    with tempfile.TemporaryDirectory(prefix='pacioli-ci-', dir=scratch) as work:
        work = Path(work)
        data = work / 'data'
        log = work / 'postgres.log'
        initdb = bindir / 'initdb'
        pg_ctl = bindir / 'pg_ctl'
        run(str(initdb), '--no-instructions', '--auth-local=trust',
            '--auth-host=reject', '-D', str(data))

        postgres_options = "-h '' -k {} -p {}".format(work, POSTGRES_PORT)
        started = False
        try:
            run(str(pg_ctl), '-D', str(data), '-o', postgres_options,
                '-l', str(log), 'start')
            started = True

            username = quote(getpass.getuser(), safe='')
            query = urlencode({'host': str(work), 'port': POSTGRES_PORT})
            environment = os.environ.copy()
            environment['PACIOLI_TEST_ADMIN_URL'] = (
                'postgresql+psycopg2://{}@/postgres?{}'.format(username, query)
            )
            # Never load this checkout's persistent app configuration in tests.
            environment['PACIOLI_CONFIG'] = str(work / 'no-app-config.json')
            environment['PIP_CACHE_DIR'] = str(work / 'pip-cache')
            environment['TMPDIR'] = str(work)
            environment['PYTHONDONTWRITEBYTECODE'] = '1'

            check_python_syntax()
            # Environments built by uv carry no pip; check them with uv instead.
            if importlib.util.find_spec('pip'):
                run(sys.executable, '-m', 'pip', 'check', env=environment)
            else:
                run('uv', 'pip', 'check', '--python', sys.executable, env=environment)
            run(sys.executable, '-B', '-m', 'pytest', '-q',
                '--basetemp', str(work / 'pytest'),
                '--disable-warnings', '-p', 'no:cacheprovider', 'tests', *sys.argv[1:],
                env=environment)
        except subprocess.CalledProcessError:
            if not started and log.exists():
                print(log.read_text(), file=sys.stderr)
            raise
        finally:
            if started:
                run(str(pg_ctl), '-D', str(data), 'stop', '-m', 'fast')


if __name__ == '__main__':
    main()
