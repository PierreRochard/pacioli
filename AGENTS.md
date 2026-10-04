# Agent Instructions

Scope: this file applies to the `projects/pacioli` tree. The repository-root
`AGENTS.md` (disk space safety) still applies.

## Financial Privacy

Pacioli holds the owner's real financial records: bank, card, brokerage, and
Amazon transactions, balances, account numbers, and the ledger built from them.
Anything an agent reads into its context is uploaded to a remote model
provider. Treat that upload as a disclosure, and keep it as small as the task
allows.

### Minimize what gets uploaded

- Do not read, `cat`, `head`, query, or print real financial data (database
  rows, OFX/QFX/CSV exports, statements, Amazon order files, evidence
  snapshots, logs containing transactions) unless the task genuinely cannot be
  done without it. Prefer working from code, schemas, tests, and fixtures.
- When you do need to inspect data, ask for the least revealing form:
  counts, aggregates, column names, types, null rates, distinct-value counts,
  or hashes instead of raw rows. Filter and aggregate locally in SQL or Python
  and print only the summary.
- When a few rows are unavoidable, limit them (`LIMIT 5`), select only the
  needed columns, and redact or truncate account numbers, names, addresses,
  and free-text memos before printing.
- Never print or paste credentials, tokens, session cookies, passkeys, `.env`
  values, full account or routing numbers, or SSNs/TINs. Reference them by
  variable name only.
- Never send financial data to external services (web search, pastebins,
  remote APIs, cloud artifacts or docs, issue trackers) unless the owner
  explicitly asks for that specific disclosure.
- Use synthetic or anonymized fixtures in tests and docs. Do not copy real
  transactions into test files, commit messages, docs, or changelogs.
- Keep records of real runs (production figures, item-level results, live
  evaluation output) in the untracked `.local/docs/`, not in `docs/`. The
  `origin` remote is a public GitHub repository.

### Maximize what gets done locally

Before doing a data-heavy task by reading the data yourself, prefer one of
these, in order:

1. **Put the logic in Pacioli.** If a task inspects, classifies, reconciles,
   or reports on financial data, and it is likely to recur, implement it as
   Pacioli code (a function under `pacioli/functions/`, a view, a `manage.py`
   command, or a SQL view) with tests on synthetic data. Run it locally and
   read only its summarized output. Deterministic code beats judgment calls
   made in a remote context.
2. **Use the local LLM.** When the task needs fuzzy judgment over real records
   (categorizing items, mapping merchants to accounts, reading page content),
   route it through the local Ollama instance at `127.0.0.1:11434` (see
   `pacioli/functions/chase_local_model.py` for the pinned-model pattern and
   `docs/local_mapping_*.md`). Keep the remote agent's role to writing and
   tuning the prompt/code, and evaluating results through aggregate metrics or
   owner-approved samples.
3. **Only then read data remotely**, following the minimization rules above.

Local-model rules:

- Connect only to loopback; refuse redirects or non-local endpoints.
- Pin model name and digest; do not silently fall back to a hosted model.
- Treat model output as untrusted advice: validate it, never execute it, and
  never let it post to the ledger without the existing review/approval gates.
- If Ollama is unavailable, stop and report rather than switching to a remote
  model or reading the data yourself.

### Approved disclosures

The owner has explicitly approved these specific disclosures. Anything broader
needs a fresh approval.

- **Market prices from Yahoo Finance (2026-09-29), for brokerage month-end
  valuations.** Only ticker symbols and dates may leave the machine. Every
  request uses the same fixed request universe: a large, private list of decoy
  tickers (`.local/decoy_tickers.txt`, never committed) plus every symbol ever
  held, kept append-only. The request therefore never varies with current
  holdings. Fetches run only on an explicit owner command or button, never on a
  schedule or on page load. Quantities, amounts, cost basis and account numbers
  are never sent.

### When in doubt

If a task seems to require uploading more than a small, redacted sample of real
financial data, stop and ask the owner first, and propose a local alternative
(new Pacioli code or a local-LLM pass) alongside the question.
