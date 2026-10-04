"""Policy questions collect explicit source-bound answers without posting."""
from copy import deepcopy
import re
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict


BASE = '/reconciliation/amazon/policies/history/'


@pytest.fixture
def policy_routes(monkeypatch):
    from pacioli.functions import amazon_policy_questions as policies
    from pacioli.functions import amazon_item_workflow as workflow
    from pacioli.functions import amazon_suspense as suspense
    from pacioli.functions import amazon_item_posting as posting
    from pacioli.views import reconciliation_views

    calls = []
    item = dict(item_id='exact-purchase', title='<img src=x onerror=alert(1)>', amount='108.00',
                quantity=1, source_refs=[dict(table_id=41, row_number=2)], answer=None,
                components=[dict(kind='item', amount='100.00', source_refs=[]),
                            dict(kind='tax', amount='8.00', source_refs=[dict(table_id=41,
                                row_number=2, fields=['Unit Price Tax'])])])
    question = dict(key='equipment', title='How should durable equipment be treated?',
                    prompt='Choose a personal accounting policy, with no assumed dollar threshold.',
                    kind='policy', count=1, amount='108.00', examples=[item], items=[item],
                    allow_future=True, needs_threshold_parameters=True,
                    choices=[dict(key='expense', label='Expense ordinary equipment',
                                  explanation='Suggested direction; confirm personal use.', recommended=True),
                             dict(key='threshold', label='Use a threshold', explanation='Enter an exact threshold.', recommended=False),
                             dict(key='assets', label='Record as assets', explanation='Useful lives still need review.', recommended=False),
                             dict(key='custom', label='Use my own answer', explanation='Describe the treatment.', recommended=False),
                             dict(key='defer', label='Decide later', explanation='Keep these purchases in suspense.', recommended=False)])
    context = dict(plan=dict(id=5, review_run_id=8, current_policy=True, posting_active=False),
                   token='current-source-digest', questions=[question], question=question,
                   totals=dict(remaining_count=1, remaining_amount='108.00', unverified_count=0,
                               answered_count=0, deferred_count=0), saved_answers=[])
    preview = dict(preview_id=9, token='frozen-preview-digest', plan_id=5, question=question,
                   choice=question['choices'][0], scope='selected', items=[item], count=1,
                   amount='108.00', custom_text='<script>UNTRUSTED_NOTE</script>', parameters={},
                   status='saved_policy_pending_implementation',
                   next_steps=['Implement the accounting policy and review the affected purchases.'],
                   already_confirmed=False, answer_id=None, stale=False, posting_active=False)

    def page(plan_id=None, question_key=None):
        calls.append(('page', plan_id, question_key))
        return deepcopy(context)

    def prepare_preview(**kwargs):
        calls.append(('prepare_preview', kwargs))
        return 9

    def preview_page(preview_id, reviewer):
        calls.append(('preview_page', preview_id, reviewer))
        return deepcopy(preview)

    def confirm(preview_id, expected_digest, reviewer):
        calls.append(('confirm', preview_id, expected_digest, reviewer))
        return dict(id=10, plan_id=5, question_key='equipment', status=preview['status'],
                    count=1, amount='108.00', replayed=False)

    def forbidden(*args, **kwargs):
        raise AssertionError('Policy questions must not classify, release holds, run models or post journals')

    for name, implementation in [('page', page), ('prepare_preview', prepare_preview),
                                 ('preview_page', preview_page), ('confirm', confirm)]:
        monkeypatch.setattr(policies, name, implementation)
    for module, names in [(workflow, ('run_worker', 'post_reviewed', 'review_item', 'install_item_categories')),
                          (suspense, ('apply_reviews', 'save_fact', 'prepare_new_review_run')),
                          (posting, ('create_request',))]:
        for name in names:
            monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(reconciliation_views.subprocess, 'Popen', forbidden)
    return SimpleNamespace(calls=calls, context=context, preview=preview, policies=policies)


def login(client, admin_user):
    assert client.post('/login/', data=admin_user).status_code == 302


def form(**changes):
    values = dict(action='preview', plan_id='5', question_key='equipment', choice='expense',
                  scope='selected', item_id='exact-purchase', token='current-source-digest',
                  custom_text='This is personal household use.')
    values.update(changes)
    return values


