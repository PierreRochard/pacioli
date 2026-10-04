"""Report security, transaction consistency and independently validated evidence."""
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
import io
import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pacioli.extensions import db
from pacioli.functions import accounting_review as reviews
from pacioli.functions import reconciliation as reconciliation
from pacioli.functions.cash_flow_evidence import (
    cash_flow_context, cash_flow_classification_fingerprint)
from pacioli.functions.journal_posting import post_pair
from pacioli.functions.source_identity import source_identity
from pacioli.models import (BankFeedAccounts, JournalEntries, JournalEntryEvents,
                            Mappings, Subaccounts, Connections, ConnectionResponses,
                            OFXFileImports, Users)
from pacioli.views.cash_flow_views import cash_flow_session, report_dates


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 2, 1, tzinfo=timezone.utc)
URL = '/cash-flows/?start=2026-01-01&end=2026-01-31'


def bank(card=False):
    name = ('Report card ' if card else 'Report cash ') + uuid4().hex[:12]
    db.session.add(Subaccounts(name=name, parent='Accounts Payable' if card else 'Cash'))
    aid = db.session.execute(text("INSERT INTO ofx.acctfrom(subclass,name) "
        "VALUES(:subclass,:name) RETURNING id"),
        dict(subclass='ccacctfrom' if card else 'bankacctfrom', name=name)).scalar()
    if card:
        db.session.execute(text('INSERT INTO ofx.ccacctfrom(id,acctid) VALUES(:id,:number)'),
                           dict(id=aid, number=uuid4().hex[:20]))
    db.session.add(BankFeedAccounts(ofx_account_id=aid, currency='USD'))
    db.session.flush()
    return aid, name


def bank_transaction(account, amount, category='Transfer Clearing', day=10,
                     mapping=None, description='REPORT PAYMENT'):
    aid, name = account
    amount = Decimal(amount)
    fitid = uuid4().hex
    when = datetime(2026, 1, day, 12, tzinfo=timezone.utc)
    db.session.execute(text('''INSERT INTO ofx.stmttrn
        (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
        VALUES(:fitid,:aid,'OTHER',:when,:amount,:name)'''),
        dict(fitid=fitid, aid=aid, when=when, amount=amount, name=description))
    entry = post_pair(timestamp=when, debit=name if amount > 0 else category,
        credit=category if amount > 0 else name, functional_amount=abs(amount),
        transaction_source='ofx', transaction_id=source_identity(db.session, aid, fitid),
        mapping_id=mapping)
    db.session.flush()
    return entry


def test_cash_flow_dates_are_explicit_and_leap_safe():
    assert report_dates({}, date(2026, 9, 27)) == (date(2025, 9, 27), date(2026, 9, 26))
    assert report_dates({}, date(2024, 2, 29)) == (date(2023, 2, 28), date(2024, 2, 28))
    for arguments in ({'start': '2026-01-01'}, {'start': '2026-02-01', 'end': '2026-01-01'},
                      {'start': '2026-1-1', 'end': '2026-01-31'},
                      {'start': '20260101', 'end': '20260131'},
                      {'start': '2000-01-01', 'end': '2026-01-01'}):
        with pytest.raises(ValueError):
            report_dates(arguments)


def test_cash_flow_requires_admin_and_valid_filters(clean_ledger, client, admin_user):
    client.get('/logout/')
    assert client.get(URL).status_code == 302
    client.post('/login/', data=admin_user)
    for suffix in ('&section=bad', '&page=0', '&page=10001', '&page=no',
                   '&label=Salary', '&export=secret', '&direction=inflow',
                   '&section=operating&direction=sideways'):
        assert client.get(URL + suffix).status_code == 400
    assert client.get('/cash-flows/?start=2026-01-01').status_code == 400
    response = client.get(URL)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store, private'
    assert b'direct method' in response.data.lower()


