"""A single explicit batch review uses source-bound accounting controls."""
from copy import deepcopy
import re
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict


BASE = '/reconciliation/amazon/policies/'


@pytest.fixture
def batch_routes(monkeypatch):
    from pacioli.functions import amazon_policy_application as application
    from pacioli.functions import amazon_item_posting as posting
    from pacioli.functions import amazon_item_workflow as workflow
    from pacioli.views import reconciliation_views

    calls = []
    context = dict(plan=dict(id=5, review_run_id=8, current_policy=True, posting_active=False),
        token='exact-batch-token', needs_implementation=False,
        totals=dict(count=1, amount='108.00', eligible_count=1, eligible_amount='108.00',
                    held_count=0, held_amount='0.00', reviewed_count=0, resolved_count=3,
                    resolved_amount='27.00', unverified_count=0),
        owner_decisions=[dict(label='Expense ordinary movable goods', count=1,
                             amount='108.00', scope='future', answer_id=3)],
        items=[dict(item_id='exact-item', title='<img src=x onerror=alert(1)>', amount='108.00',
                    category='Household Supplies', allowed_categories=['Household Supplies', 'Entertainment'],
                    reason='Supported by source evidence and your saved answer.', hold_reason='',
                    source_refs=[dict(table_id=41, row_number=2)],
                    research=[dict(ref='https://example.test/product', summary='Ordinary movable product.')],
                    components=[dict(kind='item', amount='100.00', source_refs=[]),
                                dict(kind='tax', amount='8.00', source_refs=[dict(table_id=41,
                                     row_number=2, fields=['Unit Price Tax'])])])])

    def page(plan_id=None):
        calls.append(('page', plan_id))
        return deepcopy(context)

    def prepare(plan_id, selections, expected_digest, reviewer):
        calls.append(('prepare', plan_id, selections, expected_digest, reviewer))
        return 9

    def preview(preview_id, reviewer):
        calls.append(('preview', preview_id, reviewer))
        return dict(token='frozen-preview-token')

    def apply(preview_id, token, reviewer):
        calls.append(('apply', preview_id, token, reviewer))
        return dict(run_id=8, reviewed=1, replayed=False)

    def forbidden(*args, **kwargs):
        raise AssertionError('Saving a batch must not start models, workers or ledger posting')

    monkeypatch.setattr(application, 'page', page)
    monkeypatch.setattr(application, 'prepare_preview', prepare)
    monkeypatch.setattr(application, 'preview_page', preview)
    monkeypatch.setattr(application, 'apply_preview', apply)
    monkeypatch.setattr(workflow, 'run_worker', forbidden)
    monkeypatch.setattr(workflow, 'post_reviewed', forbidden)
    monkeypatch.setattr(posting, 'create_request', forbidden)
    monkeypatch.setattr(reconciliation_views.subprocess, 'Popen', forbidden)
    return SimpleNamespace(calls=calls, context=context, application=application)


def login(client, admin_user):
    assert client.post('/login/', data=admin_user).status_code == 302


def form(**changes):
    result = {'action': 'save', 'plan_id': '5', 'page': '1', 'token': 'exact-batch-token',
              'item_id': 'exact-item', 'category_exact-item': 'Household Supplies'}
    result.update(changes)
    return result


def test_batch_requires_private_admin_access_before_reading_or_writing(client, admin_user, monkeypatch, batch_routes):
    for response in (client.get(BASE), client.post(BASE, data=form())):
        assert response.status_code == 302
        assert response.headers['Cache-Control'] == 'private, no-store'
    assert batch_routes.calls == []
    login(client, admin_user)
    from pacioli.models import Users
    monkeypatch.setattr(Users, 'has_role', lambda self, role: False)
    assert client.get(BASE).status_code == 403
    assert client.post(BASE, data=form()).status_code == 403
    assert batch_routes.calls == []


