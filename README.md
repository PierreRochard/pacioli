# Pacioli

Pacioli is a legacy Flask accounting application backed by PostgreSQL. This
repository can be set up locally with Python 3.11; the dependency set preserves
the older application APIs.

## Local setup

For a new local installation, install Python 3.11 and the PostgreSQL server
binaries, then run:

```sh
make env
make db-init
```

`db-init` creates a persistent PostgreSQL cluster and a `pacioli` database in
the ignored `.local/` directory, loads the chart, and runs the accounting
check. It is safe to rerun. The server listens only on a private Unix socket
and authenticates the current operating-system user with PostgreSQL peer
authentication, so there is no database password. A private mode-0600
`.local/config.json` holds the database URL and application encryption keys;
Pacioli reads it automatically. The configuration must stay with the data:
losing its credential key makes encrypted bank passwords and responses
unreadable. Neither the database nor the config is committed to Git.

Use `make db-start`, `make db-stop`, and `make db-status` to manage the server.
`make db-backup` writes a custom-format dump to `.local/backups` and checks
that PostgreSQL can read its archive index. Restore a backup periodically to
verify the data, not just the archive index.
Because that directory is in the same checkout as the database, copy important
backups and the private config to secure, separate storage. Once initialized,
create an administrator and start the application:

```sh
.venv/bin/python manage.py create_admin --email you@example.com
.venv/bin/python manage.py run_server
```

`create_admin` prompts for a password. `createdb` creates the seven application
schemas, tables, triggers, and reporting views inside the selected database;
`populate_chart_of_accounts` can be rerun without duplicating accounts. The
development server listens on port 5000.

This checkout can also be served on the LAN at `https://pacioli.lan`. Host-specific
service, proxy, and certificate files stay in the untracked `deploy/` directory. Run
`.venv/bin/python manage.py create_admin_link` to generate a two-hour,
one-use browser link for creating the first administrator without email.

For an existing or externally managed PostgreSQL database, use `.env.example`
as a guide and supply settings through environment variables, or set
`PACIOLI_CONFIG` to a private JSON config file. Environment variables take
precedence over the config file. `DATABASE_URL` alone is enough to choose a
database; separate `PGUSER` and `PGPASSWORD` are not required when it is set.
Keep the application credential key outside the database and its dumps.

`install.py` only creates a virtual environment and installs pinned packages.
Use `python3.11 install.py` to omit the test dependency, or `make env` for the
same development setup shown above.

