# Pacioli

Pacioli is a Flask accounting application backed by PostgreSQL, set up locally
with Python 3.14. Direct dependencies are listed in `requirements.in`;
`requirements.txt` is the hash-pinned lock compiled from it (`make lock`), and
`make audit` checks the installed environment for known vulnerabilities.

## Local setup

For a new local installation, install Python 3.14 and the PostgreSQL server
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
Those dumps share a disk with the database. For an encrypted copy on another
machine on the LAN, install restic and run once:

```sh
.venv/bin/python scripts/lan_backup.py init sftp:HOST:backups/pacioli
```

`make lan-backup`, or a daily systemd timer, then dumps the database and stores
the dump, `.local/` and `deploy/` as an encrypted, deduplicated restic snapshot
on that host. Live PostgreSQL files, browser profiles, CI scratch and old
virtual environments are left out. It keeps 14 daily, 8 weekly and 24 monthly
snapshots and verifies a tenth of the stored data after each run. The home page
warns when no backup has succeeded for 36 hours. `init` generates the
repository password in `.local/lan-backup/password`. Keep a copy in a password
manager: the backups cannot be decrypted without it, and the keys from
`.local/config.json` are only inside them.

To restore on a new machine, install Python 3.14, PostgreSQL and restic, clone
this repository, and recreate `.local/lan-backup/` (mode 0700) with the
`repository` address and the `password` (mode 0600). Then run:

```sh
python3.14 scripts/lan_backup.py restore
make env
.venv/bin/python manage.py db upgrade
.venv/bin/python manage.py accounting_check
```

`restore` puts `.local/` and `deploy/` back from the latest snapshot, then
rebuilds the database from its dump with the original keys. It refuses to run
in a checkout that already has a config or cluster.

Once initialized, create an administrator and start the application:

```sh
.venv/bin/python manage.py create_admin --email you@example.com
.venv/bin/python manage.py run_server
```

`create_admin` prompts for a password. The schema migrations that `db-init`
applies create the seven application schemas, tables, triggers, and reporting
views inside the selected database;
`populate_chart_of_accounts` can be rerun without duplicating accounts. The
development server listens on port 5000.

This checkout can also be served on the LAN at `https://pacioli.lan`. Host-specific
service, proxy, and certificate files stay in the untracked `deploy/` directory. Run
`.venv/bin/python manage.py create_admin_link` to generate a two-hour,
one-use browser link for creating the first administrator without email.
### Web service container

The `pacioli-web` user service runs the web application in a Docker container
(`Dockerfile`, `compose.yml`). PostgreSQL, the LAN backups, local CI and the
Chase browser worker stay on the host and keep using `.venv`.

After pulling or committing changes, run `make deploy`:
1. It refuses uncommitted changes to tracked files, because the image is built
   from `HEAD`.
2. It saves a database backup and syncs `.venv` with the hashed development
   lock.
3. It builds the image from a `git archive` of `HEAD` plus the locked wheels in
   `.local/wheels`. Nothing else from `.local` enters the image.
4. It applies pending schema migrations, restarts the service, waits for the
   login page, and keeps the last three builds.

The image contains the private ofxtools and Flask-Admin builds. Never push it
to a registry. Install `docker-buildx` to build with BuildKit; without it, the
deploy falls back to Docker's deprecated classic builder.

The container:
- runs Python 3.14.8 from a digest-pinned image, as your user ID;
- has a read-only root filesystem, no capabilities and no new privileges;
- shares the host network, so it listens only on `127.0.0.1:8001` behind the
  LAN proxy and reaches Ollama on loopback;
- reads `config.json`, the decoy ticker list and the LAN backup status from
  read-only mounts, and the host's `/etc/localtime`;
- mounts read-write only the PostgreSQL socket directory, the Chase profile
  (for its lock), the Chase launch queue and the Schwab downloads;
- gets `nvidia-smi` for the local model's GPU checks, and nothing else of the
  GPU.

It has no access to your desktop session. To start a Chase sync, it leaves an
empty file named after the run ID in `.local/chase-launch`. The host
`pacioli-chase-launcher.path` unit then runs `scripts/chase_launcher.py`, which
starts the worker as a transient user service, as before.