def test_default_batch_prefills_supported_categories_without_questionnaire(client, admin_user, batch_routes):
    login(client, admin_user)
    response = client.get(BASE + '?plan_id=5')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert b'Review Amazon classifications' in response.data
    assert b'Expense ordinary movable goods' in response.data
    assert b'1 purchases' in response.data and b'108.00 USD' in response.data
    assert b'3 purchases' in response.data and b'27.00 USD' in response.data
    assert b'&lt;img src=x onerror=alert(1)&gt;' in response.data
    assert b'<img src=x onerror=alert(1)>' not in response.data
    assert re.search(rb'<option value="Household Supplies"[^>]+selected', response.data)
    assert b'Keep in suspense' in response.data
    assert b'Save classifications and review posting' in response.data
    assert b'name="choice"' not in response.data and b'name="scope"' not in response.data
    assert not re.search(rb'<input[^>]+type="(?:radio|checkbox)"', response.data)
    assert b'/reconciliation/amazon/policies/history/?plan_id=5' in response.data
    assert b'/reconciliation/amazon/table/41/' in response.data
    assert b'Unit Price Tax' in response.data
    assert b'https://example.test/product' in response.data
    assert batch_routes.calls == [('page', 5)]


def test_one_save_applies_frozen_classifications_and_opens_separate_posting_preview(client, admin_user, batch_routes):
    login(client, admin_user)
    response = client.post(BASE, data=form(**{'note_exact-item': 'I reviewed this purchase.'}))
    assert response.status_code == 302
    assert response.location.endswith('/amazon/items/?run_id=8#post-items-heading')
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert batch_routes.calls == [
        ('prepare', 5, [dict(item_id='exact-item', category='Household Supplies',
                            reason='I reviewed this purchase.')], 'exact-batch-token', admin_user['email']),
        ('preview', 9, admin_user['email']),
        ('apply', 9, 'frozen-preview-token', admin_user['email'])]


def test_keep_in_suspense_is_an_explicit_saved_decision(client, admin_user, batch_routes):
    login(client, admin_user)
    response = client.post(BASE, data=form(**{'category_exact-item': '__suspense__'}))
    assert response.status_code == 302
    assert response.location.endswith('/amazon/policies/?plan_id=5&page=1')
    assert batch_routes.calls[0][2] == [dict(item_id='exact-item', category=None, reason='')]


def test_csrf_is_required_before_any_batch_operation(client, admin_user, app, monkeypatch, batch_routes):
    login(client, admin_user)
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    response = client.post(BASE, data=form())
    assert response.status_code == 400
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert batch_routes.calls == []
    response = client.get(BASE)
    token = re.search(rb'name="csrf_token" value="([^"]+)"', response.data).group(1).decode()
    assert client.post(BASE, data=form(csrf_token=token)).status_code == 302


@pytest.mark.parametrize('query', [
    'plan_id=0', 'plan_id=-1', 'plan_id=2147483648', 'plan_id=5&plan_id=6',
    'preview_id=0', 'preview_id=9&preview_id=10', 'question_key=../private',
    'question_key=' + 'x' * 129, 'page=0', 'page=bad', 'page=1&page=2',
])
def test_batch_rejects_malformed_get_identifiers(client, admin_user, batch_routes, query):
    login(client, admin_user)
    response = client.get(BASE + '?' + query)
    assert response.status_code == 400
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert batch_routes.calls == []


@pytest.mark.parametrize('query,expected', [
    ('preview_id=9', 'preview_id=9'),
    ('plan_id=5&question_key=equipment', 'plan_id=5&question_key=equipment'),
])
def test_existing_answer_receipt_bookmarks_redirect_to_private_history(client, admin_user, batch_routes, query, expected):
    login(client, admin_user)
    response = client.get(BASE + '?' + query)
    assert response.status_code == 302
    assert response.location.endswith('/amazon/policies/history/?' + expected)
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert batch_routes.calls == []


@pytest.mark.parametrize('changes', [
    dict(action='post'), dict(plan_id='0'), dict(token=''), dict(item_id='../private'),
    {'category_exact-item': ''}, {'category_exact-item': 'x' * 201},
    {'note_exact-item': 'x' * 1001}, {'category_unlisted-item': 'Groceries'},
])
def test_malformed_batch_never_prepares_or_applies(client, admin_user, batch_routes, changes):
    login(client, admin_user)
    response = client.post(BASE, data=form(**changes))
    assert response.status_code == 400
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert all(call[0] == 'page' for call in batch_routes.calls)


