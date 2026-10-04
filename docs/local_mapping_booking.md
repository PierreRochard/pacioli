# Local model mappings and journal booking

The local Qwen model proposes classifications. A separate review approves the
exact source IDs, amounts and accounts. `scripts/book_local_mappings.py` now
persists each approved classification in `admin.mappings` and links every journal
entry through `mapping_id`, in the same transaction.

Qwen rules use **exact description matching**, ignoring case. SQL wildcard
characters and merchant prefixes have no special meaning in this mode. A saved
rule can be applied to a future transaction with the same full bank description.
Descriptions with changing reference numbers require new rules or separate
review before introducing a broader keyword. Existing manually created keyword
rules retain their previous matching mode. The Mappings screen shows matching
mode, review status, reason and model/review provenance.

## Accounting policy

The owner authorized supermarket purchases in full as Groceries and unresolved
transactions as suspense pending review. The latter use **Bookkeeping Suspense**
or **Transfer Clearing**, both balance-sheet accounts. They are not reported as
settled income or expense classifications. Transfers must not be booked twice
as direct bank-to-bank pairs: clearing records each imported side separately,
with matching amounts offsetting there until reconciliation is completed.

The statements show the number and net balance of suspense/clearing entries.
`audit_ledger` reports `classification_pending` even when clearing nets to zero,
and period close remains blocked while those classifications are unresolved.
A suspense balance is a provisional holding amount, not a confirmed asset value.

Outgoing payroll is not Salary. Incoming payroll may be recorded as net deposits;
gross wages, withholding and benefits require paystub evidence. Unknown loan
splits and broad-retailer purchases require suspense until supporting details
are available. Supermarket cash-back withdrawals still need separate treatment.

## Workflow

1. `scripts/classify_remaining_mappings.py prepare --batch PATH` takes an
   encrypted snapshot of unposted source records and the planned chart.
2. `classify --batch PATH` asks Qwen about one merchant group at a time. New
   snapshots use Qwen3-14B thinking with every distinct description, sign and
   currency; oversized groups defer for a smaller review scope. Historical
   snapshots retain their original protocol. The reviewer must inspect every
   approved source. One model response is not proof of correctness.
   Multi-merchant output was found to mix categories and is retained as rejected
   diagnostic evidence, not used for posting.
3. Store an encrypted review identifying source IDs, category, reason, reviewer,
   authorization and snapshot/proposal hashes. Preserve original drafts and any
   explicit review corrections separately; never silently relabel a model answer.
4. Run `book_local_mappings.py preview --batch PATH --review REVIEW.enc
   --proposals PROPOSALS.enc`. It validates sources, chart and mapping conflicts;
   staging the rule records is rolled back, and no journals are committed.
5. Take a database backup. Run `post` with the same arguments and `--bulk` for
   large historical batches. Rules and journals commit together after audit.
6. Replay the same batch. Existing exact postings and mapping links are reused;
   conflicting postings or rules abort the entire transaction.

The original 392-entry batch can be replayed to populate missing mapping records
and links. This creates audited metadata updates without changing dates, amounts
or debit/credit accounts. Closed-period entries cannot be modified.

## Bulk posting and validation

The bulk path holds the journal table and accounting advisory locks. It defers
only the derived trial-balance trigger inside the transaction, leaves audit and
closed-period controls active, rebuilds balances using SQL aggregation/window
sums, and enables the trigger again before validation and commit. Any exception
rolls back trigger state, mappings, journals, audit events and derived balances.
The independent Python accounting oracle verifies all rebuilt balances.

Snapshot amounts, descriptions, dates, feed-account names, currency and existing
chart classifications must still match. Chart additions are allowed; changing
any previously recorded chart classification requires a new review. Source
amounts use exact Decimal arithmetic and source/allocation uniqueness prevents
replays from doubling journals. Bank-balance differences and explicit unresolved
classification are reported as unfinished reconciliation; other audit failures
abort the transaction.

Encrypted artifacts use the application Fernet key and owner-only permissions.
The classifier calls local Ollama only. Preserve the key, encrypted evidence
and database backups together.

For the measured model comparison, GPU memory budget, prompt design and audited
corrections to already-booked suspense, see [the suspense advisor](suspense_advisor.md).

Run GPU inference and local CI serially:

```sh
free -h
.venv/bin/python scripts/ci_local.py -k local_mapping
```

Accounting tests cover exact matches including wildcard characters, future rule
reuse, mapping provenance, metadata backfill, conflicting rules, positive and
negative amounts, stale sources, closed periods, duplicate prevention, bulk
rebuilds and transaction rollback including trigger restoration.
