# Consolidated direct-method cash flows

Accounting → **Cash Flows**, at `/cash-flows/`, reports the cash accounts recorded
in Pacioli. Its default is the trailing year through yesterday. Inclusive `start`
and `end` query parameters make a period shareable; accounting-day boundaries are
UTC, consistent with the ledger. The requested initial statement is
`/cash-flows/?start=2025-09-27&end=2026-09-26`.

The page provides gross receipts, gross payments, net activity, source drilldowns,
CSV export, account balances, coverage and source checks. Administrator access is
required for HTML and CSV. Responses prohibit browser/proxy caching. Nothing in a
report request posts a journal, applies a mapping, imports data, or invokes Qwen.

Each activity section lists inflows and outflows separately, largest gross amount
first within each direction. A category with both payments and refunds appears on
both sides with only that side's amounts and movement count. Category/subtotal
drilldowns retain the selected direction across pages. Net cash flow is shown at
the section total rather than used to rank expenses. CSV uses the same ordering
and includes a Direction column.

## Accounting population and presentation

Cash is identified by `bookkeeping.accounts.is_cash` through the subaccount's
parent. It is not inferred from account names or from being an asset. All cash
accounts with recorded history or an actual OFX connection are included; unused
generic chart entries are not displayed as if they were verified zero balances.
Unconnected personal accounts remain outside the recorded consolidation scope.

| Journal or evidence | Treatment |
| --- | --- |
| Debit to cash | Gross cash receipt |
| Credit to cash | Gross cash payment |
| No cash leg, including a card purchase | Excluded from cash flows |
| Both legs are cash in one journal | Internal transfer; eliminated from external flows |
| Validated two-cash transfer pair inside the period | Both cash legs eliminated; gross eliminated amounts disclosed |
| Validated two-cash pair crossing a boundary | Separate timing adjustment preserving the cash reconciliation |
| Cash payment to a card, including a validated clearing match | Retained as a cash outflow; activity allocation remains unresolved |
| Validated transfer between cash and a brokerage account | Investing flow, labelled "Transfers with brokerage accounts" |
| Opening journal inside the period | Separate basis adjustment; not financing income |
| Ordinary external cash movement | Counterpart account's Operating/Investing/Financing metadata |
| Suspense, unknown activity, or unsupported transfer | Explicit unresolved cash receipts/payments |
| Exact owner-reviewed aggregate purpose | Report-only classification with revalidated evidence; journal unchanged |

Brokerage cash (for example the Schwab sweep) is an investment, not cash. Money
moving between a checking account and the brokerage is an investing flow, and
activity inside the brokerage (trades, dividends, interest, fees) has no
cash-flow effect: dividends and interest are income but are not cash receipts
here until they are transferred out.

The report does not allocate an aggregate card payment to current card purchases:
different purchase/payment dates, opening debt, refunds, interest and mixed uses
make that inference unreliable. Equal transfer amounts alone do not establish an
internal transfer. Refund receipts and payments remain gross rather than being
netted before presentation. Investing/financing rows with no classified activity
do not prove there was no activity when unresolved cash flows remain.

Owner-confirmed household employee payments can be grouped into one operating
cash-payment line for combined wages, taxes and fees. This aggregate presentation
does not establish individual payroll component amounts. Card payroll charges
remain noncash at purchase and are excluded from that cash-payment line.

The reconciliation is:

```text
Beginning recorded cash
+ Operating + Investing + Financing + Unclassified cash flows
+ In-period opening basis and internal-transfer timing adjustments
= Ending recorded cash
```

Each account also has its own opening + movement = closing rollforward. A zero
difference verifies arithmetic within the recorded population; it does not certify
that every personal account, transaction or classification is complete.

