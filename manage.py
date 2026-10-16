#!/usr/bin/env python

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import json

import click
from dateutil.tz import tzlocal
from flask.cli import ScriptInfo
from flask_migrate import Migrate
from flask_security.utils import hash_password
from ofxtools.ofxalchemy import OFXParser, Session as OFX_Session
from ofxtools.ofxalchemy import Base as OFX_Base
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from sqlalchemy.engine.url import make_url

from pacioli import create_app
from pacioli.database.setup import create_database, ofx_engine, populate_chart
from pacioli.extensions import db, run_sql
from pacioli.models import (Users, Roles, BankFeedAccounts,
                            ChaseBrowserCredentials, ChaseBrowserRuns, ChaseLocalPreferences,
                            ChaseExportCoverage, ChaseLocalEvents)
from pacioli.secure_fields import encrypt_existing_data as migrate_encrypted_data

env = os.environ.get('pacioli_ENV', 'Dev')
app = create_app('pacioli.settings.{0}Config'.format(env), env=env)

migrate = Migrate(app, db)

@app.cli.command('run_server')
def run_server():
    from pacioli.models import register_views
    with app.app_context():
        register_views()

    import pacioli.views.admin_views
    import pacioli.views.bookkeeping_views
    import pacioli.views.accounting_views
    import pacioli.views.ofx_views
    import pacioli.views.amazon_views
    import pacioli.views.payroll_views
    import pacioli.views.tax_views

    app.run(port=5000)


@app.shell_context_processor
def make_shell_context():
    return dict(app=app, db=db, User=Users)


@app.cli.command('createdb')
def createdb():
    create_database()


@app.cli.command('migrate_chase_browser')
def migrate_chase_browser():
    """Add or extend Chase connector tables without touching accounting data."""
    run_sql('CREATE SCHEMA IF NOT EXISTS admin')
    db.metadata.create_all(bind=db.engine, tables=[
        ChaseBrowserCredentials.__table__, ChaseBrowserRuns.__table__,
        ChaseLocalPreferences.__table__, ChaseExportCoverage.__table__,
        ChaseLocalEvents.__table__])
    for clause in (
        'ALTER TABLE admin.chase_browser_credentials ADD COLUMN IF NOT EXISTS session_state text',
        'ALTER TABLE admin.chase_browser_credentials ADD COLUMN IF NOT EXISTS browser_profile_key varchar(32)',
        "ALTER TABLE admin.chase_browser_credentials ADD COLUMN IF NOT EXISTS browser_engine varchar(16) NOT NULL DEFAULT 'chromium'",
        'ALTER TABLE admin.chase_browser_credentials ADD COLUMN IF NOT EXISTS provider_verified_at timestamptz',
        'ALTER TABLE admin.chase_browser_credentials ADD COLUMN IF NOT EXISTS passkey_credential text',
        'ALTER TABLE admin.chase_browser_credentials ADD COLUMN IF NOT EXISTS passkey_verified_at timestamptz',
        'ALTER TABLE admin.chase_browser_credentials ADD COLUMN IF NOT EXISTS selected_account_labels json',
        'ALTER TABLE admin.chase_browser_credentials ADD COLUMN IF NOT EXISTS selected_ofx_account_ids json',
        "ALTER TABLE admin.chase_browser_runs ADD COLUMN IF NOT EXISTS mode varchar(16) NOT NULL DEFAULT 'interactive'",
        'ALTER TABLE admin.chase_browser_runs ADD COLUMN IF NOT EXISTS mfa_prompt varchar(16)',
        'ALTER TABLE admin.chase_browser_runs ADD COLUMN IF NOT EXISTS mfa_code text',
        'ALTER TABLE admin.chase_browser_runs ADD COLUMN IF NOT EXISTS browser_requested boolean NOT NULL DEFAULT false',
        'ALTER TABLE admin.chase_browser_runs ADD COLUMN IF NOT EXISTS browser_visible boolean NOT NULL DEFAULT false',
        'ALTER TABLE admin.chase_browser_runs ADD COLUMN IF NOT EXISTS provider_confirmed boolean NOT NULL DEFAULT false',
        'ALTER TABLE admin.chase_browser_runs ADD COLUMN IF NOT EXISTS passkey_confirmed boolean NOT NULL DEFAULT false',
        'ALTER TABLE admin.chase_browser_runs ADD COLUMN IF NOT EXISTS diagnostics text',
    ):
        run_sql(clause)
    run_sql("UPDATE admin.chase_browser_runs SET mode = 'automatic' WHERE mode = 'scheduled'")
    for column in ('auto_sync_enabled', 'scheduled_by_id', 'last_scheduled_at'):
        run_sql('ALTER TABLE admin.chase_browser_credentials DROP COLUMN IF EXISTS {}'.format(column))