@pytest.mark.parametrize('field,value', [('action', 'save'), ('plan_id', '6'), ('page', '2'),
                                       ('category_exact-item', 'Entertainment'), ('note_exact-item', 'other')])
def test_duplicate_scalar_fields_are_rejected(client, admin_user, batch_routes, field, value):
    login(client, admin_user)
    values = MultiDict(form())
    if field == 'note_exact-item':
        values.add(field, 'first')
    values.add(field, value)
    assert client.post(BASE, data=values).status_code == 400
    assert all(call[0] == 'page' for call in batch_routes.calls)


@pytest.mark.parametrize('ids', [[], ['exact-item', 'exact-item'], ['exact-item', ' exact-item'],
                                ['item-' + str(i) for i in range(101)]])
def test_batch_occurrences_are_distinct_and_bounded(client, admin_user, batch_routes, ids):
    login(client, admin_user)
    values = MultiDict(form())
    values.setlist('item_id', ids)
    assert client.post(BASE, data=values).status_code == 400
    assert all(call[0] == 'page' for call in batch_routes.calls)


def test_stale_batch_preserves_corrections_without_applying_them(client, admin_user, monkeypatch, batch_routes):
    login(client, admin_user)

    def stale(*args):
        raise ValueError('The source changed; refresh this page.')

    monkeypatch.setattr(batch_routes.application, 'prepare_preview', stale)
    response = client.post(BASE, data=form(**{'category_exact-item': 'Entertainment',
                                            'note_exact-item': '<script>Private correction</script>'}))
    assert response.status_code == 400
    assert b'The source changed' in response.data
    assert re.search(rb'<option value="Entertainment"[^>]+selected', response.data)
    assert b'&lt;script&gt;Private correction&lt;/script&gt;' in response.data
    assert b'<script>Private correction</script>' not in response.data
    assert batch_routes.calls == [('page', 5)]


@pytest.mark.parametrize('flag', ['posting_active', 'old_policy', 'needs_implementation'])
def test_blocked_batch_cannot_be_submitted_from_ui(client, admin_user, batch_routes, flag):
    login(client, admin_user)
    if flag == 'posting_active':
        batch_routes.context['plan']['posting_active'] = True
    elif flag == 'old_policy':
        batch_routes.context['plan']['current_policy'] = False
    else:
        batch_routes.context['needs_implementation'] = True
    response = client.get(BASE)
    assert response.status_code == 200
    assert re.search(rb'<button type="submit"[^>]+disabled', response.data)


def test_held_purchase_has_reason_and_context_without_unsupported_category(client, admin_user, batch_routes):
    login(client, admin_user)
    batch_routes.context['items'][0].update(category=None, allowed_categories=[],
        hold_reason='Confirm whether this was installed as part of a larger construction project.')
    response = client.get(BASE)
    assert b'larger construction project' in response.data
    assert b'Add a correction or context' in response.data
    assert b'>Save notes and suspense decisions</button>' in response.data
    assert b'<option value="Household Supplies"' not in response.data
    assert re.search(rb'<option value="__suspense__"[^>]+selected', response.data)


def test_finished_batch_still_links_postings_and_saved_answers(client, admin_user, batch_routes):
    login(client, admin_user)
    batch_routes.context['items'] = []
    batch_routes.context['totals'].update(count=0, amount='0.00', eligible_count=0,
        eligible_amount='0.00', resolved_count=4, resolved_amount='135.00')
    response = client.get(BASE)
    assert b'No purchases remain to classify in this batch.' in response.data
    assert b'4 purchases' in response.data and b'135.00 USD' in response.data
    assert b'Expense ordinary movable goods' in response.data
    assert b'View posted allocations and status' in response.data
    assert b'id="amazon-policy-batch"' not in response.data


