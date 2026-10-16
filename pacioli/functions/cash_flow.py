"""Read-only, direct-method cash flows aggregated by PostgreSQL.

Amounts and accounting-day bounds are exact USD NUMERIC and UTC timestamptz.
The caller validates transfer/evidence context against the same read snapshot;
this module never guesses it from descriptions or writes classifications back.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json

from sqlalchemy import DateTime, String, bindparam, text


ZERO = Decimal('0')
PAGE_SIZE = 100
SECTIONS = (
    ('operating', 'Operating activities'),
    ('investing', 'Investing activities'),
    ('financing', 'Financing activities'),
    ('unclassified', 'Cash flows awaiting classification'),
    ('adjustments', 'Reconciliation adjustments'),
    ('internal', 'Internal cash transfers eliminated'),
)


# Every public query uses this same classification expression. Cash legs retain
# both sides of a cash-to-cash journal so each account still reconciles, while
# the consolidated external statement eliminates that journal.
_CTES = """
WITH chart AS (
    SELECT s.name, a.name AS account_name, a.cash_source,
           COALESCE(a.is_cash, false) AS is_cash
    FROM bookkeeping.subaccounts s
    LEFT JOIN bookkeeping.accounts a ON a.name=s.parent
), card_accounts AS (
    SELECT a.name FROM ofx.acctfrom a JOIN ofx.ccacctfrom c ON c.id=a.id
    UNION SELECT 'Credit Card'
), transfer_context AS (
    SELECT * FROM jsonb_to_recordset(CAST(:transfers AS jsonb))
        AS t(journal_id integer, kind text, match_id integer)
), review_context AS (
    SELECT * FROM jsonb_to_recordset(CAST(:overrides AS jsonb))
        AS r(journal_id integer, section text, label text, evidence_id integer)
), entries AS (
    SELECT j.id, j.timestamp, j.debit_subaccount, j.credit_subaccount,
           j.functional_amount, j.transaction_source, j.transaction_id,
           COALESCE(d.is_cash, false) AS debit_cash,
           COALESCE(c.is_cash, false) AS credit_cash,
           d.cash_source AS debit_activity, c.cash_source AS credit_activity,
           (d.account_name IS NULL OR c.account_name IS NULL) AS unknown_chart,
           t.kind AS transfer_kind, t.match_id,
           r.section AS review_section, r.label AS review_label, r.evidence_id
    FROM bookkeeping.journal_entries j
    LEFT JOIN chart d ON d.name=j.debit_subaccount
    LEFT JOIN chart c ON c.name=j.credit_subaccount
    LEFT JOIN transfer_context t ON t.journal_id=j.id
    LEFT JOIN review_context r ON r.journal_id=j.id
    WHERE j.timestamp < :end
), cash_legs AS (
    SELECT e.*, debit_subaccount AS cash_account,
           credit_subaccount AS counterpart, credit_activity AS activity,
           functional_amount AS amount
    FROM entries e WHERE debit_cash
    UNION ALL
    SELECT e.*, credit_subaccount AS cash_account,
           debit_subaccount AS counterpart, debit_activity AS activity,
           -functional_amount AS amount
    FROM entries e WHERE credit_cash
), classified AS (
    SELECT l.*,
        CASE
          WHEN debit_cash AND credit_cash THEN 'internal'
          WHEN transaction_source='statement_opening' THEN 'adjustments'
          WHEN transfer_kind='cash_transfer' THEN 'internal'
          WHEN transfer_kind='cash_transfer_timing' THEN 'adjustments'
          WHEN transfer_kind='investment_transfer' THEN 'investing'
          WHEN transfer_kind='card_settlement' OR counterpart IN (SELECT name FROM card_accounts)
            THEN 'unclassified'
          WHEN review_section IS NOT NULL THEN review_section
          WHEN counterpart IN ('Bookkeeping Suspense', 'Transfer Clearing', 'Amazon Suspense Account')
            THEN 'unclassified'
          WHEN activity IN ('Operating', 'Investing', 'Financing') THEN lower(activity)
          ELSE 'unclassified'
        END AS section,
        CASE
          WHEN debit_cash AND credit_cash THEN 'Cash-to-cash journals'
          WHEN transaction_source='statement_opening' THEN 'Opening balance basis adjustments'
          WHEN transfer_kind='cash_transfer' THEN 'Reviewed transfers between cash accounts'
          WHEN transfer_kind='cash_transfer_timing' THEN 'Internal transfer timing differences'
          WHEN transfer_kind='investment_transfer' THEN 'Transfers with brokerage accounts'
          WHEN transfer_kind='card_settlement' OR counterpart IN (SELECT name FROM card_accounts)
            THEN 'Credit-card settlements — allocation pending'
          WHEN review_section IS NOT NULL THEN review_label
          ELSE counterpart
        END AS label
    FROM cash_legs l
), period_flows AS (
    SELECT * FROM classified WHERE timestamp >= :start
)
"""


_SUMMARY = _CTES + """
, flow_summary AS (
    SELECT CASE WHEN GROUPING(label)=1 THEN 'section' ELSE 'flow' END AS record_type,
           section AS key, label,
           COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0) AS receipts,
           -COALESCE(SUM(amount) FILTER (WHERE amount < 0), 0) AS payments,
           COALESCE(SUM(amount), 0) AS net,
           NULL::numeric AS opening, NULL::numeric AS movement, NULL::numeric AS closing,
           count(*) AS entry_count
    FROM period_flows GROUP BY GROUPING SETS ((section, label), (section))
), direction_summary AS (
    SELECT CASE WHEN amount > 0 THEN 'inflow' ELSE 'outflow' END AS record_type,
           section AS key, label,
           COALESCE(SUM(amount) FILTER (WHERE amount > 0), 0) AS receipts,
           -COALESCE(SUM(amount) FILTER (WHERE amount < 0), 0) AS payments,
           SUM(amount) AS net,
           NULL::numeric AS opening, NULL::numeric AS movement, NULL::numeric AS closing,
           count(*) AS entry_count
    FROM period_flows WHERE amount <> 0
    GROUP BY section, label, (amount > 0)
), account_summary AS (
    SELECT CASE WHEN GROUPING(c.name)=1 THEN 'cash' ELSE 'account' END AS record_type,
           c.name AS key, NULL::text AS label,
           NULL::numeric AS receipts, NULL::numeric AS payments, NULL::numeric AS net,
           COALESCE(SUM(l.amount) FILTER (WHERE l.timestamp < :start), 0) AS opening,
           COALESCE(SUM(l.amount) FILTER (WHERE l.timestamp >= :start), 0) AS movement,
           COALESCE(SUM(l.amount), 0) AS closing,
           count(l.id) FILTER (WHERE l.timestamp >= :start) AS entry_count
    FROM chart c LEFT JOIN cash_legs l ON l.cash_account=c.name
    WHERE c.is_cash AND (l.id IS NOT NULL OR EXISTS (
        SELECT 1 FROM ofx.acctfrom a WHERE a.name=c.name))
    GROUP BY GROUPING SETS ((c.name), ())
), reconciliation AS (
    SELECT 'reconciliation'::text AS record_type, NULL::text AS key, NULL::text AS label,
           COALESCE(SUM(amount) FILTER (WHERE section NOT IN ('internal','adjustments')),0) AS receipts,
           COALESCE(SUM(amount) FILTER (WHERE section='adjustments'),0) AS payments,
           COALESCE(SUM(amount) FILTER (WHERE section='internal'),0) AS net,
           (SELECT closing-opening FROM account_summary WHERE record_type='cash')
             - COALESCE(SUM(amount) FILTER (WHERE section <> 'internal'),0) AS opening,
           NULL::numeric AS movement, NULL::numeric AS closing,
           0::bigint AS entry_count FROM period_flows
), metrics AS (
    SELECT count(*) AS journal_count,
           count(*) FILTER (WHERE NOT debit_cash AND NOT credit_cash) AS noncash_count,
           count(*) FILTER (WHERE debit_cash AND credit_cash) AS direct_internal_count,
           count(*) FILTER (WHERE debit_cash OR credit_cash) AS cash_count,
           count(*) FILTER (WHERE unknown_chart) AS unknown_chart_count
    FROM entries WHERE timestamp >= :start
), metric_rows AS (
    SELECT 'metric'::text AS record_type, m.key, NULL::text AS label,
           NULL::numeric AS receipts, NULL::numeric AS payments, NULL::numeric AS net,
           NULL::numeric AS opening, NULL::numeric AS movement, NULL::numeric AS closing,
           m.value AS entry_count
    FROM metrics CROSS JOIN LATERAL (VALUES
        ('journals',journal_count), ('noncash_journals',noncash_count),
        ('direct_internal_journals',direct_internal_count), ('cash_journals',cash_count),
        ('unknown_chart_journals',unknown_chart_count)) m(key,value)
)
SELECT * FROM (
    SELECT * FROM flow_summary UNION ALL SELECT * FROM direction_summary
    UNION ALL SELECT * FROM account_summary UNION ALL SELECT * FROM reconciliation
    UNION ALL SELECT * FROM metric_rows
) summary
ORDER BY record_type, key,
         CASE WHEN record_type='outflow' THEN payments ELSE net END DESC NULLS LAST,
         label
