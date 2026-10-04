#!/usr/bin/env python
"""Serial, encrypted local evaluation; never tunes prompts or writes journals.

--cases is an owner-only encrypted JSON list or {"categories": [...], "cases":
[...], "partition": "holdout"}. Each case has id, item (the model_item packet),
expected (existing category name or null), and an independently supplied reason.
Optional expected_decision checks the abstention type; family identifies a frozen
product-family group; amount_minor supplies already-attributed USD cents for
separately reported weighting. Labels/reasons/weights/families never enter Qwen.
The caller owns independent labeling and a frozen family-disjoint partition.
This script does not claim that a supplied corpus is independently held out.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pacioli.functions.amazon_item_advisor import (
    CATEGORY_DEFINITIONS, DECISIONS, POLICY, POLICY_VERSION, _hash, infer, request_for,
)
from pacioli.functions.chase_local_model import LocalModel
from pacioli.functions.chase_local_bridge import LocalChaseError
from pacioli.functions.suspense_advisor import PROFILES
from pacioli.secure_fields import encrypt_value
from scripts.book_local_mappings import read_encrypted


def _correct(row, *, check_validation=True):
    if check_validation and row.get('validation_errors'):
        return False
    answer = row.get('answer')
    if not isinstance(answer, dict) or answer.get('decision') not in DECISIONS:
        return False
    if row.get('category') != row['expected']:
        return False
    if row['expected'] is None and answer['decision'] == 'suggest':
        return False
    if row['expected'] is not None and answer['decision'] != 'suggest':
        return False
    return not row.get('expected_decision') or answer['decision'] == row['expected_decision']


def summarize(rows, cases):
    """All planned cases remain in coverage denominators, including failures."""
    valid = [row for row in rows if not row.get('validation_errors')]
    proposed = [row for row in valid if isinstance(row.get('answer'), dict)
                and row['answer'].get('decision') == 'suggest' and row.get('category') is not None]
    held = [row for row in valid if isinstance(row.get('answer'), dict)
            and row['answer'].get('decision') in DECISIONS[1:] and row.get('category') is None]
    supported = [case for case in cases if case['expected'] is not None]
    correct = [row for row in rows if _correct(row)]
    recovered = [row for row in correct if row['expected'] is not None]
    correct_proposals = [row for row in proposed if _correct(row)]
    weighted = {case['id']: case['amount_minor'] for case in cases if 'amount_minor' in case}
    result = {
        'planned_cases': len(cases), 'completed_cases': len(rows),
        'not_run': len(cases) - len(rows), 'valid_responses': len(valid),
        'invalid_responses': len(rows) - len(valid), 'correct': len(correct),
        'raw_correct': sum(_correct(row, check_validation=False) for row in rows),
        'supported_cases': len(supported), 'supported_recovered': len(recovered),
        'proposals': len(proposed), 'correct_proposals': len(correct_proposals),
        'wrong_category_proposals': len(proposed) - len(correct_proposals),
        'false_resolutions': sum(row['expected'] is None for row in proposed),
        'holds': len(held), 'unnecessary_holds': sum(row['expected'] is not None for row in held),
        'accepted_precision': len(correct_proposals) / len(proposed) if proposed else None,
        'supported_coverage': len(recovered) / len(supported) if supported else None,
        'overall_planned_accuracy': len(correct) / len(cases),
        'planned_proposal_coverage': len(proposed) / len(cases),
        'median_seconds': statistics.median(row['elapsed_seconds'] for row in rows) if rows else None,
        'family_labels_supplied': sum('family' in case for case in cases),
        'families': len({case['family'] for case in cases if 'family' in case}),
        'weighting': {
            'scope': 'supplied_attributed_item_cents_only_shared_unattributed_costs_excluded',
            'cases_with_weight': len(weighted), 'planned_minor': sum(weighted.values()),
            'correct_minor': sum(weighted.get(row['case_id'], 0) for row in correct),
            'supported_recovered_minor': sum(weighted.get(row['case_id'], 0) for row in recovered),
            'wrong_proposal_minor': sum(weighted.get(row['case_id'], 0) for row in proposed if not _correct(row)),
        },
    }
    return result


def load_cases(path, think):
    corpus = read_encrypted(path)
    categories = corpus.get('categories', list(CATEGORY_DEFINITIONS)) if isinstance(corpus, dict) else list(CATEGORY_DEFINITIONS)
    cases = corpus.get('cases') if isinstance(corpus, dict) else corpus
    if not isinstance(cases, list) or not cases:
        raise ValueError('nonempty_case_list_required')
    seen = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str)
                or not case['id'].strip() or len(case['id']) > 256 or case['id'] in seen
                or 'expected' not in case or not isinstance(case.get('reason'), str)
                or not case['reason'].strip()):
            raise ValueError('independent_unique_case_and_reason_required')
        seen.add(case['id'])
        request = request_for(case.get('item'), categories, think=think)
        allowed = request['format']['properties']['category']['enum']
        if case['expected'] not in allowed:
            raise ValueError('expected_category_absent_from_eligible_chart')
        if 'expected_decision' in case:
            decision = case['expected_decision']
            if decision not in DECISIONS or (decision == 'suggest') != (case['expected'] is not None):
                raise ValueError('inconsistent_expected_decision')
        if 'family' in case and (not isinstance(case['family'], str) or not case['family'].strip()):
            raise ValueError('invalid_family')
        if 'amount_minor' in case and (type(case['amount_minor']) is not int or case['amount_minor'] < 0):
            raise ValueError('invalid_attributed_amount')
    return cases, categories, corpus.get('partition') if isinstance(corpus, dict) else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--think', action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    cases, categories, partition = load_cases(args.cases, args.think)
    name, digest, headroom = PROFILES['text14']
    meta = {
        'started_at': datetime.now(timezone.utc).isoformat(), 'model': name,
        'model_digest': digest, 'policy_version': POLICY_VERSION, 'policy_sha256': _hash(POLICY),
        'cases_sha256': _hash(cases), 'categories': categories, 'think': args.think,
        'declared_partition': partition, 'planned_cases': len(cases),
        'evaluation_scope': 'independently_supplied_item_expectations_not_posted_journals',
        'partition_verified_by_script': False,
        'request_options': request_for(cases[0]['item'], categories, think=args.think)['options'],
    }
    # Validate encryption and reserve a private exclusive file before any GPU work.
    encrypt_value('{}')
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    rows, status, failure_type, failure_code = [], 'interrupted', None, None
    try:
        with LocalModel(model=name, digest=digest, minimum_free_mib=headroom) as model:
            for case in cases:
                result = infer(model, case['item'], categories, think=args.think)
                result.update(case_id=case['id'], expected=case['expected'])
                if 'expected_decision' in case:
                    result['expected_decision'] = case['expected_decision']
                rows.append(result)
                print(json.dumps({'completed': len(rows), 'total': len(cases),
                                  'seconds': round(result['elapsed_seconds'], 2)}), flush=True)
        status = 'complete'
    except Exception as exc:
        # Exception text and traceback may contain private source or response text.
        failure_type = type(exc).__name__
        # Our local connector exceptions contain fixed codes. Expose only that
        # constrained vocabulary, never arbitrary exception/source text.
        if isinstance(exc, LocalChaseError) and re.fullmatch(r'local_model_[a-z_]{1,80}', str(exc)):
            failure_code = str(exc)
    finally:
        report = dict(meta, status=status, failure_type=failure_type, failure_code=failure_code, results=rows,
                      summary=summarize(rows, cases))
        with os.fdopen(descriptor, 'w') as output:
            output.write(encrypt_value(json.dumps(report, ensure_ascii=True, allow_nan=False)))
            output.flush()
            os.fsync(output.fileno())
    print(json.dumps({'status': status, 'failure_type': failure_type,
                      'failure_code': failure_code, 'summary': report['summary']}), flush=True)
    return 0 if status == 'complete' and report['summary']['correct'] == len(cases) else 1


if __name__ == '__main__':
    sys.exit(main())
