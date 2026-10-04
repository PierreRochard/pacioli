"""Private evaluation preserves evidence without exposing or booking live data."""
import json
import stat

import pytest

from pacioli.secure_fields import PREFIX
from scripts import evaluate_suspense_advisor as evaluator
from scripts.book_local_mappings import read_encrypted, write_encrypted


def case(identifier='private-source-label', expected='Restaurants', transactions=7):
    return {
        'id': identifier,
        'expected': expected,
        'transactions': transactions,
        'input': {'examples': [{'description': 'PRIVATE CAFE PURCHASE',
                                'sign': 'debit', 'currency': 'USD'}]},
    }


def response(category='Restaurants', errors=None):
    return {'category': category, 'validation_errors': errors or [],
            'answer': {'category': category, 'evidence': 'PRIVATE CAFE PURCHASE'},
            'raw_content': 'PRIVATE CAFE PURCHASE', 'elapsed_seconds': 1.25,
            'vram_used_free_mib': '11000, 4000'}


@pytest.fixture
def fake_model(monkeypatch):
    calls = []

    class Model:
        def __init__(self, **kwargs):
            calls.append(('init', kwargs))

        def __enter__(self):
            calls.append(('enter', None))
            return self

        def __exit__(self, *args):
            calls.append(('exit', None))

    monkeypatch.setattr(evaluator, 'LocalModel', Model)
    return calls


@pytest.mark.parametrize('private', [False, True])
def test_reports_are_exclusive_private_files_and_predictions_never_receive_labels(
        tmp_path, monkeypatch, capsys, fake_model, private):
    source = tmp_path / ('cases.enc' if private else 'cases.json')
    output = tmp_path / ('report.enc' if private else 'report.json')
    item = case()
    if private:
        write_encrypted(source, [item])
    else:
        source.write_text(json.dumps([item]))
    requests = []

    def infer(model, record, *args, **kwargs):
        requests.append((record, kwargs))
        return response()

    monkeypatch.setattr(evaluator, 'infer', infer)
    args = ['--cases', str(source), '--output', str(output)]
    if private:
        args.append('--private')
    assert evaluator.main(args) == 0
    assert len(requests) == 1
    assert requests[0][0] == item['input']
    assert 'expected' not in requests[0][0]
    assert 'transactions' not in requests[0][0]
    assert 'private-source-label' not in json.dumps(requests)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert {path.name for path in tmp_path.iterdir()} == {source.name, output.name}
    report = read_encrypted(output) if private else json.loads(output.read_text())
    assert report['status'] == 'complete'
    assert report['results'][0]['expected'] == item['expected']
    assert report['summary']['transaction_weighted']['transactions'] == 7
    assert report['evaluation_scope'] == 'reviewed_expectations_not_live_bookings'
    printed = capsys.readouterr().out
    if private:
        assert output.read_text().startswith(PREFIX)
        assert 'PRIVATE CAFE' not in output.read_text()
        assert 'private-source-label' not in printed
        assert 'Restaurants' not in printed
    else:
        assert 'private-source-label' in printed
        assert 'Restaurants' in printed

    previous = output.read_bytes()
    calls_before = len(fake_model)
    with pytest.raises(FileExistsError):
        evaluator.main(args)
    assert len(fake_model) == calls_before
    assert output.read_bytes() == previous


def test_private_reports_retain_partial_results_on_failure_without_plaintext(
        tmp_path, monkeypatch, capsys, fake_model):
    source = tmp_path / 'cases.enc'
    output = tmp_path / 'report.enc'
    write_encrypted(source, [case('one'), case('two')])
    calls = []

    def infer(*args, **kwargs):
        calls.append(None)
        if len(calls) == 2:
            raise RuntimeError('Simulated inference interruption')
        return response()

    monkeypatch.setattr(evaluator, 'infer', infer)
    with pytest.raises(RuntimeError, match='interruption'):
        evaluator.main(['--private', '--cases', str(source), '--output', str(output)])
    report = read_encrypted(output)
    assert report['status'] == 'interrupted'
    assert report['planned_cases'] == 2
    assert len(report['results']) == 1
    assert report['results'][0]['case_id'] == 'one'
    assert report['summary']['correct'] == 1
    assert 'PRIVATE CAFE' not in output.read_text()
    assert 'PRIVATE CAFE' not in capsys.readouterr().out
    assert fake_model[-1][0] == 'exit'


