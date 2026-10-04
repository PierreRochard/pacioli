"""Install a separate, private Amazon native host without changing Chase."""
import json
import os
from pathlib import Path
import stat


def install():
    root=Path(__file__).resolve().parents[1]
    source=(root/'scripts/chase_native_host.py').read_text()
    source=source.replace('pacioli-chase-extension.sock','pacioli-amazon-extension.sock')
    source=source.replace("('ping','prepare','observe','act','download','release','diagnostics')",
                          "('ping','amazon_observe','amazon_navigate','amazon_back')")
    if "'amazon_observe'" not in source:raise RuntimeError('Native host source has changed; review adapter')
    directory=root/'.local/amazon-extension';directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    target=directory/'host.py'
    if directory.is_symlink() or target.is_symlink():raise RuntimeError('Unsafe host path')
    target.write_text(source);target.chmod(0o700)
    manifest=dict(name='lan.pacioli.amazon',description='Pacioli private read-only Amazon bridge',
        path=str(target),type='stdio',allowed_extensions=['amazon-local@pacioli.lan'])
    for base in [Path.home()/'.mozilla',Path.home()/'snap/firefox/common/.mozilla']:
        native=base/'native-messaging-hosts';native.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=native.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid():raise RuntimeError('Unsafe native manifest directory')
        path=native/'lan.pacioli.amazon.json'
        if path.is_symlink():raise RuntimeError('Unsafe manifest path')
        path.write_text(json.dumps(manifest,indent=2)+'\n');path.chmod(0o600)
    print('Amazon native host installed. Load extensions/amazon-local/manifest.json in Firefox.')


if __name__=='__main__':install()