def test_policy_questions_are_private_before_authentication(client, policy_routes):
    for response in (client.get(BASE), client.post(BASE, data=form())):
        assert response.status_code == 302
        assert response.headers['Cache-Control'] == 'private, no-store'
    assert policy_routes.calls == []


def test_policy_questions_require_administrator_role(client, admin_user, monkeypatch, policy_routes):
    from pacioli.models import Users
    login(client, admin_user)
    monkeypatch.setattr(Users, 'has_role', lambda self, role: False)
    response = client.get(BASE)
    assert response.status_code == 403
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert policy_routes.calls == []


def test_policy_page_has_unselected_answers_exact_costs_and_collection_boundary(client, admin_user, policy_routes):
    login(client, admin_user)
    response = client.get(BASE + '?plan_id=5&question_key=equipment')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert b'Saving an answer changes no journals.' in response.data
    assert b'needs accounting implementation' not in response.data  # No saved answer is implied.
    assert b'No dollar threshold, useful life or tax treatment is assumed.' in response.data
    assert b'does not classify future purchases or post automatically.' in response.data
    assert b'0 purchases have saved answers.' in response.data
    assert b'1 purchases' in response.data and b'108.00 USD still in suspense' in response.data
    assert b'&lt;img src=x onerror=alert(1)&gt;' in response.data
    assert b'<img src=x onerror=alert(1)>' not in response.data
    assert b'/reconciliation/amazon/table/41/' in response.data
    assert b'Unit Price Tax' in response.data and b'8.00' in response.data
    assert not re.search(rb'<input[^>]+(?:radio|checkbox)[^>]+\bchecked\b', response.data)
    assert policy_routes.calls == [('page', 5, 'equipment')]


def test_context_question_disallows_future_scope_in_ui(client, admin_user, policy_routes):
    login(client, admin_user)
    policy_routes.context['question'].update(kind='context', allow_future=False,
                                              needs_threshold_parameters=False)
    response = client.get(BASE)
    assert b'id="policy-scope-future"' not in response.data
    assert b'id="policy-threshold-parameters"' not in response.data
    assert b'cannot become a policy for future purchases' in response.data


def test_post_preview_uses_exact_selected_occurrences_and_reviewer(client, admin_user, policy_routes):
    login(client, admin_user)
    values = MultiDict(form(scope='future'))
    values.add('item_id', 'second-occurrence')
    response = client.post(BASE, data=values)
    assert response.status_code == 302 and response.location.endswith('?preview_id=9')
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert policy_routes.calls == [('prepare_preview', dict(plan_id=5, question_key='equipment',
        choice='expense', scope='future', item_ids=['exact-purchase', 'second-occurrence'],
        custom_text='This is personal household use.', parameters={}, expected_digest='current-source-digest',
        reviewer=admin_user['email']))]


def test_threshold_preview_keeps_exact_amount_and_explicit_accounting_unit(client, admin_user, policy_routes):
    login(client, admin_user)
    response = client.post(BASE, data=form(choice='threshold', threshold_amount='1250.25',
                                          threshold_basis='complete_assembly'))
    assert response.status_code == 302
    assert policy_routes.calls[0][1]['parameters'] == dict(threshold_amount='1250.25', threshold_basis='complete_assembly')


def test_preview_shows_proposed_scope_no_posting_and_escaped_note(client, admin_user, policy_routes):
    login(client, admin_user)
    policy_routes.preview['scope'] = 'future'
    response = client.get(BASE + '?preview_id=9')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert b'Journal impact now: 0.00 USD.' in response.data
    assert b'After saving: saved; needs accounting implementation and review.' in response.data
    assert b'guidance for future reviews' in response.data
    assert b'&lt;script&gt;UNTRUSTED_NOTE&lt;/script&gt;' in response.data
    assert b'<script>UNTRUSTED_NOTE</script>' not in response.data
    assert b'Save answer and continue' in response.data
    assert policy_routes.calls == [('preview_page', 9, admin_user['email'])]


