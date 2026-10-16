"""The containerized web service: Chase launch queue, host launcher, image context."""

import importlib.util
from pathlib import Path
import stat
import subprocess

import pytest

from pacioli.functions import chase_browser


ROOT = Path(__file__).resolve().parents[1]


def load_launcher():
    spec = importlib.util.spec_from_file_location('chase_launcher', ROOT / 'scripts' / 'chase_launcher.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_container_queues_chase_runs_instead_of_calling_systemd(app, app_context, tmp_path, monkeypatch):
    queue = tmp_path / 'chase-launch'
    monkeypatch.setitem(app.config, 'CHASE_LAUNCH_QUEUE', str(queue))
    monkeypatch.setattr(chase_browser, 'systemd_run', lambda run_id: pytest.fail('systemd-run called'))
    chase_browser._launch_systemd(42)
    chase_browser._launch_systemd(42)  # a resumed run may queue its ID again
    assert [path.name for path in queue.iterdir()] == ['42']
    assert stat.S_IMODE(queue.stat().st_mode) == 0o700
    assert stat.S_IMODE((queue / '42').stat().st_mode) == 0o600
    assert (queue / '42').stat().st_size == 0


def test_without_a_queue_the_worker_starts_through_systemd(app, app_context, monkeypatch):
    monkeypatch.setitem(app.config, 'CHASE_LAUNCH_QUEUE', None)
    started = []
    monkeypatch.setattr(chase_browser, 'systemd_run', started.append)
    chase_browser._launch_systemd(7)
    assert started == [7]


def test_launcher_empties_the_queue_and_starts_numeric_runs(app, tmp_path, monkeypatch):
    launcher = load_launcher()
    queue = tmp_path / 'chase-launch'
    queue.mkdir(mode=0o700)
    for name in ('12', '3', 'not-a-run', '.partial'):
        (queue / name).touch()
    run_ids = launcher.queued_runs(queue)
    assert sorted(run_ids) == [3, 12]
    assert list(queue.iterdir()) == []

    started, failed = [], []

    def systemd_run(run_id):
        if run_id == 12:
            raise subprocess.CalledProcessError(1, 'systemd-run')
        started.append(run_id)

    monkeypatch.setattr(chase_browser, 'systemd_run', systemd_run)
    monkeypatch.setattr(chase_browser, '_set_run',
                        lambda run_id, status, **fields: failed.append((run_id, status, fields)))
    launcher.launch(app, [3, 12])
    assert started == [3]
    assert failed == [(12, 'failed', {'error_code': 'desktop_launch_failed'})]


def test_image_context_admits_no_private_files():
    rules = [line.strip() for line in (ROOT / '.dockerignore').read_text().splitlines()
             if line.strip() and not line.startswith('#')]
    assert rules[0] == '*'
    allowed = [rule[1:] for rule in rules if rule.startswith('!')]
    assert [rule for rule in allowed if rule.startswith('.')] == ['.local/wheels/*.whl']
    assert not {'tests/', 'docs/', 'deploy/', '.env'} & set(allowed)
