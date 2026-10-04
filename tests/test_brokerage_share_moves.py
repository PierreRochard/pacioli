"""Schwab cash journals, in-account sweeps, offsetting share moves and memo rules (synthetic)."""
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions import reconciliation as r
from pacioli.functions import share_move_advisor as advisor
from pacioli.functions.brokerage_accounts import register_account
from pacioli.functions.brokerage_holdings import quantities_as_of
from pacioli.functions.brokerage_posting import post, preview
from pacioli.functions.suspense_advisor import PROFILES


D = Decimal
NOTICE = 'CASH INTEREST ACCRUAL NOTICE'


@pytest.fixture
def account(clean_ledger):
    account_id = register_account(db.session, provider='schwab', label='Moves', masked_number='654',
                                  opened_on=date(2026, 1, 2), reviewer='owner@example.com')
    row = r.evidence('schwab_source', {'fixture': uuid4().hex}, 'owner@example.com')
    export_id = db.session.execute(text('''INSERT INTO investments.brokerage_exports
        (account_id, kind, format, evidence_id, sha256, row_count, state, reviewer)
        VALUES (:account, 'transactions', 'json', :evidence, :sha, 0, 'active', 'owner@example.com')
        RETURNING id'''), dict(account=account_id, evidence=row.id, sha=uuid4().hex * 2)).scalar()
    db.session.commit()
    return account_id, export_id


def activity(account, day, action, amount=None, symbol=None, quantity=None, price=None, description=''):
    account_id, export_id = account
    source = db.session.execute(text('''INSERT INTO investments.brokerage_sources(account_id, kind)
        VALUES (:account, 'activity') RETURNING source_id, public_id'''), {'account': account_id}).first()
    db.session.execute(text('''INSERT INTO investments.brokerage_activity
        (source_id, account_id, posted_on, action, symbol, description, quantity, price, amount,
         economic_digest, day_ordinal, first_export_id)
        VALUES (:source, :account, :day, :action, :symbol, :description, :quantity, :price, :amount,
                :digest, 1, :export)'''),
        dict(source=source.source_id, account=account_id, day=day, action=action, symbol=symbol,
             description=description, quantity=quantity, price=price, amount=amount,
             digest=sha256(uuid4().bytes).hexdigest(), export=export_id))
    return source


def statuses():
    return {row['public_id']: (row['status'], row['reason']) for row in preview(db.session)[1]}


class FakeModel:
    """Stands in for the pinned local model; records requests, answers from a script."""
    def __init__(self, answer):
        self.answer, self.requests, self.loaded = answer, [], False

    def resources(self):
        pass

    def api(self, path, data=None):
        if path == 'ps':
            return {'models': [{'name': PROFILES[advisor.PROFILE][0], 'digest': PROFILES[advisor.PROFILE][1]}]}
        self.requests.append(data)
        user = json.loads(data['messages'][1]['content'])
        answer = dict(self.answer, signature=user['signature'])
        return {'message': {'content': json.dumps(answer)}, 'done_reason': 'stop'}


def test_cash_journals_are_transfers_and_in_account_sweeps_can_be_matched(account):
    sweep_in = activity(account, date(2026, 1, 30), 'Journaled Shares', '250.00', description='BANK SWEEP DEPOSIT')
    sweep_out = activity(account, date(2026, 1, 30), 'Internal Transfer', '-250.00', description='BANK SWEEP')
    later = activity(account, date(2026, 2, 3), 'Journaled Shares', '-250.00', description='SWEEP REDEMPTION')
    db.session.commit()
    assert {statuses()[s.public_id][0] for s in (sweep_in, sweep_out, later)} == {'ready'}
    summary, _ = preview(db.session)
    post(summary['digest'], 'owner@example.com', 'Synthetic sweeps')
    pairs = r.transfer_candidates()
    # Only the same-day pair: one account's sweeps days apart are not an internal move.
    assert len(pairs) == 1 and pairs[0]['internal_move'] and pairs[0]['unique']
    left, right = (leg['journal_id'] for leg in pairs[0]['legs'])
    r.match_transfer(left, right, pairs[0]['digest'], 'owner@example.com', 'Sweep inside the account')
    db.session.commit()
    assert {left, right} <= r.resolved_transfer_ids(db.session)
    # Bank legs keep the different-account rule.
    bank = dict(account_id=7, date='2026-01-30T00:00:00+00:00')
    assert not r.internal_move(bank, dict(bank))


