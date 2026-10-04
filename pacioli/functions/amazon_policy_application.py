"""Apply reviewed owner policies to exact source occurrences, never by title alone.

An immutable implementation packet joins saved owner answers and independently
reviewed product evidence. It grants only its enumerated categories for its
exact occurrences. Classification is a separate immutable, replayable review;
financial authorization stays in amazon_item_posting.
"""
from decimal import Decimal
import json

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from pacioli.extensions import db
from pacioli.models import AccountingEvidence, AmazonItemJobs, AmazonSuspensePlans
from pacioli.functions import amazon_item_advisor as advisor
from pacioli.functions import amazon_item_workflow as workflow
from pacioli.functions import amazon_policy_questions as questions
from pacioli.functions import amazon_suspense as suspense
from pacioli.functions.reconciliation import digest, dump, evidence, money


VERSION = 'amazon-policy-implementation-v1'
PREVIEW_VERSION = 'amazon-policy-classification-v1'
TREATMENTS = {
    'ordinary_movable': ('durable_goods', 'expense'),
    'protective_accessory': ('protective_accessories', 'supplies'),
    'veterinary_supply': ('veterinary_care', 'separate'),
    'incidental_feature': ('sound_workbook', 'entertainment'),
    'replacement_repair': ('repair_context', 'replacement'),
    'product_evidence': None,
    'hold': None,
}
MAX_ITEMS = 100


def _documents(kind):
    for row in db.session.query(AccountingEvidence).filter_by(kind=kind).order_by(AccountingEvidence.id):
        body = workflow._document(row.id, kind)
        yield row, body


def _implementations(plan_id):
    previous, result = None, []
    for row, body in _documents('amazon_policy_implementation'):
        if body.get('plan_id') != plan_id:
            continue
        if (body.get('version') != VERSION or body.get('previous_implementation_id') != previous
                or not isinstance(body.get('items'), list)
                or len({i.get('item_id') for i in body['items']}) != len(body['items'])):
            raise ValueError('Policy implementation evidence ancestry changed')
        result.append((row.id, body)); previous = row.id
    return result


def _implemented_items(plan_id):
    result = {}
    for identifier, body in _implementations(plan_id):
        for item in body['items']:
            result[item['item_id']] = (identifier, body, item)
    return result


def _answer_binding(answer, item_id, latest):
    """An answer retains meaning after a chart/prompt change, never a correction."""
    if item_id in answer['item_ids']:
        return latest.get((answer['question_key'], item_id), {}).get('id') == answer['id']
    # A future policy may support another occurrence, but a partial correction
    # to its original scope requires independent implementation review again.
    return (answer['scope'] == 'future' and answer['status'] != 'deferred'
            and all(latest.get((answer['question_key'], key), {}).get('id') == answer['id']
                    for key in answer['item_ids']))


def _answer_refs(entry, source_manifest_digest, latest, history, facts):
    by_id = {answer['id']: answer for answer in history}
    refs = []
    for identifier in entry['answer_ids']:
        answer = by_id.get(identifier)
        if (answer is None or answer['source_manifest_digest'] != source_manifest_digest
                or not _answer_binding(answer, entry['item_id'], latest)
                or answer['choice'] in ('custom', 'defer', 'threshold', 'assets', 'project')):
            raise ValueError('The supporting owner answer is missing, superseded or not implemented')
        # The answer recorded facts as they stood when the owner supplied it.
        # A changed fact cannot silently inherit the old owner preference.
        for source_id in answer['item_ids']:
            live_fact = facts[source_id][-1]['evidence_id'] if facts.get(source_id) else None
            if answer['fact_evidence_ids'].get(source_id) != live_fact:
                raise ValueError('The source facts supporting the owner answer changed')
        refs.append(dict(id=answer['id'], evidence_id=answer['evidence_id'],
            question_key=answer['question_key'], choice=answer['choice'], scope=answer['scope']))
    required = TREATMENTS[entry['treatment']]
    if required and not any((ref['question_key'], ref['choice']) == required for ref in refs):
        raise ValueError('This treatment requires its exact implemented owner answer')
    if entry['treatment'] == 'replacement_repair':
        matching = [by_id[r['id']] for r in refs if r['question_key'] == 'repair_context']
        if not any(entry['item_id'] in answer['item_ids'] for answer in matching):
            raise ValueError('A repair context answer applies only to its selected purchases')
    return refs