When configuring an external database, generate `PACIOLI_CREDENTIAL_KEY` with
`.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
Keep this key outside database backups: losing it makes encrypted connection
passwords and OFX responses unreadable. New records are encrypted on write.
For a database created by an older version, run
`.venv/bin/python manage.py createdb` to install the updated balance trigger,
then `.venv/bin/python manage.py encrypt_existing_data` after setting the key
to encrypt existing plaintext values. The encryption command is repeatable and
checks that the key can decrypt records already encrypted. Use **Refresh** on
the Trial Balances page once to rebuild balances created by the old trigger.

Reporting periods use UTC. Each observed period carries forward balances for
accounts with earlier postings, even when those accounts have no new entry in
that period. A period disappears when its last journal entry is removed. Weekly
periods use PostgreSQL `YYYY-WW` calendar weeks (seven-day blocks beginning
January 1), rather than ISO week numbering. Trial balances sum
`functional_amount`; source amounts are retained for reference and are not
converted by the balance trigger.

Financial statements now calculate their selected period directly from journal
entries. A month with no new entries still shows the prior balances. Equity is
shown as owner contributions minus distributions plus cumulative income, and
the balance sheet displays the accounting-equation difference. Accounts under
the `Cash` parent are marked explicitly as cash by chart population; review the
flag for any custom cash account. The income statement uses activity in the
selected period, while the balance sheet uses all entries through its end.

**Accounting → Cash Flows** (`/cash-flows/`) provides a consolidated direct-method
statement for the trailing year or an explicit date range. It separates gross
receipts/payments, eliminates supported internal cash transfers, retains card
settlements, and reconciles beginning to ending cash. Unresolved classifications
and source-coverage gaps remain visible. The page supports source drilldowns and
CSV export. See [cash-flow reporting](docs/cash_flow.md) for accounting policy,
PostgreSQL snapshot/aggregation design, and evidence checks.

## Personal accounting controls

Journal postings use USD as the functional currency and require positive,
finite amounts in cents. USD source amounts must match functional amounts.
EUR source amounts use cents and BTC source amounts use satoshis; either needs
the source amount, positive USD-per-unit conversion rate, rate source, and rate
date. These rules are enforced by the posting service and PostgreSQL checks.
Every imported source transaction has a stable source ID. A split may use
several uniquely keyed debit/credit pairs whose amounts add to the source
total. Journal admin pages are read-only; use a reversal to correct a posting.
Database triggers retain the old and new row for any direct SQL update or
delete, and audit events cannot be edited or deleted individually.

Run `.venv/bin/python manage.py accounting_check` to compare every stored trial
balance with an independent journal calculation, compare posted Amazon/OFX
totals with source transactions, and compare imported OFX ledger balances with
booked cash as of each statement date. The command is read-only, prints JSON,
and exits unsuccessfully when differences exist. Bank reconciliation requires
an opening-balance journal and a posted or explicitly resolved transaction for
every change since that balance. The report also checks each monthly accounting
equation and flags journal accounts with no chart classification.
OFX accounts must have a reviewed currency registration before mapping.
Set `currency` on each OFX connection before syncing. For an account imported
from a file, inspect its bank statement and run
`.venv/bin/python manage.py set_ofx_account_currency --account-id 123 --currency USD`.
The account ID is shown on Banking → Accounts. Existing OFX postings without
that registration are flagged by `accounting_check`; confirm USD only from the
original statement. EUR/BTC bank feeds may be registered for visibility but
cannot be mapped automatically until each transaction has conversion evidence.

For a manual posting, use a stable reference so a retry cannot silently book
the same source twice:

```sh
.venv/bin/python manage.py post_journal --date 2026-09-23 --reference opening-checking \
  --debit 'Chase Checking' --credit 'Personal Equity' --amount 100.00
.venv/bin/python manage.py reverse_journal --id 123 --date 2026-09-24 \
  --reason 'Incorrect source amount'
