# Accounting evidence and reconciliation

The Accounting → Reconciliation workbench at `/reconciliation/` contains five
connected workflows. All routes require an active administrator. Mutations use
POST and CSRF protection. Documents, receipt text, review snapshots and model
proposals use the existing application encryption key. No cloud model is used.

## Statements and opening balances

Import the original PDF with reviewed account identity, calendar dates and signed
beginning/movement/ending amounts. Positive balances represent assets; negative
balances represent card debt. The importer validates exact USD arithmetic and
preserves the PDF, its hash and the extraction/review notes. It does not pretend
that a PDF hash proves the extracted amounts are correct.

An opening preview rolls each statement closing balance back through covered OFX
activity. Back-calculated openings require at least two agreeing statements;
one directly documented beginning balance can support a new account. Export
coverage is required for the entire calculation interval. Approval recomputes the
preview under a journal lock and rejects stale evidence, prior bank journals,
closed periods, or disagreement with the booked ledger. The opening is posted
immediately before the cutover day against Personal Equity. Zero openings create
an evidence record without a zero-valued journal. Repeating approval is a no-op.

The statement page separates dated statement closes from legacy QFX snapshots.
Historical QFX discrepancies remain visible. Unresolved discrepancies continue
to block month close.
Balance agreement alone does not establish that every intervening transaction
or its classification is correct. Saved row-level statement checks are separate
encrypted evidence.

Some retained QFX balances precede activity later posted on their labeled date.
The workbench can preview and record a **specific reviewed cutoff explanation**:
the original export omits that date's activity, every intervening source appears
in a later original export, the exact signed movement bridges the balances, and
a reconciled PDF statement plus complete export coverage anchors the calculation.
Both source files are rehashed and parsed, including account identity and every
exported transaction. Bank journal amounts and posting times must reproduce each
source. The original snapshot is never rewritten or offset with an adjustment.
The proof explicitly records both export cutoffs and includes the earlier date
but excludes the later date. New activity on or after the later cutoff does not
invalidate an earlier review; a changed transaction inside that interval does.

Approval is explicit, CSRF protected, locked, encrypted, immutable and idempotent.
The audit and Chase balance summary revalidate the proof on every read. Changed
files, revised balances, missing sources, coverage gaps or altered bank postings
reopen the issue. Expense reclassification that preserves the bank leg does not
invalidate it. A new explicit review can supersede a stale proof for the same
snapshot; both immutable reviews remain visible and only the active proof can
reconcile it. Original versus end-of-day amounts and source references remain
visible after review. This is evidence of the reviewed export cutoff, not a
general rule for all OFX providers or proof of Chase's actual processing time.

`scripts/adopt_statement_evidence.py DIRECTORY [--apply]` adopts the previously
reviewed local statement archive. It rechecks PDF hashes, regenerates PDF text,
compares the exact multiplicity of previously matched source rows, and computes
openings again. Preview rolls back all staged evidence and journals. A new
account with a directly documented zero beginning balance uses that statement
date, not an inferred earlier opening. Historical PDFs showing a different masked
account suffix retain their explicit account-association caveat.

## Transfers and payments

Candidates require opposite exact amounts, different reviewed USD bank accounts
and dates within five days. Candidate uniqueness is displayed, but equal amounts
alone never authorize a match. The reviewer records evidence of the counterpart.
Each journal can belong to only one match. Linking changes no cash, expense,
income, source ID or journal amount. Reports exclude a pair from provisional
classification only when both current source-preserving legs still match the
saved fingerprint and both dates fall within the reporting period. A transfer
crossing a statement boundary remains pending at the earlier boundary. Closed
periods cannot be newly matched through the workflow.

The page also lists every unresolved clearing leg, including payments with no
candidate in the imported accounts, ambiguous candidates and invalid saved
matches. An absent counterpart is a request for more evidence, not permission
to assume an expense, income or an external account's ownership.

### Cash withdrawals

By owner decision, ATM and other cash withdrawals are **Cash Spending** (an
expense under Discretionary Costs) when withdrawn. Pacioli has no feed for
physical cash, so a withdrawal parked in Transfer Clearing could never match.
The local advisor policy proposes Cash Spending for new withdrawals; a purchase
with cash back still needs a split, and brokerage or crypto withdrawals remain
funding transfers.

