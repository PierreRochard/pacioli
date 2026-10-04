"""Start the user's ordinary Firefox with a loopback automation connection.

Never kill Firefox, copy its profile, change its preferences, or attach to a
non-loopback endpoint. Quit Firefox normally before invoking this launcher.
"""

import argparse
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time


def firefox_running():
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if (process / 'comm').read_text().strip() in ('firefox', 'firefox-bin'):
                return True
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return False


def endpoint_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.5):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=9222)
    parser.add_argument('--desktop', action='store_true')
    parser.add_argument('--install-desktop', action='store_true')
    arguments = parser.parse_args()
    if not 1024 <= arguments.port <= 65535:
        parser.error('Choose a port from 1024 to 65535')
    if arguments.install_desktop:
        directory = Path.home() / '.local' / 'share' / 'applications'
        directory.mkdir(parents=True, exist_ok=True)
        entry = directory / 'pacioli-firefox.desktop'
        if entry.is_symlink():
            raise SystemExit('Refusing to replace a symlink launcher')
        def quote(value):
            value = str(value).replace('%', '%%')
            for char in ('\\', '"', '`', '$'):
                value = value.replace(char, '\\' + char)
            return '"' + value + '"'
        entry.write_text('[Desktop Entry]\nType=Application\nName=Firefox for Pacioli\n'
                         'Comment=Use your Firefox profile with local Pacioli automation\n'
                         'Exec={} {} --desktop --port {}\nIcon=firefox\nTerminal=false\n'
                         'Categories=Network;WebBrowser;\n'.format(
                             quote(sys.executable), quote(Path(__file__).resolve()), arguments.port))
        entry.chmod(0o644)
        print('Installed Firefox for Pacioli in the application menu.')
        return

    def stop(message):
        if arguments.desktop and shutil.which('zenity'):
            subprocess.run(['zenity', '--info', '--title=Firefox for Pacioli',
                            '--text=' + message], check=False)
        raise SystemExit(message)
    if endpoint_open(arguments.port):
        print('The local browser port is already open. Use Check Firefox connection in Pacioli.')
        return
    if firefox_running():
        stop('Quit Firefox normally first so it can save your session. Then open Firefox for Pacioli again.')
    process = subprocess.Popen([
        '/usr/bin/firefox', '--remote-debugging-port', str(arguments.port),
        'https://pacioli.lan/connections/chase/'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if endpoint_open(arguments.port):
            print('Firefox is ready. Open Pacioli and click Check Firefox connection.')
            return
        if process.poll() is not None and process.returncode:
            break
        time.sleep(.5)
    stop('Firefox did not expose its connection. Check the browser window and retry.')


if __name__ == '__main__':
    main()