def _validate_entry(entry, original, categories, source_digest, latest, history, facts):
    if (not isinstance(entry, dict) or entry.get('item_id') != original['item_id']
            or entry.get('item_digest') != digest(original)
            or entry.get('treatment') not in TREATMENTS
            or not isinstance(entry.get('reason'), str) or not 1 <= len(entry['reason'].strip()) <= 2000
            or not isinstance(entry.get('answer_ids'), list)
            or any(type(i) is not int for i in entry['answer_ids'])
            or len(set(entry['answer_ids'])) != len(entry['answer_ids'])
            or not isinstance(entry.get('research'), list) or len(entry['research']) > 20
            or not isinstance(entry.get('hold_reason', ''), str) or len(entry.get('hold_reason', '')) > 2000):
        raise ValueError('Each implementation must identify an exact source, treatment and evidence')
    for ref in entry['research']:
        if (not isinstance(ref, dict) or set(ref) != {'ref', 'summary'}
                or not isinstance(ref['ref'], str) or not 1 <= len(ref['ref']) <= 2000
                or not isinstance(ref['summary'], str) or not 1 <= len(ref['summary']) <= 2000):
            raise ValueError('Product research requires bounded source references and findings')
    allowed = entry.get('allowed_categories')
    if (not isinstance(allowed, list) or len(allowed) > 8
            or any(not isinstance(c, str) or c not in categories for c in allowed)
            or len(set(allowed)) != len(allowed)):
        raise ValueError('Enumerate the supported current expense categories')
    if entry['treatment'] == 'hold':
        if entry.get('category') is not None or allowed or not entry.get('hold_reason'):
            raise ValueError('A held implementation cannot grant an expense category')
    elif (entry.get('category') not in allowed or original.get('blockers')
            or money(original['amount']) < 0 or not entry['research']):
        raise ValueError('An expense implementation needs source support and an eligible category')
    refs = _answer_refs(entry, source_digest, latest, history, facts)
    return dict(entry, reason=entry['reason'].strip(), answer_refs=refs,
        fact_evidence_id=facts[entry['item_id']][-1]['evidence_id'] if facts.get(entry['item_id']) else None)


def import_implementation(plan_id, packet, reviewer):
    """Install reviewed source-specific policy interpretation; caller commits.

    Packet: {version, source_manifest_digest, policy_digest, chart_digest,
    items:[{item_id,item_digest,treatment,category,allowed_categories,reason,
    answer_ids,research:[{ref,summary}],hold_reason?}]}. No journals or reviews.
    """
    suspense._actor(reviewer)
    with db.session.begin_nested():
        plan, doc, manifest = suspense._plan(plan_id, lock=True)
        suspense._require_posting_idle()
        _locked_tables()
        if (not isinstance(packet, dict) or packet.get('version') != VERSION
                or packet.get('source_manifest_digest') != doc['source_manifest_digest']
                or packet.get('policy_digest') != workflow._policy()
                or packet.get('chart_digest') != suspense._chart_digest()
                or not isinstance(packet.get('items'), list) or not 1 <= len(packet['items']) <= 10000
                or len(dump(packet).encode()) > 8 * 1024 * 1024):
            raise ValueError('A bounded source-reviewed implementation for the current policy and chart is required')
        old = _implementations(plan_id)
        key = digest(packet)
        for identifier, body in old:
            if body.get('packet_digest') == key:
                return dict(evidence_id=identifier, count=len(body['items']), replayed=True)
        ids = [item.get('item_id') for item in packet['items'] if isinstance(item, dict)]
        if (len(ids) != len(packet['items']) or any(not isinstance(i, str) for i in ids)
                or len(set(ids)) != len(ids) or not set(ids).issubset({i['item_id'] for i in doc['items']})):
            raise ValueError('Select distinct occurrences belonging to this suspense plan')
        source_items = workflow._items(manifest)
        categories = {k for k, v in workflow.chart_accounts(db.session).items()
                      if v[0] == 'Expenses' and k in advisor.CATEGORY_DEFINITIONS}
        latest, history = questions._answers(plan_id)
        facts = suspense._facts(plan_id)
        triage = {row['item_id']: row for row in doc['items']}
        for row in packet['items']:
            if row.get('treatment') == 'product_evidence' and triage[row['item_id']].get('policy_gate') != 'needs_fact':
                raise ValueError('Product research cannot substitute for an unresolved owner property policy')
        items = [_validate_entry(row, source_items[row['item_id']], categories,
                    doc['source_manifest_digest'], latest, history, facts) for row in packet['items']]
        suspense._validate_sources(manifest, set(ids))
        posted = suspense._posted_items(manifest, set(ids))
        if any(i not in posted or posted[i]['category'] != 'Bookkeeping Suspense' for i in ids):
            raise ValueError('Policy implementation applies only to verified unresolved source purchases')
        body = dict(version=VERSION, plan_id=plan_id, plan_evidence_id=plan.evidence_id,
            source_manifest_digest=doc['source_manifest_digest'], policy_digest=workflow._policy(),
            chart_digest=suspense._chart_digest(), packet_digest=key,
            previous_implementation_id=old[-1][0] if old else None,
            reviewer=reviewer.strip(), items=items)
        document = evidence('amazon_policy_implementation', body, reviewer.strip())
        return dict(evidence_id=document.id, count=len(items), replayed=False)


