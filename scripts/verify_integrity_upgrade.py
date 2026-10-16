"""Rehearse an accounting schema upgrade on a restored private database.

Use --rehearse DUMP before --apply. Reports contain counts/digests, never source
contents or keys. The encryption key remains in the separate private app config.
Application and worker writers must be stopped before applying a live migration.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode
import getpass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def snapshot(connection):
    # Include every retained financial record/evidence object; ignore only the
    # new nullable journal column so the old/new schema has the same projection.
    tables = ('ofx.acctfrom', 'ofx.bankacctfrom', 'ofx.ccacctfrom', 'ofx.stmttrn',
              'ofx.ledgerbal', 'ofx.availbal', 'bookkeeping.journal_entries',
              'bookkeeping.journal_entry_events', 'bookkeeping.closed_periods',
              'bookkeeping.trial_balances', 'bookkeeping.accounting_evidence',
              'bookkeeping.bank_statements', 'bookkeeping.opening_balances',
              'bookkeeping.transfer_matches', 'bookkeeping.transfer_match_legs',
              'bookkeeping.accounting_reviews', 'amazon.items', 'amazon.orders',
              'amazon.evidence_bundles', 'admin.mappings', 'admin.ofx_file_imports',
              'admin.connection_responses')
    result = {}
    for table in tables:
        expression = 'to_jsonb(t)::text'
        if table == 'bookkeeping.journal_entries':
            expression = "(to_jsonb(t)-'mapping_decision')::text"
        elif table == 'admin.connection_responses':
            expression = "(to_jsonb(t)-'import_status'-'import_error')::text"
        rows = [r[0] for r in connection.exec_driver_sql('SELECT '+expression+' FROM '+table+' t ORDER BY 1')]
        result[table] = {'count': len(rows), 'sha256': sha256(json.dumps(rows).encode()).hexdigest()}
    rows = [r[0] for r in connection.exec_driver_sql('SELECT to_jsonb(t)::text FROM ofx.transactions t ORDER BY 1')]
    result['ofx.transactions'] = {'count': len(rows), 'sha256': sha256(json.dumps(rows).encode()).hexdigest()}
    return result


def upgrade(url=None, expected=None):
    from sqlalchemy import inspect
    from pacioli import create_app
    from pacioli.settings import ProdConfig
    from pacioli.extensions import db
    from pacioli.database.setup import create_database
    from pacioli.models import register_views
    from pacioli.secure_fields import EncryptedText, decrypt_value
    class Configuration(ProdConfig):
        SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'options': '-c timezone=UTC'}}
        if url:
            SQLALCHEMY_DATABASE_URI = url
    application = create_app(Configuration)
    with application.app_context():
        with db.engine.connect() as connection:
            before = snapshot(connection)
        if expected is not None and before != expected:
            raise RuntimeError('Live database changed since the restore rehearsal; create a fresh backup and rehearse again')
        if expected is not None:
            with db.engine.connect() as connection:
                active = connection.exec_driver_sql("""SELECT
                    (SELECT count(*) FROM admin.chase_browser_runs WHERE status IN
                        ('queued','signing_in','waiting_for_mfa','choosing_accounts','exporting','importing')) +
                    (SELECT count(*) FROM amazon.collection_runs WHERE state IN ('queued','running'))""").scalar()
                if active:
                    raise RuntimeError('An import worker is active; wait for it before migrating')
        create_database()
        with db.engine.connect() as connection:
            after = snapshot(connection)
        if before != after:
            changed = [table for table in before if before[table] != after[table]]
            raise RuntimeError('Upgrade changed retained financial evidence: '+', '.join(changed))
        register_views()
        # Verify that the separately configured key decrypts every encrypted
        # column, without logging any ciphertext or plaintext.
        inspector = inspect(db.engine)
        decrypted = 0
        with db.engine.connect() as connection:
            for table in db.metadata.tables.values():
                encrypted = [c for c in table.columns if isinstance(c.type, EncryptedText)]
                if not encrypted or not inspector.has_table(table.name, schema=table.schema):
                    continue
                available = {c['name'] for c in inspector.get_columns(table.name, schema=table.schema)}
                for column in encrypted:
                    if column.name not in available:
                        continue
                    for value, in connection.exec_driver_sql('SELECT "'+column.name+'" FROM "'+table.schema+'"."'+table.name+'" WHERE "'+column.name+'" IS NOT NULL'):
                        decrypt_value(value)
                        decrypted += 1
        from pacioli.functions.accounting_report import audit_ledger
        issues = audit_ledger(db.session)
        summary = dict(Counter(issue['kind'] for issue in issues))
        # Classification/reconciliation findings are reported, never repaired by
        # a schema upgrade. Missing identities or altered source links are fatal.
        fatal = [issue for issue in issues if issue['kind'] in ('journal_money', 'source_identity', 'source_association')]
        if fatal:
            raise RuntimeError('Integrity upgrade still has database/source control violations')
        db.session.remove()
        db.engine.dispose()
        return {'before': before, 'after': after, 'financial_records_preserved': True,
                'encrypted_values_readable': decrypted, 'audit_issue_counts': summary,
                'pending_classification_journals': sum(issue.get('journal_count', 0) for issue in issues
                    if issue['kind'] == 'classification_pending')}


@contextmanager
def restored_database(archive):
    from scripts.ci_local import check_resources
    scratch = Path(os.environ['TMPDIR']).resolve()
    check_resources(scratch)
    bindir = Path(subprocess.check_output(['pg_config','--bindir'], text=True).strip())
    def run(*command):
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory(prefix='pacioli-restore-',dir=scratch) as work:
        work = Path(work)
        data = work/'data'
        run(str(bindir/'initdb'),'--no-instructions','--auth-local=trust','--auth-host=reject','-D',str(data))
        started = False
        try:
            run(str(bindir/'pg_ctl'),'-D',str(data),'-o',"-h '' -k {} -p 55440".format(work),'-l',str(work/'postgres.log'),'start')
            started = True
            run(str(bindir/'createdb'),'-h',str(work),'-p','55440','pacioli_restore')
            run(str(bindir/'pg_restore'),'--exit-on-error','--no-owner','--no-privileges',
                '-h',str(work),'-p','55440','-d','pacioli_restore',str(archive))
            query = urlencode({'host': str(work), 'port': 55440})
            yield 'postgresql+psycopg2://{}@/pacioli_restore?{}'.format(quote(getpass.getuser(),safe=''),query)
        finally:
            if started:
                run(str(bindir/'pg_ctl'),'-D',str(data),'stop','-m','fast')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--rehearse',type=Path,metavar='DUMP')
    action.add_argument('--apply',type=Path,metavar='REHEARSAL_REPORT')
    parser.add_argument('--report',type=Path,required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error('Use a new report path; existing evidence is not overwritten')
    if args.rehearse:
        with restored_database(args.rehearse) as url:
            report = upgrade(url)
        report['rehearsal_passed'] = True
        report['backup_sha256'] = sha256(args.rehearse.read_bytes()).hexdigest()
    else:
        previous = json.loads(args.apply.read_text())
        if not previous.get('rehearsal_passed'):
            parser.error('A successful restore rehearsal is required')
        report = upgrade(expected=previous['before'])
        report['applied'] = True
    args.report.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    with args.report.open('x') as destination:
        json.dump(report,destination,indent=2)
        destination.write('\n')
    print(json.dumps({key:value for key,value in report.items() if key not in ('before','after')},sort_keys=True))


if __name__ == '__main__':
    main()
