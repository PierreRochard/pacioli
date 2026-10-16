"""Validated personal-ledger postings. Amounts are exact decimal quantities."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from sqlalchemy import text, func

from pacioli.extensions import db
from pacioli.models import BankFeedAccounts, JournalEntries


PRECISION = {'USD': Decimal('0.01'), 'EUR': Decimal('0.01'),
             'BTC': Decimal('0.00000001')}


def ensure_ofx_usd(transaction_id):
    rows = db.session.execute(text(
        'SELECT id,account_id,account,date,amount FROM ofx.transactions WHERE id = :id'),
        {'id': transaction_id}).fetchall()
    if len(rows) != 1:
        raise ValueError('OFX source transaction does not exist or is ambiguous')
    account = db.session.get(BankFeedAccounts, rows[0].account_id)
    if account is None or account.currency != 'USD':
        raise ValueError('OFX account requires reviewed USD currency before posting')
    from pacioli.functions.accounting_report import chart_accounts
    if chart_accounts(db.session).get(rows[0].account, (None,))[0] not in ('Assets', 'Liabilities'):
        raise ValueError('OFX source account must be a reviewed balance-sheet account')
    return rows[0]


def validate_import_posting(*, timestamp, debit, credit, amount, source_amount,
                            source_currency, transaction_source, transaction_id,
                            allocation_key='0'):
    """Conserve imported cash movements; allocations may be staged incrementally.

    A completed batch is checked independently by source_differences. The shared
    ledger lock prevents two writers from each consuming the same remaining sum.
    OFX uses its exact normalized UTC instant; Amazon has only a shipment day.
    """
    if transaction_source == 'schwab':
        return validate_brokerage_posting(timestamp=timestamp, debit=debit, credit=credit,
                                          amount=amount, source_amount=source_amount,
                                          source_currency=source_currency,
                                          transaction_id=transaction_id, allocation_key=allocation_key)
    if transaction_source not in ('ofx', 'amazon'):
        return
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
    from pacioli.functions.accounting_report import utc_timestamp, chart_accounts
    if transaction_source == 'ofx':
        row = ensure_ofx_usd(transaction_id)
        expected = row.amount
        if row.date is None or timestamp != utc_timestamp(row.date):
            raise ValueError('Posting time must preserve the exact OFX source timestamp')
        if expected is None or not expected.is_finite() or not expected:
            raise ValueError('OFX source requires a finite nonzero amount')
        bank_side, other_side = (debit, credit) if expected > 0 else (credit, debit)
        if bank_side != row.account or other_side == row.account:
            raise ValueError('Posting must preserve the OFX bank account and signed movement')
    else:
        from pacioli.models import AmazonItems, AmazonEvidenceBundles
        import json
        item = db.session.get(AmazonItems, int(transaction_id)) if str(transaction_id).isdigit() else None
        if item is None:
            raise ValueError('Amazon source item does not exist')
        if item.shipment_date is None or timestamp.date() != item.shipment_date:
            raise ValueError('Posting must preserve the Amazon shipment date in UTC')
        expected = item.item_total
        if item.currency != 'USD' or expected is None or not expected.is_finite() or not expected:
            raise ValueError('Amazon source requires a finite nonzero USD amount')
        settlement = credit if expected > 0 else debit
        element, cash = chart_accounts(db.session).get(settlement, (None, False))
        if element != 'Liabilities' and not cash:
            raise ValueError('Amazon settlement account must be a liability or cash')
        for bundle in db.session.query(AmazonEvidenceBundles).filter(
                AmazonEvidenceBundles.transaction_id.isnot(None)):
            if any(line['order_id'] == item.order_id for line in json.loads(bundle.details)['lines']):
                raise ValueError('Amazon item already supports a reconciled bank allocation')
    if source_currency != 'USD' or source_amount != amount:
        raise ValueError('Imported USD amounts cannot be relabeled or converted')
    booked = db.session.query(func.coalesce(func.sum(JournalEntries.functional_amount), 0)).filter_by(
        transaction_source=transaction_source, transaction_id=transaction_id).scalar()
    if booked + amount > abs(expected):
        raise ValueError('Allocations exceed the imported source amount')


def validate_brokerage_posting(*, timestamp, debit, credit, amount, source_amount,
                               source_currency, transaction_id, allocation_key):
    """A brokerage leg must be exactly the planned leg for its allocation key."""
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
    from pacioli.functions.brokerage_plan import legs_for
    source_id = db.session.execute(text('SELECT source_id FROM investments.brokerage_sources '
                                        'WHERE public_id=:id'), {'id': transaction_id}).scalar()
    if source_id is None:
        raise ValueError('Brokerage source does not exist')
    if source_currency != 'USD' or source_amount != amount:
        raise ValueError('Imported USD amounts cannot be relabeled or converted')
    planned = {leg.key: leg for leg in legs_for(db.session, source_id)}
    leg = planned.get(allocation_key)
    if leg is None or (leg.debit, leg.credit, leg.amount, leg.timestamp) != (debit, credit, amount, timestamp):
        raise ValueError('Posting differs from the planned brokerage leg')


def decimal_amount(value, currency):
    if currency not in PRECISION:
        raise ValueError('Unsupported currency: {}'.format(currency))
    if isinstance(value, float):
        raise TypeError('Accounting amounts must be Decimal or decimal strings')
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError):
        raise ValueError('Invalid accounting amount')
    if not amount.is_finite() or amount <= 0:
        raise ValueError('Accounting amounts must be positive and finite')
    if amount != amount.quantize(PRECISION[currency]):
        raise ValueError('{} amounts exceed supported precision'.format(currency))
    return amount


def post_pair(*, timestamp, debit, credit, functional_amount,
              transaction_source=None, transaction_id=None, allocation_key='0',
              source_amount=None, source_currency='USD', conversion_rate=None,
              conversion_source=None, conversion_date=None, mapping_id=None,
              memo=None, mapping_decision=None):
    """Add a balanced debit/credit pair to the current transaction.

    The caller commits the transaction, allowing an entire import to roll back.
    A foreign-currency posting records its conversion evidence, including the
    USD value of one source unit. The database repeats these checks for raw SQL.
    """
    # All service writers share the lock order used by import and close.
    # Raw maintenance writes must run while application writers are quiesced.
    db.session.execute(text("SET LOCAL lock_timeout = '10s'"))
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
    functional = decimal_amount(functional_amount, 'USD')
    if source_currency != 'USD' and source_amount is None:
        raise ValueError('Foreign currency requires an explicit source amount')
    source = decimal_amount(source_amount if source_amount is not None else functional,
                            source_currency)
    if not debit or not credit or not isinstance(timestamp, datetime) or (
            timestamp.tzinfo is None or timestamp.utcoffset() is None):
        raise ValueError('UTC-aware posting time and both subaccounts are required')
    if debit == credit:
        raise ValueError('Debit and credit subaccounts must differ')
    timestamp = timestamp.astimezone(timezone.utc)
    if (transaction_source is None) != (transaction_id is None):
        raise ValueError('Source and source transaction ID must be provided together')
    if not allocation_key:
        raise ValueError('Allocation key is required')
    if source_currency == 'USD':
        if source != functional or any(value is not None for value in
                                       (conversion_rate, conversion_source, conversion_date)):
            raise ValueError('USD source amount must equal functional amount')
        rate = None
    else:
        if isinstance(conversion_rate, float):
            raise TypeError('Conversion rate must be Decimal or a decimal string')
        try:
            rate = Decimal(conversion_rate) if conversion_rate is not None else None
        except (InvalidOperation, TypeError):
            raise ValueError('Invalid conversion rate')
        if rate is not None and (not rate.is_finite() or rate <= 0):
            raise ValueError('Conversion rate must be positive and finite')
        if (rate is None or not isinstance(conversion_source, str) or
                not conversion_source.strip() or
                not isinstance(conversion_date, date)):
            raise ValueError('Foreign currency requires a rate, source, and date')
        if (source * rate).quantize(PRECISION['USD'], rounding=ROUND_HALF_UP) != functional:
            raise ValueError('Functional amount does not match conversion evidence')
    validate_import_posting(timestamp=timestamp, debit=debit, credit=credit,
                            amount=functional, source_amount=source,
                            source_currency=source_currency,
                            transaction_source=transaction_source, transaction_id=transaction_id,
                            allocation_key=allocation_key)
    if mapping_id is not None and mapping_decision is None:
        from pacioli.functions.mapping_resolution import default_mapping_decision
        mapping_decision = default_mapping_decision(mapping_id)
    entry = JournalEntries(timestamp=timestamp, debit_subaccount=debit,
                           credit_subaccount=credit, functional_amount=functional,
                           functional_currency='USD', source_amount=source,
                           source_currency=source_currency, conversion_rate=rate,
                           conversion_source=conversion_source,
                           conversion_date=conversion_date,
                           transaction_source=transaction_source,
                           transaction_id=transaction_id,
                           allocation_key=allocation_key, mapping_id=mapping_id,
                           memo=memo, mapping_decision=mapping_decision)
    db.session.add(entry)
    return entry


def post_allocations(*, timestamp, source, transaction_id, fixed_account,
                     allocations, total, variable_side='debit'):
    """Split one USD source amount among several expense or income accounts.

    ``allocations`` is a sequence of (stable_key, variable_account, amount).
    The caller commits; a retry should first look up the source transaction.
    """
    if variable_side not in ('debit', 'credit'):
        raise ValueError('Variable side must be debit or credit')
    expected = decimal_amount(total, 'USD')
    if source == 'ofx' and expected != abs(ensure_ofx_usd(transaction_id).amount):
        raise ValueError('Allocations do not equal the imported source total')
    parts = [(key, account, decimal_amount(amount, 'USD'))
             for key, account, amount in allocations]
    if not parts or len({key for key, _, _ in parts}) != len(parts):
        raise ValueError('Allocations require distinct stable keys')
    if sum((amount for _, _, amount in parts), Decimal('0')) != expected:
        raise ValueError('Allocations do not equal source total')
    def pair(account):
        return ((account, fixed_account) if variable_side == 'debit'
                else (fixed_account, account))

    existing = (db.session.query(JournalEntries)
                .filter_by(transaction_source=source, transaction_id=transaction_id)
                .all())
    if existing:
        recorded = {(row.allocation_key, row.debit_subaccount,
                     row.credit_subaccount, row.functional_amount)
                    for row in existing}
        desired = {(key, *pair(account), amount) for key, account, amount in parts}
        if (recorded != desired or len(existing) != len(parts) or
                any(row.timestamp != timestamp or row.source_currency != 'USD' or
                    row.source_amount != row.functional_amount for row in existing)):
            raise ValueError('A different allocation already exists for this source')
        return existing
    return [post_pair(timestamp=timestamp, debit=pair(account)[0],
                      credit=pair(account)[1],
                      functional_amount=amount, transaction_source=source,
                      transaction_id=transaction_id, allocation_key=key)
            for key, account, amount in parts]


def reverse_entry(entry_id, *, timestamp, reason):
    """Create an idempotent, linked compensating entry without erasing history."""
    if not reason or not reason.strip():
        raise ValueError('A reversal reason is required')
    original = db.session.get(JournalEntries, entry_id)
    if original is None:
        raise ValueError('Journal entry does not exist')
    if timestamp < original.timestamp:
        raise ValueError('Reversal cannot predate the original')
    existing = (db.session.query(JournalEntries)
                .filter_by(transaction_source='reversal', transaction_id=str(entry_id))
                .first())
    if existing is not None:
        return existing
    db.session.execute(text("SELECT set_config('pacioli.reason', :reason, true)"),
                       {'reason': reason.strip()})
    return post_pair(timestamp=timestamp, debit=original.credit_subaccount,
                     credit=original.debit_subaccount,
                     functional_amount=original.functional_amount,
                     source_amount=original.source_amount,
                     source_currency=original.source_currency,
                     conversion_rate=original.conversion_rate,
                     conversion_source=original.conversion_source,
                     conversion_date=original.conversion_date,
                     transaction_source='reversal', transaction_id=str(entry_id),
                     memo='Reverses journal entry {}: {}'.format(entry_id, reason.strip()))