Install the units from `deploy/` (`pacioli-web.service`,
`pacioli-chase-launcher.path` and `.service`) in `~/.config/systemd/user`,
then run `systemctl --user daemon-reload` and
`systemctl --user enable --now pacioli-chase-launcher.path`. To roll back to a
previous build, retag it with `docker tag pacioli-web:<revision>
pacioli-web:current` and restart the service.

For an existing or externally managed PostgreSQL database, use `.env.example`
as a guide and supply settings through environment variables, or set
`PACIOLI_CONFIG` to a private JSON config file. Environment variables take
precedence over the config file. `DATABASE_URL` alone is enough to choose a
database; separate `PGUSER` and `PGPASSWORD` are not required when it is set.
Keep the application credential key outside the database and its dumps.

`install.py` only creates a virtual environment and installs pinned packages.
Use `python3.14 install.py` to omit the test dependency, or `make env` for the
same development setup shown above.

When configuring an external database, generate `PACIOLI_CREDENTIAL_KEY` with
`.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
Keep this key outside database backups: losing it makes encrypted connection
passwords and OFX responses unreadable. New records are encrypted on write.
For a database created by an older version, bring its schema up to date (see
[Schema migrations](#schema-migrations)), then run
`.venv/bin/python manage.py encrypt_existing_data` after setting the key
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

These journal constraints are part of the baseline schema migration. Zero and
fractional-cent USD postings, mismatched USD source amounts, and
foreign-currency postings without conversion evidence are rejected. Existing
chart records are preserved, so review any custom account classification and
cash designation.

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

`.venv/bin/python manage.py backup_db` writes a PostgreSQL custom-format
archive of the configured database to `PACIOLI_BACKUP_DIR`, or to
`.local/backups` by default, readable only by its owner. The archive holds
the whole ledger, so it is never mailed or uploaded; copy it to another disk
yourself, and keep `.local/config.json` (the encryption key) with it.

## Local CI

Run the complete local gate after `make env` or `python3.14 install.py --dev`:

```sh
make ci
```

This command checks available memory and disk space, starts a private
PostgreSQL cluster under `.local/ci` (or `PACIOLI_CI_SCRATCH`), checks Python
syntax and installed package dependencies, runs the tests serially, then stops
and removes the cluster. Keep the scratch directory on a fast local disk:
browser tests time out when Firefox profiles sit on a busy hard drive.
It needs local PostgreSQL server binaries and does not need `.env` or an
existing application database.

## Schema migrations

Alembic, through Flask-Migrate, owns the schema. Revisions live in
`migrations/versions/`. Revision 0001 is the schema the earlier hand-written
installers built, frozen in `0001_baseline.sql`. Revision 0002 aligns the one
database created before Alembic with that baseline. `make db-init` and
`make deploy` run `.venv/bin/python manage.py db upgrade`.

After changing a model, draft a revision and review it before committing:

```sh
.venv/bin/python manage.py db migrate -m "describe the change" --rev-id 0003
```

Autogenerate cannot see triggers, views, functions, or the raw-SQL tables listed
in `pacioli/database/migration_scope.py`; write those changes with
`op.execute`. `make ci` builds its database through the migrations and fails if
the models and the migrated schema disagree.

A database created before Alembic has no version table, and revision 0001
refuses to rebuild a populated database. Only for one brought up to date by
the installers at commit 1de7fd3: back it up, run `manage.py db stamp 0001`,
then `manage.py db upgrade`.

## Admin theme and front-end libraries

Flask-Admin comes from a patched build: tag `v2.2.1+pacioli.1` of the private
`PierreRochard/flask-admin-private` repository, a short patch series on
Flask-Admin 2.2.1 that its `PACIOLI.md` describes. The patches replace
Flask-Admin's Bootstrap 4 theme and its old bundled libraries with a Bootstrap
5 theme and pinned current releases: Bootstrap 5.3.8, Bootstrap Icons 1.13.1,
jQuery 3.7.1, Select2 4.1.0, daterangepicker 3.1.0 and moment 2.31.0. A small
inline editor replaces x-editable for `column_editable_list`. Map widgets and
Bootswatch swatches are not supported.

As with ofxtools, the wheel is not in git: copy
`flask_admin-2.2.1+pacioli.1-py3-none-any.whl` into `.local/wheels/` before
installing. To change the theme or a library, change the fork, run its tests,
then build and tag a new `+pacioli.N` wheel as `PACIOLI.md` describes. Point
`requirements.in` at the new wheel and run `make lock` and `make ci`.

`make ci` renders every admin page looking for Bootstrap 4 markup and checks
that the installed Flask-Admin is a patched build that no longer serves the old
libraries. It also drives the widgets in a headless Firefox.

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

Pacioli drives **your existing Firefox**. Firefox keeps its existing LastPass
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
the saved Chase password in Firefox or alter WebAuthn. Downloads follow your
normal Firefox settings; complete any Save As dialog in Firefox. Pacioli matches
the browser's download ID to its own tab, imports that exact QFX, and leaves the
original in your normal download location. It never scans your Downloads folder
or changes browser-wide download settings. Cancelling stops further processing;
Firefox and its other tabs remain open. Forgetting the Pacioli connection does
not erase your Firefox profile or sign you out there.

A supervised export asks which visible accounts to export, downloads up to the
last 24 months of QFX for each selected account, and imports the files. It does
not post journal entries. One session runs at a time; session progress and
cancellation are available on the same page. A stopped worker becomes
restartable after five minutes.

After one successful supervised export, an administrator may click **Sync now**.
It tries the previously selected accounts and checks every downloaded QFX
against their approved OFX account IDs before importing it. Repeated files and
transactions are deduplicated. There is no schedule or remote CI workflow.

If Chase presents one unambiguous one-time-code field, an administrator can
relay the code through Pacioli; it is encrypted briefly and cleared after use
or when the run ends. If Chase needs other verification during **Sync now**,
Pacioli waits up to ten minutes for **Open verification window**, which brings
its Chase tab forward in Firefox. Complete verification there; export resumes
with the same approved accounts. Cancellation, timeouts, sign-out, and changed
account choices stop the run without broadening account approval. Chase can
expire a session at any time, so unattended completion is best effort.

The browser worker needs the systemd user manager and a Firefox started with
the Pacioli launcher. The web container queues each session, and the host's
`pacioli-chase-launcher` path unit starts it as a transient user service
outside the web service's sandbox. The worker has a 3 GB
memory limit and a 40-minute runtime limit (including handoff waits). Only a
numeric run ID is passed to the worker. If Chase changes its login, MFA, or
download controls, the connector stops with a generic error instead of guessing
or bypassing a control.
The schema migrations create the connector tables; `make deploy` applies them
and restarts `pacioli-web.service`. Run `make ci` for the local
disposable-PostgreSQL test suite. The Firefox connector tests launch a real
headless Firefox against a local fixture bank: MFA relay, session reuse and
expiry, the verification handoff, account allowlist rejection, duplicate QFX,
and the page's live status updates are exercised. Human approvals are simulated
in these tests; a supervised live sign-in is still required to establish Chase
acceptance.

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
agent session (the since-removed Chromium connector) using browser UI clicks.
Earlier experiments that opened DevTools and invoked DOM `click()` ended at
Chase's `/logout` page. Those experiments also took longer, so the exact logout
trigger remains unknown. Keep the browser interaction supervised, use UI
clicks, and treat Chase session termination as a stop rather than trying to
bypass it.

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
accounts.

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

`make deploy` applies the migrations, including the event table, and restarts
the web service; reload the systemd user manager after changing unit files. The web service uses eight
threads, with at most four concurrent 20-second event streams; other clients
fall back to polling. Database sessions are released between stream checks.
The September 25 live update completed both bank accounts and verified real-file
replay without duplicate transactions. Chase returned an export error for the
first card even in a direct mouse-click comparison; three card intervals remain
pending. All-account live validation is therefore incomplete.
See [extension setup, limitations, and local tests](extensions/chase-local/README.md).
