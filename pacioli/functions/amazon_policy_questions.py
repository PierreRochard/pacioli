"""Collect source-bound owner decisions without changing accounting eligibility.

Recommendations and confirmed preferences are distinct from implemented policy.
This module writes only encrypted preview/answer evidence and its immutable
index. Existing source, policy, item-review and journal controls stay in force.
"""
from copy import deepcopy
from decimal import Decimal
import re

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pacioli.extensions import db
from pacioli.models import AmazonPolicyAnswers, AmazonSuspensePlans
from pacioli.functions import amazon_suspense as suspense
from pacioli.functions import amazon_item_workflow as workflow
from pacioli.functions.reconciliation import digest, evidence, money


VERSION = 'amazon-policy-questions-v1'
MAX_ITEMS = 100
SCOPES = ('this_item', 'selected', 'future')


def _choice(key, label, explanation, recommended=False):
    return dict(key=key, label=label, explanation=explanation, recommended=recommended)


def _definition(key, title, prompt, kind, choices):
    return dict(key=key, title=title, prompt=prompt, kind=kind,
        allow_future=kind == 'policy', needs_threshold_parameters=key == 'durable_goods',
        choices=choices + [
            _choice('custom', 'Use my own answer', 'Describe the treatment or fact you want independently reviewed.'),
            _choice('defer', 'Decide later', 'Keep these purchases unresolved and return to this question later.')])


QUESTIONS = {
    'durable_goods': _definition('durable_goods', 'Choose a personal durable-goods policy',
        'How should ordinary equipment, furnishings and related components be treated in your personal ledger? '
        'Installed improvements and project components still need separate review; this preference cannot identify them by itself.',
        'policy', [
            _choice('threshold', 'Use a threshold I specify',
                'Expense below an explicit all-in cost threshold and review property at or above it. Choose the amount '
                'and separately usable item versus complete assembly basis. No threshold or useful life is assumed.', True),
            _choice('expense', 'Expense ordinary movable goods',
                'Prefer immediate expense for ordinary movable equipment and furnishings. Review selected major '
                'property, installed improvements, projects and mixed bundles separately.'),
            _choice('assets', 'Track durable goods as assets',
                'Plan an acquisition register and source-supported asset destinations. Valuation and lifecycle '
                'rules still need separate decisions; no depreciation is invented.')]),
    'protective_accessories': _definition('protective_accessories', 'Define small household protective accessories',
        'Should simple removable chair-leg caps and waste-bin lid restraints belong in Household Supplies?', 'policy', [
            _choice('supplies', 'Include in Household Supplies',
                'Propose a narrow extension for small removable non-powered protective accessories, excluding furniture and installed projects.', True),
            _choice('separate', 'Use a separate accessory category',
                'Keep protective household accessories distinct; specify the preferred category in your own answer if needed.')]),
    'sound_workbook': _definition('sound_workbook', 'Define an incidental sound feature in an activity book',
        'Should a children\'s activity workbook with a sound button remain an Entertainment purchase?', 'policy', [
            _choice('entertainment', 'Classify the whole activity workbook as Entertainment',
                'Propose a narrow incidental-feature exception. Do not invent a price split or apply it to standalone electronic toys.', True),
            _choice('equipment', 'Review it under equipment treatment',
                'Keep the powered-feature boundary strict and review the workbook under the durable-goods policy.')]),
    'veterinary_care': _definition('veterinary_care', 'Define veterinary treatment purchases',
        'Which category should cover veterinary treatment supplies when household-pet use is established?', 'policy', [
            _choice('pet_care', 'Include veterinary supplies in Pet Care',
                'Propose a defined veterinary-care extension. Actual household-pet use still needs confirmation; this does not infer a diagnosis or human medical treatment.', True),
            _choice('separate', 'Create a distinct veterinary-care category',
                'Plan a separate expense category and its exact boundary before reviewing purchases.')]),
    'repair_context': _definition('repair_context', 'Confirm replacement versus project context',
        'For only the purchases you select, were these repairs to an existing home or appliance, or parts of a new installation or improvement?', 'context', [
            _choice('replacement', 'Routine replacement or repair',
                'Confirm replacement or repair of an existing household item. Equipment-repair policy may still need implementation.'),
            _choice('project', 'New installation or improvement',
                'Record the project or asset in your own answer so its components can be reviewed together.')]),
    'craft_context': _definition('craft_context', 'Confirm the use of craft and multipurpose materials',
        'Were the selected materials used for ordinary personal modeling/crafts, or for an equipment or improvement project?', 'context', [
            _choice('craft', 'Ordinary personal modeling or craft use',
                'This is a source-based guess for the modeling materials. Confirm each selected purchase; generic tape alone does not establish its use.', True),
            _choice('project', 'Equipment or improvement project',
                'Record project context before any accounting classification.')]),
    'sensor_specification': _definition('sensor_specification', 'Confirm how the soil-moisture indicator works',
        'Is this soil-moisture product a passive indicator or a powered electronic device?', 'context', [
            _choice('passive', 'Passive indicator; no power source',
                'Confirm from the product or its specifications; the title alone does not prove this.'),
            _choice('powered', 'Powered electronic sensor',
                'Equipment policy is still needed before its accounting treatment can be reviewed.')]),
    'jewelry_material': _definition('jewelry_material', 'Confirm the crucifix material',
        'Does “oxidized silver” mean base-metal or costume material, or precious metal?', 'context', [
            _choice('base_metal', 'Base-metal or costume material',
                'Confirm material from the item or receipt. A silver color description does not establish precious-metal content.'),
            _choice('precious_metal', 'Precious metal',
                'Record the actual material for review; this answer does not infer investment or gift purpose.')]),
    'marine_context': _definition('marine_context', 'Identify the marine sealant use',
        'What did you actually use the marine-marketed sealant for?', 'context', [
            _choice('household_repair', 'Routine household repair',
                'Confirm ordinary household repair use; the marketing label does not prove boat ownership.'),
            _choice('craft', 'Personal craft or model',
                'Confirm ordinary personal craft use. Use your own answer for a vehicle, boat or larger project.')]),
    'tray_context': _definition('tray_context', 'Identify the tray use',
        'Was the laboratory-style tray an ordinary personal container or part of another activity/project?', 'context', [
            _choice('household', 'Ordinary household container',
                'Confirm actual household use rather than inferring it from the seller\'s department.'),
            _choice('craft', 'Ordinary personal craft tray',
                'Confirm personal craft use; describe another purpose in your own answer.')]),
}


