#!/usr/bin/env python

import csv
from datetime import datetime, timedelta
import os

from dateutil.tz import tzlocal
from flask.ext.migrate import MigrateCommand, Migrate
from flask.ext.script import Manager, Server
from flask.ext.script.commands import ShowUrls, Clean
from flask.ext.security.utils import encrypt_password
from flask_mail import Message
from ofxtools.ofxalchemy import Base as OFX_Base
from ofxtools.ofxalchemy import OFXParser
from sqlalchemy.exc import IntegrityError, ProgrammingError

from pacioli import create_app, mail
from pacioli.controllers.utilities import results_to_table
from pacioli.models import db, User, Role, Elements, Classifications, Accounts, Subaccounts
from pacioli.controllers.ofx_views import sync_ofx

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

    db.create_all()
    OFX_Base.metadata.create_all(db.engine)
    try:
        db.engine.execute('DROP VIEW ofx.new_transactions;')
    except ProgrammingError:
        pass
    db.engine.execute("""
    CREATE VIEW ofx.new_transactions AS SELECT concat(ofx.stmttrn.fitid, ofx.stmttrn.acctfrom_id) AS id,
            ofx.stmttrn.dtposted AS date,
            ofx.stmttrn.trnamt AS amount,
            concat(ofx.stmttrn.name, ofx.stmttrn.memo) AS description,
            ofx.acctfrom.name AS account
        FROM ofx.stmttrn
        LEFT OUTER JOIN pacioli.journal_entries ON pacioli.journal_entries.transaction_id = concat(ofx.stmttrn.fitid,
                ofx.stmttrn.acctfrom_id)
        JOIN ofx.acctfrom ON ofx.acctfrom.id = ofx.stmttrn.acctfrom_id
        WHERE pacioli.journal_entries.transaction_id IS NULL ORDER BY ofx.stmttrn.dtposted DESC;
    """)


@manager.command
def dropdb():
    db.drop_all()
    OFX_Base.metadata.drop_all(db.engine)

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
def import_ofx():
    directory = 'configuration_files/data/'
    files = [ofx_file for ofx_file in os.listdir(directory) if ofx_file.endswith(('.ofx', '.OFX', '.qfx', '.QFX'))]
    for ofx_file_name in files:
        ofx_file_path = os.path.join(directory, ofx_file_name)
        parser = OFXParser()
        parser.parse(ofx_file_path)
        parser.instantiate()


@manager.command
def update_ofx():
    sync_ofx()
    from pacioli.controllers.ofx_views import Transactions, AccountsFrom
    start = datetime.now().date() - timedelta(days=1)
    new_transactions = (db.session.query(db.func.concat(Transactions.fitid, Transactions.acctfrom_id).label('id'),
                                        Transactions.dtposted.label('date'), Transactions.trnamt.label('amount'),
                                        db.func.concat(Transactions.name, ' ', Transactions.memo).label('description'))
                        .join(AccountsFrom, AccountsFrom.id == Transactions.acctfrom_id)
                        .filter(Transactions.dtposted > start)
                        .order_by(Transactions.fitid.desc()).all())
    if new_transactions:
        html_body = results_to_table(new_transactions)
        msg = Message('New Transactions', recipients=['pierre@rochard.org'], html=html_body)
        mail.send(msg)


@manager.command
def populate_chart_of_accounts():
    chart_of_accounts_csv = os.path.join(os.path.dirname(__file__), 'Chart of Accounts.csv')
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