@pytest.mark.parametrize('status,notice', [
    ('saved_policy_pending_implementation', b'Policy direction saved; accounting implementation'),
    ('saved_context_pending_review', b'Purchase context saved; individual accounting review'),
    ('deferred', b'Decision deferred. These purchases remain in suspense.'),
])
def test_confirm_saves_only_frozen_preview_then_moves_to_next_question(client, admin_user, policy_routes, status, notice):
    login(client, admin_user)
    policy_routes.preview['status'] = status
    response = client.post(BASE, data=dict(action='confirm', preview_id=9, token='frozen-preview-digest'), follow_redirects=True)
    assert response.status_code == 200 and notice in response.data
    assert policy_routes.calls == [('confirm', 9, 'frozen-preview-digest', admin_user['email']), ('page', 5, None)]


def test_confirmed_receipt_cannot_look_like_an_unsaved_answer(client, admin_user, policy_routes):
    login(client, admin_user)
    policy_routes.preview.update(already_confirmed=True, answer_id=10)
    response = client.get(BASE + '?preview_id=9')
    assert b'This answer is already saved.' in response.data
    assert b'name="action" value="confirm"' not in response.data


@pytest.mark.parametrize('flag', ['already_confirmed', 'stale'])
def test_historical_or_stale_preview_returns_to_current_questions_without_obsolete_key(client, admin_user, policy_routes, flag):
    login(client, admin_user)
    policy_routes.preview[flag] = True
    response = client.get(BASE + '?preview_id=9')
    assert response.status_code == 200
    assert b'Back to current questions' in response.data
    assert b'question_key=' not in response.data
    assert b'/reconciliation/amazon/policies/history/?plan_id=5' in response.data


@pytest.mark.parametrize('action', ['preview', 'confirm'])
def test_all_answer_mutations_require_csrf_and_errors_are_private(client, admin_user, app, monkeypatch, policy_routes, action):
    login(client, admin_user)
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    response = client.post(BASE, data=form(action=action, preview_id='9'))
    assert response.status_code == 400
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert policy_routes.calls == []


def test_valid_csrf_can_prepare_review_without_posting(client, admin_user, app, monkeypatch, policy_routes):
    login(client, admin_user)
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    page = client.get(BASE)
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post(BASE, data=form(csrf_token=token))
    assert response.status_code == 302
    assert policy_routes.calls[-1][0] == 'prepare_preview'


@pytest.mark.parametrize('query', [
    'plan_id=0', 'plan_id=-1', 'plan_id=bad', 'plan_id=2147483648', 'plan_id=5&plan_id=6',
    'question_key=../../private', 'question_key=' + 'x' * 129, 'preview_id=0', 'preview_id=bad',
])
def test_invalid_get_identifiers_do_not_reach_backend(client, admin_user, policy_routes, query):
    login(client, admin_user)
    response = client.get(BASE + '?' + query)
    assert response.status_code == 400
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert policy_routes.calls == []


@pytest.mark.parametrize('changes', [
    dict(plan_id='0'), dict(question_key='../private'), dict(choice=''), dict(scope='all'),
    dict(item_id=''), dict(item_id='../private'), dict(token=''), dict(token='x' * 129),
    dict(custom_text='x' * 2001), dict(threshold_amount='100.00'), dict(action='post'),
])
def test_invalid_post_never_creates_preview_or_answer(client, admin_user, policy_routes, changes):
    login(client, admin_user)
    response = client.post(BASE, data=form(**changes))
    assert response.status_code == 400
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert all(call[0] == 'page' for call in policy_routes.calls)


@pytest.mark.parametrize('ids', [[], ['exact-purchase', 'exact-purchase'], ['exact-purchase', ' exact-purchase'],
                                ['purchase-' + str(index) for index in range(101)]])
def test_selection_is_explicit_distinct_and_bounded(client, admin_user, policy_routes, ids):
    login(client, admin_user)
    values = MultiDict(form())
    values.setlist('item_id', ids)
    assert client.post(BASE, data=values).status_code == 400
    assert all(call[0] == 'page' for call in policy_routes.calls)


def test_one_purchase_scope_rejects_two_selected_occurrences(client, admin_user, policy_routes):
    login(client, admin_user)
    values = MultiDict(form(scope='this_item'))
    values.add('item_id', 'second-purchase')
    assert client.post(BASE, data=values).status_code == 400
    assert all(call[0] == 'page' for call in policy_routes.calls)