def test_cash_flow_web_csv_and_details_are_read_only(clean_ledger, client, admin_user):
    post_pair(timestamp=START, debit='Chase Checking', credit='Salary', functional_amount='123.45')
    post_pair(timestamp=START, debit='Rent', credit='Chase Checking', functional_amount='23.40')
    db.session.commit()
    before = (db.session.query(JournalEntries).count(), db.session.query(JournalEntryEvents).count())
    client.post('/login/', data=admin_user)
    page = client.get(URL)
    assert page.status_code == 200
    assert b'123.45' in page.data and b'23.40' in page.data and b'100.05' in page.data
    detail = client.get(URL + '&section=operating&label=Salary')
    assert detail.status_code == 200 and b'123.45' in detail.data
    assert client.get(URL + '&section=operating').status_code == 200
    output = client.get(URL + '&export=csv')
    assert output.status_code == 200 and output.mimetype == 'text/csv'
    assert 'pacioli-cash-flows-2026-01-01-2026-01-31.csv' in output.headers['Content-Disposition']
    rows = list(csv.reader(io.StringIO(output.data.decode())))
    assert ['Ending cash', '100.05'] in rows
    assert (db.session.query(JournalEntries).count(), db.session.query(JournalEntryEvents).count()) == before


def test_cash_flow_page_and_csv_rank_each_direction_and_filter_drilldowns(
        clean_ledger, client, admin_user):
    from lxml import html

    for debit, credit, amount in (
            ('Chase Checking', 'Salary', '1000'),
            ('Rent', 'Chase Checking', '500'),
            ('Groceries', 'Chase Checking', '20'),
            ('Chase Checking', 'Rent', '25'),
            ('Chase Checking', 'Groceries', '10')):
        post_pair(timestamp=START, debit=debit, credit=credit, functional_amount=amount)
    db.session.commit()
    client.post('/login/', data=admin_user)
    document = html.fromstring(client.get(URL).data)
    expected = {'inflow': [('Salary', '1000.00'), ('Rent', '25.00'), ('Groceries', '10.00')],
                'outflow': [('Rent', '500.00'), ('Groceries', '20.00')]}
    for direction, wanted in expected.items():
        rows = document.xpath('//table[@aria-labelledby="cash-flow-operating-' + direction + '"]/tbody/tr')
        actual = [(row.xpath('./th/a')[0].text, row.xpath('./td')[0].text.replace(',', '')) for row in rows]
        assert actual == wanted
        rent = next(row for row in rows if row.xpath('./th/a')[0].text == 'Rent')
        href = rent.xpath('./th/a/@href')[0].split('#')[0]
        detail = html.fromstring(client.get(href).data)
        movements = detail.xpath('//section[@aria-labelledby="cash-flow-details"]//tbody/tr')
        assert len(movements) == 1
        amount = Decimal(movements[0].xpath('./td')[3].text.replace(',', ''))
        assert amount == (Decimal('25') if direction == 'inflow' else Decimal('-500'))
    exported = list(csv.reader(io.StringIO(client.get(URL + '&export=csv').data.decode())))
    operating = [row for row in exported if len(row) == 7 and row[0] == 'Operating activities']
    assert [(row[1], row[2]) for row in operating[:-1]] == [
        ('Salary', 'Inflow'), ('Rent', 'Inflow'), ('Groceries', 'Inflow'),
        ('Rent', 'Outflow'), ('Groceries', 'Outflow')]
    assert operating[-1] == ['Operating activities', 'TOTAL', '', '1035.00', '520.00', '515.00', '5']


def test_cash_flow_database_snapshot_stays_stable_and_blocks_writes(clean_ledger):
    with cash_flow_session() as report_session:
        assert report_session.execute(text('SHOW transaction_isolation')).scalar() == 'repeatable read'
        assert report_session.execute(text('SHOW transaction_read_only')).scalar() == 'on'
        assert report_session.query(JournalEntries).count() == 0
        post_pair(timestamp=START, debit='Chase Checking', credit='Salary', functional_amount='1.00')
        db.session.commit()
        assert report_session.query(JournalEntries).count() == 0
    with cash_flow_session() as report_session:
        assert report_session.query(JournalEntries).count() == 1
        with pytest.raises(DBAPIError):
            report_session.execute(text("UPDATE bookkeeping.accounts SET is_cash=is_cash"))
    # A pooled connection's next request must not inherit read-only mode.
    post_pair(timestamp=START, debit='Chase Checking', credit='Salary', functional_amount='2.00')
    db.session.commit()
    assert db.session.query(JournalEntries).count() == 2


