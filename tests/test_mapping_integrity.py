"""Mapping resolution is a policy decision, never a side effect of rule order."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pacioli.extensions import db
from pacioli.models import BankFeedAccounts, JournalEntries, JournalEntryEvents, Mappings
from pacioli.functions.mapping_resolution import MappingConflictError


@pytest.fixture
def mapping_case(clean_ledger):
    """Hide unrelated retained rule fixtures without changing their contents."""
    from pacioli.functions import ofx_functions
    marker = 'mapping-test-' + uuid4().hex
    prior = [row.id for row in db.session.query(Mappings).filter_by(source='ofx')]
    if prior:
        db.session.query(Mappings).filter(Mappings.id.in_(prior)).update(
            {'source': marker}, synchronize_session=False)
    accounts = []
    rules = []
    token = uuid4().hex[:10]

    def bank(name='Chase Checking'):
        aid = db.session.execute(text("INSERT INTO ofx.acctfrom (subclass,name) "
            "VALUES ('bankacctfrom',:name) RETURNING id"), {'name': name}).scalar()
        accounts.append(aid)
        db.session.add(BankFeedAccounts(ofx_account_id=aid, currency='USD'))
        return aid

    account = bank()

    def source(description=None, amount='-20', aid=None):
        aid = account if aid is None else aid
        fitid = uuid4().hex
        db.session.execute(text('''INSERT INTO ofx.stmttrn
            (fitid,acctfrom_id,trntype,dtposted,trnamt,name)
            VALUES (:fitid,:aid,'OTHER','2049-01-02 12:00:00',:amount,:name)'''),
            {'fitid': fitid, 'aid': aid, 'amount': Decimal(amount),
             'name': description or token + ' cafe'})
        from pacioli.functions.source_identity import source_identity
        return source_identity(db.session, aid, fitid)

    def rule(keyword=None, category='Restaurants', **kwargs):
        row = Mappings(source='ofx', keyword=keyword or token,
            positive_credit_subaccount_id=category,
            negative_debit_subaccount_id=category, **kwargs)
        db.session.add(row)
        db.session.flush()
        rules.append(row.id)
        return row

    yield SimpleNamespace(ofx=ofx_functions, token=token, bank=bank,
                          source=source, rule=rule)
    db.session.rollback()
    db.session.execute(text('TRUNCATE bookkeeping.opening_balances, bookkeeping.transfer_match_legs, '
        'bookkeeping.ofx_journal_sources, investments.brokerage_journal_sources, bookkeeping.journal_entries, '
        'bookkeeping.trial_balances, bookkeeping.journal_entry_events RESTART IDENTITY'))
    if rules:
        db.session.query(Mappings).filter(Mappings.id.in_(rules)).delete(synchronize_session=False)
    if prior:
        db.session.query(Mappings).filter(Mappings.id.in_(prior)).update(
            {'source': 'ofx'}, synchronize_session=False)
    for aid in accounts:
        db.session.execute(text('DELETE FROM bookkeeping.bank_feed_accounts WHERE ofx_account_id=:id'), {'id': aid})
        db.session.execute(text('DELETE FROM ofx.stmttrn WHERE acctfrom_id=:id'), {'id': aid})
        db.session.execute(text('DELETE FROM ofx.acctfrom WHERE id=:id'), {'id': aid})
    db.session.commit()


@pytest.mark.parametrize('reverse_insertion', [False, True])
@pytest.mark.parametrize('reverse_application', [False, True])
def test_exact_winner_independent_of_rule_insertion_and_application(mapping_case, reverse_insertion,
                                                                 reverse_application):
    case = mapping_case
    identifier = case.source()
    choices = [('broad', case.token, 'Groceries', 'keyword'),
               ('exact', case.token + ' cafe', 'Restaurants', 'exact')]
    if reverse_insertion:
        choices.reverse()
    rules = {name: case.rule(keyword, category, match_mode=mode)
             for name, keyword, category, mode in choices}
    db.session.commit()
    order = ['exact', 'broad'] if reverse_application else ['broad', 'exact']
    for name in order:
        case.ofx.apply_single_ofx_mapping(rules[name].id)
    entry = db.session.query(JournalEntries).filter_by(transaction_id=identifier).one()
    assert entry.debit_subaccount == 'Restaurants'
    assert entry.mapping_id == rules['exact'].id
    assert len(entry.mapping_decision['candidates']) == 2
    assert db.session.query(JournalEntryEvents).count() == 1


def test_equal_authority_conflict_rolls_back_entire_apply_all(mapping_case):
    case = mapping_case
    case.source(case.token + ' valid')
    conflict = case.source(case.token + ' cafe')
    case.rule(case.token, 'Groceries')
    case.rule(case.token + ' cafe', 'Restaurants')
    db.session.commit()
    with pytest.raises(MappingConflictError) as error:
        case.ofx.apply_all_mappings()
    assert error.value.source_id == conflict
    assert len(error.value.mapping_ids) == 2
    assert db.session.query(JournalEntries).count() == 0
    assert db.session.query(JournalEntryEvents).count() == 0


def test_apply_all_rolls_back_flushed_posting_if_later_post_fails(mapping_case, monkeypatch):
    case = mapping_case
    case.source(case.token + ' first')
    case.source(case.token + ' second')
    case.rule()
    db.session.commit()
    original = case.ofx.post_pair
    calls = []
    def injected_failure(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise RuntimeError('injected posting failure')
        return original(**kwargs)
    monkeypatch.setattr(case.ofx, 'post_pair', injected_failure)
    with pytest.raises(RuntimeError, match='injected'):
        case.ofx.apply_all_mappings()
    assert len(calls) == 2
    assert db.session.query(JournalEntries).count() == 0
    assert db.session.query(JournalEntryEvents).count() == 0
    assert db.session.execute(text('SELECT count(*) FROM bookkeeping.trial_balances')).scalar() == 0


def test_mapping_edit_cannot_race_resolution_and_posting(mapping_case, monkeypatch):
    case = mapping_case
    case.source()
    mapping = case.rule()
    db.session.commit()
    mapping_id = mapping.id
    original = case.ofx.post_pair
    checked = []

    def attempt_concurrent_edit(**kwargs):
        with db.engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(text("SET LOCAL lock_timeout = '100ms'"))
                with pytest.raises(DBAPIError) as error:
                    connection.execute(text('UPDATE admin.mappings '
                        'SET negative_debit_subaccount_id=\'Groceries\' WHERE id=:id'),
                        {'id': mapping_id})
                assert error.value.orig.pgcode == '55P03'
                checked.append(True)
            finally:
                transaction.rollback()
        return original(**kwargs)

    monkeypatch.setattr(case.ofx, 'post_pair', attempt_concurrent_edit)
    case.ofx.apply_all_mappings()
    assert checked == [True]
    assert db.session.query(JournalEntries).one().debit_subaccount == 'Restaurants'


def test_keyword_wildcards_are_literal_and_bank_account_scope_is_respected(mapping_case):
    case = mapping_case
    literal = case.source(case.token + '_100% shop')
    case.source(case.token + 'X100 other shop')
    other = case.bank('Chase Savings')
    case.source(case.token + '_100% shop', aid=other)
    mapping = case.rule(case.token + '_100% shop', negative_credit_subaccount_id='Chase Checking')
    db.session.commit()
    result = case.ofx.apply_single_ofx_mapping(mapping.id)
    assert result['posted'] == 1
    assert db.session.query(JournalEntries).one().transaction_id == literal


def test_provisional_exact_cannot_be_overridden_by_a_broad_expense_rule(mapping_case):
    case = mapping_case
    case.source()
    case.rule(case.token, 'Groceries')
    exact = case.rule(case.token + ' cafe', 'Transfer Clearing', match_mode='exact', review_required=True)
    db.session.commit()
    case.ofx.apply_all_mappings()
    entry = db.session.query(JournalEntries).one()
    assert entry.mapping_id == exact.id
    assert entry.debit_subaccount == 'Transfer Clearing'
    assert 'Provisional' in entry.memo


def test_reviewed_exact_wins_over_provisional_case_variant(mapping_case):
    case = mapping_case
    case.source()
    case.rule(case.token + ' cafe', 'Bookkeeping Suspense', match_mode='exact', review_required=True)
    reviewed = case.rule((case.token + ' cafe').upper(), 'Restaurants', match_mode='exact')
    db.session.commit()
    case.ofx.apply_all_mappings()
    entry = db.session.query(JournalEntries).one()
    assert entry.mapping_id == reviewed.id
    assert entry.debit_subaccount == 'Restaurants'


def test_charge_and_refund_use_their_reviewed_direction(mapping_case):
    case = mapping_case
    outgoing = case.source(amount='-20')
    incoming = case.source(amount='5')
    case.rule()
    db.session.commit()
    case.ofx.apply_all_mappings()
    entries = {entry.transaction_id: entry for entry in db.session.query(JournalEntries)}
    assert (entries[outgoing].debit_subaccount, entries[outgoing].credit_subaccount) == (
        'Restaurants', 'Chase Checking')
    assert (entries[incoming].debit_subaccount, entries[incoming].credit_subaccount) == (
        'Chase Checking', 'Restaurants')


def test_conflict_is_visible_in_frontend_and_posts_nothing(mapping_case, client, admin_user):
    case = mapping_case
    case.source()
    case.rule(case.token, 'Groceries')
    case.rule(case.token + ' cafe', 'Restaurants')
    db.session.commit()
    client.post('/login/', data=admin_user)
    response = client.post('/banking/transactions/apply-all-mappings/', follow_redirects=True)
    assert response.status_code == 200
    assert b'Mapping conflict' in response.data
    assert db.session.query(JournalEntries).count() == 0


def test_rule_edits_preserve_original_decision_and_replay_does_not_reclassify(mapping_case):
    case = mapping_case
    case.source()
    mapping = case.rule()
    db.session.commit()
    assert case.ofx.apply_all_mappings()['posted'] == 1
    entry = db.session.query(JournalEntries).one()
    snapshot = deepcopy(entry.mapping_decision)
    mapping.negative_debit_subaccount_id = 'Groceries'
    mapping.review_note = 'Future classification changed'
    db.session.commit()
    assert case.ofx.apply_all_mappings() == {'posted': 0, 'already_posted': 1}
    db.session.refresh(entry)
    assert entry.debit_subaccount == 'Restaurants'
    assert entry.mapping_decision == snapshot
    assert snapshot['candidates'][0]['negative_debit_subaccount_id'] == 'Restaurants'
    entry.mapping_decision = dict(snapshot, policy='rewritten')
    with pytest.raises(DBAPIError, match='cannot be changed'):
        db.session.commit()
    db.session.rollback()


def test_overlap_formatter_escapes_stored_text_and_attributes(app_context):
    from flask import current_app
    from pacioli.views.utilities import link_transaction_search_formatter
    payload = '<img src=x onerror=window.poc=1>'
    view = SimpleNamespace(url='" onmouseover="window.poc=2',
        get_delete_form=lambda: lambda: SimpleNamespace(
            csrf_token=SimpleNamespace(_value=lambda: '" unsafe')))
    model = SimpleNamespace(keyword_1=payload, mapping_id_1='" injected')
    with current_app.test_request_context('/'):
        rendered = str(link_transaction_search_formatter(view, None, model, 'keyword_1'))
    assert payload not in rendered
    assert '&lt;img' in rendered
    assert 'value="" injected' not in rendered
    assert 'value="" unsafe' not in rendered


def test_overlap_page_shows_conflicting_case_equivalent_exact_rules_once(mapping_case, client, admin_user):
    case = mapping_case
    case.source()
    first = case.rule(case.token + ' cafe', 'Restaurants', match_mode='exact')
    second = case.rule((case.token + ' cafe').upper(), 'Groceries', match_mode='exact')
    db.session.commit()
    rows = db.session.execute(text('SELECT mapping_id_1,mapping_id_2 FROM admin.mapping_overlaps')).all()
    assert [tuple(row) for row in rows] == [(first.id, second.id)]
    with pytest.raises(MappingConflictError):
        case.ofx.apply_all_mappings()
    client.post('/login/', data=admin_user)
    response = client.get('/mapping-overlaps/')
    assert response.status_code == 200
    assert (case.token + ' cafe').encode() in response.data
    assert (case.token + ' cafe').upper().encode() in response.data


def test_overlap_page_uses_literal_words_and_source_account_scope(mapping_case, client, admin_user):
    case = mapping_case
    case.source(case.token + 'X100 other shop')
    other_account = case.bank('Chase Savings')
    case.source(case.token + '_100% shop', aid=other_account)
    case.source(case.token + '_100% shop', amount='5', aid=other_account)
    broad = case.rule(case.token, 'Groceries')
    literal = case.rule(case.token + '_100% shop', 'Restaurants',
                       positive_debit_subaccount_id='Chase Checking',
                       negative_credit_subaccount_id='Chase Checking')
    db.session.commit()
    assert db.session.execute(text('SELECT count(*) FROM admin.mapping_overlaps')).scalar() == 0
    client.post('/login/', data=admin_user)
    response = client.get('/mapping-overlaps/')
    assert response.status_code == 200
    assert literal.keyword.encode() not in response.data
    case.source(case.token + '_100% shop')
    db.session.commit()
    rows = db.session.execute(text('SELECT mapping_id_1,mapping_id_2 FROM admin.mapping_overlaps')).all()
    assert [tuple(row) for row in rows] == [(broad.id, literal.id)]
    response = client.get('/mapping-overlaps/')
    assert response.status_code == 200
    assert literal.keyword.encode() in response.data


@pytest.mark.parametrize('description,keyword,mode,expected', [
    ('100% SHOP_ONE', '% shop_', 'keyword', True),
    ('100 PERCENT SHOPXONE', '% shop_', 'keyword', False),
    ('CAFE', 'cafe cafe', 'keyword', False),
    ('CAFE old CAFE', 'cafe cafe', 'keyword', True),
    ('north one south', ' south\tnorth ', 'keyword', False),
    ('north one south', ' north\tsouth\n', 'keyword', True),
    ('north one south', ' north\u00a0south\u202f', 'keyword', True),
    ('north one south', '\u00a0\u202f', 'keyword', False),
    ('CAFE', '  ', 'keyword', False),
    ('PINE CAFE', 'pine cafe', 'exact', True),
    ('PINE CAFETERIA', 'pine cafe', 'exact', False),
])
def test_database_and_python_description_matchers_agree(app_context, description, keyword, mode, expected):
    from pacioli.functions.mapping_resolution import description_matches
    mapping = SimpleNamespace(keyword=keyword, match_mode=mode)
    sql_result = db.session.execute(text(
        'SELECT admin.mapping_description_matches(:description,:keyword,:mode)'),
        {'description': description, 'keyword': keyword, 'mode': mode}).scalar()
    assert sql_result is expected
    assert description_matches(mapping, description) is expected
