"""Install the reviewed native-host manifest for the local Firefox extension."""
import json
import os
from pathlib import Path
import shutil
import stat


def install():
    root = Path(__file__).resolve().parents[1]
    source = root / 'scripts' / 'chase_native_host.py'
    # A private executable wrapper avoids assumptions about Firefox's Python PATH.
    directory = root / '.local' / 'chase-extension'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise SystemExit('Unsafe host directory')
    target = directory / 'host.py'
    if target.is_symlink(): raise SystemExit('Unsafe host executable')
    shutil.copyfile(source, target); target.chmod(0o700)
    manifest = {'name': 'lan.pacioli.chase', 'description': 'Pacioli private Chase bridge',
                'path': str(target), 'type': 'stdio',
                'allowed_extensions': ['chase-local@pacioli.lan']}
    for base in [Path.home()/'.mozilla', Path.home()/'snap/firefox/common/.mozilla']:
        native = base/'native-messaging-hosts'
        native.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = native.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise SystemExit('Unsafe native manifest directory')
        path = native/'lan.pacioli.chase.json'
        if path.is_symlink(): raise SystemExit('Unsafe native manifest')
        path.write_text(json.dumps(manifest, indent=2)+'\n'); path.chmod(0o600)
    print('Installed private native host. Load extensions/chase-local/manifest.json in Firefox.')


if __name__ == '__main__':
    install()