def _question(row):
    """Presentation grouping only; no keyword authorizes accounting treatment."""
    reason = row.get('reason') or ''
    title = row.get('source_title', row.get('title')) or ''
    reason = reason.lower() if isinstance(reason, str) else ''
    title = title.lower() if isinstance(title, str) else ''
    gate = row.get('policy_gate')
    if gate == 'needs_property':
        if ('floor-protection caps' in reason or 'waste-container lid restraint' in reason):
            key = 'protective_accessories'
        elif 'workbook' in reason and 'sound button' in reason:
            key = 'sound_workbook'
        elif 'veterinary' in reason:
            key = 'veterinary_care'
        elif any(word in reason for word in ('powered', 'equipment', 'furnishing', 'furniture',
                 'fixture', 'appliance', 'hardware', 'assembly', 'scooter', 'landscaping')):
            key = 'durable_goods'
        else:
            key = None
    elif gate == 'needs_fact':
        if any(word in title for word in ('socket', 'central vacuum', 'drain stopper')):
            key = 'repair_context'
        elif any(word in title for word in ('realistic water', 'plastic weld', 'masking tape')):
            key = 'craft_context'
        elif 'soil moisture' in title or 'soil-moisture' in reason:
            key = 'sensor_specification'
        elif 'crucifix' in title:
            key = 'jewelry_material'
        elif 'marine' in title:
            key = 'marine_context'
        elif 'tray' in title:
            key = 'tray_context'
        else:
            key = None
    else:
        key = None
    if key:
        return deepcopy(QUESTIONS[key])
    kind = 'policy' if gate == 'needs_property' else 'context'
    return _definition('item_' + row['item_id'], 'Review one purchase ' + ('policy' if kind == 'policy' else 'context'),
        row.get('action_needed') or row.get('reason') or 'Describe the missing treatment or purchase context.', kind, [])


