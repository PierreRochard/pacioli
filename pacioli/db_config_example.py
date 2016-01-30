
PROD_PG_USERNAME = ''
PROD_PG_PASSWORD = ''
PROD_PG_HOST = ''
PROD_PG_PORT = 5432

PROD_PACIOLI_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(PROD_PG_USERNAME, PROD_PG_PASSWORD,
                                                                                     PROD_PG_HOST, PROD_PG_PORT)


PROD_OFX_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(PROD_PG_USERNAME, PROD_PG_PASSWORD, PROD_PG_HOST,
                                                                  PROD_PG_PORT)


DEV_PG_USERNAME = ''
DEV_PG_PASSWORD = ''
DEV_PG_HOST = 'localhost'
DEV_PG_PORT = 5432

DEV_PACIOLI_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/pacioli'.format(DEV_PG_USERNAME, DEV_PG_PASSWORD,
                                                                                     DEV_PG_HOST, DEV_PG_PORT)


DEV_OFX_URI = 'postgresql+psycopg2://{0}:{1}@{2}:{3}/ofx'.format(DEV_PG_USERNAME, DEV_PG_PASSWORD, DEV_PG_HOST,
                                                                 DEV_PG_PORT)