def test_ambiguous_duplicate_choice_field_is_rejected(client, admin_user, policy_routes):
    login(client, admin_user)
    values = MultiDict(form())
    values.add('choice', 'assets')
    assert client.post(BASE, data=values).status_code == 400
    assert all(call[0] == 'page' for call in policy_routes.calls)


def test_stale_preview_preserves_explicit_answers_for_review_before_retry(client, admin_user, monkeypatch, policy_routes):
    login(client, admin_user)

    def stale(**kwargs):
        raise ValueError('The source changed. Refresh and review the selected purchases again.')

    monkeypatch.setattr(policy_routes.policies, 'prepare_preview', stale)
    response = client.post(BASE, data=form(choice='custom', custom_text='Confirmed replacement for an existing fitting.'))
    assert response.status_code == 400
    assert b'The source changed. Refresh and review' in response.data
    assert b'Confirmed replacement for an existing fitting.' in response.data
    assert re.search(rb'name="choice" value="custom"[^>]+checked', response.data)
    assert re.search(rb'name="scope" value="selected"[^>]+checked', response.data)
    assert re.search(rb'value="exact-purchase"[^>]+checked', response.data)
    assert b'name="token" value="current-source-digest"' in response.data


def test_active_posting_disables_answer_submission(client, admin_user, policy_routes):
    login(client, admin_user)
    policy_routes.context['plan']['posting_active'] = True
    response = client.get(BASE)
    assert b'A posting is queued or running.' in response.data
    assert re.search(rb'<button type="submit"[^>]+disabled', response.data)


def test_old_accounting_policy_disables_new_answer_previews(client, admin_user, policy_routes):
    login(client, admin_user)
    policy_routes.context['plan']['current_policy'] = False
    response = client.get(BASE)
    assert b'Prepare a current-policy review' in response.data
    assert re.search(rb'<button type="submit"[^>]+disabled', response.data)


@pytest.mark.parametrize('flag,notice', [('stale', b'create a fresh preview before saving'),
                                       ('posting_active', b'A posting is queued or running')])
def test_stale_or_posting_preview_cannot_be_confirmed_in_ui(client, admin_user, policy_routes, flag, notice):
    login(client, admin_user)
    policy_routes.preview[flag] = True
    response = client.get(BASE + '?preview_id=9')
    assert notice in response.data
    assert re.search(rb'<button class="btn btn-primary"[^>]+disabled', response.data)


def test_saved_custom_answers_thresholds_and_history_remain_readable(client, admin_user, policy_routes):
    login(client, admin_user)
    answer = dict(id=10, preview_id=9, choice='custom', choice_label='Use my own answer',
                  custom_text='<script>PRIVATE_CUSTOM_ANSWER</script>',
                  status='saved_policy_pending_implementation', scope='selected', stale=True,
                  parameters=dict(threshold_amount='1250.25', threshold_basis='complete_assembly'),
                  reviewer='owner@example.test', created_at='2026-09-27T22:00:00',
                  item_ids=['exact-purchase'], amount='108.00')
    policy_routes.context['question']['items'][0]['answer'] = answer
    policy_routes.context['saved_answers'] = [answer]
    response = client.get(BASE)
    assert b'Saved answer history' in response.data
    assert b'needs fresh review' in response.data
    assert b'&lt;script&gt;PRIVATE_CUSTOM_ANSWER&lt;/script&gt;' in response.data
    assert b'<script>PRIVATE_CUSTOM_ANSWER</script>' not in response.data
    assert b'1250.25 USD' in response.data and b'Complete assembly or project' in response.data
    assert b'owner@example.test' in response.data and b'2026-09-27T22:00:00' in response.data
    assert b'/reconciliation/amazon/policies/history/?preview_id=9' in response.data
    assert b'Earlier answers stay available' in response.data
    assert not re.search(rb'<input[^>]+(?:radio|checkbox)[^>]+\bchecked\b', response.data)


def test_empty_review_has_no_answer_form(client, admin_user, policy_routes):
    login(client, admin_user)
    policy_routes.context['plan'] = None
    response = client.get(BASE)
    assert response.status_code == 200
    assert b'No suspense review is available' in response.data
    assert b'id="amazon-policy-answer"' not in response.data