@app.cli.command('dropdb')
def dropdb():
    db.drop_all()
    OFX_Base.metadata.drop_all(ofx_engine())


@app.cli.command('create_admin')
@click.option('-e', '--email', required=True)
@click.option('-p', '--password', prompt=True, hide_input=True,
              confirmation_prompt=True)
def create_admin(email, password):
    try:
        admin = Users()
        admin.email = email
        admin.password = hash_password(password)
        admin.active = True
        admin.confirmed_at = datetime.now(tzlocal())
        db.session.add(admin)
        db.session.commit()

    except IntegrityError:
        db.session.rollback()
        admin = db.session.query(Users).filter(Users.email == email).one()
        admin.password = hash_password(password)
        admin.confirmed_at = datetime.now(tzlocal())
        db.session.commit()
        print('Password reset for {0}'.format(email))

    try:
        administrator = Roles()
        administrator.name = 'administrator'
        administrator.description = 'administrator'
        db.session.add(administrator)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        administrator = (db.session.query(Roles)
                         .filter(Roles.name == 'administrator').one())
    admin.roles.append(administrator)
    db.session.commit()


@app.cli.command('create_admin_link')
def create_admin_link():
    """Issue a short-lived, one-use browser link for the first administrator."""
    from pacioli.admin_setup import issue_admin_link
    try:
        link = issue_admin_link(db.session,
                                app.config.get('PACIOLI_PUBLIC_URL',
                                               'https://pacioli.lan'))
        db.session.commit()
    except ValueError as error:
        db.session.rollback()
        raise click.ClickException(str(error))
    click.echo(link)


@app.cli.command('import_ofx')
def import_ofx():
    from pacioli.functions.ofx_functions import fix_ofx_file
    directory = os.path.abspath(os.path.join('configuration_files', 'data'))
    files = [ofx_file for ofx_file in os.listdir(directory) if ofx_file.endswith(('.ofx', '.OFX', '.qfx', '.QFX'))]
    for ofx_file_name in files:
        ofx_file_path = os.path.join(directory, ofx_file_name)
        parser = OFXParser()
        OFX_Session.configure(bind=ofx_engine())
        if 'fixed' not in ofx_file_path:
            ofx_file_path = fix_ofx_file(ofx_file_path)
        parser.parse(ofx_file_path)
        parser.instantiate()
        OFX_Session.commit()
        OFX_Session.remove()


@app.cli.command('runs_at_7am_and_7pm')
def runs_at_7am_and_7pm():
    from pacioli.models import register_views
    with app.app_context():
        register_views()

    from pacioli.functions.ofx_functions import sync_ofx
    sync_ofx()

    from pacioli.functions.ofx_functions import apply_all_mappings
    apply_all_mappings()

    from pacioli.functions.email_reports import send_ofx_bank_transactions_report
    send_ofx_bank_transactions_report()
    # Market prices are fetched only on explicit owner command (scripts/schwab.py
    # value --fetch) through the decoy universe; never on a schedule.


