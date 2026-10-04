import os
import pytest

from pacioli import create_app
from pacioli.settings import DevConfig, database_url, load_local_config


def test_app_factory_accepts_test_configuration():
    class Config(DevConfig):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = 'sqlite://'

    app = create_app(Config, env='test')

    assert app.config['TESTING'] is True
    assert app.config['SQLALCHEMY_DATABASE_URI'] == 'sqlite://'
    assert '/login/' in [rule.rule for rule in app.url_map.iter_rules()]


def test_development_configuration_uses_postgresql():
    configured_url = os.environ.get('DATABASE_URL')
    if configured_url:
        assert DevConfig.SQLALCHEMY_DATABASE_URI == configured_url
    else:
        assert DevConfig.SQLALCHEMY_DATABASE_URI.startswith('postgresql+psycopg2://')
    assert DevConfig.DEBUG is True


def test_database_url_override_needs_no_separate_pg_credentials(monkeypatch):
    expected = 'postgresql+psycopg2://example@localhost/pacioli'
    monkeypatch.setenv('DATABASE_URL', expected)
    monkeypatch.delenv('PGUSER', raising=False)
    monkeypatch.delenv('PGPASSWORD', raising=False)
    assert database_url() == expected


def test_bootstrap_config_must_be_private_and_not_a_symlink(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text('{"DATABASE_URL": "postgresql:///pacioli"}')
    config.chmod(0o600)
    assert load_local_config(config)['DATABASE_URL'] == 'postgresql:///pacioli'

    config.chmod(0o644)
    with pytest.raises(RuntimeError, match='private'):
        load_local_config(config)

    config.chmod(0o600)
    link = tmp_path / 'config-link.json'
    link.symlink_to(config)
    with pytest.raises(RuntimeError, match='symbolic link'):
        load_local_config(link)
