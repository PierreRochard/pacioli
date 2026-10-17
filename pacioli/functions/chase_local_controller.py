"""Deterministic export policy around local vision proposals."""
from datetime import date, datetime, timedelta
import re
import time

from pacioli.functions import ofx_document
from pacioli.functions.chase_local_bridge import LocalChaseError, read_qfx_download
from pacioli.functions.ofx_file_import import _utc, import_chase_qfx, public_account_summary


def split_interval(start, end):
    left, right = date.fromisoformat(start), date.fromisoformat(end)
    if left >= right:
        raise LocalChaseError('minimum_interval_exceeded')
    middle = left + (right-left)//2
    return [(left.isoformat(), middle.isoformat()),
            ((middle+timedelta(days=1)).isoformat(), right.isoformat())]


def task_for(job):
    return {'last4': job['last4'],
            'start': date.fromisoformat(job['start']).strftime('%m/%d/%Y'),
            'end': date.fromisoformat(job['end']).strftime('%m/%d/%Y')}


def controls_of(observation, kind):
    return [c for c in observation['controls'] if c['kind'] == kind]


def single(observation, kind):
    controls = controls_of(observation, kind)
    if len(controls) != 1:
        raise LocalChaseError('export_controls_unrecognized')
    return controls[0]


def date_value(value):
    try:
        return date.fromisoformat(value).strftime('%m/%d/%Y')
    except ValueError:
        return value


def is_qfx_format(value):
    return bool(re.search(r'\bqfx\b|\bquicken\s+web\s+connect\b', value, re.I)
                and not re.search(r'\b(csv|qif|qbo|quickbooks)\b', value, re.I))


def expected_step(observation, task):
    """Model agreement is required, but model output cannot grant capabilities."""
    if observation['kind'] != 'form':
        raise LocalChaseError('export_controls_unrecognized')
    download = single(observation, 'download')
    if not download['enabled']:
        return 'wait', None
    account = single(observation, 'account')
    numbers = re.findall(r'(?<!\d)\d{4}(?!\d)', account['value'])
    if numbers != [task['last4']]:
        return 'select_account', account['id']
    fmt = single(observation, 'format')
    if not is_qfx_format(fmt['value']):
        return 'select_qfx_format', fmt['id']
    if date_value(single(observation, 'from')['value']) != task['start'] or date_value(single(observation, 'to')['value']) != task['end']:
        return 'set_date_range', single(observation, 'from')['id']
    return 'download_qfx', download['id']


def verify_qfx(content, job, *, verified_export=False):
    """Validate source identity and explicit coverage before any importer writes."""
    try:
        document = ofx_document.parse(content)
    except ofx_document.OFXError:
        raise LocalChaseError('qfx_invalid') from None
    statements = [response.statement for response in document.responses if response.statement]
    if len(statements) != 1:
        raise LocalChaseError('qfx_identity_mismatch')
    statement = statements[0]
    account = statement.account.acctid if statement.account else None
    if account != job['account_number'] or statement.currency != 'USD':
        raise LocalChaseError('qfx_identity_mismatch')
    tranlist = statement.transactions
    begin, end = (tranlist.start, tranlist.end) if tranlist else (None, None)
    left, right = date.fromisoformat(job['start']), date.fromisoformat(job['end'])
    posted = [transaction.dtposted for transaction in (tranlist.transactions if tranlist else ())]
    if any(value is None for value in posted):
        raise LocalChaseError('qfx_coverage_unverified')
    dates = [_utc(value).date() for value in posted]
    if not begin or not end or any(value < left or value > right for value in dates):
        raise LocalChaseError('qfx_coverage_unverified')
    begin, end = _utc(begin).date(), _utc(end).date()
    if begin > end:
        raise LocalChaseError('qfx_coverage_unverified')
    if begin <= left <= right <= end:
        return
    # An empty successful export can declare a narrower, valid interval inside
    # the requested dates. Only the verified form + success response establishes
    # the wider no-activity interval; an unverified empty file cannot do so.
    if verified_export and not dates and left <= begin <= end <= right:
        return
    # Chase's browser export can declare only the first/last posting dates.
    # Accept that representation only after the verified request succeeded;
    # store the request bounds separately instead of rewriting the source dates.
    if not (verified_export and dates and begin == min(dates) and end == max(dates)):
        raise LocalChaseError('qfx_coverage_unverified')


