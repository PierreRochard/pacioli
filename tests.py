import os
import unittest
import uuid

import psycopg2
from sqlalchemy.engine.url import URL

from manage import createdb
from pacioli import create_app, db
from pacioli.settings import Config
# from pacioli.models import User
# from manage import createdb
# from install import run_command

test_user_password = str(uuid.uuid4()).replace('-', '')


class TestConfig(Config):
    pg_uri = URL(drivername='postgresql+psycopg2',
                 username='test_user',
                 password=test_user_password,
                 host='localhost',
                 port=5432,
                 database='pacioli_test')
    SQLALCHEMY_DATABASE_URI = pg_uri

    TESTING = True


class TestCase(unittest.TestCase):
    def setUp(self):
        connection = psycopg2.connect("dbname=postgres")
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("""
            CREATE ROLE test_user WITH
                NOSUPERUSER
                CREATEDB
                NOCREATEROLE
                NOINHERIT
                LOGIN
                NOREPLICATION
                NOBYPASSRLS
                ENCRYPTED
                PASSWORD %s;
            """, (test_user_password, ))
        cursor.close()
        connection.close()

        connection = psycopg2.connect(database='postgres', user='test_user',
                                      password=test_user_password)
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("""
            CREATE DATABASE pacioli_test
              ENCODING 'utf-8'
              TEMPLATE template0;
        """)
        cursor.close()
        connection.close()

    def tearDown(self):
        db.session.remove()
        # db.drop_all()
        connection = psycopg2.connect("dbname=postgres")
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute("""
            SELECT
              pg_terminate_backend(pid)
            FROM
              pg_stat_activity
            WHERE
              pid <> pg_backend_pid()
            AND datname = 'pacioli_test'
            ;
        """)
        cursor.execute("""
            DROP DATABASE pacioli_test;
            """)
        cursor.execute("""
            DROP ROLE test_user;
            """.format(test_user_password))
        cursor.close()
        connection.close()

    def test_create_database(self):
        app = create_app(TestConfig)
        self.app = app.test_client()
        with app.app_context():
            createdb()
    # def test_avatar(self):
    #     u = User(nickname='john', email='john@example.com')
    #     avatar = u.avatar(128)
    #     expected = 'http://www.gravatar.com/avatar/d4c74594d841139328695756648b6bd6'
    #     assert avatar[0:len(expected)] == expected
    #
    # def test_make_unique_nickname(self):
    #     u = User(nickname='john', email='john@example.com')
    #     db.session.add(u)
    #     db.session.commit()
    #     nickname = User.make_unique_nickname('john')
    #     assert nickname != 'john'
    #     u = User(nickname=nickname, email='susan@example.com')
    #     db.session.add(u)
    #     db.session.commit()
    #     nickname2 = User.make_unique_nickname('john')
    #     assert nickname2 != 'john'
    #     assert nickname2 != nickname

if __name__ == '__main__':
    unittest.main()