def _answers(plan_id):
    """Project immutable answers per occurrence, preserving partial selections."""
    latest, rows = {}, []
    for row in db.session.query(AmazonPolicyAnswers).filter_by(plan_id=plan_id).order_by(AmazonPolicyAnswers.id):
        body = workflow._document(row.evidence_id, 'amazon_policy_answer')
        preview = workflow._document(row.preview_evidence_id, 'amazon_policy_preview')
        ids = preview.get('item_ids', [])
        if (body.get('version') != VERSION or body.get('plan_id') != plan_id
                or body.get('question_key') != row.question_key
                or body.get('preview_evidence_id') != row.preview_evidence_id
                or body.get('preview_digest') != digest(preview)
                or preview.get('question', {}).get('key') != row.question_key
                or preview.get('plan_id') != plan_id
                or not ids or len(set(ids)) != len(ids)
                or body.get('reviewer') != preview.get('reviewer')):
            raise ValueError('Saved policy answer evidence changed')
        previous = {i: latest[(row.question_key, i)]['id'] if (row.question_key, i) in latest else None for i in ids}
        if body.get('previous_answer_ids') != previous:
            raise ValueError('Saved policy answer ancestry changed')
        answer = dict(id=row.id, evidence_id=row.evidence_id, preview_id=row.preview_evidence_id,
            question_key=row.question_key, choice=preview['choice']['key'], choice_label=preview['choice']['label'],
            scope=preview['scope'], custom_text=preview['custom_text'], parameters=preview['parameters'],
            status=preview['status'], reviewer=body['reviewer'], item_ids=ids,
            amount=preview['amount'], created_at=row.created_at.isoformat(),
            policy_digest=preview['policy_digest'], chart_digest=preview['chart_digest'],
            source_manifest_digest=preview['source_manifest_digest'],
            definition_digest=digest(preview['question']), fact_evidence_ids=preview['fact_evidence_ids'])
        rows.append(answer)
        for identifier in ids:
            latest[(row.question_key, identifier)] = answer
    return latest, rows


def _snapshot(plan_id, locked=False):
    plan, doc, source_manifest = suspense._plan(plan_id, lock=locked)
    if locked:
        suspense._require_posting_idle()
        # Prevent source/chart/ledger changes between the preview check and its
        # evidence write. The same suspense advisory lock serializes item posts.
        try:
            db.session.execute(text('LOCK TABLE ofx.stmttrn, ofx.acctfrom, ofx.ccacctfrom, ofx.bankacctfrom, '
                'bookkeeping.bank_feed_accounts, bookkeeping.subaccounts, bookkeeping.accounts, '
                'bookkeeping.classifications, bookkeeping.elements, bookkeeping.journal_entries, '
                'amazon.evidence_bundles, amazon.item_jobs, amazon.item_runs IN SHARE MODE'))
        except DBAPIError as exc:
            if getattr(exc.orig, 'pgcode', None) == '55P03':
                raise ValueError('Another accounting action is in progress; wait, refresh this page and retry') from None
            raise
    run, manifest, jobs = suspense._run(plan, source_manifest)
    policy_digest, chart_digest = workflow._policy(), suspense._chart_digest()
    current = (manifest['policy_digest'] == policy_digest
               and digest(manifest['chart']) == chart_digest)
    if locked and (not current or run.state in ('queued', 'running')):
        raise ValueError('Prepare a current, completed review before answering these questions')
    source_items = workflow._items(source_manifest)
    posted = suspense._posted_items(source_manifest, {row['item_id'] for row in doc['items']})
    latest, history = _answers(plan.id)
    facts = suspense._facts(plan.id)
    grouped = {}
    unverified, resolved = [], []
    for row in doc['items']:
        identifier = row['item_id']
        financial = posted.get(identifier)
        if financial is None:
            unverified.append(row)
            continue
        if financial['category'] != 'Bookkeeping Suspense':
            resolved.append(row)
            continue
        definition = _question(row)
        question = grouped.setdefault(definition['key'], dict(definition, items=[]))
        original = source_items[identifier]
        answer = latest.get((definition['key'], identifier))
        if answer:
            # Preserve what the owner actually answered. Accounting policy,
            # chart and explanatory-copy revisions invalidate implementations
            # and pending previews, not the historical meaning of an answer.
            answer = dict(answer, stale=(answer['source_manifest_digest'] != doc['source_manifest_digest']
                or answer['fact_evidence_ids'].get(identifier) !=
                    (facts[identifier][-1]['evidence_id'] if facts.get(identifier) else None)))
        question['items'].append(dict(item_id=identifier, title=original['title'], amount=str(money(row['amount'])),
            quantity=original['quantity'], components=original['components'], source_refs=original['source_refs'],
            reason=row.get('reason', ''), answer=answer))
    questions = list(grouped.values())
    for question in questions:
        question['items'].sort(key=lambda item: (-money(item['amount']), item['item_id']))
        question.update(count=len(question['items']),
            amount=str(sum((money(i['amount']) for i in question['items']), Decimal('0.00'))),
            examples=[{key: item[key] for key in ('item_id', 'title', 'amount')} for item in question['items'][:3]],
            answered_count=sum(bool(i['answer'] and not i['answer']['stale']) for i in question['items']))
    questions.sort(key=lambda q: (q['kind'] != 'policy', -money(q['amount']), q['key']))
    token = digest(dict(version=VERSION, review=suspense._token(plan, source_manifest), ledger=posted,
        answers=[(r['id'], r['evidence_id']) for r in history],
        definitions=[{k: v for k, v in q.items() if k not in ('items', 'examples', 'answered_count')} for q in questions]))
    return dict(plan=plan, doc=doc, source_manifest=source_manifest, manifest=manifest,
        current=current, run=run, questions=questions, token=token, latest=latest, history=history, facts=facts,
        unverified=unverified, resolved=resolved)


