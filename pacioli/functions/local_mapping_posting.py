"""Persist reviewed mappings and atomically post their selected source journals."""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re

from sqlalchemy import text

from pacioli.extensions import db
from pacioli.functions.journal_posting import decimal_amount, post_pair
from pacioli.functions.accounting_report import audit_ledger
from pacioli.models import JournalEntries, Mappings
from pacioli.functions.mapping_workflow import SUSPENSE_ACCOUNTS, persist_exact_mapping


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


CHART_SQL = '''SELECT s.name,a.name account,c.name classification,e.name element
 FROM bookkeeping.subaccounts s JOIN bookkeeping.accounts a ON a.name=s.parent
 JOIN bookkeeping.classifications c ON c.name=a.parent
 JOIN bookkeeping.elements e ON e.name=c.parent ORDER BY e.name,s.name'''
SOURCE_SQL = '''SELECT t.id,t.date,t.amount,t.description,t.type::text,t.account,t.account_id,b.currency
 FROM ofx.transactions t LEFT JOIN bookkeeping.bank_feed_accounts b ON b.ofx_account_id=t.account_id
 WHERE t.id = ANY(:ids) ORDER BY t.date,t.id'''


def blocking_batch_issues(session, issues, selected_sources):
    """Permit an unrelated unposted backlog while retaining the full audit.

    A bounded review need not book the entire bank history at once. Only a plain
    omission outside its authorized selection may remain. Partial, malformed,
    overbooked, or misdirected existing allocations always block the batch.
    """
    posted_sources = {(source, identifier) for source, identifier in session.query(
        JournalEntries.transaction_source, JournalEntries.transaction_id).filter(
            JournalEntries.transaction_source.in_(('ofx', 'amazon'))).distinct()}
    blocking = []
    for issue in issues:
        if issue['kind'] in ('bank_balance', 'classification_pending'):
            continue
        # Unposted or unsupported brokerage rows block closing a month, not other batches.
        if issue['kind'].startswith('brokerage') and issue.get('scope') == 'close':
            continue
        key = issue.get('source'), issue.get('transaction_id')
        plain_omission = (
            issue['kind'] == 'source_amount' and
            issue.get('problem') == 'unposted_source' and
            issue.get('booked') == 0 and issue.get('source_amount', 0) > 0 and
            key not in selected_sources and key not in posted_sources)
        if not plain_omission:
            blocking.append(issue)
    return blocking


def reviewed_selection(snapshot, proposals, review):
    """Bind a separate review to the exact snapshot, drafts and selected source IDs."""
    snapshot_hash = snapshot['snapshot_sha256']
    if digest({key:value for key,value in snapshot.items() if key!='snapshot_sha256'}) != snapshot_hash:
        raise ValueError('Snapshot fingerprint mismatch')
    if review.get('snapshot_sha256') != snapshot_hash:
        raise ValueError('Review belongs to another snapshot')
    if not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip():
        raise ValueError('Reviewer identity is required')
    if not isinstance(review.get('authorization'),str) or not review['authorization'].strip():
        raise ValueError('User authorization reference is required')
    groups = {group['id']:group for group in snapshot['groups']}
    sources = {row['id']:row for row in snapshot['rows']}
    decisions = {row['group_id']:row for row in proposals}
    if len(sources)!=len(snapshot['rows']) or len(groups)!=len(snapshot['groups']) or len(decisions)!=len(proposals):
        raise ValueError('Duplicate snapshot or proposal identities')
    selected = {};approved_groups = set()
    for approval in review.get('approvals',[]):
        group_id = approval['group_id']
        if group_id in approved_groups or group_id not in groups or group_id not in decisions:
            raise ValueError('Invalid or duplicate approved group')
        approved_groups.add(group_id)
        proposal = decisions[group_id]
        proposal_hash = digest({key:value for key,value in proposal.items() if key!='proposal_sha256'})
        if (proposal.get('snapshot_sha256')!=snapshot_hash or proposal_hash!=proposal.get('proposal_sha256')
                or approval.get('proposal_sha256')!=proposal_hash):
            raise ValueError('Proposal changed after review')
        draft = proposal.get('draft') or {}
        if draft.get('decision')!='propose_mapping' or proposal.get('validation_failures'):
            raise ValueError('Only valid proposed drafts may be approved')
        category = draft.get('category')
        if not category or approval.get('category') != category:
            raise ValueError('Approved category differs from the reviewed model draft')
        if not isinstance(approval.get('reason'),str) or not approval['reason'].strip():
            raise ValueError('A review reason is required')
        keyword = draft.get('keyword')
        exact = snapshot.get('mapping_match_mode') == 'exact'
        if not exact and (not isinstance(keyword,str) or len(keyword.strip())<3 or len(keyword)>100
                or any(c in keyword for c in '%_\\')):
            raise ValueError('Unsafe draft keyword')
        pattern = None if exact else '.*'.join(re.escape(term) for term in keyword.lower().split())
        identifiers = approval.get('source_ids',groups[group_id]['source_ids'])
        if not identifiers or len(set(identifiers))!=len(identifiers):
            raise ValueError('Empty or duplicate approved source list')
        if not set(identifiers)<=set(groups[group_id]['source_ids']):
            raise ValueError('Approval expands beyond the reviewed group')
        for identifier in identifiers:
            if identifier in selected or identifier not in sources:
                raise ValueError('Overlapping or unknown approved source')
            row = sources[identifier]
            if not exact and re.search(pattern,row['description'].lower(),re.S) is None:
                raise ValueError('Keyword does not cover an approved source')
            amount = Decimal(row['amount'])
            if row['currency']!='USD':
                raise ValueError('Only reviewed USD sources may be posted')
            decimal_amount(abs(amount),'USD')
            if category=='Salary' and amount<0:
                raise ValueError('Outgoing payroll needs separate reversal or expense evidence')
            if category in ('Amazon Payable','Amazon Suspense Account','Discretionary Costs','Revenues'):
                raise ValueError('Unsupported settlement or catch-all category')
            if category in SUSPENSE_ACCOUNTS and not snapshot.get('suspense_authorized'):
                raise ValueError('Suspense posting requires an explicit owner policy')
            selected[identifier] = {'source':row,'category':category,'group_id':group_id,
                                    'proposal_sha256':proposal_hash,'review_reason':approval['reason']}
    if not selected:
        raise ValueError('No reviewed sources selected')
    return sorted(selected.values(),key=lambda item:(item['source']['date'],item['source']['id']))


