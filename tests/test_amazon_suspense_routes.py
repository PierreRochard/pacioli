"""Private, explicit suspense review operations never invoke models or the ledger."""
from copy import deepcopy
import re
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import MultiDict


BASE = '/reconciliation/amazon/suspense/'


@pytest.fixture
def suspense_routes(app, monkeypatch):
    from pacioli.functions import amazon_suspense as suspense
    from pacioli.functions import amazon_item_workflow as workflow
    from pacioli.functions import amazon_item_posting as posting
    from pacioli.views import reconciliation_views

    calls = []
    item = dict(item_id='fixture-item', title='<img src=x onerror=alert(1)>', amount='10.80',
                quantity=1, department='Home',
                source_refs=[dict(table_id=41, row_number=2)],
                components=[dict(kind='item', amount='10.00',
                                 source_refs=[dict(table_id=41, row_number=2, fields=['Unit Price'])]),
                            dict(kind='tax', amount='0.80',
                                 source_refs=[dict(table_id=41, row_number=2, fields=['Unit Price Tax'])])])
    context = dict(
        plan=dict(id=5, source_run_id=7, review_run_id=8, archive_id=297, current_policy=True, posting_active=False),
        token='immutable-review-token',
        totals=dict(count=26, amount='1010.80', fact_count=1, property_count=1,
                    remaining_count=25, remaining_amount='1000.00', resolved_count=1, resolved_amount='10.80',
                    unverified_count=0, unverified_amount='0.00',
                    reviewed_unposted_count=1, reviewed_unposted_amount='10.80'),
        groups=[dict(key='property_policy', label='Decide property treatment', count=1, amount='1000.00'),
                dict(key='category_gap', label='Review ordinary purchase categories', count=25, amount='10.80')],
        categories=[dict(key='Household Supplies', label='Household Supplies', count=25, amount='10.80')],
        items=[dict(item_id='fixture-item', item=item, group='category_gap',
                    group_label='Review ordinary purchase categories', proposed_category='Household Supplies',
                    reason='<script>UNTRUSTED_REVIEW</script>', action_needed='Review the source product.',
                    current_decision=dict(category='Personal Care', reason='Saved owner decision'),
                    posted=dict(category='Bookkeeping Suspense', amount='10.80', decision_id=4, bundle_id=9),
                    can_review=True, hold_reason=None, needs_fact=False, job_id=30,
                    facts=[dict(id=1, note='<script>UNTRUSTED_NOTE</script>', reviewer='owner@example.test',
                                created_at='2026-09-27T19:30:00Z')])],
        page=1, pages=2, total=26, available_categories=['Household Supplies', 'Personal Care'],
    )

    def page(plan_id=None, group='', category='', page=1):
        calls.append(('page', plan_id, group, category, page))
        return deepcopy(context)

    def prepare(plan_id, reviewer, think=False):
        calls.append(('prepare', plan_id, reviewer, think))
        return SimpleNamespace(id=8)

    def fact(plan_id, item_id, note, expected_digest, reviewer):
        calls.append(('fact', plan_id, item_id, note, expected_digest, reviewer))
        return SimpleNamespace(id=10)

    def review(plan_id, item_ids, category, reason, expected_digest, reviewer, remember_product=False):
        calls.append(('review', plan_id, item_ids, category, reason, expected_digest, reviewer, remember_product))
        return dict(reviewed=len(item_ids))

    def forbidden(*args, **kwargs):
        raise AssertionError('The suspense workbench must not run models, workers, or ledger posting')

    monkeypatch.setattr(suspense, 'page', page)
    monkeypatch.setattr(suspense, 'prepare_new_review_run', prepare)
    monkeypatch.setattr(suspense, 'save_fact', fact)
    monkeypatch.setattr(suspense, 'apply_reviews', review)
    monkeypatch.setattr(workflow, 'run_worker', forbidden)
    monkeypatch.setattr(workflow, 'post_reviewed', forbidden)
    monkeypatch.setattr(posting, 'create_request', forbidden)
    monkeypatch.setattr(reconciliation_views.subprocess, 'Popen', forbidden)
    return SimpleNamespace(context=context, calls=calls, suspense=suspense)


def login(client, admin_user):
    assert client.post('/login/', data=admin_user).status_code == 302


def review_form(**overrides):
    result = dict(action='review', plan_id='5', item_id='fixture-item', category='Household Supplies',
                  reason='Reviewed exact source product and household use', token='immutable-review-token')
    result.update(overrides)
    return result


def test_suspense_workbench_requires_authentication_before_read_or_mutation(client, suspense_routes):
    assert client.get(BASE).status_code == 302
    for action in ('prepare', 'fact', 'review'):
        assert client.post(BASE, data=review_form(action=action, note='Context')).status_code == 302
    assert suspense_routes.calls == []