def page(plan_id=None, question_key=None):
    if plan_id is None:
        last = db.session.query(AmazonSuspensePlans).order_by(AmazonSuspensePlans.id.desc()).first()
        if last is None:
            return dict(plan=None, token='', questions=[], question=None, saved_answers=[],
                totals=dict(remaining_count=0, remaining_amount='0.00', unverified_count=0, answered_count=0, deferred_count=0))
        plan_id = last.id
    state = _snapshot(plan_id)
    questions = state['questions']
    if question_key is not None and (not isinstance(question_key, str)
                                     or question_key not in {q['key'] for q in questions}):
        raise ValueError('Choose a question from the current unresolved purchases')
    if question_key is not None:
        selected = next(q for q in questions if q['key'] == question_key)
    else:
        selected = next((q for q in questions if q['answered_count'] < q['count']), questions[0] if questions else None)
    items = [i for q in questions for i in q['items']]
    plan = state['plan']
    return dict(plan=dict(id=plan.id, review_run_id=plan.review_run_id,
        current_policy=state['current'], posting_active=suspense._posting_active()), token=state['token'],
        questions=questions, question=selected, saved_answers=state['history'],
        totals=dict(remaining_count=len(items), remaining_amount=str(sum((money(i['amount']) for i in items), Decimal('0.00'))),
            unverified_count=len(state['unverified']),
            answered_count=sum(bool(i['answer'] and not i['answer']['stale'] and i['answer']['status'] != 'deferred') for i in items),
            deferred_count=sum(bool(i['answer'] and not i['answer']['stale'] and i['answer']['status'] == 'deferred') for i in items)))


