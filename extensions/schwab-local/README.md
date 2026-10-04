# Pacioli Local Schwab Connector

The extension controls only the Schwab tab you explicitly select with its
toolbar button. It talks to Pacioli through Firefox native messaging and a
private same-user Unix socket, the same design as the Chase connector. It does
not read credentials or cookies, sign in, or start anything on a schedule. You
sign in to Schwab yourself.

## Setup

1. Run `.venv/bin/python scripts/install_schwab_extension.py` from the project.
2. In Firefox, open `about:debugging#/runtime/this-firefox`.
3. Choose **Load Temporary Add-on…**. In the file picker, press Ctrl+L and enter
   the absolute path to this folder's `manifest.json`.
4. Sign in at schwab.com and open **Accounts → History**.
5. Open Firefox menu → **Extensions** → **Pacioli Local Schwab Connector**. The
   badge changes to **ON**.

Temporary add-ons disappear when Firefox quits. Load and select the connector
again after a restart. The Chase and Amazon connectors are separate add-ons and
can't select a Schwab tab.

## Structure probe

Pacioli needs the layout of Schwab's export controls before it can operate
them. With the Schwab tab selected, run:

```sh
.venv/bin/python scripts/schwab_probe.py --save
```

**What the probe reports:** only the page's interactive controls, meaning their
tag, role, enabled state, position, and labels.

**How labels are masked:**
- A word is kept only if it's on a fixed list of UI vocabulary (export, history,
  date, range…). Any other word becomes `*`.
- Any token containing a digit becomes `#`.
- Short all-caps words (ticker-like) become `*`.
- Controls inside tables or grids, where transactions and holdings appear, are
  skipped entirely.
- Sign-in and verification pages return nothing.

**Double-check:** the script re-checks every string against the same vocabulary
before printing or saving anything. No screenshot is taken.

Probe these pages: History (Transactions), the export dialog after pressing
Export, Realized Gain/Loss, and Positions.

## Screenshots for the local model

Before each export click, the local Qwen model checks a screenshot of the
export dialog. Schwab's History, Realized Gain/Loss and Positions are separate
page loads. Firefox's `activeTab` grant ends at each page load, so by owner
decision (2026-09-29) this connector has the `<all_urls>` permission, which
allows `captureVisibleTab` without a click per page.

**What the code captures:** only the tab selected with the toolbar button, and
only while it is active, on the origin that was selected, and showing a
recognized Schwab export dialog.

**Where screenshots go:** only to the pinned local model over loopback. They are
downsized in memory and never stored.

**Trade-off:** this is broader than the Chase connector, which keeps
`activeTab` only. Firefox itself would allow this extension to capture any page.

## Downloads

**What counts as a verified download:**
- The file is `.json`, `.csv` or `.xml`, at most 20 MiB.
- It comes from an approved Schwab origin.
- One of these ties it to the selected tab: a correlated attachment response, a
  blob URL the page created during the authorized export, or a same-origin
  referrer.

**What stops the run:** concurrent downloads or an unverified source.