```

After reviewing a completed UTC month and resolving every
`accounting_check` difference, run
`.venv/bin/python manage.py close_month --period 2026-08`. A close stores the
statement totals, journal count, and a SHA256 journal fingerprint. PostgreSQL
then rejects backdated inserts and changes to any journal in or before that
month. Corrections to a closed month use a reversal in an open month; the old
statement remains reproducible. `accounting_check` also verifies stored close
fingerprints and statement snapshots. The close and audit rows are immutable
through normal row updates/deletes; the database owner can still administer
tables, so backups and database access controls remain necessary.

The existing `Accruals` schedules can now post daily income or expense through
`.venv/bin/python manage.py generate_accruals --through 2026-09-23`.
An expense schedule debits an expense and credits a liability; an income
schedule debits a receivable asset and credits revenue. `business_days` means
Monday through Friday, without a holiday calendar. Each schedule/date gets a
stable source ID, so reruns are idempotent and changed amounts or accounts are
rejected for review. Settle a payable or receivable with a separate journal
pair. Generate and review accruals before closing the affected month.

To split one USD transaction, create a JSON file such as the following and run
`.venv/bin/python manage.py post_split /path/to/split.json`. For an OFX split,
use `"source": "ofx"` and the transaction ID shown by Pacioli; the local
accounting check then verifies the split total against the imported amount.

```json
{
  "date": "2026-09-23",
  "source": "manual",
  "transaction_id": "receipt-123",
  "fixed_account": "Chase Checking",
  "variable_side": "debit",
  "total": "10.00",
  "allocations": [
    {"key": "food", "account": "Groceries", "amount": "7.00"},
    {"key": "transport", "account": "Public transportation", "amount": "3.00"}
  ]
}
```

For an existing database, **back it up before running the updated `createdb`**.
`createdb` installs the new constraints only if every historical journal row
already satisfies them; on failure it lists up to ten IDs per violated rule and
does not round, delete, or rewrite those entries. Zero and fractional-cent USD
postings, mismatched USD source amounts, and foreign-currency postings without
conversion evidence require review. `prepare_accounting_columns` adds the
nullable conversion fields for a reviewed backfill; after correcting historical
records, rerun `createdb` and `accounting_check`. Existing chart records are
preserved, so review any custom account classification and cash designation.

The bundled chart now places `Cold Wallet` under `Bitcoin Investments` and
provides a separate `Personal Distributions` branch. Existing chart records
are preserved by `populate_chart_of_accounts`, so review those account
classifications in databases populated from the older chart. Historical
postings are not recategorized automatically.
It also adds `Amazon Payable` as a liability. New Amazon mappings debit an
expense or asset and credit that payable; a refund reverses the pair. Map the
later bank or credit-card payment to debit `Amazon Payable`, so it clears the
liability without booking the expense again. Older mappings through the
expense-classified `Amazon Suspense Account` are rejected for new postings and
flagged by `accounting_check` if already posted; review and reverse/repost them
individually rather than silently reclassifying history.

`.venv/bin/python manage.py backup_db` creates a PostgreSQL custom-format
archive for the configured database and emails it to `MAIL_USERNAME`. It uses
`PACIOLI_BACKUP_DIR` if set, otherwise `TMPDIR`. A failed email leaves the
archive in that directory for recovery; a successful send removes it.

## Local CI

Run the complete local gate after `make env` or `python3.11 install.py --dev`:

```sh
make ci
```

This command checks available memory and disk space, starts a private
PostgreSQL cluster under `TMPDIR`, checks Python syntax and installed package
dependencies, runs the tests serially, then stops and removes the cluster.
It needs local PostgreSQL server binaries and does not need `.env` or an
existing application database.

## Tests with an existing PostgreSQL server

Provide a PostgreSQL connection to the **postgres maintenance database** with
permission to create databases. The test suite creates a randomly named
`pacioli_test_*` database, then removes that database after the run. It does not
use or alter the `pacioli` application database.

```sh
export PACIOLI_TEST_ADMIN_URL='postgresql+psycopg2://pacioli:your_password@localhost:5432/postgres'
make test
```

The two configuration tests can run alone without PostgreSQL:
`.venv/bin/python -m pytest -q tests/test_config.py`.

## Amazon and market data

### Chase QFX files

Chase's current Quicken connection uses a bank-hosted authorization flow that
Pacioli's legacy username/password OFX client cannot perform. On the Pacioli
workstation, **Admin → Connections → Supervised Chase connection** can save a
Chase username and password encrypted in the local PostgreSQL database. Start
an export while you can see the workstation desktop.

The default browser for new connections is **your existing Firefox**. Select
**My existing Firefox** on the Chase page for an older connection. The selected
browser is stored in the app database. Firefox keeps its existing LastPass
installation, passkeys, cookies, and other tabs. Pacioli creates and closes only
its own Chase tab; it neither copies your profile nor exports Firefox's vault or
cookies into the app. A session check proves account access, not which login
method was used.

Firefox runs keep a bounded diagnostic record encrypted in the app database:
connector stages, address categories without paths or queries, and recognized
error phrases. Read this report, including while the worker is running, with:
`.venv/bin/python -W ignore scripts/inspect_chase_firefox.py RUN_ID`.
The inspector reads the database without taking over Firefox. Failed Chase tabs remain
open for inspection; successful runs close their own tab. No screenshots,
password fields, account contents, or network bodies are collected.
Normal worker stops release the Firefox connection. An abrupt process kill can
leave Firefox's direct BiDi session attached; quit Firefox normally and reopen
the Pacioli launcher if that occurs. Pacioli does not force-close your browser.

Quit Firefox normally once, then launch it from this checkout with:

```bash
.venv/bin/python scripts/firefox_connect.py
```

To add a **Firefox for Pacioli** entry to the workstation application menu, run
`.venv/bin/python scripts/firefox_connect.py --install-desktop` once.

The launcher starts ordinary Firefox with its usual startup settings and a WebDriver
BiDi endpoint on `127.0.0.1:9222`. It refuses to close an already running browser.
It changes no profile preferences, and does not expose the endpoint to the LAN.
This local automation endpoint can control the browser; keep its default
loopback binding and do not add remote-allow-origin exceptions. Use this launcher
again after fully quitting Firefox; no restart is needed between Pacioli runs.
The API is documented by [Mozilla](https://developer.mozilla.org/en-US/docs/Web/WebDriver/How_to/Create_BiDi_connection).
Before the one-time restart, use Firefox's **Open previous windows and tabs**
startup setting if you want automatic tab restoration, or use **History →
Restore Previous Session** afterward. Quit from the Firefox menu rather than
closing windows individually. See [Firefox session restoration](https://support.mozilla.org/en-US/kb/restore-previous-session).

Click **Check Firefox connection** for a sign-in-only test, then **Open Chase and
export QFX** to approve USD accounts and import. Later **Sync now** runs use a
background tab in the open Firefox. If Chase needs sign-in, Pacioli offers to
bring that same tab forward for LastPass or MFA approval. It does not autofill
the saved legacy password in Firefox or alter WebAuthn. Downloads follow your
normal Firefox settings; complete any Save As dialog in Firefox. Pacioli matches
the browser's download ID to its own tab, imports that exact QFX, and leaves the
original in your normal download location. It never scans your Downloads folder
or changes browser-wide download settings. Cancelling stops further processing;
Firefox and its other tabs remain open. Forgetting the Pacioli connection does
not erase your Firefox profile or sign you out there.

The following profile/session details describe the **legacy Chromium** option.
Its short-lived Chromium session enters the saved login into Chase's own sign-in
form; complete any Chase MFA there. The connector then asks which visible
accounts to export, downloads up to the last 24 months of QFX for each selected
account, imports the files, and closes the browser. It does not post journal entries. One
session runs at a time; session progress and cancellation are available on the
same page. A stopped worker becomes restartable after five minutes.

After one successful supervised export, an administrator may click **Sync now**
to start a headless, clearly labeled browser on demand. It tries the previously
selected accounts and checks every downloaded QFX against their approved OFX
account IDs before importing it. Repeated files and transactions are deduplicated.
There is no schedule or remote CI workflow.

Normal exports now use a dedicated persistent Chromium profile under
`.local/chase-browser/<random-id>/`. The directory is private (0700), and a
process lock prevents overlapping workers from using or removing it, including
when a stale database job is replaced. This profile contains cookies and site
storage on disk; it is **not encrypted with Pacioli's database field key**.
Pacioli also keeps an encrypted Chase cookie/localStorage snapshot in PostgreSQL
for session-cookie restoration and initial profile recovery. Native snapshots read only Chase frames and request
only Chase cookies; provider vault/extension storage is not exported. IndexedDB
and provider state stay in the private Chromium profile. Changing the saved username or password, or forgetting the
login, removes the dedicated profile and its saved authentication snapshot.
The worker never opens the user's everyday browser profile. Profiles left by an
interrupted passkey test are removed on the next run or when forgetting the
login. Chromium's application-profile browsing/download history is discarded
before launch; cookies, site storage, and provider settings remain. This works
around a locally reproduced Chromium 153 crash on a second download after
profile reuse (similar to [Playwright issue 42506](https://github.com/microsoft/playwright/issues/42506)).
Normal runs also omit Playwright's mock-keychain/basic-password-store flags so
Chromium can use the workstation's native credential store.

If Chase presents one unambiguous one-time-code field, an administrator can
relay the code through Pacioli; it is encrypted briefly and cleared after use
or when the run ends. For an unrecognized headless challenge, Pacioli closes
the headless browser and waits up to ten minutes for **Open verification window**.
That action reopens the same profile visibly on the workstation in the same run.
Complete verification in Chase; export resumes with the same approved accounts.
The visible attempt does not automatically resubmit the password. There is at
most one headless-to-visible handoff per run. Cancellation, timeouts, sign-out,
and changed account choices stop the run without broadening account approval.
Chase can expire a session at any time, so unattended completion is best effort.

**Test my existing passkey** starts a separate, fresh profile with no cached
Chase state and no password autofill. Choose Chase's passkey sign-in and use an
existing provider, such as the Apple phone/QR flow. Pacioli leaves native
WebAuthn enabled: no virtual authenticator, credential API replacement, or
copied provider signing key participates in this path. Once accounts appear,
confirm in Pacioli that you used your existing passkey. Only then does Pacioli
adopt the test profile and save its session; cancellation discards the test
profile and retains the old one. This is an observed accounts-page success plus
user confirmation, not independent evidence of which provider authenticated.
The test neither enrolls a new key nor downloads or imports transactions.
Device approval must still be completed through the provider when requested.
An accepted confirmation survives a temporary Chase page refresh. Pacioli still
waits for authenticated account controls to return before saving the session;
confirmation alone cannot complete the test.

For a passkey already saved in LastPass in another browser, choose **Test my
LastPass passkey**. It opens the official LastPass Chrome Web Store listing in a
second tab of the fresh test browser. Install the extension and unlock it directly
in LastPass, return to the Chase tab, reload it, and choose passkey sign-in. Pacioli
does not enter a LastPass master password. Confirmation after reaching accounts
retains that browser profile (including the provider installation) for later
on-demand syncs. An unsuccessful or cancelled fresh test discards its profile;
it does not revoke the Chase passkey stored in LastPass. Repeating the fresh test
requires setting up the provider again. Routine exports and Sync now reuse the
retained profile.
Chase session cookies are restored from the encrypted bank-only snapshot when
reopening that profile. Chromium does not retain those cookies across clean
shutdowns. Restoration adds only Chase session cookies; it does not reset
LastPass storage or overwrite the profile's persistent cookies. A visible
verification handoff carries forward the current attempt's session cookies.

The optional **local software passkey** experiment is disabled in production.
Chase rejected supervised enrollment with the same generic error using both
Playwright's JavaScript WebAuthn shim and Chromium's native virtual
authenticator. Pacioli saved no signing key, and Chase listed no new passkey
after either failure. The native authenticator passed a localhost registration
and signed assertion test, so the live rejection is specific to Chase or this
workstation. Chase's published system requirements list Windows and macOS,
while this workstation runs Linux. The exact reason for Chase's rejection is
unknown. Keep enrollment disabled until a supported, Chase-accepted method is
demonstrated. The existing on-demand supervised browser and manual QFX import
remain available without Plaid.

For a deliberately supervised investigation after enrollment is re-enabled,
Pacioli records a bounded diagnostic report on the Chase run. It records
relative timing, browser navigation fingerprints, native WebAuthn credential
events, failed network requests, Chase HTTP status codes, and narrowly
validated machine-readable error codes from small JSON responses. It does not
record request bodies, full headers, cookies, URL queries, raw console
messages, passkey identifiers, or signing keys. Selected server request IDs
are retained to help correlate errors. The report is encrypted in
`admin.chase_browser_runs.diagnostics` and available only through the
administrator's **View passkey diagnostic report** link. The report can show
which step rejected registration; Chase may still return only a generic error
without disclosing its internal reason. The **Chase rejected the passkey**
button ends the run without saving a candidate credential and records that
observed outcome.

If a future supported enrollment succeeds, Pacioli encrypts the signing key in
PostgreSQL. **Test local passkey sign-in** uses a fresh headless browser without
cached Chase cookies and downloads no QFX. Only a successful WebAuthn assertion
and account sign-in mark the key verified. Removing the local copy in Pacioli
does not revoke the credential at Chase.
Run `.venv/bin/python scripts/passkey_smoke.py` to verify registration,
restoration, and a signed assertion against a local test site without
contacting Chase. Run `.venv/bin/python scripts/passkey_cdp_smoke.py` to
profile the native Chromium authenticator used by the connector. The probe
uses only `localhost`, prints no credential IDs or keys, and compares the
current options with isolated CTAP 2.1, backup-eligible, and simulated
already-backed-up variants. It checks CBOR attestation against Chromium's
own authenticator-data API, RP and origin,
presence and verification bits, backup bits, AAGUID, ES256 public key,
resident-key extension, and signature after importing the credential into a
fresh browser context. On this workstation, the current profile creates a
single-device credential (`BE=0`, `BS=0`) with Chromium's virtual-authenticator
AAGUID; the backup-eligible variant creates `BE=1`, `BS=0`. The simulated
backed-up variant is diagnostic only: claiming `BS=1` for a key that is not
yet durably backed up would be inaccurate in production. The probe also checks
that Pacioli's stored credential retains Chromium's backup fields and that a
backup-eligible key keeps its signed BE flag even if imported into an
authenticator with the old defaults. The probe checks two successive fresh
browser assertions; Pacioli now stores the updated signature counter after a
successful Chase sign-in so it does not restart each run from the enrollment
counter. These local results do not reveal
which field caused Chase's HTTP 400. The production authenticator settings
and enrollment gate remain unchanged.

The browser worker needs Playwright's Python package, `/snap/bin/chromium`, and
the systemd user manager. Supervised runs also need a visible desktop. The web service
starts a transient user service for each session because its own
`NoNewPrivileges=yes` setting prevents snap Chromium from launching. The worker
has a 3 GB memory limit and a 40-minute runtime limit (including handoff waits). Only a numeric run ID
is passed to the worker; it reads encrypted credentials from the database using
Pacioli's private key. Raw QFX files are held in a private directory under
`$TMPDIR` and removed when the worker exits. The source retained in Pacioli is
encrypted. If Chase changes its login, MFA, or download controls, the connector
stops with a generic error instead of guessing or bypassing a control.
Fresh databases created with `manage.py createdb` include the connector tables.
For an existing database, run `.venv/bin/python manage.py migrate_chase_browser`
after updating dependencies, then restart `pacioli-web.service`. Run `make ci`
for the local disposable-PostgreSQL test suite. The native-connector tests also
launch real Chromium sequentially with all page requests intercepted by a fake
bank: MFA relay, persistent reuse, session expiry, same-run GUI handoff, account
allowlist rejection, duplicate QFX, balance/opening-balance calculations, and
provider-confirmation/cancellation are exercised. They require the workstation's
Chromium installation. Human provider approvals are simulated in these tests;
a supervised live sign-in is still required to establish Chase acceptance.

The **Admin → Connections → Import Chase QFX** page remains the manual path:
download a Quicken Web
Connect `.qfx` file for one Chase account on a desktop computer. In Chase,
open Accounts, choose the account, then Download account activity. Select
Quicken Web Connect (QFX) and All transactions, then Download. Confirm the
account and USD currency before uploading the file. The page records the file
checksum, retains the source encrypted, deduplicates a repeat upload, creates
a named cash or credit-card liability subaccount, and shows file date coverage
and the latest OFX ledger balance. It does not post transactions to the journal.
Additional files for the same account extend the coverage if Chase offers
older periods. Keep the original downloaded files as independent evidence.

Chase credit-card exports have a 1,200-transaction limit. If All transactions
exceeds it, use consecutive date ranges and shorten any range that still
exceeds the limit. Overlapping boundary dates are safe to reimport.
Some Chase card QFX files assign the same FITID to a purchase and its
FOREIGN TRANSACTION FEE or TRANSACTION FEE. For Chase's B1/10898 institution
identifier, the importer gives those fee rows a deterministic internal ID,
preserving both amounts and deduplicating later fee-only or overlapping
downloads. The encrypted original QFX retains the bank's original IDs.
Other conflicting reused IDs, including changes to already imported records,
reject the whole file for review instead of silently keeping the first row.
Previously imported fees with the old unmodified IDs also require review;
they are not automatically duplicated or rewritten.

The displayed opening candidate equals the latest imported ledger balance
minus the sum of imported signed transactions through that balance date. It is
only a candidate: verify that all transactions in the window are present and
reconcile the result to a Chase statement before posting an opening balance.
Pacioli interprets the OFX fork's timezone-less dates as UTC. For Chase credit
cards, the OFX ledger balance is compared with a liability subaccount; for
checking and savings, it is compared with a cash subaccount.

Chase describes desktop QFX downloads for up to the last two years, but the
actual available history must be determined from your exported files:
https://www.chase.com/personal/banking/education/budgeting-saving/what-is-chase-spending-planner

A supervised export on 2026-09-24 succeeded in a clearly labeled Chromium
agent session using browser UI clicks. Earlier experiments that opened
DevTools and invoked DOM `click()` ended at Chase's `/logout` page. Those
experiments also took longer, so the exact logout trigger remains unknown.
Keep the browser interaction supervised, use UI clicks, and treat Chase session
termination as a stop rather than trying to bypass it.

The new [accounting workbench](docs/accounting_workbench.md) provides statement
openings, transfer reconciliation, a separate local Firefox Amazon receipt
reader, exact item-to-bank allocations, merchant review, and real-data Qwen
evaluation. Open **Accounting → Reconciliation** at `/reconciliation/`.

The Amazon suspense workbench at
`/reconciliation/amazon/suspense/` adds source-linked item review, immutable
purchase-context notes, verified ledger totals, and exact-product suggestion
rules. Policy holds remain enforced through review and posting; model suggestions
do not approve journal entries.

The [Amazon classification review](docs/amazon_policy_questions.md) at
`/reconciliation/amazon/policies/` applies saved owner decisions through
source-reviewed implementation evidence. Supported categories are prefilled in
one editable batch; genuine exceptions retain their specific missing facts.
Saving classifications opens the existing posting preview. Original answers and
revisions remain at `/reconciliation/amazon/policies/history/`.

The legacy Amazon path is to download an item-order CSV and run
`.venv/bin/python manage.py import_amazon_csv /path/to/report.csv`. Reimporting
the same order and title leaves existing items intact. Amazon mappings book
shipped USD items to journal entries; refunds use the mapping's negative
accounts. The legacy `submit_amazon_report_request` and
`import_amazon_report` commands still depend on Amazon's report page and a
Gmail IMAP mailbox. Their parsing is covered by local tests, but login and
download have **not** been verified against live services.

`runs_at_7am_and_7pm` syncs OFX accounts, applies OFX mappings, emails a
transaction report, and updates security prices through `yfinance`. Local tests
mock external responses; no live bank or market-data request was made.
The legacy OFX storage drops source timezone information; Pacioli interprets
its stored naive transaction and balance timestamps as UTC for repeatable
posting and treats each ledger balance as the end-of-day balance on its stored
UTC date. Confirm this assumption against each bank's
statement dates before relying on a period close.

## Local AI Chase connector

The on-demand Firefox extension connector is available at
`https://pacioli.lan/connections/chase/local/`. It uses the installed local
Qwen3-VL model to check proposed export actions against the selected Chase tab,
with deterministic account/date/format guards, saved interval progress, and the
existing QFX importer. **Update Chase** starts or resumes an incremental background
job for the saved accounts. It fills known coverage gaps and rechecks a configurable
overlap (seven days by default); repeated files and transaction IDs are deduplicated.
Account choices and overlap are saved in the database. Progress and requests for
Chase verification appear in Pacioli. The Firefox extension must be connected;
temporary extensions need reloading after a Firefox restart.

