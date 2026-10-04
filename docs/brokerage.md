# Brokerage accounts (Charles Schwab)

Pacioli books a Schwab brokerage account from the files Schwab's website exports:
transaction history, realized gain/loss lots, and positions. Nothing connects to
Schwab. Everything runs locally except month-end closing prices, which come from
Yahoo on explicit request (see "Market prices" below).

## Owner decisions this design follows

- **Unrealized gains** are part of net worth, not income. They are booked to an
  Other Comprehensive Income (OCI) element, so monthly income and spending never
  swing with the market. Realized gains, dividends and interest are income.
- **Cost basis comes from Schwab's realized lots.** They match the 1099-B,
  including wash sales. Pacioli checks quantities and totals rather than
  computing its own lots.
- **Brokerage cash is an investment, not cash.** Transfers between checking and
  Schwab are investing flows, and they match like any other transfer.
- **History starts at account opening.** Activity kinds that aren't supported
  yet (bonds, options, margin, corporate actions) are flagged for review and
  never guessed.

## Chart

Registering an account (`scripts/schwab.py register`) adds four subaccounts named
after its label:
- `<Label> Brokerage Cash` (brokerage cash)
- `<Label> Securities at Cost` (Schwab's adjusted basis)
- `<Label> Market Value Adjustment` (market value minus cost)
- `<Label> Unrealized Gain (Loss)` (OCI)

The generic chart adds shared accounts:
- Investment Income: Qualified Dividends, Ordinary Dividends, Investment
  Interest, Capital Gain Distributions.
- Realized Gain/Loss, split into short and long term.
- Wash Sale Disallowed Loss.
- Investment Fees and Foreign Taxes Paid.

Only journals whose source is the brokerage can post to the four registered
subaccounts. A database trigger enforces this, so a checking leg can't be booked
straight into brokerage cash.

## How each row is booked

| Schwab action | Journal |
| --- | --- |
| MoneyLink, wire or journal cash transfer | Brokerage Cash ↔ Transfer Clearing. The checking side is matched on the Transfers page. |
| Buy, Reinvest Shares | Securities ← Brokerage Cash, at the full amount (commission included) |
| Sell | Using the lots closed that day: basis, plus short/long-term gain or loss legs. The legs sum exactly to the proceeds and the lot cost. |
| Dividends, capital gain distributions, bank interest | Brokerage Cash ← the income account |
| ADR/service/wire fees, foreign tax | Expense ← Brokerage Cash |
| Stock split | Quantity only; no journal |
| Journaled Shares, Internal or Security Transfer with only an amount (no symbol or quantity) | A cash transfer, as above. Sweeps show up as a same-day pair inside one account, which the Transfers page offers as a match. |
| Share move (one of those labels with only a signed quantity) | See *Share moves* below |
| Wash-sale disallowance | Securities ← Wash Sale Disallowed Loss |
| Month-end valuation | Market Value Adjustment ↔ Unrealized Gain (Loss), for the change in (market value − cost) |

**Dates:** journals are dated at the trade date, 00:00 UTC.

**Share moves.** Schwab reports shares moving with no cash amount. A positive
quantity means shares came in. Handling:
- *Offsetting:* a symbol's moves in one account on one day that sum to zero
  (moved out and back in) change neither shares nor cost. No journal.
- *Memo:* a notice with no economic effect, such as a monthly accrual recorded
  as pseudo-shares. Local Qwen proposes it (`scripts/schwab_share_moves.py
  propose --apply`) from the description and facts Pacioli computes. The owner
  approves it on Reconciliation → Schwab share moves, which records a rule for
  that action, symbol, description and direction. Code allows a memo only if
  the symbol never has a price or cash and never appears in a positions export.
  If that stops being true, the rows turn *not ready*.
- Everything else stays flagged (`share_move_needs_review`). That covers moves
  between accounts, from or to outside Schwab, and exchanges into another
  symbol. Booking these needs the moved cost basis. None is posted by guessing.

**Wash sales:** the disallowance is dated when the replacement makes it known.
That is the sale date if the replacement was bought in the 30 days before it,
otherwise the first replacement buy within the 30 days after. A later lots
export therefore can't change an earlier month.

## Openings

Schwab's transaction export covers at most four years. An account that already
held cash or shares at the start of that window gets one opening, dated at the
end of the day before the window. It is rolled back from the latest end-of-day
positions export through every imported activity row:

- cash: the positions cash line minus every activity amount;
- shares per symbol: the positions quantity minus every quantity change (approved
  memo moves excluded, offsetting moves included);
- cost per symbol: Schwab's cost basis minus buys, plus the basis of lots sold,
  minus wash-sale adjustments.

Adoption is blocked when it can't be verified:
- a symbol with no opening shares does not roll back to exactly $0 cost;
- an opening quantity is negative;
- the cost of a symbol held at the opening is unknown, because a sale has no
  lots, a move is unresolved, or Schwab shows no cost basis;
- the positions export is not end of day.

An adopted opening posts Brokerage Cash and Securities at Cost against Personal
Equity, as bank openings do. `value` also values the opening day, and that
point's unrealized gain goes to Personal Equity rather than OCI, so gains held
before the window never appear as one period's income. Activity dated on or
before an opening is refused (`before_opening`).

```sh
.venv/bin/python scripts/schwab.py opening                                      # statuses only
.venv/bin/python scripts/schwab.py opening --adopt --reviewer EMAIL --apply
.venv/bin/python scripts/schwab.py post --reviewer EMAIL --apply
```

## Workflow

```sh
.venv/bin/python scripts/schwab.py register Schwab 123 2025-03-01 --reviewer EMAIL --apply
.venv/bin/python scripts/schwab.py preview
.venv/bin/python scripts/schwab.py post --reviewer EMAIL --apply
.venv/bin/python scripts/schwab.py value --reviewer EMAIL --fetch --apply
.venv/bin/python scripts/schwab.py post --reviewer EMAIL --apply
```

**Preview and post.** Preview lists each source's status: posted, ready, not
ready (for example waiting for lots), unsupported, or stale. Post recomputes
everything under the ledger lock and refuses if anything changed since the
preview. It commits only when the ledger audit passes.

**Audit scopes.** The audit re-derives every brokerage journal from its source:
- Wrong postings block all posting batches.
- Unposted or unsupported rows block only closing the affected month.

**Imports.**
- A replayed file is a no-op.
- Overlapping exports must agree day by day.
- An export's last day may still grow if it was exported the same or the next day.
- A disagreeing export is saved as evidence and activates nothing.

## Market prices

Month-end market values use Yahoo Finance closing prices. This is an
owner-approved disclosure (AGENTS.md, "Approved disclosures"):
- Only ticker symbols and a date range are sent.
- Every request asks for the same universe: the private decoy list in
  `.local/decoy_tickers.txt` plus every symbol ever held (append-only). Never
  commit the decoy list: a public copy could be subtracted from a request to
  recover the holdings. Fetches refuse to run when the list is missing or has
  fewer than 200 distinct tickers.
- Only held symbols' prices are kept.
- Closes are un-adjusted for splits that happened after the valuation date.

Prices are fetched only by `value --fetch`, never on a schedule. A month can be
valued only once every source up to its end is posted.

## Close

Month closes record source contract version 2. That version adds brokerage facts
(activity, lots, wash adjustments and valuations) and the OCI totals to the
statement snapshot. Closes recorded under version 1 are re-verified with the
version 1 layout.
