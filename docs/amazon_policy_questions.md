# Amazon policy implementation and batch review

Open `/reconciliation/amazon/policies/` for the current classification review.
Saved owner decisions now feed actual source-reviewed classifications. The page
prefills supported categories in one editable table, orders supported purchases
by amount, and explains the specific remaining exceptions. It does not repeat
questions that the owner has already answered or ask the owner to research
ordinary product specifications.

## Review flow

The batch shows at most 100 purchases per page, with source titles, exact
all-in amounts, reasons, product references and expandable cost components.
Change a supported category, retain an item in suspense, or add a correction.
Previous explicit suspense decisions remain selected, including decisions made
through the older item-review page. Saved review explanations remain visible;
structured correction notes refill the editor without accumulating duplicate text. Unreviewed model proposals are not owner
choices.

One **Save classifications and review posting** action records the entire
selected page atomically and opens the existing financial posting preview.
A save containing only notes and suspense decisions returns to the review page
without a posting detour. There is no additional classification-confirmation screen. Financial posting
still uses its own frozen approval and durable worker. Saving a classification
does not write a journal, and selecting a category does not invent a new owner
fact or amend an existing answer.

`/reconciliation/amazon/policies/history/` preserves original answers and allows
deliberate revisions. Historical receipt statuses describe their original
save-time state; the main review shows current accounting treatment. Old
question and receipt links redirect to history. The five existing answers retain
their exact scope and ancestry after wording, prompt or chart revisions.
A changed answer or fact invalidates dependent classifications and approvals.

## Source-bound accounting implementation

`amazon_policy_application.import_implementation` records an encrypted immutable
`amazon_policy_implementation` document. Each entry binds one exact imported
occurrence, its complete source digest, a reviewed treatment and bounded allowed
categories, supporting owner-answer IDs, research references and current facts.
It is an internal reviewed implementation interface, not a title-matching rule
or a model approval. Product research can resolve a product-evidence gap but
cannot substitute for an unresolved owner property policy.

The reviewed policy expenses ordinary personal retail goods, including
ordinary electronics/components, minor household fittings and household equipment/furnishings. Development
or commercial marketing does not by itself prove business use or a capital
project. Simple wall attachment does not alone establish a building improvement.
The separate **Veterinary Care** category identifies veterinary treatment
products without claiming a diagnosis, treatment event or deduction.
**Household Equipment and Furnishings** covers ordinary movable appliances and
furnishings. Both are operating expense accounts, not cash accounts.

The four selected repair answers remain specific to those purchases; no broader
replacement history is inferred. Version 4 also expenses independently identified
minor shower/door hardware, point-of-use household appliances and appliance trim
under the ordinary personal-purchase default. A connection or attachment alone
does not establish a capital improvement. A retail product explicitly sold as a
rug/mat can be treated as a furnishing without claiming its actual installation.
An integrated tablet package follows its principal device when no separate
service consideration is recorded; included content is not assigned zero value.
Actual capital-project linkage, structural systems, permanent-landscaping
evidence, separately charged services and independently evidenced prepayments
still require their own treatment. No price split or dollar threshold is invented.

The seven previously unanswered ordinary-product cases were resolved through
complete source review and purpose-neutral categories. Manufacturer references
include [Sustee](https://sustee.jp/eng/story/),
[Woodland Scenics](https://woodlandscenics.woodlandscenics.com/show/item/C1211),
[Plastruct](https://plastruct.com/products/ppc-2),
[3M's adhesive application guide](https://multimedia.3m.com/mws/media/238690O/bonding-and-sealing-applic-prof.pdf),
and [United Scientific's catalog](https://www.unitedsci.com/media/wysiwyg/smartwave/porto/cms_source/United%20Lab%20Products%20Catalog%202019%20lores.pdf).
These establish product characteristics; they do not assert undocumented owner
use, precious-metal purity, boat ownership or business purpose.

## Integrity and concurrency

Classification preview and apply revalidate the exact source, immutable answer
ancestry, current facts, policy, chart, existing allocation and review state.
Batches use savepoints and locks: a second-row failure rolls back the first row
as well. PostgreSQL lock contention produces a retry message. Duplicate apply
returns the existing receipt. Pages require administrator authentication and
CSRF-protected actions, with private/no-store responses.

Journal posting independently repeats eligibility and source checks, preserves
source dates/currency/totals, and retains all cents of tax, shipping, discounts
and residual suspense. Existing posted siblings in partial settlements can be
carried across policy revisions only when their immutable manifest ancestry,
source decision, category, owner answers, implementation and facts still match.
This exception preserves a proven posting; it cannot authorize a fresh review
under stale policy. The approved financial retry is a no-op.

The current Qwen prompt is versioned as `amazon-item-v4`. Model output remains a
suggestion. This deployment applies independent source review and saved owner
choices; it does not establish a new model-accuracy benchmark. Future answers
supply review guidance, not a global automatic-posting permission. Exact product
suggestion rules remain separate from policy implementation packets.

## Deployment records

The version 3 and version 4 deployment verification records contain production
figures. They are kept outside version control in
`.local/docs/amazon_policy_questions_runs.md`.
