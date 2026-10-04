"""Private item review routes, explicit posting and detached worker handoff."""
from copy import deepcopy
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest


BASE = '/reconciliation/amazon/items/'


@pytest.fixture
def item_routes(app, monkeypatch):
    from pacioli.functions import amazon_item_workflow as workflow
    from pacioli.functions import amazon_item_posting as posting_workflow
    from pacioli.views import reconciliation_views

    calls, processes = [], []
    posting_state = SimpleNamespace(value=None)
    item = dict(item_id='opaque-item', title='<img src=x onerror=alert(1)>', department='Books',
                asin='B012345678', marketplace='Amazon.com', quantity=1, condition='New',
                amount='10.80', unit_price='10.00', unit_tax='0.80', blockers=[],
                source_refs=[dict(table_id=1, row_number=2)],
                components=[dict(id='component', kind='item', amount='10.00',
                                 source_refs=[dict(table_id=1, row_number=2,
                                                   fields=['Unit Price', 'Original Quantity'])]),
                            dict(id='tax', kind='tax', amount='0.80',
                                 source_refs=[dict(table_id=1, row_number=2, fields=['Unit Price Tax'])])])
    settlement = dict(order_id='fixture-order', date='2026-09-03', amount='10.80', currency='USD',
                      source_record=dict(candidates=[dict(account='Credit Card', date='2026-09-04',
                                                         amount='-10.80', description='AMAZON RETAIL')]))
    context = dict(
        run=dict(id=7, state='completed', progress='Classification ready for review', archive_id=297,
                 manifest_id=301, review_digest='frozen-review-digest', counts=dict(total=1, suggested=1), revision='fixture-revision',
                 posting_summary=dict(settlements=1, classified_amount='10.80', suspense_amount='0.00',
                                      held_settlements=2,
                                      categories=[dict(category='Books and Publications', amount='10.80')])),
        runs=[SimpleNamespace(id=7, state='completed')],
        rows=[dict(id=3, item=item, record_key='record', state='suggested', settlement=settlement,
                   proposal=dict(answer=dict(decision='suggest', category='Books and Publications',
                                             reason='<script>UNTRUSTED_REASON</script>', missing_fact=None),
                                 validation_errors=[]),
                   decision=None, posted=False, bundle_id=None)],
        categories=['Books and Publications'], page=1, pages=1, total=1,
        archives=[dict(id=297, filename='Orders.zip')],
    )

    def page(run_id=None, page=1):
        calls.append(('page', run_id, page))
        return deepcopy(context)

    def status(run_id):
        calls.append(('status', run_id))
        if run_id != 7:
            raise ValueError('Missing item run')
        return dict(id=7, state='completed', progress='Ready for review', counts=dict(total=1), revision='fixture-revision',
                    secret_source='NEVER_RETURN_SOURCE')

    def create(archive_id, reviewer, think=False):
        calls.append(('start', archive_id, reviewer, think))
        return SimpleNamespace(id=7)

    def review(job_id, category, reason, reviewer):
        calls.append(('review', job_id, category, reason, reviewer))
        return SimpleNamespace(id=401)

    def request_post(run_id, review_digest, reviewer, reason):
        calls.append(('posting_request', run_id, review_digest, reviewer, reason))
        posting_state.value = dict(id=91, run_id=run_id, state='queued', progress='Waiting for posting worker',
                                  result=None, error=None, revision='posting-revision')
        return SimpleNamespace(id=91)

    def no_post(*args, **kwargs):
        raise AssertionError('Ledger posting must not run in the web request')

    def launch(argv, **kwargs):
        processes.append((argv, kwargs))
        return SimpleNamespace(pid=123)

    def no_model(*args, **kwargs):
        raise AssertionError('Inference must not run in the web request')

    monkeypatch.setattr(workflow, 'page', page)
    monkeypatch.setattr(workflow, 'run_status', status)
    monkeypatch.setattr(workflow, 'create_run', create)
    monkeypatch.setattr(workflow, 'review_item', review)
    monkeypatch.setattr(workflow, 'post_reviewed', no_post)
    monkeypatch.setattr(posting_workflow, 'create_request', request_post)
    monkeypatch.setattr(posting_workflow, 'latest_status', lambda run_id: deepcopy(posting_state.value))
    monkeypatch.setattr(workflow, 'run_worker', no_model)
    monkeypatch.setattr(reconciliation_views.subprocess, 'Popen', launch)
    return SimpleNamespace(context=context, calls=calls, processes=processes, workflow=workflow,
                           posting_workflow=posting_workflow, posting_state=posting_state)