def test_suspense_page_is_private_escaped_and_separates_proposal_decision_and_ledger(
        client, admin_user, suspense_routes):
    login(client, admin_user)
    response = client.get(BASE + '?plan_id=5')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert b'<img src=x onerror=alert(1)>' not in response.data
    assert b'&lt;img src=x onerror=alert(1)&gt;' in response.data
    assert b'<script>UNTRUSTED_REVIEW</script>' not in response.data
    assert b'&lt;script&gt;UNTRUSTED_REVIEW&lt;/script&gt;' in response.data
    assert b'&lt;script&gt;UNTRUSTED_NOTE&lt;/script&gt;' in response.data
    assert b'Evidence-review suggestion</dt><dd>Household Supplies' in response.data
    assert b'Saved item decision</dt><dd>Personal Care' in response.data
    assert b'Ledger allocation</dt><dd>Bookkeeping Suspense' in response.data
    assert b'10.80 USD including adjustments' in response.data
    assert b'/reconciliation/amazon/table/41/' in response.data
    assert b'0.80' in response.data and b'Unit Price Tax' in response.data
    assert b'no capitalization threshold is assumed' in response.data
    assert b'run_id=8#post-items-heading' in response.data
    assert b'does not approve future purchases or post entries' in response.data
    assert b'Nothing is selected automatically' in response.data
    assert b'Confirmed still in suspense in this review' in response.data
    assert re.search(rb'<strong>25 items.*1000.00 USD</strong>', response.data)
    assert b'The original review covered' in response.data and b'1010.80 USD' in response.data
    assert suspense_routes.calls == [('page', 5, '', '', 1)]


def test_suspense_filters_and_pagination_retain_exact_filter_values(client, admin_user, suspense_routes):
    login(client, admin_user)
    response = client.get(BASE, query_string=dict(plan_id=5, group='category_gap', category='Household Supplies', page=2))
    assert response.status_code == 200
    assert suspense_routes.calls == [('page', 5, 'category_gap', 'Household Supplies', 2)]
    assert b'name="filter_group" value="category_gap"' in response.data
    assert b'name="filter_category" value="Household Supplies"' in response.data
    assert b'category=Household+Supplies' in response.data or b'category=Household%20Supplies' in response.data


@pytest.mark.parametrize('query', [
    'plan_id=0', 'plan_id=-1', 'plan_id=no', 'plan_id=2147483648', 'plan_id=9999999999999999999999',
    'page=0', 'page=1.2', 'page=-2', 'page=invalid', 'group=' + 'x' * 65, 'category=' + 'x' * 201,
])
def test_suspense_invalid_get_inputs_do_not_reach_backend(client, admin_user, suspense_routes, query):
    login(client, admin_user)
    assert client.get(BASE + '?' + query).status_code == 400
    assert suspense_routes.calls == []


@pytest.mark.parametrize('action', ['prepare', 'fact', 'review'])
def test_suspense_all_mutations_require_csrf(client, admin_user, app, monkeypatch, suspense_routes, action):
    login(client, admin_user)
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    assert client.post(BASE, data=review_form(action=action, note='Context')).status_code == 400
    assert suspense_routes.calls == []


def test_suspense_valid_csrf_appends_fact_with_frozen_review_token(
        client, admin_user, app, monkeypatch, suspense_routes):
    login(client, admin_user)
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    page = client.get(BASE + '?plan_id=5')
    csrf_token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post(BASE, data=review_form(action='fact', note='Replaced the worn existing fitting.',
                                                  csrf_token=csrf_token))
    assert response.status_code == 302
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert suspense_routes.calls[-1] == ('fact', 5, 'fixture-item', 'Replaced the worn existing fitting.',
                                        'immutable-review-token', admin_user['email'])


def test_suspense_prepare_is_manual_and_does_not_launch_background_work(
        client, admin_user, suspense_routes):
    login(client, admin_user)
    response = client.post(BASE, data=dict(action='prepare', plan_id=5))
    assert response.status_code == 302
    assert suspense_routes.calls == [('prepare', 5, admin_user['email'], False)]


def test_suspense_review_saves_only_selected_items_and_optional_future_suggestion_rule(
        client, admin_user, suspense_routes):
    login(client, admin_user)
    form = MultiDict(review_form(remember_product='1', filter_group='category_gap', filter_category='Household Supplies'))
    form.add('item_id', 'second-item')
    response = client.post(BASE, data=form)
    assert response.status_code == 302
    assert 'group=category_gap' in response.location
    assert suspense_routes.calls == [('review', 5, ['fixture-item', 'second-item'], 'Household Supplies',
        'Reviewed exact source product and household use', 'immutable-review-token', admin_user['email'], True)]


def test_suspense_can_explicitly_keep_selected_item_in_suspense(client, admin_user, suspense_routes):
    login(client, admin_user)
    assert client.post(BASE, data=review_form(category='__suspense__')).status_code == 302
    assert suspense_routes.calls[0][3] is None
    assert suspense_routes.calls[0][-1] is False