def test_cash_flow_context_separates_card_settlement_and_cash_transfer(clean_ledger):
    checking, savings, card = bank(), bank(), bank(True)
    left = bank_transaction(checking, '-10.00')
    right = bank_transaction(savings, '10.00', day=11)
    payment = bank_transaction(checking, '-20.00', day=12)
    paid = bank_transaction(card, '20.00', day=13)
    db.session.commit()
    for a, b in ((left, right), (payment, paid)):
        reconciliation.match_transfer(a.id, b.id, reconciliation.digest(reconciliation.transfer_pair(a,b)),
                                      'Fixture owner', 'Confirmed corresponding account movement')
    db.session.commit()
    context = cash_flow_context(db.session, START, END)
    assert context['transfer_kinds'][left.id]['kind'] == 'cash_transfer'
    assert context['transfer_kinds'][right.id]['kind'] == 'cash_transfer'
    assert context['transfer_kinds'][payment.id]['kind'] == 'card_settlement'
    boundary = cash_flow_context(db.session, datetime(2026, 1, 11, tzinfo=timezone.utc), END)
    assert boundary['transfer_kinds'][right.id]['kind'] == 'cash_transfer_timing'
    right.functional_amount = right.source_amount = Decimal('9.99')
    db.session.commit()
    context = cash_flow_context(db.session, START, END)
    assert left.id not in context['transfer_kinds'] and right.id not in context['transfer_kinds']


def test_cash_flow_source_checks_do_not_hide_equal_offsetting_errors(clean_ledger):
    account = bank()
    first = bank_transaction(account, '-10.00', category='Rent')
    second = bank_transaction(account, '-10.00', category='Rent')
    first.functional_amount = first.source_amount = Decimal('9.00')
    second.functional_amount = second.source_amount = Decimal('11.00')
    db.session.commit()
    context = cash_flow_context(db.session, START, END)
    check = next(row for row in context['source_checks'] if row['account_id'] == account[0])
    assert check['difference'] == 0
    assert set(check['invalid_journal_ids']) == {first.id, second.id}
    assert context['issues']


def test_cash_flow_non_administrator_cannot_read_or_export(clean_ledger, client):
    viewer = Users(email='cash-report-' + uuid4().hex + '@example.com', active=True)
    db.session.add(viewer)
    db.session.commit()
    with client.session_transaction() as session:
        session['_user_id'] = str(viewer.id)
        session['_fresh'] = True
    assert client.get(URL).status_code == 403
    assert client.get(URL + '&export=csv').status_code == 403


def test_cash_flow_source_without_journal_remains_visible(clean_ledger):
    account = bank()
    fitid = uuid4().hex
    db.session.execute(text('''INSERT INTO ofx.stmttrn
        (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
        VALUES(:fitid,:aid,'OTHER',:when,-12.00,'UNPOSTED CASH')'''),
        dict(fitid=fitid, aid=account[0], when=datetime(2026, 1, 15, 12, tzinfo=timezone.utc)))
    identifier = source_identity(db.session, account[0], fitid)
    db.session.commit()
    context = cash_flow_context(db.session, START, END)
    check = next(row for row in context['source_checks'] if row['account_id'] == account[0])
    assert check['unposted_sources'] == [identifier]
    assert check['source_movement'] == Decimal('-12')
    assert check['booked_ofx_movement'] == 0 and check['difference'] == 12


def coverage(account, start, end):
    connection = Connections(source='cash-flow-test', currency='USD')
    db.session.add(connection)
    db.session.flush()
    response = ConnectionResponses(connection_id=connection.id, response='fixture only')
    db.session.add(response)
    db.session.flush()
    row = OFXFileImports(connection_id=connection.id, response_id=response.id,
        ofx_account_id=account[0], sha256=uuid4().hex + uuid4().hex,
        transaction_count=0, coverage_start=start, coverage_end=end,
        balance_amount=Decimal('0'), balance_asof=end)
    db.session.add(row)
    db.session.flush()


def test_cash_flow_period_coverage_cannot_hide_gap_before_beginning(clean_ledger, monkeypatch):
    account = bank()
    bank_transaction(account, '-10.00', category='Rent', day=10)
    coverage(account, START, datetime(2026, 1, 10, tzinfo=timezone.utc))
    coverage(account, datetime(2026, 1, 15, tzinfo=timezone.utc),
             datetime(2026, 1, 31, tzinfo=timezone.utc))
    reconciliation.import_statement(dict(account_id=account[0], start='2026-01-01',
        end='2026-01-31', opening='100', closing='90', movement='-10',
        verification='Fixture statement arithmetic'), b'%PDF-1.4 coverage fixture', 'Fixture')
    monkeypatch.setattr(reconciliation, 'account_ranges',
                        lambda aid: ([(date(2026, 1, 1), date(2026, 1, 31))], False))
    preview = reconciliation.opening_preview(account[0], date(2026, 1, 1))
    reconciliation.post_opening(account[0], date(2026, 1, 1), preview['digest'], 'Fixture owner')
    db.session.commit()
    context = cash_flow_context(db.session, datetime(2026, 1, 15, tzinfo=timezone.utc), END)
    period = next(row for row in context['coverage'] if row['account_id'] == account[0])
    beginning = next(row for row in context['opening_checks'] if row['account_id'] == account[0])
    assert period['complete']
    assert beginning['valid'] and not beginning['covers_start']
    assert beginning['coverage_gaps'] == [(date(2026, 1, 11), date(2026, 1, 14))]
    assert beginning['beginning_difference'] == 0