`scripts/reclassify_cash_withdrawals.py --reviewer EMAIL` previews, and
`--apply` commits, the move of existing withdrawal rules and journals out of
Transfer Clearing. It selects only exact provisional clearing rules described
as withdrawals, excluding transfer, wire, ACH, peer-payment, brokerage and fee
wording, and skips any rule with an incoming amount or a leg in a saved
transfer match. The change uses the audited reclassification: bank legs,
amounts, dates and source IDs are unchanged, journal events keep before/after
images, the ledger audit must pass, and a `cash_withdrawal_policy` evidence
record is saved. Output is counts only.

## Amazon collection and item allocation

### Original CSV exports and local downloads

The Amazon workbench links to Amazon's **Request Your Data → Your Orders**
workflow. It can require a fresh sign-in and email confirmation. The provider
prepares the archive asynchronously; submitting or confirming a request is not
evidence that the full history has already been received.

Upload Amazon's original CSV or ZIP (up to 64 MiB, 128 MiB expanded) through **Import Amazon
export**. The importer stores the exact original bytes encrypted, alongside
immutable table snapshots. Every valid CSV becomes a snapshot, including
companion reports without an Order ID column. Each table records its original
byte count and SHA-256; every source field and repeated row is retained. Tables
without order identifiers have no distinct-order count. Non-CSV files, including
delivery photos, remain in the original archive and are listed separately.
All ZIP members are checked for corruption. The importer does not merge a changed export into older rows or infer item,
shipment, refund, tax, or settlement semantics from unfamiliar fields. Repeating
the exact export, including under a renamed filename, adds no duplicate evidence.
Malformed rows are rejected atomically; unfamiliar schemas remain source evidence.
No journals are posted by
archive intake. Actual provider layouts and settlement identity must be validated
against the received export before promoting source rows into reviewed bundles.

Download buttons export **all stored records**, independently of the page's
100-record display limit: original receipt text, verified item lines (including
separate repeated units), and booked debit/credit allocations. The ZIP bundles
these three CSVs, every imported provider table snapshot, a provenance manifest,
and an explanatory README. Overlapping provider snapshots must not be summed as
separate purchases. Text that a spreadsheet might treat
as a formula is escaped, and long numeric identifiers are kept as text. These
report CSVs are distinct from the eight-column canonical item import format.
Original provider exports remain separately downloadable with exact bytes.

The command line provides the same controls:

```bash
.venv/bin/python scripts/amazon_csv.py import /path/to/Orders.zip
.venv/bin/python scripts/amazon_csv.py import /path/to/Orders.zip --apply
.venv/bin/python scripts/amazon_csv.py export .local/amazon-csv-export
```

The first command validates and rolls back. Applying saves source evidence only.
Exports require a private output directory and never overwrite existing files.

### Review export settlements against bank activity

After importing the provider archive, choose it under **Match the export to bank
transactions** and select **Review bank matches**. This saves an encrypted review
snapshot with source-row references and possible bank settlements. It creates no
journals, mappings, or item allocations. A unique candidate is a review outcome,
not an approved settlement or an expense classification.

The review separates unique candidates, records already booked, ambiguous
matches, records needing more evidence, unmatched records, and records outside
imported bank history. Source exclusions and bank transactions without a
candidate remain visible. Purchase amounts are positive and refunds negative;
bank amounts use the opposite sign. Expand a record to review its items,
adjustments, source rows, candidate bank account, and current allocation.

Filters and pagination preserve the chosen snapshot. **Download full settlement
review (CSV)** exports the entire saved review, independent of the page or
filter. Separate CSV downloads expose bank transactions without source candidates
and excluded source rows with their reasons. Original source tables remain downloadable beside the relevant rows.
Report downloads require the same active-administrator access as the workbench
and are marked private and non-cacheable.

These are saved observations, not continuously validated approvals. Before
booking, compare the candidate with current bank records and verify shipment or
refund identity, signed totals, and all-in item categories through the existing
allocation preview and approval workflow. A missing card, split settlement,
ambiguous match, or incomplete source remains unresolved until evidence supports
it. Generating another review does not turn uncertainty into posting authority.

The same review is available locally with
`.venv/bin/python scripts/reconcile_amazon.py ARCHIVE_EVIDENCE_ID` (read-only preview) or
`.venv/bin/python scripts/reconcile_amazon.py ARCHIVE_EVIDENCE_ID --apply` (save review evidence).
Reads use a PostgreSQL repeatable-read, read-only snapshot; repeated runs with
unchanged source and financial data reuse the same content-addressed evidence.
Choose one archive at a time, so overlapping exports are not added together.

