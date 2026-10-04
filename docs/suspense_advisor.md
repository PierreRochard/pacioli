# Evidence-aware local suspense review

The accounting advisor uses a pinned local Qwen text model to propose categories.
The browser connector continues to use its pinned vision model. A proposal does
not authorize a posting. Unknown purchase purpose remains provisional.

The new default for `classify_remaining_mappings.py prepare` is Qwen3-14B with
thinking enabled, an 8,192-token context, at most 1,536 generated tokens,
temperature 0.6, seed 42, and six CPU threads. Existing historical snapshots keep
their original classifier. `--legacy` prepares the old vision-model protocol for
comparison. The model name, digest, policy and source snapshot are recorded.

## Why this can improve classifications

The old live workflow asked for only a category and inspected up to four examples
per merchant group. Its broad refusal policy also suppressed supported service
classifications. The new prompt distinguishes a clear service from unknown
merchandise, uses explicit accounting contrasts, requests a short observable
justification before the category, and asks for the missing fact when deferring.
That justification is evidence to review, not a calibrated confidence score.

Every distinct descriptor, sign and currency in a selected group is included.
Oversized groups are explicitly deferred for a smaller review scope; unseen rows
are never classified from sampled examples. Exact saved descriptions remain the
executable rule scope. A grouping label is never an executable broad rule.

The validator checks schema, chart membership, currency and sign, and rejects
unsupported loan allocations, contractor-versus-utility classifications and
mixed fuel/car-wash classifications. These checks do not prove a merchant's
real-world purpose. Refund links, receipts, ownership and capital/expense splits
cannot be manufactured by a model. Transfer Clearing is still provisional.

## GPU and inference fundamentals

The measured workstation has an RTX 5060 Ti with 16,311 MiB VRAM and a Ryzen 9
5950X. About 0.6 GiB of GPU memory was occupied before experiments. The installed
Qwen3-14B Q4_K_M file is about 9.28 GB; its BF16 parameter weights alone would
require about 29.5 GB, before the cache and runtime buffers. Quantization is what
makes this model practical on this GPU. Q4_K_M mixes tensor precisions and block
metadata; it is not simply four bits for every stored value, and it is not a
claim that Blackwell's advertised NVFP4 peak throughput is attained.

At an 8K context the 14B runner reported full GPU residency and observed total
GPU use was about 10 GiB, leaving substantial desktop headroom. The existing
Ollama service already enables Flash Attention, q8_0 KV cache, one parallel
request and one loaded model. These global settings were inspected, not changed.

For conventional grouped-query attention the unquantized KV-cache estimate is
`2 × layers × KV_heads × head_dimension × context_tokens × bytes_per_element`.
The installed 14B model reports 40 layers, eight KV heads and 128-dimensional
keys/values. At 8K context, F16 cache is about 1.25 GiB. Q8 cache is approximately
half that, plus block-scale overhead. The 8B text model has 36 layers. Total
memory also includes weights, buffers and desktop use; the formula is not a
complete allocation prediction.

Context length is a capacity limit, not a reasoning-quality knob. A short,
relevant record does not improve merely because a huge cache is allocated.
Prefill processes the input; autoregressive decoding generates tokens serially.
For a small batch, weight/cache bandwidth and output-token count often matter
more than advertised peak arithmetic throughput. Thinking adds generated tokens
and latency. The development run generated 14,162 tokens in 340.7 seconds of
reported decode time, roughly 41.6 tokens/second. Its median request latency was
8.31 seconds; this is not the latency of a full bank update.

Qwen recommends non-greedy sampling for thinking mode. Here the output budget is
deliberately bounded for a small classification task. Truncation or malformed
output is retained as a failed proposal and cannot post. GPU work is serial,
uses the existing exclusive lock, checks host/GPU headroom, and unloads the
selected model on exit.

Increasing model size, output reasoning or temperature does not supply missing
receipts. Fine-tuning weights is premature without independent real labels:
training on existing model guesses or suspense labels would reinforce those
errors. Grouped merchant holdouts, explicit owner policies and new evidence are
more useful first steps than fitting the current provisional ledger.

A nominal 32B model at four bits already needs about 16 GB for weights alone,
before mixed-precision tensors, cache, runtime buffers and the desktop. It is
not a comfortable fully resident choice for this 16 GB card. CPU offload would
change latency and resource use and was not tested here. The 14B choice is the
best of the configurations measured here, not a claim of global optimality.