def test_cash_flow_csv_escapes_spreadsheet_labels(clean_ledger, client, admin_user):
    suspicious = '=HYPERLINK("https://example.invalid")'
    db.session.add(Subaccounts(name=suspicious, parent='Rent Expense'))
    db.session.flush()
    post_pair(timestamp=START, debit=suspicious, credit='Chase Checking', functional_amount='1.00')
    db.session.commit()
    client.post('/login/', data=admin_user)
    output = client.get(URL + '&export=csv')
    assert output.status_code == 200
    rows = list(csv.reader(io.StringIO(output.data.decode())))
    assert any(len(row) > 1 and row[1] == "'" + suspicious for row in rows)


def test_cash_flow_revalidates_opening_anchor_beyond_report_end(clean_ledger):
    account = bank()
    bank_transaction(account, '-10.00', category='Rent', day=10)
    coverage(account, START, datetime(2026, 1, 31, tzinfo=timezone.utc))
    reconciliation.import_statement(dict(account_id=account[0], start='2026-01-01',
        end='2026-01-20', opening='100', closing='90', movement='-10',
        verification='Fixture independent statement'), b'%PDF-1.4 later anchor', 'Fixture')
    preview = reconciliation.opening_preview(account[0], date(2026, 1, 1))
    reconciliation.post_opening(account[0], date(2026, 1, 1), preview['digest'], 'Fixture owner')
    db.session.commit()
    report_start = datetime(2026, 1, 11, tzinfo=timezone.utc)
    report_end = datetime(2026, 1, 16, tzinfo=timezone.utc)
    def beginning():
        context = cash_flow_context(db.session, report_start, report_end)
        return next(row for row in context['opening_checks'] if row['account_id'] == account[0])
    assert beginning()['covers_start']
    # Outside both report and the original anchor: does not change its proof.
    bank_transaction(account, '-2.00', category='Rent', day=25)
    db.session.commit()
    assert beginning()['covers_start']
    # Outside the report but INSIDE its opening calculation's anchor window.
    bank_transaction(account, '-3.00', category='Rent', day=18)
    db.session.commit()
    assert not beginning()['valid'] and not beginning()['covers_start']


def test_cash_flow_override_requires_unchanged_review_and_exact_journals(clean_ledger):
    account = bank()
    mapping = Mappings(source='ofx', keyword='HOUSEHOLD PAYROLL', match_mode='exact',
        review_required=True, negative_debit_subaccount_id='Bookkeeping Suspense',
        positive_credit_subaccount_id='Bookkeeping Suspense')
    db.session.add(mapping)
    db.session.flush()
    entry = bank_transaction(account, '-100.00', category='Bookkeeping Suspense',
                             mapping=mapping.id, description='HOUSEHOLD PAYROLL')
    review = reviews.create_review(mapping.id, 'Household employee wages, taxes and fees combined.',
                                   'Fixture owner confirmation', 'Fixture reviewer')
    db.session.flush()
    payload = dict(journal_ids=[entry.id], section='Operating',
        label='Household employee payments (combined wages, taxes and fees)',
        review_ids=[review.id],
        source_fingerprint=cash_flow_classification_fingerprint(db.session, [entry.id], [review.id]))
    reconciliation.evidence('cash_flow_classification', payload, 'Fixture report reviewer')
    db.session.commit()
    context = cash_flow_context(db.session, START, END)
    assert context['classifications'][entry.id]['section'] == 'Operating'
    assert entry.debit_subaccount == 'Bookkeeping Suspense'
    review.facts = json.dumps(dict(text='Purpose disputed', source='New owner facts', verified_by='Owner'))
    db.session.commit()
    context = cash_flow_context(db.session, START, END)
    assert entry.id not in context['classifications']
    assert context['issues']