@pytest.mark.parametrize('changes', [
    dict(plan_id='0'), dict(plan_id='bad'), dict(page='-1'), dict(item_id=''), dict(item_id='../secret'),
    dict(item_id='x' * 129), dict(category=''), dict(category='__choose__'), dict(reason=' '),
    dict(reason='x' * 2001), dict(token=''), dict(token='x' * 129), dict(remember_product='true'),
    dict(category='__suspense__', remember_product='1'), dict(action='post'),
])
def test_suspense_invalid_post_inputs_do_not_reach_backend(client, admin_user, suspense_routes, changes):
    login(client, admin_user)
    assert client.post(BASE, data=review_form(**changes)).status_code == 302
    assert suspense_routes.calls == []


@pytest.mark.parametrize('ids', [[], ['fixture-item', 'fixture-item'], [' fixture-item', 'fixture-item'],
                                ['item-' + str(n) for n in range(101)]])
def test_suspense_bulk_selection_is_explicit_distinct_and_bounded(client, admin_user, suspense_routes, ids):
    login(client, admin_user)
    form = MultiDict(review_form())
    form.setlist('item_id', ids)
    assert client.post(BASE, data=form).status_code == 302
    assert suspense_routes.calls == []


@pytest.mark.parametrize('note', ['', ' ', 'x' * 2001])
def test_suspense_fact_requires_bounded_nonempty_text(client, admin_user, suspense_routes, note):
    login(client, admin_user)
    assert client.post(BASE, data=review_form(action='fact', note=note)).status_code == 302
    assert suspense_routes.calls == []


def test_suspense_stale_review_is_rejected_with_visible_refresh_instruction(
        client, admin_user, monkeypatch, suspense_routes):
    login(client, admin_user)

    def stale(*args, **kwargs):
        raise ValueError('This review changed. Refresh before saving classifications.')

    monkeypatch.setattr(suspense_routes.suspense, 'apply_reviews', stale)
    response = client.post(BASE, data=review_form(), follow_redirects=True)
    assert response.status_code == 200
    assert b'This review changed. Refresh before saving classifications.' in response.data
    assert b'Reviewed classifications saved.' not in response.data
    assert all(call[0] == 'page' for call in suspense_routes.calls)


def test_suspense_property_hold_is_visible_and_cannot_be_selected(client, admin_user, suspense_routes):
    login(client, admin_user)
    row = suspense_routes.context['items'][0]
    row.update(group='property_policy', can_review=False,
               hold_reason='A property-accounting policy is still needed.', posted=None)
    response = client.get(BASE)
    assert response.status_code == 200
    assert b'A property-accounting policy is still needed.' in response.data
    assert re.search(rb'class="suspense-selection"\s+disabled', response.data)
    assert b'No item allocation is confirmed by this review.' in response.data


def test_suspense_old_policy_requires_prepare_before_review(client, admin_user, suspense_routes):
    login(client, admin_user)
    suspense_routes.context['plan']['current_policy'] = False
    response = client.get(BASE)
    assert response.status_code == 200
    assert b'Prepare the review before saving item classifications.' in response.data
    assert re.search(rb'class="btn btn-primary"\s+disabled>Save reviewed classifications', response.data)


def test_suspense_unverified_ledger_coverage_is_not_presented_as_resolved_or_held(
        client, admin_user, suspense_routes):
    login(client, admin_user)
    suspense_routes.context['totals'].update(remaining_count=24, remaining_amount='900.00',
                                             unverified_count=1, unverified_amount='100.00')
    response = client.get(BASE)
    assert response.status_code == 200
    assert re.search(rb'<strong>24 items.*900.00 USD</strong>', response.data)
    assert b'The current ledger allocation could not be verified for 1 items' in response.data
    assert b'excluded from the confirmed suspense and posted totals' in response.data


def test_suspense_active_posting_disables_preparation_review_and_fact_mutation(
        client, admin_user, suspense_routes):
    login(client, admin_user)
    suspense_routes.context['plan']['posting_active'] = True
    response = client.get(BASE)
    assert response.status_code == 200
    assert b'Posting is in progress.' in response.data
    assert re.search(rb'class="btn btn-secondary"\s+disabled>Prepare current-policy review', response.data)
    assert re.search(rb'class="btn btn-primary"\s+disabled>Save reviewed classifications', response.data)
    assert re.search(rb'class="btn btn-secondary"\s+disabled>Save purchase context', response.data)


def test_suspense_empty_workbench_has_actionable_source_link(client, admin_user, suspense_routes):
    login(client, admin_user)
    suspense_routes.context['plan'] = None
    response = client.get(BASE)
    assert response.status_code == 200
    assert b'No Amazon suspense review is available yet.' in response.data
    assert b'/reconciliation/amazon/items/' in response.data
