# Local mapping prompt tuning, 2026-09-26

The final configuration passed **97/97 synthetic cases**: 73 regression cases
and 24 cases held out from prompt tuning. This measures the complete draft
workflow with calculated matching facts and bounded correction prompts. Its
first responses passed **92/97 (94.8%)**; five required one correction request.
This is a perfect score on these cases, not a guarantee about future transactions.

These are historical results for the earlier chart and receipt policy. The live
booking workflow now has an expanded chart, owner-authorized groceries/suspense
policies and exact-description mappings; see `local_mapping_booking.md`.
Live merchant classification produced semantic mistakes that required independent
review and suspense fallbacks. The 97/97 score does not establish live accuracy.

The work is in `scripts/evaluate_local_mappings.py`. It is a read-only evaluation
tool, not an activated mapping or posting feature. No application database
access, service restart, model installation change or external inference was
needed. Production mappings and journal entries were not created by this work.

## Final measured results

| Set | First response correct | After validation feedback | Supported drafts correct | Required deferrals correct |
|---|---:|---:|---:|---:|
| Expanded regression | 70/73 | 73/73 | 32/32 | 41/41 |
| Held-out validation | 22/24 | 24/24 | 12/12 | 12/12 |
| Combined | 92/97 | 97/97 | 44/44 | 53/53 |

No final proposal failed a scored classification or matching check. All five
corrections concerned mechanical failures: three merchant substring collisions
and two unsupported currencies. Each was corrected by the model to `needs_review`.
The evaluator did not replace answers with expected labels or silently turn
failures into passing results. The original drafts and their failing grades
remain in the artifacts.

The regression median warm latency was 0.801 seconds per case, including any
correction requests; the held-out median was 0.819 seconds. First requests took
about 4.4 seconds, including model loading. These are small synthetic draft
requests, not timings for a production import or whole-history rule preview.

## What changed

The prompt now states the accounting policy explicitly and includes contrasting
examples. It distinguishes interest paid from interest earned, a refund from
income, an Amazon item from a bank settlement, and supported categories from
missing chart categories. It requires every example to support a classification;
one receipt cannot classify another unknown purchase at the same broad retailer.

Keywords must use literal common input terms, including wages or payroll as
actually written. They cannot invent synonyms, omit necessary merchant
discriminators, ignore reversals, or assume word boundaries. The prompt explains
that `do_not_match` is a test list: the real mapping does not store or enforce
that list as an exclusion filter.

The final configuration supplies a common keyword candidate and calculated
matching facts. These facts come only from the input strings. They establish
neither the accounting category nor that the candidate is the only possible
keyword. Order and exclusions are tested separately, so the model is shown
when a candidate does not actually cover every example.

Up to two correction requests are allowed after executable keyword/currency
validation fails. Correction prompts contain the rejected draft, the exact
missed examples or unwanted matches, unsupported currencies, and a common
candidate. They never contain case IDs, expected categories, semantic grades or
the answer key. If a correction still fails, the benchmark fails. An incorrect
category that passes mechanical checks remains an error; it receives no
answer-key-assisted correction.

Pacioli's debit/credit fields are still constructed deterministically from the
category and source. This evaluation does not claim that the model independently
generated or proved the journal entries.

## Evaluation history and separation

The original 25 development and eight earlier holdout inputs and labels were
compared with the saved baseline artifacts and are unchanged. Their original
combined result was 29/33. Several longer prompts and explicit role formatting
regressed and were discarded. Prompt wording alone eventually reached 32/33;
one mechanical correction brought that configuration to 33/33.

That configuration scored **34/40** on a newly authored set. Its failures were
five keyword problems and one erroneous deferral of interest expense. That set
was then promoted to regression coverage, making 73 development cases; its file
retains the historical name `tests/fixtures/local_mapping_holdout_v2.json`.
The final configuration passes all 73. The earlier 34/40 result remains recorded
and is not presented as an untouched final holdout result.

A separate 24-case set is in `tests/fixtures/local_mapping_holdout_v3.json`.
It covers different merchants, reversal ordering, category contrasts, substring
collisions, missing categories, split amounts, partial item accounting, currencies
and role markers in transaction text. It was not used to tune the final prompt.
It ran against the preceding candidate as well as the final candidate and passed
24/24 both times. The final prompt edit addressed the regression set's remaining
receipt-coverage failure and was made before those holdout results were examined.

All sets are small, curated synthetic examples with supplied owner policy,
evidence and negative controls. None measures accuracy against user-reviewed
labels for the actual imported transactions. Passing includes correctly deferring
unsupported cases; it does not mean all 97 cases produced an active mapping.
Explanations are recorded, but their factual wording is not independently graded.

## Reproduction and evidence

The installed model is `qwen3-vl:8b-instruct`, digest
`0533d74300e4f9bc367d675d4e64ffd073d50ff16a2b4096cc2e8a1cf8c96319`,
on the workstation's RTX 5060 Ti. Final inference uses temperature 0, seed 42,
context 4096 and output limit 384. The existing raw model template and the
production Chase browser prompt were left unchanged. Explicit ChatML framing
was tested but is not part of the selected configuration.

The final policy/clarification hash is
`9fbba91779f429aa0988fbc875c26cfc160a7b39e5c38d4d3cac0a017f923c1c`.
Artifacts record the model digest, prompt, chart, schema, inference options,
corpus/source/request hashes, input records, every attempt, grades and timings:

- `.local/reports/local-mapping-tuned-v10-expanded-20260926.json`
- `.local/reports/local-mapping-tuned-v10-holdout-20260926.json`
- `.local/reports/local-mapping-tuned-fresh-holdout-20260926.json`
  preserves the earlier 34/40 result.
- Earlier numbered tuning artifacts preserve unsuccessful experiments.

Run these commands serially after checking `free -h` for at least 10 GiB
available RAM. The runner also checks GPU headroom, takes the existing exclusive
local-model lock, verifies the pinned model and unloads it on exit. Use new output
paths; existing artifacts are deliberately never overwritten.

```sh
mapping_reports=$(mktemp -d "$TMPDIR/pacioli-mapping-eval.XXXXXX")
PYTHONWARNINGS=ignore .venv/bin/python scripts/evaluate_local_mappings.py \
  --suite expanded --output "$mapping_reports/regression.json"
PYTHONWARNINGS=ignore .venv/bin/python scripts/evaluate_local_mappings.py \
  --cases-file tests/fixtures/local_mapping_holdout_v3.json \
  --output "$mapping_reports/validation.json"
```

The default tuned mode supplies matching context and permits two correction
requests. `--no-match-context --repair-attempts 0` disables both; `--policy baseline`
selects the original compact prompt and defaults to disabling both. The runner
exits nonzero when any case fails, so it can be used as an explicit local CI gate.
GPU inference was not added to an automatically triggered or remote workflow.

Eleven evaluator unit tests passed, covering wrong-but-balanced categories,
substring collisions, refund directions, candidate-order checks, prompt-boundary
escaping, label isolation, nonzero failure exits, immutable artifacts, original
failure retention and bounded correction attempts. The full application test
suite was not rerun for this evaluation-only change.

Before enabling accounting automation, the remaining application work is still
draft storage/review, full-history match previews, overlap handling, explicit
approval and posting provenance. These benchmark results do not replace those
controls or the need for reviewed examples from real history.