def control(plan, doc, original, category, decision=None, historical_carried=False):
    """Return exact authority, or None when no implementation exists.

    historical_carried is reserved for suspense.review_control after it proves
    an existing posted decision's immutable current-manifest carry ancestry.
    It preserves its historical treatment without issuing a new classification;
    source, latest implementation, owner-answer and fact checks still apply.
    """
    applied = _implemented_items(plan.id).get(original['item_id'])
    if applied is None:
        return None
    identifier, body, entry = applied
    if (body.get('source_manifest_digest') != doc['source_manifest_digest']
            or body.get('plan_evidence_id') != plan.evidence_id
            or (not historical_carried and (body.get('policy_digest') != workflow._policy()
                or body.get('chart_digest') != suspense._chart_digest()))
            or entry.get('item_digest') != digest(original)):
        raise ValueError('Implemented source policy or category chart changed; refresh the implementation')
    latest, history = questions._answers(plan.id)
    facts = suspense._facts(plan.id)
    refs = _answer_refs(entry, doc['source_manifest_digest'], latest, history, facts)
    live_fact = facts[original['item_id']][-1]['evidence_id'] if facts.get(original['item_id']) else None
    if refs != entry['answer_refs'] or live_fact != entry['fact_evidence_id']:
        raise ValueError('The implemented owner answer or reviewed source fact changed')
    if entry['treatment'] == 'hold' or category not in entry['allowed_categories']:
        raise ValueError(entry.get('hold_reason') or 'This category is outside the source-reviewed implementation')
    result = dict(plan_id=plan.id, plan_evidence_id=plan.evidence_id,
        source_manifest_digest=doc['source_manifest_digest'], policy_digest=body['policy_digest'],
        gate='implemented_policy', fact_evidence_id=live_fact,
        implementation_evidence_id=identifier, implementation_item_digest=digest(entry),
        answer_evidence_ids=[ref['evidence_id'] for ref in refs])
    if decision is not None and decision.get('suspense_control') != result:
        raise ValueError('The implemented item eligibility changed; review this item again')
    return result


def revision(plan_ids, item_ids):
    """Opaque answer and implementation ancestry for financial approval tokens."""
    wanted = set(item_ids); result = []
    for plan_id in sorted(plan_ids):
        implemented = _implemented_items(plan_id)
        latest, _ = questions._answers(plan_id)
        for item_id in sorted(wanted.intersection(implemented)):
            identifier, _, item = implemented[item_id]
            answer_ids = []
            for ref in item['answer_refs']:
                relevant = [(key, value['evidence_id']) for key, value in latest.items()
                            if key[0] == ref['question_key']]
                answer_ids.append((ref['id'], sorted(relevant)))
            result.append(dict(plan_id=plan_id, item_id=item_id, implementation_id=identifier,
                               owner_answer_revision=answer_ids))
    return result


