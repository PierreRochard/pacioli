# Local Firefox connector

The extension controls only the Chase tab explicitly selected with its toolbar
button. It uses Firefox native messaging and a private same-user Unix socket;
there is no browser-accessible HTTP control endpoint. It does not copy cookies,
inspect LastPass, enter credentials, or start scheduled runs.

Install the native host once from the project root:

```bash
python3 scripts/install_chase_extension.py
```

In regular Firefox, open `about:debugging#/runtime/this-firefox`, choose **Load
Temporary Add-on**, and select this directory's `manifest.json`. Firefox requires
reloading a temporary extension after a browser restart. Permanent installation
in regular Firefox requires Mozilla signing; this project has not been submitted
to Mozilla or published. No signing requirement or browser protection is disabled.

Open Chase, click the extension toolbar button, then use
`https://pacioli.lan/connections/chase/local/` and press **Update Chase**.
The button is also available on Connections and the Chase connection page.
The default selects all registered Chase accounts and a seven-day overlap.
For a quiet account, the balance refresh also includes its latest known posted
transaction: Chase may otherwise return a no-activity page without a QFX file.
Existing transactions remain deduplicated, and requested coverage is retained.
Save different account choices or an overlap of 1–90 days under settings.
The worker derives intervals from committed QFX coverage, fills interior gaps,
and rechecks recent transactions through today. Missing or invalid coverage
requires review; a first QFX import registers a new account.

The page polls progress, and you can leave Pacioli while the worker continues.
Repeated button presses reuse an active job. After a failure, **Update Chase**
resumes its remaining intervals; completed intervals stay committed. A cancelled
job starts a fresh plan. Custom dates and a read-only model preview remain under
the advanced controls. Authentication takes place directly in Chase; press
**Update Chase** again afterward.

Resuming on a later day also refreshes every originally selected account through
that day, including accounts completed before the interruption. The saved account
selection and overlap policy remain fixed for that run. Live counts distinguish
new transactions from existing FITIDs and source-file duplicates.

Keep the selected Chase tab active within its window. If starting from a Pacioli
tab in the same window, the worker moves only the selected Chase tab to a separate
window without focusing it. The capture permission is
`activeTab`, not access to all websites. Tab activation/navigation is checked
before and after capture; a changed tab discards the image. Password/code inputs
and visible frames stop capture. Screenshots remain in memory and are sent only
to the pinned local Qwen model through loopback. Prompts and screenshots are not
stored in run diagnostics. If sign-in changes the site's origin, click the
extension toolbar button again to renew Firefox's capture permission.

The controller reads the selected account, format, and dates independently. It
permits only the next applicable export action in the model response schema;
the model corroborates it or requests assistance. The content script rechecks
the snapshot, target, enabled state, and download preconditions before acting.
Transfer, payment, settings, arbitrary JavaScript, selectors, and coordinates are
not exposed as operations.

The extension activates recognized DOM controls, including Chase's custom menus
and nested date fields. It distinguishes Quicken Web Connect (QFX) from Quicken
or Microsoft Money (QIF). A supervised live run on September 25, 2026 completed
the Qwen-assisted export/import for two bank accounts through the background
worker. Replaying those real sources left 7,044 transactions, zero duplicate
account/FITID groups, and zero journal entries unchanged. The first card export
received Chase's generic feature-error response, including with keyboard-entered
dates and a direct mouse click while the model and blob tracing were inactive.
Three card intervals remain pending in the saved run. Live compatibility for
those cards is not yet established; this is a partial live validation, not an
all-account success. Authentication expiry or a bank error pauses for assistance.