@pytest.mark.parametrize('category, errors', [
    ('Bookkeeping Suspense', []),
    ('Groceries', []),
    ('Restaurants', ['unresolved_proposal']),
])
def test_any_wrong_or_invalid_case_returns_failure_without_label_repair(
        tmp_path, monkeypatch, fake_model, category, errors):
    source = tmp_path / 'cases.enc'
    output = tmp_path / 'report.enc'
    write_encrypted(source, [case()])
    calls = []

    def infer(*args, **kwargs):
        calls.append(None)
        return response(category, errors)

    monkeypatch.setattr(evaluator, 'infer', infer)
    assert evaluator.main(['--private', '--cases', str(source), '--output', str(output)]) == 1
    assert len(calls) == 1
    report = read_encrypted(output)
    assert report['results'][0]['category'] == category
    assert report['results'][0]['validation_errors'] == errors
    assert report['summary']['correct'] == 0


def test_private_input_requires_encryption_owner_only_mode_and_no_symlinks(tmp_path, fake_model):
    source = tmp_path / 'cases.enc'
    output = tmp_path / 'report.enc'
    args = ['--private', '--cases', str(source), '--output', str(output)]
    source.write_text(json.dumps([case()]))
    source.chmod(0o600)
    with pytest.raises(ValueError, match='encrypted'):
        evaluator.main(args)
    source.unlink()
    write_encrypted(source, [case()])
    source.chmod(0o644)
    with pytest.raises(ValueError, match='private'):
        evaluator.main(args)
    source.chmod(0o600)
    link = tmp_path / 'input-link.enc'
    link.symlink_to(source)
    with pytest.raises(OSError):
        evaluator.main(['--private', '--cases', str(link), '--output', str(output)])
    assert not fake_model
    assert not output.exists()


def test_output_symlink_is_rejected_before_gpu_and_target_is_untouched(tmp_path, fake_model):
    source = tmp_path / 'cases.enc'
    write_encrypted(source, [case()])
    target = tmp_path / 'untouched'
    target.write_text('Existing evidence')
    output = tmp_path / 'report.enc'
    output.symlink_to(target)
    with pytest.raises(OSError):
        evaluator.main(['--private', '--cases', str(source), '--output', str(output)])
    assert target.read_text() == 'Existing evidence'
    assert not fake_model


def test_custom_chart_is_used_and_retained_in_encrypted_report(tmp_path, monkeypatch, fake_model):
    source = tmp_path / 'cases.enc'
    output = tmp_path / 'report.enc'
    categories = ['Bookkeeping Suspense', 'Public transportation']
    write_encrypted(source, {'cases': [case(expected='Public transportation')],
                             'categories': categories})
    observed = []

    def infer(model, record, *args, **kwargs):
        observed.append(kwargs['categories'])
        return response('Public transportation')

    monkeypatch.setattr(evaluator, 'infer', infer)
    assert evaluator.main(['--private', '--cases', str(source), '--output', str(output)]) == 0
    assert observed == [categories]
    assert read_encrypted(output)['categories'] == categories


@pytest.mark.parametrize('transactions', [0, -1, True, 2.5, '10'])
def test_invalid_transaction_weights_rejected_before_gpu(tmp_path, fake_model, transactions):
    source = tmp_path / 'cases.enc'
    output = tmp_path / 'report.enc'
    write_encrypted(source, [case(transactions=transactions)])
    with pytest.raises(ValueError, match='positive integers'):
        evaluator.main(['--private', '--cases', str(source), '--output', str(output)])
    assert not fake_model
    assert not output.exists()