class Collector:
    def __init__(self, bridge, model, checkpoint, save, heartbeat, progress, *, preview=False,
                 sleeper=time.sleep, report=lambda *args, **kwargs: None):
        self.bridge, self.model, self.state = bridge, model, checkpoint
        self.save, self.heartbeat, self.progress = save, heartbeat, progress
        self.preview, self.sleep = preview, sleeper
        self.report = report

    def observe(self):
        # Chase replaces its components asynchronously after navigation. Wait
        # for recognized controls without clicking or asking the model to guess.
        for attempt in range(11):
            self.heartbeat()
            self.report('page_read', attempt=attempt+1)
            started = time.monotonic()
            try:
                observation = self.bridge.command('observe')
            except LocalChaseError as error:
                if error.code != 'stale_observation' or attempt == 10:
                    raise
                self.report('page_retry', attempt=attempt+1)
                self.sleep(1)
                continue
            self.report('page_ready', page=observation.get('kind'),
                        duration_ms=int((time.monotonic()-started)*1000))
            if observation.get('kind') != 'unknown' or attempt == 10:
                break
            self.report('page_retry', attempt=attempt+1)
            self.sleep(1)
        if observation.get('kind') == 'authentication':
            raise LocalChaseError('authentication_required')
        if observation.get('kind') == 'error':
            raise LocalChaseError('bank_export_failed')
        if observation.get('kind') == 'unknown':
            raise LocalChaseError('export_controls_unrecognized')
        if not isinstance(observation.get('controls'), list) or len(observation['controls']) > 80:
            raise LocalChaseError('invalid_bridge_response')
        return observation

    def act(self, observation, action, target, task, value=None):
        self.heartbeat()
        self.report('action_started', action=action)
        started = time.monotonic()
        self.bridge.command('act', token=observation['token'], action=action,
                            target_id=target, task=task, value=value)
        self.report('action_finished', action=action, duration_ms=int((time.monotonic()-started)*1000))
        self.sleep(.5)

    def choose(self, observation, kind, task):
        field = single(observation, kind)
        options = field.get('options')
        def matches(label):
            if kind == 'account':
                return re.findall(r'(?<!\d)\d{4}(?!\d)', label) == [task['last4']]
            return is_qfx_format(label) if kind == 'format' else bool(re.search(r'custom|specific date|date range', label, re.I))
        action = {'account': 'select_account', 'format': 'select_qfx_format', 'range': 'set_date_range'}[kind]
        if options:
            selected = [o for o in options if matches(o['label'])]
            if len(selected) != 1: raise LocalChaseError('export_option_unrecognized')
            self.act(observation, action, field['id'], task, selected[0]['value'])
        else:
            self.act(observation, action, field['id'], task)
            observed = self.observe()
            selected = [c for c in controls_of(observed, 'option') if matches(c['label'])]
            if len(selected) != 1: raise LocalChaseError('export_option_unrecognized')
            self.act(observed, 'choose_option', selected[0]['id'], task, selected[0]['label'])

    def collect(self):
        while self.state['jobs']:
            job = self.state['jobs'][0]; task = task_for(job)
            self.report('interval_started')
            self.progress('observing', job)
            for _ in range(60):
                observed = self.observe()
                if observed['kind'] == 'accounts':
                    if self.preview: raise LocalChaseError('open_export_for_preview')
                    choices = [c for c in controls_of(observed, 'open_account')
                               if re.findall(r'(?<!\d)\d{4}(?!\d)', c['value']) == [task['last4']]]
                    if len(choices) != 1: raise LocalChaseError('export_controls_unrecognized')
                    self.act(observed, 'open_account', choices[0]['id'], task)
                    continue
                if observed['kind'] in ('overview', 'download_complete'):
                    if self.preview: raise LocalChaseError('open_export_for_preview')
                    action = 'open_export' if controls_of(observed, 'open_export') else 'open_activity'
                    self.act(observed, action, single(observed, action)['id'], task)
                    continue
                if observed['kind'] == 'limit':
                    if self.preview: return {'action': 'reduce_date_range'}
                    parts = split_interval(job['start'], job['end'])
                    if len(self.state['jobs']) + len(self.state['done']) >= 512:
                        raise LocalChaseError('too_many_intervals')
                    # Clear the error first; a crash cannot lose the original interval.
                    self.act(observed, 'reduce_date_range', single(observed, 'change_range')['id'], task)
                    self.state['jobs'][:1] = [dict(job, start=a, end=b) for a, b in parts]
                    self.save(self.state)
                    self.report('interval_split', start=job['start'], end=job['end'], intervals=len(parts))
                    break
                if not controls_of(observed, 'from') or not controls_of(observed, 'to'):
                    if self.preview: raise LocalChaseError('select_custom_dates')
                    self.choose(observed, 'range', task)
                    continue
                expected, target = expected_step(observed, task)
                self.progress('thinking', job)
                answer = self.model.propose(observed, task)
                self.heartbeat()
                if (answer['action'], answer['target_id']) != (expected, target):
                    raise LocalChaseError('model_needs_review')
                self.report('model_agreed', action=expected)
                if self.preview:
                    return {'action': expected, 'target_id': target}
                self.progress(expected, job)
                if expected == 'wait':
                    self.sleep(1); continue
                if expected in ('select_account', 'select_qfx_format'):
                    self.choose(observed, 'account' if expected == 'select_account' else 'format', task)
                    continue
                if expected == 'set_date_range':
                    for kind, value in [('from', task['start']), ('to', task['end'])]:
                        self.report('date_from' if kind == 'from' else 'date_to')
                        fresh = self.observe()
                        self.act(fresh, expected, single(fresh, kind)['id'], task, value)
                    continue
                self.act(observed, 'download_qfx', target, task)
                download_started = time.monotonic()
                for attempt in range(60):
                    self.heartbeat()
                    self.report('download_wait', attempt=attempt+1,
                                duration_ms=int((time.monotonic()-download_started)*1000))
                    downloaded = self.bridge.command('download')
                    if downloaded['state'] == 'complete': break
                    # A bank limit error is returned after submitting the request.
                    page = self.bridge.command('observe')
                    if page.get('kind') == 'limit':
                        self.bridge.command('release')
                        break
                    if page.get('kind') == 'authentication':
                        raise LocalChaseError('authentication_required')
                    if page.get('kind') == 'error':
                        raise LocalChaseError('bank_export_failed')
                    self.sleep(1)
                else:
                    raise LocalChaseError('download_timeout')
                if downloaded['state'] != 'complete': continue
                self.report('download_received', duration_ms=int((time.monotonic()-download_started)*1000))
                self.report('file_read')
                content = read_qfx_download(downloaded['path'])
                self.report('file_ready', bytes=len(content))
                confirmation = self.observe()
                verified_export = confirmation['kind'] == 'download_complete'
                self.report('qfx_validate')
                verify_qfx(content, job, verified_export=verified_export)
                self.report('qfx_validated')
                self.heartbeat(); self.progress('importing', job)
                result = import_chase_qfx(content, reviewed_usd=True, allowed_account_ids=[job['account_id']],
                    verified_interval=(date.fromisoformat(job['start']), date.fromisoformat(job['end'])) if verified_export else None,
                    progress=self.report)
                self.report('import_committed', duplicate=result['duplicate'],
                            transactions=result['transaction_count'],
                            new_transactions=result['new_transactions'], existing_transactions=result['existing_transactions'])
                summary = public_account_summary(result['summary'])
                # Record what THIS fetch reported too: a stale export must not
                # appear fresh merely because a newer balance is already stored.
                summary.update(fetched_at=datetime.now().astimezone().isoformat(),
                               fetched_balance=result['reported_balance'],
                               fetched_balance_asof=result['reported_balance_asof'])
                self.state.setdefault('balances', {})[str(job['account_id'])] = summary
                self.report('balance_captured')
                self.report('opening_ready' if summary['opening_candidate'] is not None else 'opening_incomplete')
                self.state['done'].append(dict(job, sha256=result['sha256'], duplicate=result['duplicate'],
                                              new_transactions=result['new_transactions'],
                                              existing_transactions=result['existing_transactions']))
                self.state['jobs'].pop(0)
                self.save(self.state)
                self.report('checkpoint_saved', **{key: job[key] for key in ('last4', 'start', 'end')})
                break
            else:
                raise LocalChaseError('step_limit_exceeded')
        return {'completed': True}
