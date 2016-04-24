import keyring
import os


class Config(object):
    try:
        SECRET_KEY = keyring.get_password('flask_secret_key', 'flask_secret_key')
    except RuntimeError:
        SECRET_KEY = os.environ['SECRET_KEY']

    # Flask Security Core
    SECURITY_BLUEPRINT_NAME = 'security'
    SECURITY_URL_PREFIX = None
    SECURITY_FLASH_MESSAGES = True
    SECURITY_PASSWORD_HASH = 'pbkdf2_sha512'
    try:
        SECURITY_PASSWORD_SALT = keyring.get_password('flask_security', 'password_salt')
    except RuntimeError:
        SECURITY_PASSWORD_SALT = os.environ['SECURITY_PASSWORD_SALT']
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
    SECURITY_RECOVERABLE = False
    SECURITY_TRACKABLE = True
    SECURITY_PASSWORDLESS = False
    SECURITY_CHANGEABLE = False

    # Email
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 465
    MAIL_USE_SSL = True
    try:
        MAIL_USERNAME = keyring.get_password('mail_username', 'mail_username')
        MAIL_PASSWORD = keyring.get_password('mail_password', MAIL_USERNAME)
    except RuntimeError:
        MAIL_USERNAME = os.environ['MAIL_USERNAME']
        MAIL_PASSWORD = os.environ['MAIL_PASSWORD']
    MAIL_DEFAULT_SENDER = MAIL_USERNAME
    MAIL_MAX_EMAILS = None
    MAIL_ASCII_ATTACHMENTS = False

    CACHE_TYPE = 'simple'

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MODEL_MAP = {'admin': {'mapping_overlaps': 'MappingOverlaps',
                           'ofx_mapping_overlaps': 'OFXMappingOverlaps',
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
                 'pacioli': {'detailed_journal_entries': 'DetailedJournalEntries',
                             },
                 }


class ProdConfig(Config):
    try:
        POSTGRES_USERNAME = keyring.get_password('postgres_username', 'postgres_username')
        POSTGRES_PASSWORD = keyring.get_password('postgres_password', 'postgres_password')
        POSTGRES_HOST = keyring.get_password('postgres_aws_host', 'postgres_aws_host')
        POSTGRES_PORT = keyring.get_password('postgres_aws_port', 'postgres_aws_port')
    except RuntimeError:
        POSTGRES_USERNAME = os.environ['PGUSER']
        POSTGRES_PASSWORD = os.environ['PGPASSWORD']
        POSTGRES_HOST = os.environ['PGHOST']
        POSTGRES_PORT = os.environ['PGPORT']

    SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(POSTGRES_USERNAME, POSTGRES_PASSWORD,
                                                                                     POSTGRES_HOST, POSTGRES_PORT)


class DevConfig(Config):
    DEBUG = True
    DEBUG_TB_INTERCEPT_REDIRECTS = True
    ASSETS_DEBUG = True

    SQLALCHEMY_ECHO = False

    try:
        POSTGRES_USERNAME = keyring.get_password('postgres_username', 'postgres_username')
        POSTGRES_PASSWORD = keyring.get_password('postgres_password', 'postgres_password')
    except RuntimeError:
        POSTGRES_USERNAME = os.environ['PGUSER']
        POSTGRES_PASSWORD = os.environ['PGPASSWORD']

    POSTGRES_HOST = 'localhost'
    POSTGRES_PORT = 5432

    SQLALCHEMY_DATABASE_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(POSTGRES_USERNAME, POSTGRES_PASSWORD,
                                                                                     POSTGRES_HOST, POSTGRES_PORT)
