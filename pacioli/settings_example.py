from talisman.talisman import ONE_YEAR_IN_SECS, SAMEORIGIN, DENY
import tempfile

from pacioli.db_config import DEV_PACIOLI_URI, DEV_OFX_URI, PROD_PACIOLI_URI, PROD_OFX_URI

db_file = tempfile.NamedTemporaryFile()


class Config(object):
    SECRET_KEY = ''

    # Flask Security Core
    SECURITY_BLUEPRINT_NAME = 'security'
    SECURITY_URL_PREFIX = None
    SECURITY_FLASH_MESSAGES = True
    SECURITY_PASSWORD_HASH = ""
    SECURITY_PASSWORD_SALT = ""
    SECURITY_EMAIL_SENDER = "you@localhost"
    SECURITY_TOKEN_AUTHENTICATION_KEY = 'auth_token'
    SECURITY_TOKEN_AUTHENTICATION_HEADER = 'Authentication-Token'
    SECURITY_DEFAULT_HTTP_AUTH_REALM = 'Login Required'

    # Flask Security URLs and Views
    SECURITY_LOGIN_URL = "/login/"
    SECURITY_LOGOUT_URL = "/logout/"
    SECURITY_REGISTER_URL = "/register/"
    SECURITY_RESET_URL = "/reset/"
    SECURITY_CHANGE_URL = "/change/"
    SECURITY_CONFIRM_URL = "/confirm/"
    SECURITY_POST_LOGIN_VIEW = "admin.index"
    SECURITY_POST_LOGOUT_VIEW = "admin.index"
    SECURITY_POST_REGISTER_VIEW = "admin.index"
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
    MAIL_PORT = 587
    MAIL_USE_TLS = False
    MAIL_USE_SSL = True
    MAIL_USERNAME = 'you@gmail.com'
    MAIL_PASSWORD = ''
    MAIL_DEFAULT_SENDER = 'you@gmail.com'
    MAIL_MAX_EMAILS = None
    MAIL_ASCII_ATTACHMENTS = False

    CACHE_TYPE = 'simple'

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAIN_DATABASE_MODEL_MAP = {'acctfrom': 'AccountsFrom',
                               'availbal': 'AvailableBalances',
                               'bankacctfrom': 'BankAccounts',
                               'ccacctfrom': 'CreditCardAccounts',
                               'ledgerbal': 'Balances',
                               'stmttrn': 'Transactions'}


class ProdConfig(Config):
    SQLALCHEMY_DATABASE_URI = PROD_PACIOLI_URI
    # Talisman
    # force_https = True
    # force_https_permanent = True
    # frame_options = SAMEORIGIN
    # frame_options_allow_from = None
    # strict_transport_security = True
    # strict_transport_security_max_age = ONE_YEAR_IN_SECS
    # strict_transport_security_include_subdomains = True
    # content_security_policy = {'default-src': ['\'self\'', 'https:'],
    #                            'style-src': ['\'self\'', 'unsafe-inline']}
    # csp = {'default-src': ['\'self\'', 'https:'],
    #                            'style-src': ['\'self\'', 'unsafe-inline']}
    # session_cookie_secure = True
    # session_cookie_http_only = True


class DevConfig(Config):
    DEBUG = True

    DEBUG_TB_INTERCEPT_REDIRECTS = True

    # SQLALCHEMY_DATABASE_URI = DEV_PACIOLI_URI
    # SQLALCHEMY_BINDS = {'ofx': DEV_OFX_URI}

    SQLALCHEMY_DATABASE_URI = PROD_PACIOLI_URI

    ASSETS_DEBUG = True

    # Talisman
    # force_https = False
    # force_https_permanent = False
    # frame_options = DENY
    # frame_options_allow_from = False
    # strict_transport_security = False
    # strict_transport_security_max_age = ONE_YEAR_IN_SECS
    # strict_transport_security_include_subdomains = False
    # content_security_policy = {'default-src': '\'self\''}
    # session_cookie_secure = True
    # session_cookie_http_only = False


class TestConfig(Config):
    DEBUG = True
    DEBUG_TB_INTERCEPT_REDIRECTS = False

    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + db_file.name
    SQLALCHEMY_ECHO = True

    WTF_CSRF_ENABLED = False
