"""Create a local Python environment for Pacioli."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev', action='store_true',
                        help='include test dependencies')
    parser.add_argument('--venv', type=Path, default=Path(__file__).parent / '.venv',
                        help='virtual environment path (default: .venv)')
    args = parser.parse_args()

    if sys.version_info[:2] != (3, 11):
        parser.error('Pacioli currently requires Python 3.11')

    repository = Path(__file__).resolve().parent
    environment = args.venv.expanduser().resolve()
    venv.EnvBuilder(with_pip=True, symlinks=os.name != 'nt').create(environment)

    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    requirements = repository / ('requirements-dev.txt' if args.dev else 'requirements.txt')
    pip_environment = os.environ.copy()
    if pip_environment.get('TMPDIR'):
        pip_environment.setdefault('PIP_CACHE_DIR',
                                   str(Path(pip_environment['TMPDIR']) / 'pacioli-pip-cache'))
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)],
                   check=True, env=pip_environment)
    print('Pacioli environment ready:', environment)


if __name__ == '__main__':
    main()