def test_offsetting_moves_post_nothing_but_exchanges_need_review(account):
    activity(account, date(2026, 1, 5), 'Buy', '-1000.00', 'AAA', '10', '100.00')
    out = activity(account, date(2026, 3, 2), 'Journaled Shares', None, 'AAA', '-10', description='TRANSFER OF AAA')
    back = activity(account, date(2026, 3, 2), 'Internal Transfer', None, 'AAA', '10', description='AAA FUND')
    # A different symbol in exchange could change cost and quantities: never assumed.
    gone = activity(account, date(2026, 3, 9), 'Journaled Shares', None, 'AAA', '-10', description='TRANSFER OF AAA')
    other = activity(account, date(2026, 3, 9), 'Internal Transfer', None, 'BBB', '9.5', description='BBB FUND')
    partly = activity(account, date(2026, 3, 9), 'Internal Transfer', None, 'AAA', '4', description='AAA FUND')
    db.session.commit()
    found = statuses()
    assert found[out.public_id] == found[back.public_id] == ('quantity_only', None)
    assert {found[s.public_id] for s in (gone, other, partly)} == {('unsupported', 'share_move_needs_review')}
    assert quantities_as_of(db.session, account[0], date(2026, 3, 2)) == {'AAA': D('10')}


def test_memo_rule_needs_a_valid_proposal_and_owner_approval(account):
    memos = [activity(account, day, 'Journaled Shares', None, 'ZZZZ12', quantity, description=NOTICE)
             for day, quantity in ((date(2026, 1, 31), '0.37'), (date(2026, 2, 28), '0.41'))]
    db.session.commit()
    assert statuses()[memos[0].public_id] == ('unsupported', 'share_move_needs_review')
    model = FakeModel({'classification': 'memo', 'reason': 'Accrual notice; never held or priced.'})
    assert advisor.propose(db.session, model) == {'proposed': 1, 'class:memo': 1}
    assert advisor.propose(db.session, model) == {'already_proposed': 1}
    db.session.commit()
    sent = json.loads(model.requests[0]['messages'][1]['content'])
    assert sent['facts']['row_count'] == 2 and sent['facts']['share_of_rows_on_month_end'] == 1.0
    (group,) = advisor.pending(db.session)
    assert group['approvable']
    with pytest.raises(ValueError):
        advisor.approve(db.session, group['proposal'].id, 'owner@example.com', '')
    db.session.rollback()
    advisor.approve(db.session, group['proposal'].id, 'owner@example.com', 'Monthly interest notice')
    db.session.commit()
    assert {statuses()[m.public_id] for m in memos} == {('quantity_only', None)}
    assert advisor.pending(db.session) == []
    assert quantities_as_of(db.session, account[0], date(2026, 3, 31)) == {}
    # A later priced row contradicts the rule instead of being silently ignored.
    activity(account, date(2026, 3, 2), 'Qualified Dividend', '1.00', 'ZZZZ12')
    db.session.commit()
    assert statuses()[memos[0].public_id] == ('not_ready', 'memo_rule_contradicted')


def test_model_cannot_choose_what_the_facts_forbid(account):
    activity(account, date(2026, 1, 5), 'Buy', '-50.00', 'YYY', '1', '50.00')
    activity(account, date(2026, 2, 27), 'Journaled Shares', None, 'YYY', '2', description='YYY FUND')
    db.session.commit()
    advisor.propose(db.session, FakeModel({'classification': 'memo', 'reason': 'Looks informational.'}))
    db.session.commit()
    (group,) = advisor.pending(db.session)
    assert group['proposal'].classification is None and not group['approvable']
    assert group['proposal'].validation_errors == ['classification_contradicts_facts']
    with pytest.raises(ValueError):
        advisor.approve(db.session, group['proposal'].id, 'owner@example.com', 'No effect')
    db.session.rollback()
    facts = dict(group['facts'])
    assert advisor.allowed(facts) == {'external_in', 'corporate_action', 'unclear'}
    assert advisor.validate({'signature': 'x', 'classification': 'unclear', 'reason': 'r'}, group) == ['wrong_signature']
    assert advisor.validate({'classification': 'unclear'}, group) == ['invalid_fields']


def test_share_move_page_requires_login_and_csrf(account, client, admin_user, app):
    activity(account, date(2026, 1, 31), 'Journaled Shares', None, 'ZZZZ12', '0.37', description=NOTICE)
    db.session.commit()
    assert client.get('/reconciliation/schwab/share-moves/').status_code == 302
    client.post('/login/', data=admin_user)
    response = client.get('/reconciliation/schwab/share-moves/')
    assert response.status_code == 200 and b'Not asked yet' in response.data
    assert response.headers['Cache-Control'] == 'no-store'
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        assert client.post('/reconciliation/schwab/share-moves/', data={}).status_code == 400
    finally:
        app.config['WTF_CSRF_ENABLED'] = False
