#!/usr/bin/env python

import csv
from datetime import datetime
import os
import platform
import subprocess

from dateutil.tz import tzlocal
from flask import current_app
from flask_migrate import MigrateCommand, Migrate
from flask_script import Manager, Server
from flask_script.commands import ShowUrls, Clean
from flask_security.utils import encrypt_password
from flask_mail import Message
from ofxtools.ofxalchemy import OFXParser, DBSession
from ofxtools.ofxalchemy import Base as OFX_Base
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from pacioli import create_app, mail
from pacioli.models import db, User, Role, Elements, Classifications, Accounts, Subaccounts

env = os.environ.get('pacioli_ENV', 'dev')
app = create_app('pacioli.settings.%sConfig' % env.capitalize(), env=env)

print(env)

manager = Manager(app)
migrate = Migrate(app, db)

manager.add_command("runserver", Server())
manager.add_command("show-urls", ShowUrls())
manager.add_command("clean", Clean())
manager.add_command('db', MigrateCommand)


@manager.command
def run_server():
    from pacioli.models import register_models
    with app.app_context():
        register_models()

    import pacioli.views.admin_views
    import pacioli.views.bookkeeping_views
    import pacioli.views.accounting_views
    import pacioli.views.ofx_views
    import pacioli.views.amazon_views
    import pacioli.views.payroll_views

    app.run()


@manager.shell
def make_shell_context():
    return dict(app=app, db=db, User=User)


@manager.command
def createdb():
    db.engine.execute('CREATE SCHEMA IF NOT EXISTS admin;')
    db.engine.execute('CREATE SCHEMA IF NOT EXISTS bookkeeping;')
    db.engine.execute('CREATE SCHEMA IF NOT EXISTS ofx;')
    db.engine.execute('CREATE SCHEMA IF NOT EXISTS amazon;')
    db.engine.execute('CREATE SCHEMA IF NOT EXISTS investments;')
    db.engine.execute('CREATE SCHEMA IF NOT EXISTS payroll;')

    db.create_all()
    OFX_Base.metadata.create_all(db.engine)

    from pacioli.database.sql_views import create_all
    create_all()


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

    except IntegrityError:
        db.session.rollback()
        admin = db.session.query(User).filter(User.email == email).one()
        admin.password = encrypt_password(password)
        admin.confirmed_at = datetime.now(tzlocal())
        db.session.commit()
        print('Password reset for {0}'.format(email))

    try:
        administrator = Role()
        administrator.name = 'administrator'
        administrator.description = 'administrator'
        db.session.add(administrator)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        administrator = db.session.query(Role).filter(Role.name == 'administrator').one()
    admin.roles.append(administrator)
    db.session.commit()


@manager.command
def import_ofx():
    from pacioli.functions.ofx_functions import fix_ofx_file
    directory = os.path.abspath(os.path.join('configuration_files', 'data'))
    files = [ofx_file for ofx_file in os.listdir(directory) if ofx_file.endswith(('.ofx', '.OFX', '.qfx', '.QFX'))]
    for ofx_file_name in files:
        ofx_file_path = os.path.join(directory, ofx_file_name)
        parser = OFXParser()
        engine = create_engine(current_app.config['SQLALCHEMY_DATABASE_URI'], echo=False)
        DBSession.configure(bind=engine)
        if 'fixed' not in ofx_file_path:
            ofx_file_path = fix_ofx_file(ofx_file_path)
        parser.parse(ofx_file_path)
        parser.instantiate()
        DBSession.commit()


@manager.command
def runs_at_7am_and_7pm():
    from pacioli.functions.ofx_functions import sync_ofx
    sync_ofx()

    from pacioli.functions.ofx_functions import apply_all_mappings
    apply_all_mappings()

    from pacioli.functions.email_reports import send_ofx_bank_transactions_report
    send_ofx_bank_transactions_report()

    from pacioli.functions.investment_functions import update_ticker_prices
    update_ticker_prices()


@manager.command
def backup_db():
    if platform.system() == 'Linux':
        bash_path = '/bin/bash'
        pg_dump_path = '/usr/bin/pg_dump'
    elif platform.system() == 'Darwin':
        bash_path = '/usr/local/bin/bash'
        pg_dump_path = '/usr/local/bin/pg_dump'
    else:
        return False
    backup_file_name = datetime.now().strftime('%Y%m%d%H%M%S%f') + '.dump'
    backup_file_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), backup_file_name)
    command = [pg_dump_path,
               '-Fc',
               'pacioli',
               '>', backup_file_path]
    command = [c.encode('utf-8') for c in command]
    command = ' '.join(command)
    subprocess.call(command, shell=True, executable=bash_path)
    msg = Message('Database Backup', recipients=[app.config['MAIL_USERNAME']])
    with app.open_resource(backup_file_path) as fp:
        msg.attach(backup_file_name, 'application/octet-stream', fp.read())
    mail.send(msg)
    os.remove(backup_file_path)


@manager.command
def submit_amazon_report_request():
    from pacioli.functions.amazon_functions import request_amazon_report
    request_amazon_report()


@manager.command
def import_amazon_report():
    from pacioli.functions.amazon_functions import fetch_amazon_email_download
    fetch_amazon_email_download()


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