@app.cli.command('backup_db')
def backup_db():
    """Write a custom-format archive to PACIOLI_BACKUP_DIR (default .local/backups).

    The archive holds the whole ledger, so it stays on this machine with
    owner-only permissions; it is never mailed or uploaded."""
    pg_dump = shutil.which('pg_dump')
    if not pg_dump:
        raise RuntimeError('pg_dump is required for database backups')

    database_url = make_url(app.config['SQLALCHEMY_DATABASE_URI'])
    if database_url.get_backend_name() != 'postgresql' or not database_url.database:
        raise RuntimeError('Database backup requires a PostgreSQL database URL')

    backup_dir = Path(os.environ.get('PACIOLI_BACKUP_DIR') or
                      Path(__file__).resolve().parent / '.local' / 'backups')
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    with tempfile.NamedTemporaryFile(prefix='pacioli-{}-'.format(stamp), suffix='.dump',
                                     dir=str(backup_dir), delete=False) as backup:
        backup_path = Path(backup.name)

    command = [pg_dump, '--format=custom', '--no-password',
               '--file', str(backup_path)]
    host = database_url.query.get('host') or database_url.host
    port = database_url.query.get('port') or database_url.port
    if host:
        command.extend(['--host', str(host)])
    if port:
        command.extend(['--port', str(port)])
    if database_url.username:
        command.extend(['--username', database_url.username])
    command.append(database_url.database)

    environment = os.environ.copy()
    if database_url.password:
        environment['PGPASSWORD'] = database_url.password
    try:
        subprocess.run(command, check=True, env=environment)
    except Exception:
        backup_path.unlink()
        raise
    backup_path.chmod(0o600)
    click.echo('Backup written to {}'.format(backup_path))


@app.cli.command('encrypt_existing_data')
def encrypt_existing_data():
    """Encrypt pre-existing plaintext connection passwords and OFX responses."""
    click.echo('Encrypted {} existing values'.format(migrate_encrypted_data(db.engine)))


@app.cli.command('submit_amazon_report_request')
def submit_amazon_report_request():
    from pacioli.functions.amazon_functions import request_amazon_report
    request_amazon_report()


@app.cli.command('import_amazon_report')
def import_amazon_report():
    from pacioli.functions.amazon_functions import fetch_amazon_email_download
    fetch_amazon_email_download()


@app.cli.command('import_amazon_csv')
@click.argument('path', type=click.Path(exists=True, dir_okay=False))
def import_amazon_csv_command(path):
    from pacioli.functions.amazon_functions import import_amazon_csv
    with open(path, encoding='utf-8-sig', newline='') as report:
        count = import_amazon_csv(report.read())
    click.echo('Imported {} Amazon items'.format(count))


@app.cli.command('populate_chart_of_accounts')
def populate_chart_of_accounts():
    populate_chart()


@app.cli.command('accounting_check')
def accounting_check():
    """Read-only journal, source, trial-balance, and statement reconciliation."""
    from pacioli.functions.accounting_report import audit_ledger
    problems = audit_ledger(db.session)
    click.echo(json.dumps({'passed': not problems, 'problems': problems},
                          default=str, indent=2))
    if problems:
        raise click.ClickException('{} accounting differences found'.format(len(problems)))


@app.cli.command('prepare_accounting_columns')
def prepare_accounting_columns():
    """Add conversion evidence columns before reviewed historical backfill."""
    from pacioli.database.accounting_migration import prepare_journal_columns
    prepare_journal_columns()


@app.cli.command('post_journal')
@click.option('--date', 'posting_date', required=True,
              type=click.DateTime(formats=['%Y-%m-%d']))
@click.option('--reference', required=True, help='Stable manual source ID for retries.')
@click.option('--debit', required=True, help='Subaccount name.')
@click.option('--credit', required=True, help='Subaccount name.')
@click.option('--amount', required=True, help='USD functional amount.')
@click.option('--source-amount', default=None)
@click.option('--source-currency', default='USD')
@click.option('--rate', default=None, help='USD per source currency unit.')
@click.option('--rate-source', default=None)
@click.option('--rate-date', type=click.DateTime(formats=['%Y-%m-%d']))
@click.option('--memo', default=None)
def post_journal(posting_date, reference, debit, credit, amount, source_amount,
                 source_currency, rate, rate_source, rate_date, memo):
    """Post one validated, source-identified manual journal pair."""
    from pacioli.functions.journal_posting import post_pair
    try:
        entry = post_pair(timestamp=posting_date.replace(tzinfo=timezone.utc),
                          debit=debit, credit=credit, functional_amount=amount,
                          transaction_source='manual', transaction_id=reference,
                          source_amount=source_amount,
                          source_currency=source_currency, conversion_rate=rate,
                          conversion_source=rate_source,
                          conversion_date=rate_date.date() if rate_date else None,
                          memo=memo)
        db.session.commit()
    except (ValueError, TypeError, IntegrityError) as error:
        db.session.rollback()
        raise click.ClickException(str(error))
    click.echo('Posted journal entry {}'.format(entry.id))