def test_scores_separate_clearing_recovery_wrong_categories_and_transaction_weights():
    def row(expected, category, weight, errors=None):
        return dict(response(category, errors), expected=expected, transactions=weight)

    rows = [row('Restaurants', 'Restaurants', 10),
            row('Transfer Clearing', 'Transfer Clearing', 40),
            row('Medical Out of Pocket', 'Restaurants', 5),
            row('Bookkeeping Suspense', 'Groceries', 7),
            row('Electricity', 'Electricity', 3, ['unresolved_proposal']),
            row('Bookkeeping Suspense', 'Bookkeeping Suspense', 2)]
    score = evaluator.summarize(rows)
    assert score['correct'] == 3
    assert score['raw_category_correct'] == 4
    assert score['expense_income_supported'] == 3
    assert score['expense_income_recovered'] == 1
    assert score['clearing_supported'] == 1
    assert score['clearing_recovered'] == 1
    assert score['wrong_category_proposals'] == 2
    assert score['unsafe_proposals'] == 1
    assert score['invalid_responses'] == 1
    weighted = score['transaction_weighted']
    assert weighted['scope'] == 'reviewed_expectations_not_live_bookings'
    assert weighted['transactions'] == 67
    assert weighted['correct'] == 52
    assert weighted['expense_income_supported'] == 18
    assert weighted['expense_income_recovered'] == 10
    assert weighted['clearing_recovered'] == 40
    assert weighted['wrong_category_proposals'] == 12
    assert weighted['unsafe_proposals'] == 7
    assert weighted['invalid_responses'] == 3


def test_missing_weights_are_explicit_and_empty_partial_report_is_valid():
    row = dict(response(), expected='Restaurants')
    assert 'transaction_weighted' not in evaluator.summarize([row])
    score = evaluator.summarize([row, dict(row, transactions=5)])
    assert score['transaction_weighted']['transactions'] == 6
    assert score['transaction_weighted']['cases_with_transaction_counts'] == 1
    assert score['transaction_weighted']['missing_transaction_count_weight'] == 1
    assert evaluator.summarize([])['correct'] == 0


@pytest.mark.parametrize('mode', ['baseline', 'tuned', 'single', 'chatml'])
@pytest.mark.parametrize('record', [
    None, {}, {'examples': []}, {'examples': 'PRIVATE CAFE'}, {'examples': [None]},
    {'examples': [{'description': 'PRIVATE CAFE', 'sign': 'unknown', 'currency': 'USD'}]},
    {'examples': [{'description': 'PRIVATE CAFE', 'sign': [], 'currency': 'USD'}]},
    {'examples': [{'description': ' ', 'sign': 'debit', 'currency': 'USD'}]},
    {'examples': [{'description': 123, 'sign': 'debit', 'currency': 'USD'}]},
    {'examples': [{'description': 'PRIVATE CAFE', 'sign': 'debit', 'currency': 123}]},
    {'examples': [{'description': 'PRIVATE CAFE', 'sign': 'debit'}]},
])
def test_every_mode_rejects_malformed_sources_before_request_construction(mode, record):
    with pytest.raises(ValueError, match='invalid_source_examples'):
        evaluator.request_for(record, 'text14', mode)


def test_private_corpus_source_preflight_happens_before_model_creation(tmp_path, fake_model):
    source = tmp_path / 'cases.enc'
    output = tmp_path / 'report.enc'
    item = case()
    item['input'] = {'examples': []}
    write_encrypted(source, [item])
    with pytest.raises(ValueError, match='invalid_source_examples'):
        evaluator.main(['--private', '--cases', str(source), '--output', str(output)])
    assert not fake_model
    assert not output.exists()


@pytest.mark.parametrize('mode', ['baseline', 'tuned', 'single', 'chatml'])
def test_oversized_source_record_is_rejected_without_sampling(mode):
    record = case()['input']
    record['examples'][0]['description'] = 'PRIVATE CAFE ' * 1000
    original = json.dumps(record)
    with pytest.raises(ValueError, match='source_record_exceeds_byte_budget'):
        evaluator.request_for(record, 'text14', mode)
    assert json.dumps(record) == original


def test_record_limit_counts_serialized_utf8_bytes_including_unicode_escapes():
    record = case()['input']
    record['examples'][0]['description'] = '\u00e9' * 1400
    assert len(record['examples'][0]['description'].encode('utf-8')) < evaluator.MAX_RECORD_BYTES
    assert len(json.dumps(record).encode('utf-8')) > evaluator.MAX_RECORD_BYTES
    with pytest.raises(ValueError, match='source_record_exceeds_byte_budget'):
        evaluator.preflight_record(record)


