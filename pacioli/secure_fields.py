"""Encrypt stored connection secrets with an application-managed Fernet key."""

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.types import Text, TypeDecorator

from pacioli.settings import setting


PREFIX = 'fernet:'


def _cipher():
    key = setting('PACIOLI_CREDENTIAL_KEY')
    if not key:
        raise RuntimeError('Set PACIOLI_CREDENTIAL_KEY before storing or reading encrypted credentials')
    return Fernet(key.encode('ascii'))


def encrypt_value(value):
    if value is None:
        return None
    return PREFIX + _cipher().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt_value(value):
    if value is None or not value.startswith(PREFIX):
        return value  # Existing plaintext records remain readable until migrated.
    return _cipher().decrypt(value[len(PREFIX):].encode('ascii')).decode('utf-8')


class EncryptedText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        return decrypt_value(value)


def encrypt_existing_data(engine):
    """Migrate plaintext rows in one transaction; leave encrypted rows intact."""
    _cipher()
    updated = 0
    with engine.begin() as connection:
        for table, column in [('connections', 'password'),
                              ('connection_responses', 'response')]:
            rows = connection.execute(text(
                'SELECT id, {column} FROM admin.{table} '
                'WHERE {column} IS NOT NULL'.format(table=table, column=column)
            )).fetchall()
            for identifier, value in rows:
                if value.startswith(PREFIX):
                    decrypt_value(value)  # Reject the wrong key before reporting success.
                    continue
                connection.execute(text(
                    'UPDATE admin.{table} SET {column} = :value WHERE id = :id'
                    .format(table=table, column=column)
                ), {'value': encrypt_value(value), 'id': identifier})
                updated += 1
    return updated
