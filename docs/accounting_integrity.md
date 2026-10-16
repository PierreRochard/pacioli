# Accounting integrity controls

The September 2026 integrity upgrade addresses the five defects described in
[the code review](codebase_review_2026-09-26.md). It changes acceptance and audit
rules; it does not classify historical suspense or invent missing evidence.

Deployment verification on September 26: full local suite 480 passed/1 skipped;
final focused suite 308 passed; restore rehearsal and live migration preserved
financial record hashes and verified 1,057 encrypted values. All 7,061 sources and
7,065 journals were preserved. The live audit reported only 4,414 journals pending
classification review. Private evidence is retained in
`.local/integrity-upgrade-20260926/`. No live bank download was performed for this
validation.

## Source identity and migration

`bookkeeping.ofx_source_identities` assigns a unique internal identity to each
provider account/FITID pair. New records use `ofx:v1:<sequence>` public keys.
The migration preserves unambiguous legacy public keys, so existing journal
references, evidence payloads, and closed journal fingerprints are not rewritten.
It aborts on ambiguous legacy references or journals with missing sources.

`bookkeeping.ofx_journal_sources` gives each OFX journal a checked foreign-key
association. Database triggers create identities and associations even for direct
SQL writes. Source identities cannot be edited, and posted sources cannot be
deleted while their journal associations remain. The independent audit checks
raw source coverage and associations as well, rather than trusting the view.

Use `source_identity(session, account_id, fitid)` from
`pacioli.functions.source_identity` when a caller has provider keys. Never rebuild
an identifier from strings. Reimporting an existing source preserves its identity.

## Import and posting rules

Manual QFX and direct OFX sync use `import_ofx_statement`. It validates the real
document's status, account, currency, statement count, finite amounts, and repeated
FITID contents before committing financial changes. Raw acquisition evidence is
encrypted and retained with pending/accepted/rejected status and a safe error
code. Rejected imports do not leave new sources, balances, or account registrations.
Unsupported posting currencies require a reviewed conversion workflow; a USD
configuration never changes a foreign source document into USD.

For an OFX allocation, the bank account, direction, exact normalized UTC posting
instant, and total must agree with its source. Legacy Amazon items use shipment
day in UTC. Receipt evidence supporting an existing bank allocation must not
create a second movement. Manual FX journals require finite amounts and complete
conversion evidence in both the posting service and PostgreSQL constraints.

The independent audit starts with all sources and reports wholly unposted,
partially allocated, excessive, misdated, or misdirected activity. Incremental
reviewed batches may leave valid, unrelated, wholly unposted sources outstanding;
the audit continues to report them, and period closing blocks on them. Existing
malformed allocations are never treated as an ordinary backlog.

Month closing checks only relevant activity through its cutoff, including future
mispostings of historical sources. New closes retain a source fingerprint in
addition to financial statements and the journal fingerprint. Later backdated
sources are therefore visible. Older closes remain unchanged and are explicitly
reported as lacking the new source-coverage proof.

## Deterministic mappings

All eligible rules are resolved before any entries in a batch are posted.
Reviewed exact descriptions outrank provisional exact descriptions; both outrank
keyword rules. A provisional exact rule preserves an explicitly unresolved purpose
against a broad expense rule. Equally authoritative disagreements block the batch
with a source/rule-specific review message. Percent and underscore characters are
literal text, and optional bank-side fields scope a rule to that account.

New mapped entries retain an immutable original decision containing the policy,
candidate rule snapshots and hashes, and selected rule. Subsequent rule edits do
not rewrite that evidence or automatically reclassify past entries. Old entries
without snapshots are not backfilled with guesses about which rule version they
originally used. Their existing journal audit history remains intact.

## Local validation and deployment

Run validation serially with at least 10 GB available RAM:

```sh
free -h
.venv/bin/python scripts/ci_local.py
```

The regression tests use real PostgreSQL constraints/triggers and the actual OFX
parser. They cover collisions, legacy migration rollback, incomplete FX, omitted
and malformed postings, mapping order/conflicts, replay, controlled contention,
and faults after partial work. External bank transport is mocked. There is no
GitHub Actions workflow.

The integrity upgrade (`journal-money-v2`, `ofx-source-identity-v1`,
`brokerage-ledger-v1` in `admin.integrity_migrations`) was rehearsed on a
restored dump and applied to the live database before Pacioli adopted Alembic.
Its schema is part of the baseline migration, and the rehearsal script was
removed with the hand-written installers. Schema installation never silently
fixes historical financial amounts, conversions, or classifications. Direct SQL
maintenance must coordinate with application writers.
