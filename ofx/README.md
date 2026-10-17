# pacioli-ofx

Pacioli's OFX component, written in SPARK (Ada 2022) and proved with
GNATprove. It has two jobs:

- `pacioli-ofx parse` reads one OFX response (a bank's QFX/OFX download) on
  standard input and writes it as typed JSON on standard output.
- `pacioli-ofx request` builds an OFX 2.2 statement request for direct
  download from `key=value` lines on standard input.

It replaces ofxtools 0.5. Everything else that ofxtools did for Pacioli is now
Pacioli's own code: acceptance policy, the database writes to the `ofx` schema,
and HTTPS transport (`pacioli/functions/ofx_file_import.py`,
`ofx_functions.py`, and `ofx_document.py`, which runs this program).

## Why a separate SPARK program

Bank files are untrusted input. The parser runs in its own process with no
environment, reads at most 10 MiB, and keeps everything in static, bounded
storage. GNATprove shows, for every unit:

- no run-time error can occur: no out-of-range index, overflow, or failed
  range check, whatever the input;
- every loop terminates;
- the stated contracts hold: dates are valid calendar dates after time-zone
  conversion, amounts are bounded digit strings, every stored value lies
  inside the input, and every decoded character is printable Unicode, so the
  JSON it writes needs no escapes other than `"` and `\`.

The program has no side effects besides its output, so the same input always
gives the same output. Pacioli decides what to accept.

## What it reads

The grammar is the part of OFX 1.x (SGML) and 2.x (XML) that Pacioli imports.

- **Header.** The OFX 1 colon header (versions 102, 103, 151, 160) or the XML
  declaration and `<?OFX?>` instruction (200 to 220). The header's charset
  decides how bytes become characters: `USASCII` with `1252` (Windows-1252,
  which Chase uses), `ISO-8859-1`, or `NONE` (ASCII only); `UTF-8`; XML
  documents in UTF-8 or US-ASCII. Bytes the charset does not define are
  rejected.
- **Sign-on.** `SONRS`: `STATUS`, `DTSERVER`, `LANGUAGE`, `FI/ORG`, `FI/FID`.
- **Statements.** `STMTTRNRS` in `BANKMSGSRSV1` and `CCSTMTTRNRS` in
  `CREDITCARDMSGSRSV1`: `TRNUID`, `STATUS`, and the statement with `CURDEF`,
  the account, `BANKTRANLIST` with `STMTTRN`s, `LEDGERBAL`, and `AVAILBAL`.
- **Transactions.** `TRNTYPE`, `DTPOSTED`, `DTUSER`, `DTAVAIL`, `TRNAMT`,
  `FITID`, `CORRECTFITID`, `CORRECTACTION`, `SRVRTID`, `CHECKNUM`, `REFNUM`,
  `SIC`, `PAYEEID`, `NAME`, `MEMO`, `INV401KSOURCE`, and `CURRENCY` or
  `ORIGCURRENCY`.

Ignored, but still checked for well-formed markup: private extensions (any
name with a `.`, such as Intuit's `INTU.BID`), marketing text, other balances
(`BALLIST`), pending transactions (`BANKTRANLISTP`), card rewards, cash-advance
limits, and interest rates. Everything else is rejected, including other
message sets (investment statements, security lists), payees and transfer
accounts inside transactions, and images.

Values are checked when read:

- **Text** decodes `&amp;` `&lt;` `&gt;` `&quot;` `&apos;` and numeric
  references; any other `&` is literal. Edge white space is dropped. Lengths
  follow the OFX specification and Pacioli's columns (`NAME` 32, `MEMO` 255,
  `FITID` 255, ...). Control characters are rejected.
- **Dates** are `YYYYMMDD`, optionally `HHMM` or `HHMMSS`, optionally 1 to 6
  fractional-second digits, optionally `[offset:zone]` with the offset in
  hours up to 14 and whole minutes (`-5`, `+5.5`, `9.75`). No zone means UTC.
  The result is UTC.
- **Amounts** are a sign, digits, and `.` or `,` as the decimal point, at most
  18 integer and 8 fractional digits. The digits are kept as written, so
  `-12.50` stays `-12.50`; leading zeros and the sign of zero are dropped.
- **Enumerations** are the values Pacioli's database types accept, for
  example `TRNTYPE` without OFX 2's `HOLD`.

A repeated element, an end tag that does not close the open aggregate, or
text between aggregate tags is rejected. Required elements are Pacioli's
policy and are checked there.

## JSON

```json
{"format": "pacioli-ofx/1",
 "header": {"major": 1, "version": 102, "security": "NONE", "encoding": "USASCII",
            "charset": "1252", "compression": "NONE", "oldfileuid": "NONE", "newfileuid": "NONE"},
 "signon": {"status": {"code": 0, "severity": "INFO"}, "dtserver": "2026-09-04T00:00:00Z",
            "language": "ENG", "fi": {"org": "B1", "fid": "10898"}},
 "statements": [
   {"type": "creditcard", "trnuid": "1", "status": {"code": 0, "severity": "INFO"},
    "statement": {"curdef": "USD", "account": {"acctid": "...", "acctkey": null},
                  "banktranlist": {"dtstart": "...", "dtend": "...", "transactions": [
                     {"trntype": "DEBIT", "dtposted": "2026-09-03T12:00:00Z", "trnamt": "-1.50",
                      "fitid": "...", "name": "...", "memo": null, "currency": null, "...": null}]},
                  "ledgerbal": {"balamt": "575.00", "dtasof": "2026-09-03T00:00:00Z"},
                  "availbal": null}}]}
```

Bank accounts have `bankid`, `branchid`, `acctid`, `accttype` and `acctkey`.
Every listed key is present; absent or empty elements are `null`. Status
messages are checked but not written, because banks put account and credential
details there.

A rejected document exits with status 2 and one line on standard error:
`{"error": "value_invalid", "path": "OFX/.../STMTTRN/TRNAMT", "offset": 1234}`.
The kinds are `header_invalid`, `syntax_invalid`, `element_unsupported`,
`element_duplicate`, `value_invalid`, `limit_exceeded` (nesting deeper than 32,
more than 32 statements or 100,000 transactions), and `input_too_large`. An
error names element names and a byte offset, never a value.

## Requests

`request` takes `account` (`bank` or `creditcard`), `dtclient`, `userid`,
`userpass`, `appid`, `appver`, `trnuid` and `newfileuid`, then `bankid`,
`acctid` and `accttype` for bank accounts or `acctid` for cards, and optional
`org`, `fid`, `clientuid`, `dtstart` and `dtend`. Pacioli supplies the time and
the UUIDs. The output is the request body in the layout ofxtools' client sent
with version 220, with `&`, `<` and `>` escaped. An invalid field exits with
status 2 and `{"error": "request_invalid", "field": "bankid"}`.

## Building and proving

[Alire](https://alire.ado.dev/) provides the tools: GNAT 16.1 and gprbuild 26
as the default toolchain, and GNATprove 16.1, which `alire.toml` names. From
the repository root:

```sh
make ofx     # build/bin/pacioli-ofx, a static binary
make prove   # GNATprove on every unit; any unproved check fails
```

`make ci` runs both before the tests, and `make deploy` builds the program that
the host worker and the web image run.

| Unit | Role |
|---|---|
| `ofx-header` | OFX 1 and 2 headers; the charset |
| `ofx-lexer` | tags and character data |
| `ofx-parser` | the grammar; fills `ofx-model` |
| `ofx-values` | each element's type, length, and allowed values |
| `ofx-text`, `ofx-dates`, `ofx-decimals` | characters, date-times, amounts |
| `ofx-emit`, `ofx-json` | JSON output |
| `ofx-requests` | statement requests |
| `ofx-io` | the process boundary; the only unit outside SPARK |