Downloads are tracked by Firefox download ID during one pending export. A matching
bank referrer, an exact URL correlated with a successful attachment response,
or a blob URL observed in the selected tab is required, in addition to an approved
bank origin. Chase uses detached blob downloads without a referrer. During an
authorized export, a temporary pass-through wrapper records only URLs created by
that tab's `URL.createObjectURL`. It does not read blob contents, credentials,
cookies, or unrelated responses. The wrapper is restored on release or after
60 seconds; an unobserved blob is rejected.
Concurrent downloads or an unverified source stop the run. Account identity,
currency, and QFX date bounds are checked again before import. Never infer success
merely from a new file in Downloads. Chase can set `DTSTART`/`DTEND` to its first
and last posting dates. For that exact representation, a verified export form and
Chase's recognized download-success page establish the requested interval; every
transaction must fall within it. `admin.chase_export_coverage` records these bounds
against the imported source atomically. Original QFX dates remain unchanged.
For an empty successful export, valid QFX bounds inside the requested interval
also require the verified form and success response before the wider interval
is accepted as having no activity.
Missing or inconsistent dates, an unexpected account, and unrecognized responses
stop for review. Both the incremental planner and reconciliation summaries use
the same coverage evidence, preferring verified request bounds for each file.

Each export records the QFX `LEDGERBAL` value and bank as-of date directly from
the parsed source before ofxtools can reuse a prior ORM balance. Same-as-of
revisions refresh the derived OFX balance while immutable source snapshots retain
earlier values. Exact-file replay cannot roll a newer balance back. A zero-activity
QFX can extend coverage and capture a balance without creating transactions.
Available balances and pending activity are not used. These balance changes need
no new database columns. Reload the extension after updating its browser code.

The update page shows exact USD amounts, imported coverage, bank as-of dates,
fetch times, net signed activity and provisional opening candidates. The candidate
is the balance immediately before coverage starts: posted balance minus signed
transactions from that start through the bank's as-of date, using Pacioli's UTC
end-of-day OFX convention. A credit-card debt remains negative in this signed
calculation. Missing/invalid coverage blocks the candidate. Conflicting implied
opening amounts from different balance dates also block it; importing missing
activity can resolve that discrepancy. Same-day balance revisions supersede older
observations for that day. Reconcile candidates against statements before posting.
Coverage evidence cannot detect every omission by the bank, and updates do not
create opening journals or other journal entries. Earlier history than the known
coverage start requires a custom export; updates do not claim to discover an
account's entire lifetime history. A new fetch can still carry an earlier bank
as-of date, especially on weekends; the page displays both dates explicitly.

Checkpoints contain the remaining intervals and completed source hashes, encrypted
in the existing run diagnostics field. An import commits before its interval is
marked complete, so replay after interruption is handled by the importer's source
hash and transaction-ID checks. Date ranges are split into adjacent inclusive
intervals when Chase reports a limit; a refused single-day interval stops. Runs
have bounded steps/intervals and share the existing browser worker lock. Database
transaction locks serialize update requests and QFX imports, including manual
uploads. Importing an overlapping source does not create another transaction with
the same bank transaction ID. A conflicting transaction requires review.

For an existing deployment, run `python manage.py migrate_chase_browser` to create
the preferences and export-coverage tables, rerun the native-host installer after host changes, and
reload the extension after updating it. No scheduled or remote CI job is added.

Run all checks serially with disposable local PostgreSQL:

```bash
make ci
PACIOLI_TEST_LOCAL_MODEL=1 .venv/bin/python scripts/ci_local.py -k production_model_contract -s
```

The GPU test uses an isolated fake-bank Firefox profile and real local inference;
it does not access Chase. The standard CI suite skips that explicit GPU test.
Native extension guards also run under Node's built-in test runner from pytest.

Local deployment uses the pinned `qwen3-vl:8b-instruct` model and disables Ollama
cloud features in its persistent server configuration. No model prompt or image
is sent to a hosted inference provider. See
[Ollama's cloud-disable setting](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features)
when deploying on another workstation. Firefox Snap uses its native messaging
[portal or proxy](https://firefox-source-docs.mozilla.org/toolkit/components/extensions/webextensions/native-messaging-portal-design.html)
to launch the host outside its sandbox; depending on the installed version, a
one-time OS prompt may appear.

Implementation references: [native messaging](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging),
[active-tab capture](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/tabs/captureVisibleTab),
[download metadata](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/downloads/DownloadItem).
