"""LAN backup guards and the home page's overdue warning; restic is never run."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from pacioli.views import dashboard_views
from scripts import lan_backup, local_db


ROOT = Path(__file__).resolve().parents[1]


def test_restore_tools_import_without_third_party_packages():
    # On a new machine the restore runs before .venv exists.
    code = ('import sys; sys.path.insert(0, {!r}); sys.modules["cryptography"] = None; '
            'import scripts.lan_backup').format(str(ROOT))
    subprocess.run([sys.executable, '-I', '-c', code], check=True)


def test_backup_keeps_keys_and_dump_but_not_live_cluster_or_password():
    excluded = set(lan_backup.EXCLUDES)
    assert str(local_db.LOCAL / 'lan-backup' / 'password') in excluded
    assert str(local_db.LOCAL / 'postgres') in excluded
    for kept in ('config.json', 'wheels', 'decoy_tickers.txt', 'backups',
                 'lan-backup/pacioli.dump'):
        assert str(local_db.LOCAL / kept) not in excluded
    assert set(lan_backup.SOURCES) == {local_db.LOCAL, ROOT / 'deploy'}


def test_database_restore_needs_the_keys_and_never_replaces_a_cluster(tmp_path, monkeypatch):
    monkeypatch.setattr(local_db, 'DATA', tmp_path / 'postgres')
    monkeypatch.setattr(local_db, 'CONFIG', tmp_path / 'config.json')
    dump = tmp_path / 'pacioli.dump'
    with pytest.raises(SystemExit, match='config.json first'):
        local_db.restore(dump)
    (tmp_path / 'config.json').write_text('{}')
    (tmp_path / 'postgres').mkdir()
    with pytest.raises(SystemExit, match='already exists'):
        local_db.restore(dump)


def test_snapshot_restore_refuses_a_configured_checkout(tmp_path, monkeypatch):
    for name in ('repository', 'password'):
        (tmp_path / name).write_text('fixture\n')
    monkeypatch.setattr(lan_backup, 'REPOSITORY_FILE', tmp_path / 'repository')
    monkeypatch.setattr(lan_backup, 'PASSWORD_FILE', tmp_path / 'password')
    monkeypatch.setattr(local_db, 'CONFIG', tmp_path / 'config.json')
    monkeypatch.setattr(local_db, 'DATA', tmp_path / 'postgres')
    (tmp_path / 'config.json').write_text('{}')
    monkeypatch.setattr(lan_backup, 'restic',
                        lambda *args, **kwargs: pytest.fail('restic must not run'))
    with pytest.raises(SystemExit, match='fresh one'):
        lan_backup.restore()


def test_overdue_backup_is_flagged_only_when_configured(app, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, 'PACIOLI_LAN_BACKUP_DIR', tmp_path)
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    report = dict(queue=dict(uncategorized=0, suggestions_ready=0,
                             unmatched_transfers=0, open_months=0),
                  chase=None, unknown_accounts=[], accounts=[])

    def titles(age):
        return [item['title'] for item in dashboard_views.attention(report, now, age)]

    with app.test_request_context():
        assert dashboard_views.lan_backup_age(now) is None
        (tmp_path / 'repository').write_text('sftp:fixture:backups\n')
        assert dashboard_views.lan_backup_age(now) == float('inf')
        assert titles(dashboard_views.lan_backup_age(now)) == ['LAN backup is overdue']
        (tmp_path / 'last-success').write_text((now - timedelta(hours=2)).isoformat())
        assert dashboard_views.lan_backup_age(now) == pytest.approx(2)
        assert titles(dashboard_views.lan_backup_age(now)) == []
        (tmp_path / 'last-success').write_text((now - timedelta(hours=40)).isoformat())
        assert titles(dashboard_views.lan_backup_age(now)) == ['LAN backup is overdue']


def test_home_page_shows_the_overdue_backup_without_a_link(
        app, client, admin_user, tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, 'PACIOLI_LAN_BACKUP_DIR', tmp_path)
    (tmp_path / 'repository').write_text('sftp:fixture:backups\n')
    client.post('/login/', data=admin_user)
    page = client.get('/')
    assert page.status_code == 200
    assert b'LAN backup is overdue' in page.data
    assert b'href="None"' not in page.data
