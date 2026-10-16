"""Start a disposable headless Firefox with a WebDriver BiDi endpoint for tests."""

from contextlib import contextmanager
import json
from pathlib import Path
import re
import subprocess
import time
from types import SimpleNamespace

import pytest


BASE_PREFERENCES = {
    'browser.shell.checkDefaultBrowser': False,
    'browser.startup.homepage_override.mstone': 'ignore',
    'network.captive-portal-service.enabled': False,
    'network.connectivity-service.enabled': False,
    'datareporting.healthreport.uploadEnabled': False,
    'toolkit.telemetry.enabled': False,
}


@contextmanager
def launch_firefox(tmp_path, url, preferences=None):
    subprocess.run(['free', '-h'], check=True)
    available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    assert available >= 10 * 1024 * 1024, 'Not enough memory for a browser test'
    profile = tmp_path / 'profile'
    profile.mkdir(mode=0o700)
    values = dict(BASE_PREFERENCES, **(preferences or {}))
    (profile / 'user.js').write_text('\n'.join('user_pref({}, {});'.format(json.dumps(k), json.dumps(v))
                                             for k, v in values.items()))
    log_path = tmp_path / 'firefox.log'
    log = log_path.open('wb')
    # Port 0 lets Firefox bind its own free port. A port chosen here can be taken
    # before Firefox binds it; BiDi then never starts while the browser stays up.
    process = subprocess.Popen(['/snap/bin/firefox', '--headless', '--no-remote',
                                '--profile', str(profile), '--remote-debugging-port', '0', url],
                               stdout=log, stderr=subprocess.STDOUT)
    try:
        # Startup takes about five seconds; profiles on a busy HDD can stall it for minutes.
        started = time.monotonic()
        port = None
        while port is None:
            text = log_path.read_text(errors='replace')
            match = re.search(r'WebDriver BiDi listening on ws://127\.0\.0\.1:(\d+)', text)
            if match:
                port = int(match.group(1))
            elif process.poll() is not None or time.monotonic() - started > 90:
                pytest.fail('Fixture Firefox did not start (exit {}, {:.0f}s):\n{}'.format(
                    process.poll(), time.monotonic() - started, text[-2000:]))
            else:
                time.sleep(.2)
        yield SimpleNamespace(port=port, process=process)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()
