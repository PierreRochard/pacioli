"""Install a separate, private Schwab native host without changing Chase."""
import json
import os
from pathlib import Path
import stat

SOCKET = 'pacioli-schwab-extension.sock'
OPERATIONS = "('ping','prepare','probe','observe','act','download','release','diagnostics')"


def host_source(root):
    source = (root / 'scripts/chase_native_host.py').read_text()
    source = source.replace('pacioli-chase-extension.sock', SOCKET)
    source = source.replace("('ping','prepare','observe','act','download','release','diagnostics')", OPERATIONS)
    if SOCKET not in source or "'probe'" not in source:
        raise RuntimeError('Native host source has changed; review adapter')
    return source


def install():
    root = Path(__file__).resolve().parents[1]
    directory = root / '.local/schwab-extension'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / 'host.py'
    if directory.is_symlink() or target.is_symlink():
        raise RuntimeError('Unsafe host path')
    target.write_text(host_source(root))
    target.chmod(0o700)
    manifest = dict(name='lan.pacioli.schwab', description='Pacioli private Schwab export bridge',
                    path=str(target), type='stdio', allowed_extensions=['schwab-local@pacioli.lan'])
    for base in [Path.home() / '.mozilla', Path.home() / 'snap/firefox/common/.mozilla']:
        native = base / 'native-messaging-hosts'
        native.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = native.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Unsafe native manifest directory')
        path = native / 'lan.pacioli.schwab.json'
        if path.is_symlink():
            raise RuntimeError('Unsafe manifest path')
        path.write_text(json.dumps(manifest, indent=2) + '\n')
        path.chmod(0o600)
    print('Schwab native host installed. Load extensions/schwab-local/manifest.json in Firefox.')


if __name__ == '__main__':
    install()
