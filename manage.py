#!/usr/bin/env python

import csv
from datetime import datetime
import os

from dateutil.tz import tzlocal
from flask.ext.script import Manager, Server
from flask.ext.script.commands import ShowUrls, Clean
from flask.ext.security.utils import encrypt_password

from pacioli import create_app
from pacioli.models import db, User, Role, Elements, Classifications, Accounts, Subaccounts

# default to dev config because no one should use this in
# production anyway
from sqlalchemy.exc import IntegrityError

env = os.environ.get('pacioli_ENV', 'dev')
app = create_app('pacioli.settings.%sConfig' % env.capitalize(), env=env)

manager = Manager(app)
manager.add_command("server", Server())
manager.add_command("show-urls", ShowUrls())
manager.add_command("clean", Clean())


@manager.shell
def make_shell_context():
    return dict(app=app, db=db, User=User)


@manager.command
def createdb():
    db.create_all()


@manager.command
def dropdb():
    db.drop_all()


@manager.option('-e', '--email', dest='email')
@manager.option('-p', '--password', dest='password')
def create_admin(email, password):
    admin = User()
    admin.email = email
    admin.password = encrypt_password(password)
    admin.active = True
    admin.confirmed_at = datetime.now(tzlocal())
    db.session.add(admin)
    db.session.commit()

    superuser = Role()
    superuser.name = 'superuser'
    superuser.description = 'superuser'
    db.session.add(superuser)
    db.session.commit()

    admin.roles.append(superuser)
    db.session.commit()


@manager.command
def populate_chart_of_accounts():
    chart_of_accounts_csv = os.path.join(os.path.dirname(__file__), 'configuration_files', 'Chart of Accounts.csv')
    with open(chart_of_accounts_csv) as csv_file:
        reader = csv.reader(csv_file)
        rows = [pair for pair in reader]
        header = rows.pop(0)
        for row in rows:
            line = zip(header, row)
            line = dict(line)

            element = Elements()
            element.name = line['Element']
            try:
                db.session.add(element)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

            classification = Classifications()
            classification.name = line['Classification']
            classification.parent = line['Element']
            try:
                db.session.add(classification)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

            account = Accounts()
            account.name = line['Account']
            account.cash_source = line['Cash Source']
            account.parent = line['Classification']
            try:
                db.session.add(account)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()

            subaccount = Subaccounts()
            subaccount.name = line['Subaccount']
            subaccount.parent = line['Account']
            try:
                db.session.add(subaccount)
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
    return True

if __name__ == "__main__":
    manager.run()