@app.cli.command('post_split')
@click.argument('path', type=click.Path(exists=True, dir_okay=False))
def post_split(path):
    """Post a reviewed USD allocation from a JSON file, atomically."""
    from pacioli.functions.journal_posting import post_allocations
    with open(path, encoding='utf-8') as source:
        details = json.load(source)
    try:
        when = datetime.strptime(details['date'], '%Y-%m-%d').replace(
            tzinfo=timezone.utc)
        entries = post_allocations(
            timestamp=when, source=details['source'],
            transaction_id=details['transaction_id'],
            fixed_account=details['fixed_account'],
            allocations=[(row['key'], row['account'], row['amount'])
                         for row in details['allocations']],
            total=details['total'],
            variable_side=details.get('variable_side', 'debit'))
        db.session.commit()
    except (KeyError, TypeError, ValueError, IntegrityError) as error:
        db.session.rollback()
        raise click.ClickException(str(error))
    click.echo('Posted or found {} allocation entries'.format(len(entries)))


@app.cli.command('reverse_journal')
@click.option('--id', 'entry_id', required=True, type=int)
@click.option('--date', 'posting_date', required=True,
              type=click.DateTime(formats=['%Y-%m-%d']))
@click.option('--reason', required=True)
def reverse_journal(entry_id, posting_date, reason):
    """Post a compensating entry while retaining the original journal."""
    from pacioli.functions.journal_posting import reverse_entry
    try:
        entry = reverse_entry(entry_id,
                              timestamp=posting_date.replace(tzinfo=timezone.utc),
                              reason=reason)
        db.session.commit()
    except (ValueError, TypeError, IntegrityError) as error:
        db.session.rollback()
        raise click.ClickException(str(error))
    click.echo('Reversal journal entry {}'.format(entry.id))


@app.cli.command('close_month')
@click.option('--period', required=True, help='Completed UTC month, YYYY-MM.')
def close_month_command(period):
    """Reconcile and lock a completed personal-accounting month."""
    from pacioli.functions.period_close import close_month
    try:
        closed = close_month(db.session, period)
        db.session.commit()
    except (ValueError, IntegrityError) as error:
        db.session.rollback()
        raise click.ClickException(str(error))
    click.echo('Closed {} with {} journal entries; SHA256 {}'.format(
        closed.period, closed.journal_count, closed.journal_digest))


@app.cli.command('generate_accruals')
@click.option('--through', 'through_date', required=True,
              type=click.DateTime(formats=['%Y-%m-%d']))
def generate_accruals_command(through_date):
    """Post missing daily schedules through a selected date, atomically."""
    from pacioli.functions.accrual_posting import generate_accruals
    try:
        entries = generate_accruals(through_date.date())
        db.session.commit()
    except (ValueError, TypeError, IntegrityError) as error:
        db.session.rollback()
        raise click.ClickException(str(error))
    click.echo('Posted {} accrual entries'.format(len(entries)))


@app.cli.command('set_ofx_account_currency')
@click.option('--account-id', required=True, type=int)
@click.option('--currency', required=True, type=click.Choice(['USD', 'EUR', 'BTC']))
def set_ofx_account_currency(account_id, currency):
    """Attest an imported OFX account's currency before posting its amounts."""
    name = db.session.execute(text('SELECT name FROM ofx.acctfrom WHERE id = :id'),
                              {'id': account_id}).scalar()
    if name is None:
        raise click.ClickException('OFX account does not exist')
    registered = db.session.get(BankFeedAccounts, account_id)
    if registered is None:
        registered = BankFeedAccounts(ofx_account_id=account_id, currency=currency)
        db.session.add(registered)
    elif registered.currency != currency:
        raise click.ClickException('Existing currency differs; review postings and '
                                   'correct the registration in a controlled migration')
    db.session.commit()
    click.echo('Confirmed OFX account {} ({}) as {}'.format(account_id, name, currency))

if __name__ == "__main__":
    app.cli.main(obj=ScriptInfo(create_app=lambda: app))