def _locked_tables():
    try:
        db.session.execute(text('LOCK TABLE ofx.stmttrn, ofx.acctfrom, ofx.ccacctfrom, ofx.bankacctfrom, '
            'bookkeeping.bank_feed_accounts, bookkeeping.subaccounts, bookkeeping.accounts, '
            'bookkeeping.classifications, bookkeeping.elements, bookkeeping.journal_entries, '
            'amazon.evidence_bundles, amazon.item_jobs, amazon.item_runs IN SHARE MODE'))
    except DBAPIError as exc:
        if getattr(exc.orig, 'pgcode', None) == '55P03':
            raise ValueError('Another accounting action is in progress; wait, refresh this page and retry') from None
        raise


def _state(plan_id, locked=False):
    plan, doc, source_manifest = suspense._plan(plan_id, lock=locked)
    if locked:
        suspense._require_posting_idle(); _locked_tables()
    run, manifest, jobs = suspense._run(plan, source_manifest)
    current = (manifest['policy_digest'] == workflow._policy()
               and digest(manifest['chart']) == suspense._chart_digest()
               and plan.review_run_id is not None and run.state not in ('queued', 'running'))
    originals = workflow._items(source_manifest)
    posted = suspense._posted_items(source_manifest, {r['item_id'] for r in doc['items']})
    implemented = _implemented_items(plan.id)
    rows, resolved, unverified = [], [], []
    for source in doc['items']:
        item_id = source['item_id']; item = originals[item_id]; financial = posted.get(item_id)
        if financial is None:
            unverified.append(item); continue
        if financial['category'] != 'Bookkeeping Suspense':
            resolved.append(item); continue
        proof = implemented.get(item_id)
        entry = proof[2] if proof else {}
        held = entry.get('hold_reason', '') if entry.get('treatment') == 'hold' else ''
        if not proof:
            held = 'This source purchase still needs a supported implementation.'
        elif not held:
            try:
                control(plan, doc, item, entry['category'])
            except ValueError as exc:
                held = str(exc)
        if not current:
            held = 'Prepare the current accounting review before saving classifications.'
        job = jobs.get(item_id)
        if job is None:
            held = 'This source occurrence is outside the current accounting review.'
        decision = workflow._decision(job) if job else None
        category = entry.get('category') if not held else None
        if decision is not None:
            if decision['category'] is None:
                category = None
            elif not held and decision['category'] in entry.get('allowed_categories', []):
                category = decision['category']
        saved_reason = decision.get('reason', '') if decision else ''
        base_reason = (entry.get('reason', source.get('reason', '')) if decision and decision['category']
                       else 'Keep this exact source purchase in suspense pending review.')
        correction_prefix = base_reason + ' Reviewer correction: '
        # Only restore an editable note when the full known prefix matches.
        # Arbitrary historical review text stays visible without being
        # misrepresented as a structured correction or a new owner fact.
        saved_correction = saved_reason[len(correction_prefix):] if saved_reason.startswith(correction_prefix) else ''
        if len(saved_correction) > 1000:
            saved_correction = ''
        rows.append(dict(item_id=item_id, title=item['title'], amount=str(money(item['amount'])),
            category=category, recommended_category=entry.get('category'), reason=entry.get('reason', source.get('reason', '')),
            saved_review_reason=saved_reason, saved_correction=saved_correction,
            allowed_categories=entry.get('allowed_categories', []) if not held else [],
            source_refs=item['source_refs'], components=item['components'], quantity=item['quantity'],
            research=entry.get('research', []), answer_ids=entry.get('answer_ids', []),
            hold_reason=held, reviewed=bool(decision and decision['category'] is not None),
            implementation_id=proof[0] if proof else None))
    rows.sort(key=lambda row: (bool(row['hold_reason']), -money(row['amount']), row['item_id']))
    _, history = questions._answers(plan.id)
    token = digest(dict(version=PREVIEW_VERSION, plan_id=plan.id, source=doc['source_manifest_digest'],
        review=suspense._token(plan, source_manifest), posted=posted,
        implementation_ids=[(key, value[0]) for key, value in sorted(implemented.items())],
        answers=[(a['id'], a['evidence_id']) for a in history]))
    return dict(plan=plan, doc=doc, source_manifest=source_manifest, run=run, manifest=manifest,
        jobs=jobs, current=current, rows=rows, resolved=resolved, unverified=unverified,
        history=history, token=token, implementations=implemented)