def _validate_selection(question, choice, scope, item_ids, custom_text, parameters):
    if (not isinstance(choice, str) or choice not in {c['key'] for c in question['choices']}
            or scope not in SCOPES or (scope == 'future' and not question['allow_future'])
            or not isinstance(item_ids, (list, tuple)) or not 1 <= len(item_ids) <= MAX_ITEMS
            or any(not isinstance(i, str) for i in item_ids) or len(set(item_ids)) != len(item_ids)
            or (scope == 'this_item' and len(item_ids) != 1)
            or not set(item_ids).issubset({i['item_id'] for i in question['items']})):
        raise ValueError('Choose a valid answer, explicit scope and distinct purchases from this question')
    if not isinstance(custom_text, str) or len(custom_text) > 2000 or (choice == 'custom' and not custom_text.strip()):
        raise ValueError('Provide your own answer in at most 2000 characters')
    if not isinstance(parameters, dict) or any(k not in ('threshold_amount', 'threshold_basis') for k in parameters):
        raise ValueError('Unknown policy parameters')
    params = {k: v for k, v in parameters.items() if v != ''}
    if question['key'] == 'durable_goods' and choice == 'threshold':
        amount, basis = params.get('threshold_amount'), params.get('threshold_basis')
        if (set(params) != {'threshold_amount', 'threshold_basis'} or not isinstance(amount, str)
                or not re.fullmatch(r'(?:0|[1-9][0-9]{0,7})(?:\.[0-9]{1,2})?', amount)
                or basis not in ('separate_item', 'complete_assembly')
                or not Decimal('0') < money(amount) <= Decimal('10000000')):
            raise ValueError('Specify a positive threshold up to 10000000.00 USD and its item or assembly basis')
        params = dict(threshold_amount=format(money(amount), '.2f'), threshold_basis=basis)
    elif params:
        raise ValueError('Threshold parameters belong only to the threshold choice')
    if choice == 'defer' and scope == 'future':
        raise ValueError('Deciding later does not create a future policy preference')
    return next(c for c in question['choices'] if c['key'] == choice), params


def _next_steps(question, choice, scope):
    if choice == 'defer':
        return ['These purchases remain unresolved. Return to this question when more information is available.']
    steps = ['No journal, saved item classification, accounting policy, or eligibility gate changes with this answer.']
    if question['kind'] == 'policy':
        steps.append('Implement and test the chosen category and treatment rules, then prepare a fresh source-bound review.')
        if question['key'] == 'durable_goods':
            steps.append('Independently identify movable goods, installed improvements, replacement repairs, complete assemblies and mixed bundles.')
            if choice == 'threshold':
                steps.append('Use exact costs including attributed tax, shipping and discounts. Verify packs and separately usable units; do not divide by source quantity without evidence.')
            if choice in ('assets', 'threshold'):
                steps.append('For property retained as assets, establish acquisition records, asset destinations and lifecycle treatment before posting; no useful lives or depreciation are inferred.')
        if question['key'] == 'veterinary_care':
            steps.append('Confirm that each selected veterinary purchase was for household-pet use before reviewing its category.')
        if choice == 'custom':
            steps.append('Translate the custom answer into an explicit, testable rule; it cannot be executed as written.')
    else:
        steps.append('Independently review the owner context against each exact source purchase before saving classification evidence.')
    if scope == 'future':
        steps.append('Future scope records reusable-policy intent only. It does not create product mappings or authorize any future purchase or posting.')
    steps.append('Review exact proposed allocations and use the separate journal-posting approval only when all evidence and rules are ready.')
    return steps


def prepare_preview(plan_id, question_key, choice, scope, item_ids, custom_text, parameters, expected_digest, reviewer):
    suspense._actor(reviewer)
    with db.session.begin_nested():
        state = _snapshot(plan_id, locked=True)
        if not suspense._hex(expected_digest) or expected_digest != state['token']:
            raise ValueError('The sources, ledger, policy, facts or answers changed; refresh this page')
        question = next((q for q in state['questions'] if q['key'] == question_key), None)
        if question is None:
            raise ValueError('Choose a question from the current unresolved purchases')
        selected_choice, params = _validate_selection(question, choice, scope, item_ids, custom_text, parameters)
        ids = sorted(item_ids)
        suspense._validate_sources(state['source_manifest'], set(ids))
        items = [{k: v for k, v in item.items() if k != 'answer'} for item in question['items'] if item['item_id'] in ids]
        status = ('deferred' if choice == 'defer' else 'saved_policy_pending_implementation'
                  if question['kind'] == 'policy' else 'saved_context_pending_review')
        body = dict(version=VERSION, plan_id=plan_id, review_run_id=state['plan'].review_run_id,
            question={k: v for k, v in question.items()
            if k not in ('items', 'examples', 'answered_count', 'count', 'amount')}, choice=selected_choice,
            scope=scope, item_ids=ids, items=items, count=len(items),
            amount=str(sum((money(i['amount']) for i in items), Decimal('0.00'))),
            custom_text=custom_text.strip(), parameters=params, status=status,
            next_steps=_next_steps(question, choice, scope), baseline_token=state['token'],
            source_manifest_digest=state['doc']['source_manifest_digest'], policy_digest=workflow._policy(),
            chart_digest=suspense._chart_digest(), reviewer=reviewer.strip(),
            fact_evidence_ids={i: state['facts'][i][-1]['evidence_id'] if state['facts'].get(i) else None for i in ids},
            previous_answer_ids={i: state['latest'][(question_key, i)]['id']
                if (question_key, i) in state['latest'] else None for i in ids})
        document = evidence('amazon_policy_preview', body, reviewer.strip())
        return document.id


