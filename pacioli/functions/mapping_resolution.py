"""Deterministic OFX classification and durable evidence of the rules used.

Exact descriptions outrank keyword rules, including a provisional exact rule:
an unresolved purpose must not silently become an expense through a broad rule.
Within exact matches, reviewed rules outrank provisional rules. Keywords have
equal authority; a longer phrase alone is not evidence of a reviewed policy.
"""
from copy import deepcopy
from datetime import timezone
from hashlib import sha256
import json


POLICY = 'ofx-mapping-resolution-v1'
RULE_FIELDS = ('id', 'source', 'keyword', 'match_mode', 'review_required',
               'review_note', 'provenance', 'positive_debit_subaccount_id',
               'positive_credit_subaccount_id', 'negative_debit_subaccount_id',
               'negative_credit_subaccount_id')


class MappingConflictError(ValueError):
    """No accounting policy authorizes a unique outcome for this source."""

    def __init__(self, source_id, mapping_ids):
        self.source_id = source_id
        self.mapping_ids = sorted(mapping_ids)
        super().__init__('Mapping conflict for source {}: rules {} disagree; '
                         'review the mappings before posting.'.format(
                             source_id, ', '.join(map(str, self.mapping_ids))))


def rule_snapshot(mapping):
    values = {key: deepcopy(getattr(mapping, key)) for key in RULE_FIELDS}
    values['revision_sha256'] = sha256(json.dumps(
        values, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return values


def default_mapping_decision(mapping_id):
    """Record the rule seen by an explicit reviewed writer at posting time.

    This deliberately makes no claim that older entries used today's rule.
    Existing entries without evidence remain without historical snapshots.
    """
    from pacioli.extensions import db
    from pacioli.models import Mappings
    mapping = db.session.get(Mappings, mapping_id)
    if mapping is None:
        raise ValueError('The selected mapping no longer exists')
    snapshot = rule_snapshot(mapping)
    return {'policy': 'explicit-mapping-v1', 'selected_mapping_id': mapping.id,
            'selected_revision_sha256': snapshot['revision_sha256'],
            'candidates': [snapshot]}


def description_matches(mapping, description):
    """Match ordered literal words; percent and underscore are ordinary text."""
    description = (description or '').lower()
    keyword = (mapping.keyword or '').lower()
    if not keyword.strip():
        return False
    if mapping.match_mode == 'exact':
        return description == keyword
    if mapping.match_mode != 'keyword':
        raise ValueError('Unsupported mapping match mode')
    offset = 0
    for word in keyword.split():
        index = description.find(word, offset)
        if index < 0:
            return False
        offset = index + len(word)
    return True


def source_matches(mapping, transaction):
    if not description_matches(mapping, transaction.description):
        return False
    # These optional fields historically looked like overrides even though the
    # bank leg must always equal the actual source account. Treat them as scope.
    account_scope = (mapping.positive_debit_subaccount_id if transaction.amount > 0
                     else mapping.negative_credit_subaccount_id)
    return account_scope is None or account_scope == transaction.account


def source_snapshot(transaction):
    when = transaction.date
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return {'id': transaction.id, 'account': transaction.account,
            'date': when.astimezone(timezone.utc).isoformat(),
            'amount': str(transaction.amount),
            'description': transaction.description}


class MappingResolver:
    """Index exact rules so a large reviewed rule set stays cheap to resolve."""

    def __init__(self, mappings):
        self.exact = {}
        self.keywords = []
        for mapping in mappings:
            if mapping.source != 'ofx' or not (mapping.keyword or '').strip():
                continue
            if mapping.match_mode == 'exact':
                self.exact.setdefault(mapping.keyword.lower(), []).append(mapping)
            elif mapping.match_mode == 'keyword':
                self.keywords.append(mapping)
            else:
                raise ValueError('Unsupported mapping match mode')

    def candidates(self, transaction):
        pool = self.exact.get((transaction.description or '').lower(), []) + self.keywords
        return [rule for rule in pool if source_matches(rule, transaction)]

    def resolve(self, transaction, candidates=None):
        candidates = self.candidates(transaction) if candidates is None else candidates
        if not candidates:
            return None
        if transaction.amount == 0:
            raise ValueError('Cannot map a zero-amount OFX transaction')
        if not transaction.account:
            raise ValueError('OFX transaction and mapping need named accounts')

        def authority(rule):
            return (int(rule.match_mode == 'exact'),
                    int(rule.match_mode == 'exact' and not rule.review_required))

        rank = max(authority(rule) for rule in candidates)
        authoritative = [rule for rule in candidates if authority(rule) == rank]
        def outcome(rule):
            category = (rule.positive_credit_subaccount_id if transaction.amount > 0
                        else rule.negative_debit_subaccount_id)
            return category, bool(rule.review_required)

        if len({outcome(rule) for rule in authoritative}) != 1:
            raise MappingConflictError(transaction.id, [rule.id for rule in authoritative])
        snapshots = sorted((rule_snapshot(rule) for rule in candidates), key=lambda row: row['id'])
        revisions = {row['id']: row['revision_sha256'] for row in snapshots}
        selected = min(authoritative, key=lambda rule: (revisions[rule.id], rule.id))
        category, _ = outcome(selected)
        if not category:
            raise ValueError('Source {} has no mapping account for this direction; '
                             'review the rule before posting'.format(transaction.id))
        debit, credit = ((transaction.account, category) if transaction.amount > 0
                         else (category, transaction.account))
        decision = {'policy': POLICY, 'selected_mapping_id': selected.id,
                    'selected_revision_sha256': revisions[selected.id],
                    'candidates': snapshots, 'source': source_snapshot(transaction),
                    'posting': {'debit': debit, 'credit': credit,
                                'amount': str(abs(transaction.amount))}}
        return selected, decision


def verify_replay(transaction, entries):
    """A previous resolver decision is evidence, never permission to repost.

    Other reviewed workflows and legacy journals retain their own evidence.
    Classification corrections deliberately retain this original snapshot; the
    independent ledger audit validates the current allocation and correction
    workflow keeps its own before/after evidence.
    """
    from decimal import Decimal
    for entry in entries:
        decision = entry.mapping_decision
        if not decision or decision.get('policy') != POLICY:
            continue
        source = dict(decision['source'])
        current = source_snapshot(transaction)
        # PostgreSQL numeric representations can differ only in trailing zeros.
        source_amount, current_amount = Decimal(source.pop('amount')), Decimal(current.pop('amount'))
        if source != current or source_amount != current_amount:
            raise ValueError('Previously mapped source {} changed; review required'.format(transaction.id))
        bank_side = entry.debit_subaccount if transaction.amount > 0 else entry.credit_subaccount
        if (len(entries) != 1 or bank_side != transaction.account or
                entry.functional_amount != abs(transaction.amount) or
                entry.timestamp.astimezone(timezone.utc).isoformat() != current['date']):
            raise ValueError('Existing mapping posting differs from its source; review required')