Primary references: [Qwen3-14B model card](https://huggingface.co/Qwen/Qwen3-14B),
[Ollama thinking](https://docs.ollama.com/capabilities/thinking),
[Ollama cache and concurrency configuration](https://docs.ollama.com/faq),
[NVIDIA RTX 5060 family](https://www.nvidia.com/en-us/geforce/graphics-cards/50-series/rtx-5060-family/).
Hardware allocations and speed figures above are local measurements.

## Reproducible comparison

`scripts/evaluate_suspense_advisor.py` compares fixed public development and
held-out cases. It never connects to the accounting database. Expected labels
are excluded from requests. Results distinguish supported-case recovery,
wrong/unsafe proposals, guard rejections, malformed output, model latency and
observed GPU residency. A perfect result on a small curated set would not prove
perfect live accuracy. Candidate selection uses development results; the held-out
set is not fed back into prompt tuning.

The 2026-09-26 comparison used 36 development cases (22 supported categories)
and 32 separately frozen held-out cases (19 supported categories):

| Configuration | Set | Supported recovered | Raw correct overall | Unsupported non-hold proposals | Median latency |
| --- | --- | --- | --- | --- | --- |
| Existing vision 8B, category-only | Development | 15/22 | 26/36 | 3 | 0.14 s |
| Text 8B, evidence prompt + thinking | Development | 20/22 | 30/36 | 3 | 4.96 s |
| Text 14B, evidence prompt + thinking | Development | 22/22 | 33/36 | 2 | 8.31 s |
| Existing vision 8B, category-only | Held out | 16/19 | 26/32 | 3 | 0.15 s |
| Text 14B, evidence prompt + thinking | Held out | 19/19 | 31/32 | 1 | 7.61 s |

The 14B development run also produced one malformed/truncated response. After
development, generic allocation guards were added; those guards rejected the
held-out run's one unsupported proposal (electrical installation as a utility).
Thus all 19 accepted held-out non-suspense proposals were correct, with zero
unsupported proposals passing validation. The raw model still made a mistake;
a blocked answer is not counted as a correct model answer. The older development
artifacts retain their original pre-guard scores. These synthetic cases do not
estimate live accuracy or establish a 100% guarantee. Model, prompt and thinking
settings changed together, so the comparison does not isolate a single cause.

Other development candidates—14B without thinking and explicit ChatML framing
for the installed vision model—had more invalid or unsupported answers. The
vision model's installed raw template differs from the native text template,
but framing alone did not solve the accounting task. No weights were fine-tuned.

Live bank descriptions revealed distribution shift: abbreviated names and
payment-processor text are harder than explicit synthetic service descriptions.
The model sometimes deferred identifiable services and sometimes invented a
merchant purpose, such as treating an airport name as airfare or hotel food as
lodging. A plausible explanation is not proof, and the synthetic acceptance
score must not be presented as live accuracy. Future evaluation should use
independently labeled real examples held out by merchant, before further tuning.
Account type, verified merchant facts, receipts and reconciled counterpart links
are useful additional input; existing provisional model labels are not gold data.

Run GPU experiments and local CI serially, after `free -h` confirms at least
10 GiB available memory. Use a fresh private output path for each experiment:

```sh
.venv/bin/python scripts/evaluate_suspense_advisor.py \
  --profile text14 --mode tuned --think --temperature 0.6 \
  --cases tests/fixtures/suspense_holdout.json --output "$TMPDIR/suspense-holdout.json"
.venv/bin/python scripts/ci_local.py -k 'suspense or local_mapping or chase_progress'
```

## Correcting already-booked suspense

`scripts/review_suspense.py prepare --batch PRIVATE_DIRECTORY` captures an
encrypted snapshot of provisional exact mappings, every linked journal, source
records and the chart. `classify --think` creates encrypted proposals; it never
changes the database. An optional encrypted selection names group IDs from that
snapshot. The candidate-discovery pass is not an accuracy benchmark.

After independent review, `preview` and `post` require a bounded snapshot and
review file containing exact mapping IDs, categories, reasons, proposal hashes,
reviewer and user authorization. The CLI binds each approval to its unchanged
model proposal and to exactly the source evidence supplied to the model.
Normally its category must agree with a validated proposal. An independent
reviewer may explicitly override an incorrect answer or abstention, but must
record a separate override reason, the original model category and validation
errors. Those overrides are retained in mapping provenance and counted separately
from supported model proposals; they never improve the model's benchmark score.
The correction function rechecks the live sources,
chart and complete mapping use, refuses closed-period corrections, and changes
only category legs, rule metadata and explanatory memos. Dates, amounts,
currencies, bank legs, journal IDs and source IDs remain unchanged.

Take a verified local database backup before `post`. Changes commit atomically
with audit before/after images. Derived trial balances are rebuilt once, while
source constraints, audit triggers and closed-period protection stay active.
Any invariant failure rolls everything back. An exact replay changes nothing;
stale or conflicting state requires a new review. Moving transactions to Transfer
Clearing must be reported separately from finally classifying expenses/income.

## Further descriptor review

The next prompt revision reads processor wrappers and explicit service qualifiers
before deciding whether a purpose is missing. It restores guidance for recreation,
fitness, personal care, clothing, transport, vehicle maintenance, bank fees and
tuition. A peer payment naming a cleaning service is distinguishable from a bare
recipient name. Unknown ACH credits, mixed merchandise, missing account categories
and unsupported allocations still require review.

Grouping now preserves numeric merchant names, Amazon service distinctions and
payment recipients. Only bounded, labelled reference suffixes and narrowly
recognized Amazon retail references are removed. Original examples and executable
exact-description mapping scopes remain unchanged. Existing encrypted snapshots
retain their old groups; prepare a fresh batch to use the revised grouping.

Purchase-plus-cash-back descriptions require a split even under the owner's whole
supermarket purchase policy. A deterministic guard inspects each normalized
description separately, without combining unrelated purchase/rewards records.
Malformed or empty source records cannot validate a proposal.

Household employment and withdrawal composition are separate facts. Owner-confirmed
wage-only household payments can support Household Services; combined wages,
taxes and fees need payroll records, including employer-tax versus employee-
withholding distinctions. Purpose confirmation alone does not supply that split.

For private real-description comparisons, `evaluate_suspense_advisor.py --private`
reads encrypted owner-only cases and writes an encrypted, exclusive report. Input
may be a case list or an object containing `cases` and the exact chart `categories`.
Expected categories, case IDs, reasons and transaction counts are excluded from
model requests. Private progress displays ordinal counts, not descriptions.
Failed runs retain partial encrypted results and return failure. Any wrong or
invalid completed case also returns a nonzero exit status.

Every mode preflights source shape, an 8,000-byte serialized-record budget and a
20,000-byte serialized-message budget. These are operational byte limits, not
exact tokenizer proofs. Reported prompt/generation counts are checked against
the 8K context and output reserve; capacity failures invalidate the original
response rather than discarding examples. The legacy category-only mode now
receives the same mechanical source, currency, sign and allocation checks; its
retained raw response is unchanged, and historical reports are not rewritten.

Reports separate expense/income recovery from provisional Transfer Clearing,
count accepted wrong-category proposals explicitly, and optionally weight results
by reviewed transaction counts. These are descriptor-review expectations, not
receipt-verified ground truth or counts of posted journals. Do not interpret a
lower suspense count as an accuracy metric by itself.

### September 26–27 real-description evaluation

The local review covered 4,263 suspense journals linked to 2,843 exact mappings.
Conservative regrouping increased the 680 original groups to 1,266, exposing 55
explicit cleaning-service payments and one transaction fee that had been buried
in oversized or unrelated groups. Generic Amazon merchandise still requires item
evidence; a processor or marketplace name is not an expense category.

With the same local Qwen3 14B model and inference settings, the selected prompt
improved the reviewed development set from 30/46 to 39/46 valid correct answers.
Supported expense recovery rose from 10/15 to 13/15 groups, while accepted wrong
categories fell from six to two. Provisional clearing recovery remained 0/3.
These are development results, not an independent accuracy estimate.

After the prompt was frozen, it matched 15/24 independently reviewed expectations
on new merchants, recovering 5/12 supported groups and producing three accepted
wrong-category suggestions. The existing 25-case regression set scored 21/25,
with three accepted wrong suggestions. Six owner-context/split cases scored 5/6;
the model correctly held mixed payroll but also over-deferred a hypothetical
verified wage-only payment. No further prompt tuning used these validation results.
The labels are descriptor judgments, not receipt-verified ground truth.

The encrypted local study staged suggestions for 96 transactions across 83 exact
mappings, without posting or changing mappings. Household-payroll clarification
was saved in three app review records covering 176 journals; component amounts
remain required. Local CI passed 631 tests with one skipped. Full methodology,
known errors and encrypted artifact references are in the owner-only report at
`.local/suspense-tuning-20260927/REPORT.md`. These results do not support unattended
posting or a claim of 100% accuracy.