def login(client, admin_user):
    response = client.post('/login/', data=admin_user)
    assert response.status_code == 302


def test_amazon_item_routes_require_authentication_before_read_or_mutation(client, item_routes):
    assert client.get(BASE).status_code == 302
    assert client.get(BASE + '7/status/').status_code == 302
    for action in ('start', 'resume', 'review', 'post'):
        assert client.post(BASE, data=dict(action=action, run_id=7, archive_id=297,
                                          job_id=3, category='Books and Publications',
                                          reason='fixture', review_digest='digest')).status_code == 302
    assert item_routes.calls == [] and item_routes.processes == []


def test_amazon_item_page_escapes_source_and_suggestions_and_explains_posting(
        client, admin_user, item_routes):
    login(client, admin_user)
    response = client.get(BASE + '?run_id=7')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert b'<img src=x onerror=alert(1)>' not in response.data
    assert b'&lt;img src=x onerror=alert(1)&gt;' in response.data
    assert b'<script>UNTRUSTED_REASON</script>' not in response.data
    assert b'&lt;script&gt;UNTRUSTED_REASON&lt;/script&gt;' in response.data
    assert b'Matched bank source: Credit Card' in response.data
    assert b'Current posting preview:' in response.data
    assert b'2 wholly held settlements' in response.data
    assert b'10.80 USD classified' in response.data
    assert b'frozen-review-digest' in response.data
    assert b'Keep in suspense' in response.data
    assert item_routes.processes == []
    assert item_routes.calls == [('page', 7, 1)]


def test_amazon_item_posted_partial_allocation_still_allows_revised_decision(
        client, admin_user, item_routes):
    login(client, admin_user)
    item_routes.context['rows'][0]['posted'] = True
    item_routes.context['rows'][0]['bundle_id'] = 42
    response = client.get(BASE + '?run_id=7')
    assert response.status_code == 200
    assert b'An allocation has been posted.' in response.data
    assert b'Save revised item decision' in response.data
    assert b'name="job_id" value="3"' in response.data


def test_amazon_item_manual_preparation_is_not_labelled_a_qwen_hold(client, admin_user, item_routes):
    login(client, admin_user)
    item_routes.context['rows'][0].update(state='held', proposal=None, decision=None)
    response = client.get(BASE + '?run_id=7')
    assert response.status_code == 200
    assert b'Prepared for source review; Qwen has not evaluated this item under this policy.' in response.data
    assert b'Qwen hold:' not in response.data


def test_amazon_item_carried_approval_is_distinct_from_new_qwen_evaluation(client, admin_user, item_routes):
    login(client, admin_user)
    item_routes.context['rows'][0].update(state='held', proposal=None,
        decision=dict(category='Books and Publications', reason='Preserved reviewed source decision',
                      carry_forward=dict(source_run_id=4)))
    response = client.get(BASE + '?run_id=7')
    assert response.status_code == 200
    assert b'Preserved approved source decision.' in response.data
    assert b'was retained from run 4' in response.data
    assert b'This is not a new Qwen evaluation.' in response.data
    assert b'Qwen suggestion:' not in response.data


def test_amazon_item_remembered_product_suggestion_is_not_labelled_qwen(client, admin_user, item_routes):
    login(client, admin_user)
    item_routes.context['rows'][0]['proposal'].update(origin='reviewed_exact_product_rule', source_rule_id=3)
    response = client.get(BASE + '?run_id=7')
    assert response.status_code == 200
    assert b'Remembered product suggestion:' in response.data
    assert b'is not a new Qwen evaluation or posting approval' in response.data
    assert b'Qwen suggestion:' not in response.data


@pytest.mark.parametrize('query', [
    'run_id=0', 'run_id=-1', 'run_id=1.2', 'run_id=true', 'run_id=2147483648',
    'run_id=999999999999999999999999', 'page=0', 'page=-1', 'page=invalid',
])
def test_amazon_item_page_rejects_bad_identifiers(client, admin_user, item_routes, query):
    login(client, admin_user)
    assert client.get(BASE + '?' + query).status_code == 400
    assert item_routes.calls == []