"""


_DETAILS = _CTES + """
, selected AS (
    SELECT id AS journal_id, timestamp, cash_account, counterpart, amount,
           section, label, transaction_source, transaction_id,
           evidence_id, match_id
    FROM period_flows WHERE section=:section AND (:label IS NULL OR label=:label)
      AND (:direction IS NULL OR (:direction='inflow' AND amount > 0)
                              OR (:direction='outflow' AND amount < 0))
), page_rows AS (
    SELECT * FROM selected ORDER BY timestamp DESC, journal_id DESC, cash_account
    LIMIT :page_size OFFSET :offset
)
SELECT page_rows.*, total.total,
       COALESCE(NULLIF(concat(source.name, source.memo), ''), journal.memo) AS source_description
FROM (SELECT count(*) AS total FROM selected) total
LEFT JOIN page_rows ON true
LEFT JOIN bookkeeping.journal_entries journal ON journal.id=page_rows.journal_id
LEFT JOIN bookkeeping.ofx_source_identities identity
  ON page_rows.transaction_source='ofx' AND identity.public_id=page_rows.transaction_id
LEFT JOIN ofx.stmttrn source
  ON source.acctfrom_id=identity.acctfrom_id AND source.fitid=identity.fitid
ORDER BY page_rows.timestamp DESC, page_rows.journal_id DESC, page_rows.cash_account
"""


def _identifier(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('A positive integer evidence or journal identifier is required')
    return value


def _parameters(start, end, transfer_kinds, overrides):
    for bound in (start, end):
        if not isinstance(bound, datetime) or bound.tzinfo is None or bound.utcoffset() is None:
            raise ValueError('Cash-flow bounds must be timezone-aware datetimes')
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if start >= end:
        raise ValueError('Cash-flow start must precede the exclusive end')
    transfers, reviews = [], []
    for journal_id, item in (transfer_kinds or {}).items():
        if item.get('kind') not in ('cash_transfer', 'cash_transfer_timing', 'card_settlement',
                                    'investment_transfer'):
            raise ValueError('Unknown validated transfer kind')
        transfers.append(dict(journal_id=_identifier(journal_id), kind=item['kind'],
                              match_id=_identifier(item['match_id'])))
    for journal_id, item in (overrides or {}).items():
        section = str(item.get('section', '')).lower()
        label = item.get('label')
        if section not in ('operating', 'investing', 'financing'):
            raise ValueError('An evidence-backed activity section is required')
        if not isinstance(label, str) or not label.strip() or len(label) > 240:
            raise ValueError('A bounded evidence-backed cash-flow label is required')
        reviews.append(dict(journal_id=_identifier(journal_id), section=section,
                            label=label, evidence_id=_identifier(item['evidence_id'])))
    return dict(start=start, end=end, transfers=json.dumps(transfers), overrides=json.dumps(reviews))


def _query(sql):
    return text(sql).bindparams(bindparam('start', type_=DateTime(timezone=True)),
                               bindparam('end', type_=DateTime(timezone=True)))


def cash_flow_report(session, start, end_exclusive, transfer_kinds=None, overrides=None):
    """Return exact gross flows, per-cash-account rollforwards and exclusions.

    ``sections`` includes adjustments/internal disclosures. Counts describe cash
    legs, including both sides of a cash-to-cash journal; ``counts`` separately
    counts distinct journals. Internal gross amounts are disclosed but excluded
    from the external cash-flow subtotal. The three activity
    sections and unclassified comprise external cash flows; adjustments bridge
    those flows to the actual ledger cash change. ``difference`` must be zero.
    Each section's ``inflows`` and ``outflows`` rank gross category amounts
    independently, largest first. A category with refunds and payments appears
    on both sides; ``rows`` and section totals still count its cash legs once.
    Transfer classifications and overrides must already be evidence-validated.
    No ledger, mapping, cache or evidence record is changed.
    """
    params = _parameters(start, end_exclusive, transfer_kinds, overrides)
    sections = {key: dict(key=key, label=label, receipts=ZERO, payments=ZERO,
                          net=ZERO, count=0, rows=[], inflows=[], outflows=[])
                for key, label in SECTIONS}
    report = dict(sections=list(sections.values()), accounts=[], counts={},
                  opening_cash=ZERO, closing_cash=ZERO, net_cash_change=ZERO,
                  classified_cash_change=ZERO, adjustments=ZERO, difference=ZERO,
                  internal_net=ZERO)
    for row in session.execute(_query(_SUMMARY), params):
        if row.record_type in ('flow', 'section', 'inflow', 'outflow'):
            values = dict(receipts=row.receipts, payments=row.payments, net=row.net,
                          count=row.entry_count)
            if row.record_type == 'flow':
                sections[row.key]['rows'].append(dict(label=row.label, **values))
            elif row.record_type in ('inflow', 'outflow'):
                amount = row.receipts if row.record_type == 'inflow' else row.payments
                sections[row.key][row.record_type + 's'].append(
                    dict(label=row.label, amount=amount, **values))
            else:
                sections[row.key].update(values)
        elif row.record_type == 'account':
            report['accounts'].append(dict(name=row.key, opening=row.opening,
                movement=row.movement, closing=row.closing, count=row.entry_count))
        elif row.record_type == 'cash':
            report.update(opening_cash=row.opening, closing_cash=row.closing,
                          net_cash_change=row.movement)
        elif row.record_type == 'reconciliation':
            report.update(classified_cash_change=row.receipts, adjustments=row.payments,
                          internal_net=row.net, difference=row.opening)
        elif row.record_type == 'metric':
            report['counts'][row.key] = row.entry_count
    for key in ('operating', 'investing', 'financing', 'unclassified'):
        report[key] = sections[key]['net']
    return report


def cash_flow_details(session, start, end_exclusive, section, label=None, page=1,
                      transfer_kinds=None, overrides=None, direction=None):
    """Return at most 100 cash legs using the identical SQL classification.

    Source descriptions are joined only to the limited page, using unique
    canonical source identities. They are display evidence, never classifiers.
    Manual entries and blank bank descriptions fall back to the journal memo.
    An optional inflow/outflow direction filters signed cash legs before both
    counting and pagination; returned detail amounts retain their ledger sign.
    """
    params = _parameters(start, end_exclusive, transfer_kinds, overrides)
    if section not in dict(SECTIONS) or (label is not None and (
            not isinstance(label, str) or len(label) > 240)):
        raise ValueError('A report section and bounded label are required')
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 1000000:
        raise ValueError('A positive bounded page number is required')
    if direction not in (None, 'inflow', 'outflow'):
        raise ValueError('Cash-flow direction must be inflow or outflow')
    params.update(section=section, label=label, direction=direction,
                  page_size=PAGE_SIZE, offset=(page-1)*PAGE_SIZE)
    rows, total = [], 0
    query = _query(_DETAILS).bindparams(bindparam('label', type_=String()),
                                      bindparam('direction', type_=String()))
    for row in session.execute(query, params):
        values = dict(row._mapping)
        total = values.pop('total')
        if values['journal_id'] is not None:
            rows.append(values)
    return dict(rows=rows, total=total, page=page, page_size=PAGE_SIZE, direction=direction,
                pages=max(1, (total+PAGE_SIZE-1)//PAGE_SIZE))