def post_reviewed_batch(snapshot, proposals, review, *, commit=False, bulk=False, progress=lambda *args:None):
    """Validate the live sources, then atomically post or preview the selected IDs.

    The user authorizes this local command, and a separate review identifies
    supported drafts. Existing matching entries are skipped; conflicting entries
    cause rollback. The function never applies a keyword to additional sources.
    """
    selected = reviewed_selection(snapshot,proposals,review)
    batch_hash = digest(review)
    try:
        db.session.execute(text("SET LOCAL lock_timeout = '10s'"))
        db.session.execute(text("SET LOCAL statement_timeout = '120s'"))
        # Import, close, and posting services acquire the shared ledger gate
        # before table locks so no service holds a source lock while waiting here.
        db.session.execute(text("SELECT pg_advisory_xact_lock(hashtext('pacioli.trial_balances'))"))
        db.session.execute(text('LOCK TABLE bookkeeping.journal_entries IN SHARE ROW EXCLUSIVE MODE'))
        db.session.execute(text('''LOCK TABLE ofx.stmttrn,ofx.acctfrom,
            bookkeeping.bank_feed_accounts,bookkeeping.subaccounts,bookkeeping.accounts,
            bookkeeping.classifications,bookkeeping.elements IN SHARE MODE'''))
        db.session.execute(text('LOCK TABLE admin.mappings IN SHARE ROW EXCLUSIVE MODE'))
        live_chart = [dict(row._mapping) for row in db.session.execute(text(CHART_SQL))]
        chart = {row['name']:row for row in live_chart}
        if any(chart.get(row['name']) != row for row in snapshot['chart']):
            raise ValueError('Chart changed after classification; review again')
        ids = [item['source']['id'] for item in selected]
        live_rows = list(db.session.execute(text(SOURCE_SQL),{'ids':ids}))
        live_sources = {row.id:json.loads(json.dumps(dict(row._mapping),default=str)) for row in live_rows}
        if len(live_sources)!=len(ids) or len(live_rows)!=len(ids):
            raise ValueError('An approved source was removed or its identity became ambiguous')
        latest_closed = db.session.execute(text('SELECT max(period) FROM bookkeeping.closed_periods')).scalar()
        existing = defaultdict_entries(db.session.query(JournalEntries).filter(
            JournalEntries.transaction_source=='ofx',JournalEntries.transaction_id.in_(ids)).all())
        planned = [];skipped = [];existing_links = []
        for item in selected:
            row = item['source'];category = item['category'];identifier = row['id']
            if digest(live_sources[identifier])!=digest(row):
                raise ValueError('An approved source changed after classification')
            if category not in chart or (chart[category]['element']!='Expenses'
                                        and category!='Salary' and category not in SUSPENSE_ACCOUNTS):
                raise ValueError('Only reviewed expense and salary classifications are supported')
            if category in SUSPENSE_ACCOUNTS and chart[category]['element'] != 'Assets':
                raise ValueError('Suspense must remain a balance-sheet clearing account')
            if category=='Salary' and chart[category]['element']!='Revenues':
                raise ValueError('Salary must be an income account')
            account = row['account']
            if account not in chart or chart[account]['element'] not in ('Assets','Liabilities'):
                raise ValueError('The source account has no valid ledger classification')
            if category==account:
                raise ValueError('Self-offsetting entry refused')
            amount = Decimal(row['amount'])
            when = datetime.fromisoformat(row['date'])
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            when = when.astimezone(timezone.utc)
            debit,credit = (account,category) if amount>0 else (category,account)
            desired = (debit,credit,abs(amount),when,'USD',abs(amount),'USD')
            found = existing.get(identifier,[])
            if found:
                if len(found)!=1:
                    raise ValueError('Approved source already has split postings')
                entry = found[0]
                actual = (entry.debit_subaccount,entry.credit_subaccount,entry.functional_amount,
                          entry.timestamp,entry.functional_currency,entry.source_amount,entry.source_currency)
                if actual!=desired or entry.allocation_key!='0':
                    raise ValueError('Approved source already has a different posting')
                if entry.mapping_id is None and latest_closed is not None and when.strftime('%Y-%m')<=latest_closed:
                    raise ValueError('Cannot link a mapping in a closed period')
                existing_links.append((item,entry))
                skipped.append(entry.id);continue
            if latest_closed is not None and when.strftime('%Y-%m')<=latest_closed:
                raise ValueError('An approved source falls in a closed period')
            planned.append((item,when,debit,credit,abs(amount)))
        summary = {'batch_sha256':batch_hash,'selected':len(selected),'already_posted':len(skipped),
                   'would_post':len(planned),'posted':0,
                   'gross_amount':str(sum((amount for _,_,_,_,amount in planned),Decimal(0)))}
        # Validate rules even in preview. A rule conflict must not appear only
        # after the operator has reviewed a successful journal preview.
        rules = {}; created_mappings = 0
        for item in selected:
            mapping, was_created = persist_exact_mapping(item, snapshot, batch_hash)
            created_mappings += int(was_created)
            rules[item['source']['id']] = mapping
        for item, entry in existing_links:
            if entry.mapping_id is not None and entry.mapping_id != rules[item['source']['id']].id:
                raise ValueError('Existing journal has a different mapping link')
        summary.update(mappings_in_batch=len({mapping.id for mapping in rules.values()}),
                       new_mappings=created_mappings,
                       would_link_existing=sum(entry.mapping_id is None for _,entry in existing_links))
        if not commit:
            db.session.rollback();return summary
        db.session.execute(text("SELECT set_config('pacioli.actor',:actor,true)"),
                           {'actor':review['reviewer'][:240]})
        db.session.execute(text("SELECT set_config('pacioli.reason',:reason,true)"),
                           {'reason':'Authorized reviewed local mapping batch '+batch_hash})
        bulk = bulk and bool(planned or summary['would_link_existing'])
        if bulk:
            # Only the derived-balance trigger is deferred. Audit, closed-period,
            # currency and source constraints stay active. Transaction rollback
            # restores trigger state as well as journals, mappings and balances.
            state = db.session.execute(text("SELECT tgenabled FROM pg_trigger WHERE tgrelid="
                "'bookkeeping.journal_entries'::regclass AND tgname='subaccount_insert_trigger'")).scalar()
            if state != 'O':
                raise ValueError('Trial-balance trigger must be enabled before bulk posting')
            db.session.execute(text('ALTER TABLE bookkeeping.journal_entries DISABLE TRIGGER subaccount_insert_trigger'))
        linked = 0
        for item, entry in existing_links:
            if entry.mapping_id is None:
                entry.mapping_id = rules[item['source']['id']].id
                linked += 1
        db.session.flush()
        created = []
        for index,(item,when,debit,credit,amount) in enumerate(planned,1):
            entry = post_pair(timestamp=when,debit=debit,credit=credit,functional_amount=amount,
                transaction_source='ofx',transaction_id=item['source']['id'],
                mapping_id=rules[item['source']['id']].id,
                memo='Local mapping batch {}; proposal {}; {}'.format(
                    batch_hash,item['proposal_sha256'],item['review_reason']))
            db.session.flush()
            created.append(entry.id)
            if index==1 or index%25==0 or index==len(planned):
                progress(index,len(planned))
        if bulk:
            from pacioli.functions.mapping_workflow import rebuild_trial_balances
            rebuild_trial_balances()
            db.session.execute(text('ALTER TABLE bookkeeping.journal_entries ENABLE TRIGGER subaccount_insert_trigger'))
        # Preserve every finding in the result. Only an unrelated wholly
        # unposted backlog can be deferred by a bounded classification review.
        issues = audit_ledger(db.session)
        blocking = blocking_batch_issues(db.session, issues, {('ofx', identifier) for identifier in ids})
        if blocking:
            raise ValueError('Ledger reconciliation failed; entire batch rolled back')
        summary.update(posted=len(created),journal_ids=created,linked_existing=linked,
                       audit_issue_counts=dict(Counter(issue['kind'] for issue in issues)))
        db.session.commit()
        return summary
    except Exception:
        db.session.rollback()
        raise


def defaultdict_entries(entries):
    result = {}
    for entry in entries:
        result.setdefault(entry.transaction_id,[]).append(entry)
    return result