@pytest.mark.parametrize('mode', ['baseline', 'tuned', 'single', 'chatml'])
def test_combined_policy_messages_have_a_separate_byte_budget(monkeypatch, mode):
    oversized = 'POLICY ' * 3000
    monkeypatch.setattr(evaluator, 'BASELINE', oversized)
    monkeypatch.setattr(evaluator, 'messages', lambda *args, **kwargs: [
        {'role': 'system', 'content': oversized}, {'role': 'user', 'content': '{}'}])
    with pytest.raises(ValueError, match='messages_exceed_byte_budget'):
        evaluator.request_for(case()['input'], 'text14', mode)


def test_accepted_request_preserves_all_descriptors():
    record = {'examples': [dict(case()['input']['examples'][0], description=f'CAFE {index}')
                           for index in range(10)]}
    request = evaluator.request_for(record, 'text14', 'tuned')
    sent = json.loads(request['messages'][1]['content'])
    assert sent['record'] == record


def inference_fixture(monkeypatch, answer, **metrics):
    payload = {'message': {'content': json.dumps(answer)}, 'done_reason': 'stop', **metrics}

    class Model:
        loaded = False

        def resources(self):
            pass

        def api(self, path, data=None):
            if path == 'chat':
                return payload
            assert path == 'ps'
            return {'models': [{'name': evaluator.PROFILES['text14'][0],
                                'digest': evaluator.PROFILES['text14'][1],
                                'size': 10, 'size_vram': 10}]}

    monkeypatch.setattr(evaluator.subprocess, 'check_output', lambda *args, **kwargs: '11000, 4000')
    return Model()


@pytest.mark.parametrize('metrics, expected_errors', [
    ({}, []),
    ({'prompt_eval_count': 6656, 'eval_count': 1536}, []),
    ({'prompt_eval_count': 6657, 'eval_count': 10}, ['prompt_exceeds_context_budget']),
    ({'prompt_eval_count': 6500, 'eval_count': 1693}, ['generation_exceeds_context_budget']),
    ({'prompt_eval_count': 8100, 'eval_count': 100},
     ['prompt_exceeds_context_budget', 'generation_exceeds_context_budget']),
    ({'prompt_eval_count': 7000}, ['prompt_exceeds_context_budget']),
    ({'prompt_eval_count': '7000'}, ['invalid_context_metrics']),
    ({'prompt_eval_count': True}, ['invalid_context_metrics']),
    ({'eval_count': -1}, ['invalid_context_metrics']),
])
def test_reported_context_overflow_rejects_but_preserves_original_response(
        monkeypatch, metrics, expected_errors):
    answer = {'category': 'Restaurants', 'evidence': 'Cafe meal.', 'missing': ''}
    model = inference_fixture(monkeypatch, answer, **metrics)
    result = evaluator.infer(model, case()['input'], 'text14', 'tuned', think=True)
    assert result['validation_errors'] == expected_errors
    assert result['answer'] == answer
    assert json.loads(result['raw_content']) == answer
    assert result['category'] == 'Restaurants'


@pytest.mark.parametrize('description, sign, currency, category, errors', [
    ('EMPLOYER PAYROLL', 'debit', 'USD', 'Salary', ['outgoing_salary']),
    ('CAFE MEAL', 'debit', 'EUR', 'Restaurants', ['currency_not_supported']),
    ('HOME CONTRACTOR SERVICES', 'debit', 'USD', 'Home Maintenance',
     ['repair_versus_capital_purpose_missing']),
    ('SUPERMARKET PURCHASE WITH CASH BACK', 'debit', 'USD', 'Groceries',
     ['purchase_cashback_allocation_missing']),
    ('CAFE MEAL', 'debit', 'USD', 'Restaurants', []),
    ('UNKNOWN PURCHASE', 'debit', 'EUR', 'Bookkeeping Suspense', []),
])
def test_baseline_uses_shared_guards_without_fabricating_retained_model_explanations(
        monkeypatch, description, sign, currency, category, errors):
    answer = {'categories': [category]}
    model = inference_fixture(monkeypatch, answer)
    record = {'examples': [{'description': description, 'sign': sign, 'currency': currency}]}
    result = evaluator.infer(model, record, 'text14', 'baseline')
    assert result['validation_errors'] == errors
    assert result['answer'] == answer
    assert json.loads(result['raw_content']) == answer
    assert 'evidence' not in result['answer']
    assert 'missing' not in result['answer']