### Browser receipt collection

Install the separate Amazon reader with `scripts/install_amazon_extension.py`,
then load `extensions/amazon-local/manifest.json` as a temporary Firefox add-on.
On the signed-in Amazon Your Orders page, select its toolbar button. It shares
neither cookies nor passwords with Pacioli. The separate native host uses a
same-user private Unix socket. The extension can read the selected Amazon tab and
navigate observed order-history, order-detail and printable-receipt links only.
Purchases, forms, account settings and unrelated tabs are outside its interface.
Temporary Firefox add-ons must be reloaded after a browser restart.

**Collect Amazon receipts** runs an on-demand bounded worker (100 receipts or ten
history pages per batch). Progress is polled every two seconds. Sign-in, unknown
pages, interruption and limits require attention; already encrypted receipts are
retained. A batch only covers the history range selected in Amazon, not an
assertion that the account's entire history has been collected. No scheduler is
installed. The reader stores visible evidence; it does not assert that an order
total equals one credit-card charge.

Review shipment/refund lines in a captured receipt, or upload the documented CSV.
Use stable bundle and line IDs, not `(order, title)`: identical items and refunds
remain distinct. Include tax, shipping and discounts explicitly. Each bundle is
one signed settlement. The preview requires an exact signed bank amount within
seven days and an Amazon descriptor. A reviewer supplies all-in category splits.
The split totals must exactly reproduce the original source amount. Refunds
reverse those categories. The existing suspense bank journal is replaced by
audited allocation rows; no second cash movement or separate Amazon payable is
created. Replay verifies all posted allocations. Existing legacy Amazon item
journals for the order block this workflow to prevent duplicate booking.

The older item/title CSV importer remains a legacy interface. Use the workbench
for new reconciled evidence. Live DOM and provider export layouts can change;
an unrecognized page must remain a visible failure rather than an invented item
extraction or successful collection claim.

## Merchant review and local Qwen

The queue shows exact provisional descriptors, gross movement and linked journal
counts. A review freezes all linked journals, original sources and chart state.
Optional verified merchant facts require a named source. Qwen runs one bounded
group at a time with the pinned local 14B model and the existing host-wide GPU
lock/headroom checks. A failed or interrupted job can be retried; a suggestion
never posts automatically. Approval requires an independent category and reason,
updates the exact mapping and every displayed journal atomically, and retains
before/after audit events. Stale sources, mappings, charts, closed periods and
partial replay fail closed. Unsupported purchases remain in suspense.

## Independently labeled real-data evaluation

Add labels from actual source transactions with explicit independent evidence and
a normalized merchant family. All examples from that family belong to a single
development or holdout partition. Labels are immutable. The runner also rejects
identical descriptors assigned across partitions under different aliases.

`scripts/evaluate_real_accounting.py --run` evaluates serially with the pinned
local model and records encrypted results plus dataset/policy/model fingerprints.
Gold labels, label rationale and reviewer identities never enter inference
requests. The page reports coverage, correct proposals, unsupported/wrong
proposals, invalid outputs and latency independently for each partition. This
is a frozen regression benchmark: historical transactions may have appeared in
earlier exploratory model runs, so it is not proof of unseen future accuracy.
It makes no 100% accuracy claim and cannot authorize journal postings.
Add `--check` to make the runner exit nonzero unless both frozen partitions match
all labels with no invalid or unsupported proposals. The GUI displays that gate
for every recorded run. Passing a small benchmark still does not authorize
automatic posting.

## Migration and verification

`pacioli.database.workbench.install_workbench()` is additive and repeatable. New
document, statement, opening, transfer and gold-label records have database
immutability triggers. Back up before production migration. Existing source
transactions and journal history are untouched by schema installation.

Run `.venv/bin/python scripts/ci_local.py` for serial local CI using disposable
PostgreSQL. Tests exercise stale evidence, UTC boundaries, replay, duplicate
identity, ambiguous counterpart matching, refunded/split settlements, immutable
evidence, rollback, closed periods, authentication, CSRF, browser-origin and
read-only navigation restrictions, and holdout label leakage. GPU inference and
live browser collection are separately reported; synthetic fixtures do not prove
compatibility with a live Amazon page.