def test_amazon_item_status_is_private_minimal_and_unknown_runs_are_not_found(
        client, admin_user, item_routes):
    login(client, admin_user)
    response = client.get(BASE + '7/status/')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'private, no-store'
    assert response.get_json() == dict(id=7, state='completed', progress='Ready for review', counts=dict(total=1),
                                      revision='fixture-revision', posting=None)
    assert b'NEVER_RETURN_SOURCE' not in response.data
    assert client.get(BASE + '8/status/').status_code == 404
    assert client.get(BASE + '0/status/').status_code == 404
    assert client.get(BASE + '2147483648/status/').status_code == 404


@pytest.mark.parametrize('action', ['start', 'resume', 'review', 'post'])
def test_amazon_item_all_mutations_require_csrf(client, admin_user, app, monkeypatch, item_routes, action):
    login(client, admin_user)
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    response = client.post(BASE, data=dict(action=action, run_id=7, archive_id=297, job_id=3,
                                          category='Books and Publications', reason='fixture', review_digest='digest'))
    assert response.status_code == 400
    assert item_routes.calls == [] and item_routes.processes == []


def test_amazon_item_valid_csrf_saves_explicit_hold_without_posting(
        client, admin_user, app, monkeypatch, item_routes):
    login(client, admin_user)
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    page = client.get(BASE + '?run_id=7')
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    response = client.post(BASE, data=dict(action='review', run_id=7, job_id=3,
                                          category='__suspense__', reason='Purpose not documented', csrf_token=token))
    assert response.status_code == 302
    assert item_routes.calls[-1] == ('review', 3, None, 'Purpose not documented', admin_user['email'])
    assert not any(call[0] == 'posting_request' for call in item_routes.calls)
    assert item_routes.processes == []


@pytest.mark.parametrize('action', ['start', 'resume'])
def test_amazon_item_run_launches_detached_worker_and_never_posts(
        client, admin_user, item_routes, action):
    login(client, admin_user)
    response = client.post(BASE, data=dict(action=action, archive_id=297, run_id=7))
    assert response.status_code == 302 and 'run_id=7' in response.location
    argv, options = item_routes.processes[0]
    assert argv[0] == sys.executable and argv[1].endswith('/scripts/classify_amazon_items.py') and argv[2] == '7'
    assert options['start_new_session'] and options['close_fds']
    assert options['stdin'] == options['stdout'] == options['stderr'] == subprocess.DEVNULL
    assert 'shell' not in options
    assert not any(call[0] in ('posting_request', 'review') for call in item_routes.calls)


def test_amazon_item_worker_launch_failure_retains_saved_run_and_resume_link(
        client, admin_user, monkeypatch, item_routes):
    from pacioli.views import reconciliation_views
    login(client, admin_user)

    def unavailable(*args, **kwargs):
        raise OSError('fixture worker failure')

    monkeypatch.setattr(reconciliation_views.subprocess, 'Popen', unavailable)
    response = client.post(BASE, data=dict(action='start', archive_id=297), follow_redirects=True)
    assert response.status_code == 200
    assert b'The run is saved, but its background worker could not start.' in response.data
    assert b'Resume classification' in response.data
    assert item_routes.calls[0] == ('start', 297, admin_user['email'], False)
    assert item_routes.calls[-1] == ('page', 7, 1)


@pytest.mark.parametrize('fields', [
    dict(job_id='0'), dict(job_id='-1'), dict(job_id='bad'), dict(run_id='999999999999999'),
    dict(category='__choose__'), dict(reason=''), dict(reason=' '), dict(reason='x' * 2001),
])
def test_amazon_item_review_rejects_invalid_inputs_before_workflow(
        client, admin_user, item_routes, fields):
    login(client, admin_user)
    form = dict(action='review', run_id='7', job_id='3', category='Books and Publications', reason='Reviewed source')
    form.update(fields)
    assert client.post(BASE, data=form).status_code == 302
    assert item_routes.calls == [] and item_routes.processes == []