def _preview(preview_id, reviewer):
    suspense._actor(reviewer)
    body = workflow._document(suspense._id(preview_id), 'amazon_policy_preview')
    if body.get('version') != VERSION or body.get('reviewer') != reviewer.strip():
        raise ValueError('This preview belongs to a different reviewer or question version')
    return body


def preview_page(preview_id, reviewer):
    body = _preview(preview_id, reviewer)
    answer = db.session.query(AmazonPolicyAnswers).filter_by(preview_evidence_id=preview_id).one_or_none()
    state = _snapshot(body['plan_id'])
    # A confirmed preview is a historical receipt. It must not become an
    # actionable new answer merely because another choice later supersedes it.
    stale = state['token'] != body['baseline_token'] and answer is None
    return dict(preview_id=preview_id, token=digest(body), plan_id=body['plan_id'], review_run_id=body['review_run_id'],
        question=body['question'], choice=body['choice'], scope=body['scope'], items=body['items'],
        count=body['count'], amount=body['amount'], custom_text=body['custom_text'], parameters=body['parameters'],
        status=body['status'], next_steps=body['next_steps'], already_confirmed=answer is not None,
        answer_id=answer.id if answer else None, stale=stale, posting_active=suspense._posting_active())


def confirm(preview_id, expected_digest, reviewer):
    body = _preview(preview_id, reviewer)
    if not suspense._hex(expected_digest) or expected_digest != digest(body):
        raise ValueError('The preview changed; reopen the saved preview')
    with db.session.begin_nested():
        suspense._lock()
        old = db.session.query(AmazonPolicyAnswers).filter_by(preview_evidence_id=preview_id).one_or_none()
        if old:
            # Verify receipt ancestry, but replay writes nothing even after a
            # later answer supersedes some of these occurrences.
            _, history = _answers(body['plan_id'])
            found = next(a for a in history if a['id'] == old.id)
            return dict(id=old.id, plan_id=body['plan_id'], question_key=old.question_key,
                status=found['status'], count=body['count'], amount=body['amount'], replayed=True)
        state = _snapshot(body['plan_id'], locked=True)
        if state['token'] != body['baseline_token']:
            raise ValueError('The sources, ledger, policy, facts or answers changed; create a new preview')
        question = next((q for q in state['questions'] if q['key'] == body['question']['key']), None)
        if question is None:
            raise ValueError('The preview no longer belongs to unresolved purchases')
        _validate_selection(question, body['choice']['key'], body['scope'], body['item_ids'],
                            body['custom_text'], body['parameters'])
        suspense._validate_sources(state['source_manifest'], set(body['item_ids']))
        answer_body = dict(version=VERSION, plan_id=body['plan_id'], question_key=question['key'],
            preview_evidence_id=preview_id, preview_digest=digest(body), reviewer=reviewer.strip(),
            previous_answer_ids=body['previous_answer_ids'])
        document = evidence('amazon_policy_answer', answer_body, reviewer.strip())
        answer = AmazonPolicyAnswers(plan_id=body['plan_id'], question_key=question['key'],
            preview_evidence_id=preview_id, evidence_id=document.id)
        db.session.add(answer)
        db.session.flush()
        return dict(id=answer.id, plan_id=body['plan_id'], question_key=question['key'],
            status=body['status'], count=body['count'], amount=body['amount'], replayed=False)
