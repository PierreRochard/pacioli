PROD_PG_USERNAME = 'localuser'
PROD_PG_PASSWORD = ''
PROD_PG_HOST = 'localhost'
PROD_PG_PORT = 5432

PROD_PACIOLI_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(PROD_PG_USERNAME, PROD_PG_PASSWORD,
                                                                          PROD_PG_HOST, PROD_PG_PORT)


class Config(object):
    SECRET_KEY = ''

    # Flask Security Core
    SECURITY_BLUEPRINT_NAME = 'security'
    SECURITY_URL_PREFIX = None
    SECURITY_FLASH_MESSAGES = True
    SECURITY_PASSWORD_HASH = "pbkdf2_sha512"

    ## Update the salt
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

    # Update your mail settings, for Google 2FA: https://security.google.com/settings/security/apppasswords
    MAIL_SERVER = 'smtp.gmail.com'
    MAIL_PORT = 465
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
                               'bankacctfrom': 'BankAccounts',
                               'ccacctfrom': 'CreditCardAccounts',
                               'stmttrn': 'Transactions',
                               'new_transactions': 'NewTransactions'}


class ProdConfig(Config):
    SQLALCHEMY_DATABASE_URI = PROD_PACIOLI_URI


class DevConfig(Config):
    DEBUG = True
    DEBUG_TB_INTERCEPT_REDIRECTS = True
    SQLALCHEMY_DATABASE_URI = PROD_PACIOLI_URI
    ASSETS_DEBUG = True