def test_amazon_item_post_is_explicit_and_binds_the_rendered_review_digest(
        client, admin_user, item_routes):
    login(client, admin_user)
    response = client.post(BASE, data=dict(action='post', run_id=7, review_digest='frozen-review-digest',
                                          reason='Reviewed original item evidence'))
    assert response.status_code == 302
    assert item_routes.calls == [('posting_request', 7, 'frozen-review-digest', admin_user['email'],
                                 'Reviewed original item evidence')]
    argv, options = item_routes.processes[0]
    assert argv[0] == sys.executable and argv[1].endswith('/scripts/post_amazon_items.py') and argv[2] == '91'
    assert options['start_new_session'] and options['close_fds'] and 'shell' not in options
    assert options['stdin'] == options['stdout'] == options['stderr'] == subprocess.DEVNULL


def test_amazon_item_stale_posting_failure_is_visible_and_does_not_launch_worker(
        client, admin_user, monkeypatch, item_routes):
    login(client, admin_user)

    def reject(*args):
        raise ValueError('Item decisions changed since preview')

    monkeypatch.setattr(item_routes.posting_workflow, 'create_request', reject)
    response = client.post(BASE, data=dict(action='post', run_id=7, review_digest='old-digest',
                                          reason='Reviewed evidence'), follow_redirects=True)
    assert response.status_code == 200
    assert b'Item decisions changed since preview' in response.data
    assert item_routes.processes == []


def test_amazon_item_posting_status_exposes_aggregate_result_only(client, admin_user, item_routes):
    login(client, admin_user)
    item_routes.posting_state.value = dict(
        id=91, run_id=7, state='completed', progress='Posting completed', revision='done-revision', error=None,
        private_request='DO_NOT_DISCLOSE', result=dict(posted=4, revised=1, unchanged=2, held_settlements=3,
            classified_amount='100.25', suspense_amount='12.00', bundles=[123,456], source='PRIVATE SOURCE'))
    response = client.get(BASE + '7/status/')
    assert response.status_code == 200
    assert response.get_json()['posting'] == dict(
        id=91, state='completed', progress='Posting completed', revision='done-revision', error=None,
        result=dict(posted=4, revised=1, unchanged=2, held_settlements=3,
                    classified_amount='100.25', suspense_amount='12.00'))
    assert b'DO_NOT_DISCLOSE' not in response.data and b'PRIVATE SOURCE' not in response.data
    assert 'bundles' not in response.get_json()['posting']['result']
    page = client.get(BASE + '?run_id=7')
    assert page.status_code == 200
    assert b'Posting request 91' in page.data and b'Posting completed' in page.data
    assert b'100.25 USD' in page.data


def test_amazon_item_posting_worker_launch_failure_retains_frozen_request(
        client, admin_user, monkeypatch, item_routes):
    from pacioli.views import reconciliation_views
    login(client, admin_user)

    def unavailable(*args, **kwargs):
        raise OSError('fixture worker failure')

    monkeypatch.setattr(reconciliation_views.subprocess, 'Popen', unavailable)
    response = client.post(BASE, data=dict(action='post', run_id=7, review_digest='frozen-review-digest',
                                          reason='Reviewed evidence'), follow_redirects=True)
    assert response.status_code == 200
    assert b'The posting request is saved, but its background worker could not start.' in response.data
    assert b'Submit Post reviewed allocations again to retry the saved request.' in response.data
    assert b'Posting request 91' in response.data
    assert item_routes.calls[0][0] == 'posting_request'
    assert item_routes.posting_state.value['state'] == 'queued'


@pytest.mark.parametrize('state', ['queued', 'running'])
def test_amazon_item_review_during_background_posting_returns_without_waiting_for_ledger(
        client, admin_user, item_routes, state):
    login(client, admin_user)
    item_routes.posting_state.value = dict(id=91, state=state, progress='Posting in progress',
                                         result=None, error=None, revision='active-revision')
    response = client.post(BASE, data=dict(action='review', run_id=7, job_id=3,
                                          category='Books and Publications', reason='A new review'), follow_redirects=True)
    assert response.status_code == 200
    assert b'Posting is in progress. Review changes after it finishes.' in response.data
    assert not any(call[0] == 'review' for call in item_routes.calls)
    assert item_routes.processes == []