def test_research_references_cannot_be_script_links(client, admin_user, batch_routes):
    login(client, admin_user)
    batch_routes.context['items'][0]['research'] = [dict(ref='javascript:alert(1)', summary='<script>bad</script>')]
    response = client.get(BASE)
    assert b'href="javascript:alert(1)"' not in response.data
    assert b'&lt;script&gt;bad&lt;/script&gt;' in response.data


def _many_items(batch_routes, count=201):
    original = batch_routes.context['items'][0]
    batch_routes.context['items'] = [dict(deepcopy(original), item_id='source-' + str(i),
        title='Purchase ' + str(i)) for i in range(1, count + 1)]
    batch_routes.context['totals'].update(count=count, amount=str(count * 108) + '.00')


def test_large_plan_has_a_saveable_bounded_first_page_and_clear_scope(client, admin_user, batch_routes):
    login(client, admin_user)
    _many_items(batch_routes)
    response = client.get(BASE + '?plan_id=5')
    assert response.status_code == 200
    assert len(re.findall(rb'name="item_id"', response.data)) == 100
    assert b'name="item_id" value="source-1"' in response.data
    assert b'name="item_id" value="source-100"' in response.data
    assert b'name="item_id" value="source-101"' not in response.data
    assert 'Showing purchases 1–100 of 201'.encode() in response.data
    assert b'Saving applies only to the purchases shown on this page.' in response.data
    assert b'/amazon/policies/?plan_id=5&amp;page=2' in response.data
    assert b'name="page" value="1"' in response.data


def test_classification_pagination_exposes_later_held_rows_without_posting(client, admin_user, batch_routes):
    login(client, admin_user)
    _many_items(batch_routes)
    for row in batch_routes.context['items']:
        row.update(category=None, allowed_categories=[], hold_reason='Needs project context.')
    response = client.get(BASE + '?plan_id=5&page=3')
    assert response.status_code == 200
    assert len(re.findall(rb'name="item_id"', response.data)) == 1
    assert b'name="item_id" value="source-201"' in response.data
    assert 'Showing purchases 201–201 of 201'.encode() in response.data
    assert b'/amazon/policies/?plan_id=5&amp;page=2' in response.data
    assert b'name="page" value="3"' in response.data
    assert batch_routes.calls == [('page', 5)]


def test_classification_page_beyond_current_review_is_rejected(client, admin_user, batch_routes):
    login(client, admin_user)
    response = client.get(BASE + '?plan_id=5&page=2')
    assert response.status_code == 400
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert batch_routes.calls == [('page', 5)]


def test_saved_correction_is_visible_escaped_and_prefilled_on_reload(client, admin_user, batch_routes):
    login(client, admin_user)
    batch_routes.context['items'][0].update(saved_correction='<script>PRIVATE_NOTE</script>',
        saved_review_reason='Supported purchase. Reviewer correction: <script>PRIVATE_NOTE</script>')
    response = client.get(BASE)
    assert response.status_code == 200
    assert b'<summary>Saved review</summary>' in response.data
    assert b'<script>PRIVATE_NOTE</script>' not in response.data
    assert re.search(rb'<textarea[^>]+name="note_exact-item"[^>]*>&lt;script&gt;PRIVATE_NOTE&lt;/script&gt;</textarea>', response.data)
    assert b'Supported purchase. Reviewer correction: &lt;script&gt;PRIVATE_NOTE&lt;/script&gt;' in response.data


def test_saving_only_context_and_holds_returns_to_review_with_no_posting_step(client, admin_user, batch_routes):
    login(client, admin_user)
    batch_routes.context['items'][0].update(category=None, allowed_categories=[],
        hold_reason='Confirm the broader installation scope.')
    response = client.post(BASE, data=form(**{'category_exact-item': '__suspense__',
                                            'note_exact-item': 'This belongs to the kitchen project.'}), follow_redirects=True)
    assert response.status_code == 200
    assert b'Notes and suspense decisions saved. No journal entries changed.' in response.data
    assert b'>Save notes and suspense decisions</button>' in response.data
    assert batch_routes.calls[0][2] == [dict(item_id='exact-item', category=None,
                                            reason='This belongs to the kitchen project.')]
    assert batch_routes.calls[-1] == ('page', 5)