This is a personal accounting report, not a claim of full IFRS/US GAAP compliance.
The direct method separates major classes of gross receipts and payments and
excludes noncash investing/financing activity, as described in
[IAS 7](https://www.ifrs.org/issued-standards/list-of-standards/ias-7-statement-of-cash-flows/).

## PostgreSQL design

`cash_flow.py` computes financial totals in PostgreSQL from source journals,
not from cached trial balances or model answers. `NUMERIC` stays `Decimal` through
the response; even the final reconciliation subtraction is performed in SQL.
This avoids floating-point error and dependence on Python's decimal context.
PostgreSQL documents the exact arithmetic of
[`numeric`](https://www.postgresql.org/docs/current/datatype-numeric.html).

The shared CTE pipeline joins chart metadata, restricts journals to the exclusive
end, expands debit and credit cash legs with `UNION ALL`, and classifies each leg.
`FILTER` aggregates preserve gross receipts/payments; `GROUPING SETS` returns
category/section totals and per-account/consolidated cash balances. Context from
validated evidence is supplied as bound JSON records with distinct journal IDs.
No description-to-category inference occurs inside SQL.

Date filters use bound timezone-aware values with `timestamp >= :start` and
`timestamp < :end`, rather than converting indexed columns to text or local dates.
The detail query uses the same classification pipeline and a deterministic order,
with 100-row database pagination. Bank descriptions join through canonical source
identity only after pagination. Account-scoped FITIDs and split allocations cannot
multiply a detail row through a many-to-many join.

Authentication may already have opened Flask's normal transaction. The report
therefore creates a separate connection and short **REPEATABLE READ, READ ONLY**
transaction. Summary, evidence and drilldown reads share its snapshot. Concurrent
imports or reviews appear on a subsequent request rather than halfway through a
statement. PostgreSQL enforces read-only mode; a 15-second per-statement timeout
bounds accidental expensive queries. The connection is closed/rolled back after
reading; no transaction remains open while a user reads the page. See PostgreSQL's
[transaction isolation documentation](https://www.postgresql.org/docs/current/transaction-iso.html).

There is no new materialized view, report cache, trigger or migration. The initial
live core query on roughly 7,000 journals ran in about 11 ms on PostgreSQL 18.6,
using `EXPLAIN (ANALYZE, BUFFERS)`. A whole-history scan is reasonable because the
opening cash calculation needs pre-period history. Adding a timestamp index alone
would not remove that work. Existing primary/unique source keys serve the bounded
evidence/detail lookups. Follow the measured plan rather than adding speculative
covering indexes or stale-summary invalidation machinery; PostgreSQL describes
the interpretation of plans in [Using EXPLAIN](https://www.postgresql.org/docs/current/using-explain.html).

If future data volume warrants optimization, measure summary and evidence queries
separately. A timestamp index can help selective date/detail reads. Monthly
summaries would need explicit invalidation for journal reclassification, cash
taxonomy changes and evidence corrections, plus an independent source reconciliation;
a cached balance alone is insufficient.

## Evidence and integrity

`cash_flow_evidence.py` uses only the supplied report session. It revalidates saved
transfer pairs against current source amounts, dates, associations, currency and
fingerprints. Cash-to-card matches are distinct from cash-to-cash transfers.

Export coverage comes from verified request bounds or valid retained OFX bounds,
not the first and last observed transactions. Gaps before the selected period
also matter because they affect beginning cash. Source-to-journal checks validate
each allocation, so equal offsetting mistakes cannot hide behind an agreeing net.
Independent source and statement arithmetic complements the main SQL aggregation.

An adopted opening must still have its exact posting, immutable preview, original
statement documents and matching source windows. Every saved opening anchor is
checked even when its date falls after a historical report's end. New activity
after that anchor's cutoff does not invalidate it. Changed source windows do.
Only validated, reconciled as-of statement dates appear as supporting anchors.

Report-only classifications use existing encrypted, immutable
`bookkeeping.accounting_evidence` records of kind `cash_flow_classification`.
Their payload specifies exact journal IDs, activity, label, owner review IDs and
an evidence fingerprint. The fingerprint binds journals, canonical bank sources,
cash taxonomy and the exact owner review facts/snapshots. Missing, changed or
conflicting evidence leaves cash unclassified and creates a visible issue.
There is no general future-matching rule and no automatic posting permission.

## Verification

The PostgreSQL tests cover gross receipts and refunds, noncash purchases, direct
and paired transfers, card settlements, boundary timing, opening adjustments,
UTC cutoffs, exact large-value arithmetic, unused-account scope, total/category
drilldowns, split source identities, statement coverage, stale classifications,
opening anchors beyond the report date, authentication, CSV escaping, read-only
enforcement, and snapshot stability across a concurrent committed posting.

Run validation serially with `scripts/ci_local.py`; it provisions disposable local
PostgreSQL. Live database reads, deployment verification and performance evidence
are recorded privately under `.local/cash-flow-20260927/`.