def page(plan_id=None):
    if plan_id is None:
        last = db.session.query(AmazonSuspensePlans).order_by(AmazonSuspensePlans.id.desc()).first()
        if last is None:
            return dict(plan=None, token='', items=[], owner_decisions=[], available_categories=[],
                totals=dict(count=0, amount='0.00', eligible_count=0, eligible_amount='0.00', held_count=0,
                            held_amount='0.00', reviewed_count=0), needs_implementation=True)
        plan_id = last.id
    state = _state(plan_id)
    rows, plan = state['rows'], state['plan']
    eligible = [r for r in rows if not r['hold_reason']]
    held = [r for r in rows if r['hold_reason']]
    decisions = []
    for answer in state['history']:
        bound = [entry for _, _, entry in state['implementations'].values() if answer['id'] in entry['answer_ids']]
        if bound:
            original_items = workflow._items(state['source_manifest'])
            decisions.append(dict(label=answer['choice_label'], count=len(bound),
                amount=str(sum((money(original_items[r['item_id']]['amount']) for r in bound), Decimal('0.00'))),
                scope=answer['scope'], answer_id=answer['id']))
    return dict(plan=dict(id=plan.id, review_run_id=plan.review_run_id,
        current_policy=state['current'], posting_active=suspense._posting_active()), token=state['token'],
        items=rows, owner_decisions=decisions, saved_answers=state['history'],
        available_categories=sorted({c for r in eligible for c in r['allowed_categories']}),
        totals=dict(count=len(rows), amount=str(sum((money(r['amount']) for r in rows), Decimal('0.00'))),
            eligible_count=len(eligible), eligible_amount=str(sum((money(r['amount']) for r in eligible), Decimal('0.00'))),
            held_count=len(held), held_amount=str(sum((money(r['amount']) for r in held), Decimal('0.00'))),
            reviewed_count=sum(r['reviewed'] for r in rows), resolved_count=len(state['resolved']),
            resolved_amount=str(sum((money(r['amount']) for r in state['resolved']), Decimal('0.00'))),
            unverified_count=len(state['unverified'])), needs_implementation=not state['implementations'])


def _selections(state, selections):
    if (not isinstance(selections, list) or not 1 <= len(selections) <= MAX_ITEMS
            or any(not isinstance(row, dict) for row in selections)):
        raise ValueError('Select a bounded list of exact source purchases')
    ids = [row.get('item_id') for row in selections]
    if any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Select each source occurrence only once')
    live = {r['item_id']: r for r in state['rows']}
    result = []
    for selected in selections:
        row = live.get(selected['item_id'])
        category = selected.get('category') or None
        note = selected.get('reason', '')
        if (set(selected) - {'item_id', 'category', 'reason'} or row is None
                or not isinstance(note, str) or len(note) > 1000
                or (category is not None and (row['hold_reason'] or category not in row['allowed_categories']))):
            raise ValueError('A selected category is outside its exact source-reviewed support')
        reason = row['reason'] if category else 'Keep this exact source purchase in suspense pending review.'
        if note.strip():
            reason += ' Reviewer correction: ' + note.strip()
        if len(reason) > 2000:
            raise ValueError('The combined review explanation exceeds its evidence limit')
        result.append(dict(item_id=row['item_id'], title=row['title'], amount=row['amount'],
            category=category, reason=reason, source_refs=row['source_refs'], components=row['components'],
            implementation_id=row['implementation_id'], answer_ids=row['answer_ids']))
    return sorted(result, key=lambda row: row['item_id'])


