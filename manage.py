#!/usr/bin/env python

import csv
from datetime import datetime, timedelta
import os

from dateutil.tz import tzlocal
from flask import current_app
from flask.ext.migrate import MigrateCommand, Migrate
from flask.ext.script import Manager, Server
from flask.ext.script.commands import ShowUrls, Clean
from flask.ext.security.utils import encrypt_password
from flask_mail import Message
from ofxtools.ofxalchemy import OFXParser, DBSession
from ofxtools.ofxalchemy import Base as OFX_Base
from pacioli.functions.accounting_functions import create_trial_balances_trigger_function
from pacioli.functions.amazon_functions import fetch_amazon_email_download, request_amazon_report
from pacioli.functions.amazon_functions import create_amazon_views
from pacioli.functions.bookkeeping_functions import create_bookkeeping_views
from pacioli.functions.investment_functions import update_ticker_prices
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError, ProgrammingError

from pacioli import create_app, mail
from pacioli.views.utilities import results_to_email_template
from pacioli.models import db, User, Role, Elements, Classifications, Accounts, Subaccounts
from pacioli.functions.ofx_functions import sync_ofx, apply_all_mappings, create_ofx_views

env = os.environ.get('pacioli_ENV', 'dev')
app = create_app('pacioli.settings.%sConfig' % env.capitalize(), env=env)

manager = Manager(app)
migrate = Migrate(app, db)

manager.add_command("server", Server())
manager.add_command("show-urls", ShowUrls())
manager.add_command("clean", Clean())
manager.add_command('db', MigrateCommand)


@manager.shell
def make_shell_context():
    return dict(app=app, db=db, User=User)


@manager.command
def createdb():
    try:
        db.engine.execute('CREATE SCHEMA admin;')
    except ProgrammingError:
        pass
    try:
        db.engine.execute('CREATE SCHEMA ofx;')
    except ProgrammingError:
        pass
    try:
        db.engine.execute('CREATE SCHEMA pacioli;')
    except ProgrammingError:
        pass
    try:
        db.engine.execute('CREATE SCHEMA amazon;')
    except ProgrammingError:
        pass
    try:
        db.engine.execute('CREATE SCHEMA investments;')
    except ProgrammingError:
        pass

    db.create_all()
    OFX_Base.metadata.create_all(db.engine)
    create_ofx_views()
    create_bookkeeping_views()
    create_amazon_views()


@manager.command
def update_trial_balances():
    create_trial_balances_trigger_function()


@manager.command
def dropdb():
    db.drop_all()
    OFX_Base.metadata.drop_all(db.engine)


@manager.option('-e', '--email', dest='email')
@manager.option('-p', '--password', dest='password')
def create_admin(email, password):
    try:
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
    except IntegrityError:
        db.session.rollback()


@manager.command
def import_ofx():
    directory = 'configuration_files/data/'
    files = [ofx_file for ofx_file in os.listdir(directory) if ofx_file.endswith(('.ofx', '.OFX', '.qfx', '.QFX'))]
    for ofx_file_name in files:
        ofx_file_path = os.path.join(directory, ofx_file_name)
        parser = OFXParser()
        engine = create_engine(current_app.config['SQLALCHEMY_DATABASE_URI'], echo=False)
        DBSession.configure(bind=engine)
        parser.parse(ofx_file_path)
        parser.instantiate()
        DBSession.commit()


@manager.command
def update_ofx():
    sync_ofx()
    from pacioli.views.ofx_views import Transactions, AccountsFrom
    start = datetime.now().date() - timedelta(days=1)
    new_transactions = (db.session.query(Transactions.id, Transactions.date, Transactions.amount,
                                         Transactions.description, Transactions.account)
                        .filter(Transactions.date > start)
                        .order_by(Transactions.date.desc()).all())
    if new_transactions:
        header = ['ID', 'Date', 'Amount', 'Description', 'Account']
        transactions = [[cell for cell in row] for row in new_transactions]
        for row in transactions:
            row[0] = '...' + str(row[0])[-4:-1]
            row[1] = row[1].date()
            row[2] = '{0:,.2f}'.format(row[2])
        html_body = results_to_email_template('New Transactions', '', header, transactions)
        msg = Message('New Transactions', recipients=[app.config['MAIL_USERNAME']], html=html_body)
        mail.send(msg)
    apply_all_mappings()


@manager.command
def submit_amazon_report_request():
    request_amazon_report()


@manager.command
def import_amazon_report():
    fetch_amazon_email_download()


@manager.command
def update_tickers():
    update_ticker_prices()


@manager.command
def populate_chart_of_accounts():
    chart_of_accounts_csv = os.path.join(os.path.dirname(__file__), 'Generic Chart of Accounts.csv')
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
