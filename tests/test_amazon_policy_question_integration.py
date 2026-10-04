"""Exercise the real HTTP -> encrypted evidence path in disposable PostgreSQL."""
import re

from pacioli.extensions import db
from pacioli.functions import amazon_suspense as suspense
from pacioli.models import AmazonPolicyAnswers, AmazonItemJobs
from tests.test_amazon_suspense import _plan
from tests.test_amazon_item_workflow import item_workflow_source, _financial_state


BASE = '/reconciliation/amazon/policies/history/'


def _field(response, name):
    pattern = rb'name="' + name.encode() + rb'" value="([^"]*)"'
    return re.search(pattern, response.data).group(1).decode()


def _prepare_http(client, admin_user, app, monkeypatch, source):
    plan_id, packet, _, _ = _plan(source, gate='needs_property')
    assert client.post('/login/', data=admin_user).status_code == 302
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', True)
    response = client.get(BASE, query_string={'plan_id': plan_id})
    assert response.status_code == 200
    request = dict(action='preview', plan_id=str(plan_id),
        question_key=_field(response, 'question_key'), token=_field(response, 'token'),
        csrf_token=_field(response, 'csrf_token'), choice='custom', scope='this_item',
        item_id=packet['items'][0]['item_id'], custom_text='Review household use; retain project exceptions.')
    preview = client.post(BASE, data=request)
    assert preview.status_code == 302
    rendered = client.get(preview.location)
    assert rendered.status_code == 200
    return plan_id, packet, rendered


def test_real_form_saves_one_immutable_answer_without_reclassifying_and_replays(
        item_workflow_source, client, admin_user, app, monkeypatch):
    plan_id, packet, response = _prepare_http(client, admin_user, app, monkeypatch, item_workflow_source)
    before = _financial_state()
    decisions = [(j.id, j.decision_id) for j in db.session.query(AmazonItemJobs).order_by(AmazonItemJobs.id)]
    request = dict(action='confirm', preview_id=_field(response, 'preview_id'),
                   token=_field(response, 'token'), csrf_token=_field(response, 'csrf_token'))
    saved = client.post(BASE, data=request, follow_redirects=True)
    assert saved.status_code == 200
    assert b'Review household use; retain project exceptions.' in saved.data
    assert b'Original receipt:' in saved.data and b'saved for accounting implementation.' in saved.data
    assert db.session.query(AmazonPolicyAnswers).count() == 1
    assert client.post(BASE, data=request).status_code == 302
    assert db.session.query(AmazonPolicyAnswers).count() == 1
    assert _financial_state() == before
    assert [(j.id, j.decision_id) for j in db.session.query(AmazonItemJobs).order_by(AmazonItemJobs.id)] == decisions
    page = suspense.page(plan_id)
    held = next(i for i in page['items'] if i['item_id'] == packet['items'][0]['item_id'])
    assert held['policy_gate'] == 'needs_property' and not held['can_review']


def test_real_form_rejects_confirmation_after_owner_fact_changes(
        item_workflow_source, client, admin_user, app, monkeypatch):
    plan_id, packet, response = _prepare_http(client, admin_user, app, monkeypatch, item_workflow_source)
    before = _financial_state()
    suspense.save_fact(plan_id, packet['items'][0]['item_id'], 'New source context arrived.',
                       suspense.page(plan_id)['token'], admin_user['email'])
    db.session.commit()
    request = dict(action='confirm', preview_id=_field(response, 'preview_id'),
                   token=_field(response, 'token'), csrf_token=_field(response, 'csrf_token'))
    rejected = client.post(BASE, data=request)
    assert rejected.status_code == 400
    assert b'create a new preview' in rejected.data
    assert db.session.query(AmazonPolicyAnswers).count() == 0
    assert _financial_state() == before