The update page also captures posted QFX balances and shows provisional opening
balances, net signed activity and coverage gaps. Verified browser export ranges
and QFX ranges feed the same planner and reconciliation calculation. Same-as-of
balance revisions are retained as separate source snapshots; file replay does not
revert the latest balance. Missing coverage or inconsistent implied openings from
different balance dates blocks a candidate. Bank as-of dates are displayed even
when older than the fetch date. Review candidates against statements before
posting opening entries; the update itself does not post journals. Resuming an
update on a later day refreshes all originally selected accounts through that day.

The progress page streams a durable, timestamped activity log as soon as the
button is pressed. It reports browser navigation and field actions, model
checks and response counts, each download wait, QFX checks, import transaction
stages, saved checkpoints and cleanup. Per-account intervals, elapsed step
time and worker check-ins remain visible. Refreshing or reconnecting replays
the log without restarting the job. SSE delivers committed events with a
250 ms server check; 500 ms polling takes over while an active stream reconnects.
Event details are encrypted and allowlisted: only masked account suffixes,
dates, controlled actions and counts are exposed, with no raw page/model text,
credentials, file paths or screenshots. Completed imports are reported only
after commit. These logs begin with runs performed after this migration;
earlier step history cannot be reconstructed.

Run `manage.py migrate_chase_browser` to add the event table, then reload the
systemd user manager and restart the web service. The web service uses eight
threads, with at most four concurrent 20-second event streams; other clients
fall back to polling. Database sessions are released between stream checks.
The September 25 live update completed both bank accounts and verified real-file
replay without duplicate transactions. Chase returned an export error for the
first card even in a direct mouse-click comparison; three card intervals remain
pending. All-account live validation is therefore incomplete.
See [extension setup, limitations, and local tests](extensions/chase-local/README.md).
