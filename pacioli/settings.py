import os
import json
import stat
from pathlib import Path
from sqlalchemy.engine.url import URL


LOCAL_CONFIG = Path(__file__).resolve().parents[1] / '.local' / 'config.json'


def load_local_config(path=None):
    """Read private bootstrap settings needed before the database is available."""
    path = Path(path or os.environ.get('PACIOLI_CONFIG') or LOCAL_CONFIG)
    if path.is_symlink():
        raise RuntimeError('Pacioli configuration must not be a symbolic link')
    if not path.exists():
        return {}
    details = path.stat()
    if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) & 0o077:
        raise RuntimeError('Pacioli configuration must be owned by the current user and private (0600)')
    with path.open(encoding='utf-8') as source:
        config = json.load(source)
    if not isinstance(config, dict):
        raise RuntimeError('Pacioli configuration must be a JSON object')
    return config


_LOCAL_SETTINGS = load_local_config()


def setting(name, default=None, required=False):
    value = os.environ.get(name)
    if value is None:
        value = _LOCAL_SETTINGS.get(name, default)
    if required and not value:
        raise RuntimeError('Set {} in the environment or private Pacioli configuration'.format(name))
    return value


def database_url():
    configured = setting('DATABASE_URL')
    if configured:
        return configured
    return str(URL.create(
        'postgresql+psycopg2',
        username=setting('PGUSER', required=True),
        password=setting('PGPASSWORD'),
        host=setting('PGHOST', 'localhost'),
        port=int(setting('PGPORT', 5432)),
        database='pacioli',
    ))


class Config(object):
    CHASE_BROWSER_DEFAULT = 'firefox'
    CHASE_FIREFOX_PORT = 9222
    SECRET_KEY = setting('SECRET_KEY', required=True)
    # Live enrollment failed on this Linux workstation. Permit an explicitly
    # supervised diagnostic window through the private local configuration.
    CHASE_PASSKEY_ENROLLMENT_ENABLED = str(setting(
        'CHASE_PASSKEY_ENROLLMENT_ENABLED', 'false')).lower() == 'true'
    # Amazon's native archive includes delivery photos; allow a bounded 64 MiB
    # archive plus multipart overhead. Individual importers retain their limits.
    MAX_CONTENT_LENGTH = 66 * 1024 * 1024
    # Flask Security Core
    SECURITY_BLUEPRINT_NAME = 'security'
    SECURITY_URL_PREFIX = None
    SECURITY_FLASH_MESSAGES = True
    # Existing pbkdf2_sha512 hashes still verify and are rehashed at next login.
    SECURITY_PASSWORD_HASH = 'argon2'
    SECURITY_PASSWORD_SALT = setting('SECURITY_PASSWORD_SALT', required=True)
    SECURITY_EMAIL_SENDER = 'no-reply@localhost'
    SECURITY_TOKEN_AUTHENTICATION_KEY = 'auth_token'
    SECURITY_TOKEN_AUTHENTICATION_HEADER = 'Authentication-Token'
    SECURITY_DEFAULT_HTTP_AUTH_REALM = 'Login Required'

    # Flask Security URLs and Views
    SECURITY_LOGIN_URL = '/login/'
    SECURITY_LOGOUT_URL = '/logout/'
    SECURITY_REGISTER_URL = '/register/'
    SECURITY_RESET_URL = '/reset/'
    SECURITY_CHANGE_URL = '/change/'
    SECURITY_CONFIRM_URL = '/confirm/'
    SECURITY_POST_LOGIN_VIEW = 'admin.index'
    SECURITY_POST_LOGOUT_VIEW = 'admin.index'
    SECURITY_POST_REGISTER_VIEW = 'admin.index'
    SECURITY_CONFIRM_ERROR_VIEW = None
    SECURITY_POST_CONFIRM_VIEW = None
    SECURITY_POST_RESET_VIEW = None
    SECURITY_POST_CHANGE_VIEW = None
    SECURITY_UNAUTHORIZED_VIEW = None

    # Flask Security Feature Flags
    SECURITY_CONFIRMABLE = True
    SECURITY_REGISTERABLE = False
    SECURITY_RECOVERABLE = True
    SECURITY_TRACKABLE = True
    SECURITY_PASSWORDLESS = False
    SECURITY_CHANGEABLE = False

    # Email
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 465
    MAIL_USE_SSL = True
    MAIL_USERNAME = setting('MAIL_USERNAME', 'noreply@localhost')
    MAIL_PASSWORD = setting('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = MAIL_USERNAME
    MAIL_MAX_EMAILS = None
    MAIL_ASCII_ATTACHMENTS = False
    PACIOLI_PUBLIC_URL = setting('PACIOLI_PUBLIC_URL', 'https://pacioli.lan')

    CACHE_TYPE = 'simple'

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MODEL_MAP = {'admin': {'mapping_overlaps': 'MappingOverlaps',
                           },
                 'amazon': {'amazon_transactions': 'AmazonTransactions',
                            },
                 'ofx': {'acctfrom': 'AccountsFrom',
                         'availbal': 'AvailableBalances',
                         'bankacctfrom': 'BankAccounts',
                         'ccacctfrom': 'CreditCardAccounts',
                         'invacctfrom': 'InvestmentAccounts',
                         'invbal': 'InvestmentBalances',
                         'invpos': 'InvestmentPositions',
                         'investment_transactions': 'InvestmentTransactions',
                         'cost_bases': 'CostBases',
                         'secinfo': 'Securities',
                         'transactions': 'Transactions',
                         },
                 'bookkeeping': {'detailed_journal_entries': 'DetailedJournalEntries',
                                 },
                 }


class ProdConfig(Config):
    SQLALCHEMY_DATABASE_URI = database_url()
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PREFERRED_URL_SCHEME = 'https'

class ProdDevConfig(ProdConfig):
    """
        Use the production database but provide local debug output.
    """

    DEBUG = True
    DEBUG_TB_INTERCEPT_REDIRECTS = True
    ASSETS_DEBUG = True

    SQLALCHEMY_ECHO = False


class DevConfig(Config):
    DEBUG = True
    DEBUG_TB_INTERCEPT_REDIRECTS = True
    ASSETS_DEBUG = True

    SQLALCHEMY_ECHO = False

    SQLALCHEMY_DATABASE_URI = database_url()