def prepare_preview(plan_id, selections, expected_digest, reviewer):
    suspense._actor(reviewer)
    with db.session.begin_nested():
        state = _state(plan_id, locked=True)
        if not state['current'] or not suspense._hex(expected_digest) or state['token'] != expected_digest:
            raise ValueError('The source, answers or accounting review changed; refresh this page')
        selected = _selections(state, selections)
        suspense._validate_sources(state['source_manifest'], {r['item_id'] for r in selected})
        body = dict(version=PREVIEW_VERSION, plan_id=plan_id, run_id=state['run'].id,
            baseline_token=expected_digest, source_manifest_digest=state['doc']['source_manifest_digest'],
            reviewer=reviewer.strip(), items=selected,
            count=len(selected), amount=str(sum((money(r['amount']) for r in selected if r['category']), Decimal('0.00'))))
        return evidence('amazon_policy_classification_preview', body, reviewer.strip()).id


def _preview(identifier, reviewer):
    suspense._actor(reviewer)
    body = workflow._document(suspense._id(identifier), 'amazon_policy_classification_preview')
    if body.get('version') != PREVIEW_VERSION or body.get('reviewer') != reviewer.strip():
        raise ValueError('This classification preview belongs to a different reviewer or version')
    return body


def _receipt(preview_id, body):
    found = [(row, doc) for row, doc in _documents('amazon_policy_classification_batch')
             if doc.get('preview_evidence_id') == preview_id]
    if len(found) > 1:
        raise ValueError('Classification receipt is not unique')
    if not found:
        return None
    row, doc = found[0]
    if (doc.get('preview_digest') != digest(body) or doc.get('run_id') != body['run_id']
            or doc.get('plan_id') != body['plan_id']):
        raise ValueError('Classification receipt ancestry changed')
    return dict(doc, evidence_id=row.id)


def preview_page(preview_id, reviewer):
    body = _preview(preview_id, reviewer)
    receipt = _receipt(preview_id, body)
    state = _state(body['plan_id'])
    return dict(body, preview_id=preview_id, token=digest(body),
        already_confirmed=receipt is not None, stale=receipt is None and state['token'] != body['baseline_token'],
        posting_active=suspense._posting_active())


def apply_preview(preview_id, expected_digest, reviewer):
    """Save exact reviewed classifications; it cannot write a journal entry."""
    body = _preview(preview_id, reviewer)
    if not suspense._hex(expected_digest) or expected_digest != digest(body):
        raise ValueError('The classification preview changed')
    with db.session.begin_nested():
        suspense._lock()
        old = _receipt(preview_id, body)
        if old:
            return dict(run_id=old['run_id'], reviewed=len(old['decision_ids']),
                decision_ids=old['decision_ids'], evidence_id=old['evidence_id'], replayed=True,
                review_digest=workflow.review_digest(old['run_id']))
        state = _state(body['plan_id'], locked=True)
        if state['token'] != body['baseline_token'] or state['run'].id != body['run_id'] or not state['current']:
            raise ValueError('The sources, owner answers or classifications changed; refresh this page')
        with suspense._run_lease(state['run'].id):
            suspense._manual_prepare(state['run'])
            db.session.query(AmazonItemJobs).populate_existing().filter_by(run_id=state['run'].id).with_for_update().all()
            originals = workflow._items(state['manifest'])
            if any(r['item_id'] not in originals or r['item_id'] not in state['jobs'] for r in body['items']):
                raise ValueError('The selected source occurrence is outside the current review')
            suspense._validate_sources(state['source_manifest'], {r['item_id'] for r in body['items']})
            decision_ids = []
            for row in body['items']:
                doc = workflow.review_item(state['jobs'][row['item_id']].id, row['category'], row['reason'], reviewer.strip())
                decision_ids.append(doc.id)
            receipt = evidence('amazon_policy_classification_batch', dict(version=PREVIEW_VERSION,
                plan_id=body['plan_id'], run_id=body['run_id'], preview_evidence_id=preview_id,
                preview_digest=digest(body), decision_ids=decision_ids, reviewer=reviewer.strip()), reviewer.strip())
            db.session.flush()
            return dict(run_id=body['run_id'], reviewed=len(decision_ids), decision_ids=decision_ids,
                evidence_id=receipt.id, replayed=False, review_digest=workflow.review_digest(body['run_id']))
