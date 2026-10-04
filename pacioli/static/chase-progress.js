/* Durable event cursors + SSE, with non-overlapping polling during reconnects. */
(() => {
  'use strict';
  const config = JSON.parse(document.getElementById('local-progress-config').textContent);
  const el = id => document.getElementById('local-' + id);
  const setText = (id, text) => { if (el(id).textContent !== text) el(id).textContent = text; };
  let run = config.initial, runId = 0, cursor = 0, history = [], seen = new Set();
  const balances = new Map((config.balances || []).map(item => [item.account_id, item]));
  let source = null, pollTimer = null, polling = false, submitting = false, stopped = false;
  let streamHealthy = false, retryAt = 0, lastContact = 0, pageEnd = null;
  let stepStarted = null, stepGroup = '', generation = 0;
  const actionLabels = {open_account:'Open account', open_activity:'Open account activity',
    open_export:'Open export form', reduce_date_range:'Reduce date range',
    select_account:'Select account', select_qfx_format:'Select QFX format',
    set_date_range:'Set dates', choose_option:'Select option', download_qfx:'Request QFX download', wait:'Wait'};
  const detailLabels = {duration_ms:'Duration', attempt:'Attempt', bytes:'File bytes', chunks:'Response chunks',
    characters:'Response characters', transactions:'Transactions in file', new_transactions:'New transactions',
    existing_transactions:'Transactions already present', intervals:'Intervals',
    prompt_tokens:'Input tokens', output_tokens:'Output tokens', load_ms:'Model load time'};
  function details(event) {
    const d = event.details, parts = [];
    if (d.last4) parts.push('Account …' + d.last4);
    if (d.start) parts.push(d.start + ' – ' + d.end);
    if (d.action) parts.push(actionLabels[d.action] || d.action);
    if (d.page) parts.push('Page: ' + d.page.replaceAll('_', ' '));
    for (const [key, label] of Object.entries(detailLabels)) {
      if (d[key] !== undefined) parts.push(label + ': ' + (key.endsWith('_ms') ? (d[key] / 1000).toFixed(2) + 's' : d[key]));
    }
    if (d.duplicate !== undefined) parts.push(d.duplicate ? 'File already imported' : 'New source file');
    return parts.join(' · ');
  }
  function timeline() {
    const end = pageEnd === null ? history.length : Math.min(pageEnd, history.length);
    const start = Math.max(0, end - 200), list = el('events');
    const previousScroll = list.scrollTop;
    const items = history.slice(start, end).map(event => {
      const li = document.createElement('li');
      li.dataset.eventId = event.id;
      li.style.marginBottom = '.6rem';
      const stamp = document.createElement('time');
      stamp.dateTime = event.at;
      stamp.textContent = new Date(event.at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit',
        second:'2-digit', hour12:false, fractionalSecondDigits:3});
      const text = document.createElement('span');
      text.textContent = ' ' + event.message;
      const extra = document.createElement('small');
      extra.style.display = 'block'; extra.textContent = details(event);
      li.append(stamp, text, extra);
      return li;
    });
    list.replaceChildren(...items);
    list.start = start + 1;
    if (el('follow').checked && pageEnd === null) list.scrollTop = list.scrollHeight;
    else list.scrollTop = previousScroll;
    el('event-count').textContent = history.length ? `Showing events ${start + 1}–${end} of ${history.length}.` : run?.active ? 'Waiting for the first recorded event.' : 'No activity was recorded for this older run. New steps will be recorded when it resumes.';
    el('older').hidden = start === 0;
    el('newest').hidden = pageEnd === null;
  }
  function renderBalances() {
    for (const item of run?.balances || []) {
      const previous = balances.get(item.account_id);
      if (!previous || (item.balance_imported_at || '') >= (previous.balance_imported_at || '')) balances.set(item.account_id, item);
    }
    const day = value => value ? value.slice(0, 10) : '—';
    const rows = [...balances.values()].map(item => {
      const row = document.createElement('tr');
      let review = item.opening_blocker || 'Candidate only; reconcile to a statement before posting.';
      if (!item.balance_is_current) review += ' Bank balance predates today.';
      for (const gap of item.coverage_gaps || []) review += ` Missing ${gap.start} through ${gap.end}.`;
      for (const conflict of item.balance_disagreements || []) review += ` ${conflict.asof}: opening differs by ${conflict.difference} USD.`;
      if (item.reviewed_balance_cutoffs?.length) review += ` ${item.reviewed_balance_cutoffs.length} historical snapshot cutoffs have validated source reviews in Accounting reconciliation.`;
      if (item.fetched_at) review += ` Fetched ${new Date(item.fetched_at).toLocaleString()}; file balance ${item.fetched_balance} as of ${day(item.fetched_balance_asof)}.`;
      else if (item.balance_imported_at) review += ` Source imported ${new Date(item.balance_imported_at).toLocaleString()}.`;
      for (const value of [item.account_name, item.balance ?? '—', day(item.balance_asof),
        `${day(item.coverage_start)} – ${day(item.coverage_end)}`, item.signed_activity ?? '—',
        item.opening_candidate === null ? 'Blocked' : `${item.opening_candidate} before ${day(item.opening_date)}`, review]) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      return row;
    });
    el('balances').replaceChildren(...rows);
  }
  function render() {
    renderBalances();
    el('run').hidden = !run && !submitting;
    document.querySelectorAll('.local-launch').forEach(b => b.disabled = submitting || !config.hasAccounts || !!run?.active || !el('poll-error').hidden);
    el('update-button').textContent = submitting ? 'Sending request…' : run?.active ? 'Updating Chase…' : 'Update Chase';
    if (!run) return;
    setText('message', run.cancel_requested && run.active ? 'Cancellation requested; waiting for the current operation to return.' : run.message + (run.error_code ? ' (' + run.error_code + ')' : ''));
    el('counts').textContent = run.mode === 'local_preview' ? 'Page check only; no downloads or imports.' : `${run.completed} interval(s) complete; ${run.remaining} remaining. New transactions recorded: ${run.new_transactions || 0}; already present: ${run.existing_transactions || 0}. New files: ${run.imported}; files already present: ${run.duplicates}.`;
    el('current').textContent = run.current ? `Account ending ${run.current.last4}: ${run.current.start} through ${run.current.end}` : '';
    el('resume').hidden = !run.resumable;
    el('cancel').hidden = !run.active;
    el('cancel').querySelector('button').disabled = !!run.cancel_requested || submitting;
    el('resume').querySelector('button').disabled = submitting;
    document.querySelectorAll('.local-run-id').forEach(input => input.value = run.id);
    el('progress').max = Math.max(1, run.completed + run.remaining);
    el('progress').value = run.completed;
    const rows = (run.intervals || []).map((interval, index) => {
      const row = document.createElement('tr');
      const status = interval.status === 'completed' ? (interval.duplicate ? 'Complete · file already imported' : 'Complete') : index === run.completed ? (run.active ? 'In progress' : 'Waiting to resume') : 'Pending';
      for (const value of [interval.last4, interval.start + ' – ' + interval.end, status]) {
        const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
      }
      return row;
    });
    el('intervals').replaceChildren(...rows);
    const latest = run.last_event || history.at(-1);
    setText('step', latest ? latest.message + (details(latest) ? ' ' + details(latest) : '') : '');
    tick();
  }
  function accept(packet) {
    // A slower fallback request must not rewind a newer stream or POST reply.
    if (packet.run && runId && (packet.run.id < runId ||
        (packet.run.id === runId && (packet.cursor < cursor ||
         Date.parse(packet.run.updated_at) < Date.parse(run.updated_at))))) return;
    if (packet.run?.id !== runId) {
      runId = packet.run?.id || 0; cursor = 0; history = []; seen.clear(); pageEnd = null;
      stepStarted = null; stepGroup = '';
    }
    run = packet.run;
    let added = false;
    for (const event of packet.events || []) {
      if (seen.has(event.id)) continue;
      seen.add(event.id); history.push(event); added = true;
      const group = /^(model_request|model_output|model_response)$/.test(event.kind) ? 'model' : event.kind === 'download_wait' ? 'download' : event.kind;
      if (group !== stepGroup) { stepStarted = event.at; stepGroup = group; }
    }
    cursor = packet.cursor;
    lastContact = Date.now(); el('poll-error').hidden = true;
    if (added || !history.length) timeline();
    render();
  }
  function duration(ms) {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
  }
  function tick() {
    if (!run) return;
    const end = run.finished_at ? Date.parse(run.finished_at) : Date.now();
    const heard = Math.max(0, Date.now() - Date.parse(run.updated_at));
    el('timing').textContent = `Run #${run.id} · ${duration(end - Date.parse(run.created_at))} since first queued` +
      (run.active && stepStarted ? ` · Current step ${duration(Date.now() - Date.parse(stepStarted))}` : '') +
      (run.active ? ` · Worker last checked in ${duration(heard)} ago` : '');
    if (submitting) setText('live', 'Sending your request…');
    else if (lastContact && Date.now() - lastContact > 15000) setText('live', 'Progress connection interrupted; reconnecting. The background update may still be running.');
    else if (run.active) setText('live', (streamHealthy ? 'Live updates connected' : 'Receiving updates by polling; reconnecting live stream') + (heard > 15000 ? ' · Waiting for the current worker operation to respond.' : ''));
    else setText('live', 'Activity saved · ' + run.status.replaceAll('_', ' '));
  }
  function closeStream() {
    if (source) source.close(); source = null; streamHealthy = false;
  }
  function schedule(delay) {
    clearTimeout(pollTimer);
    if (!stopped) pollTimer = setTimeout(poll, delay);
  }
  function connect() {
    if (source || stopped || !run?.active || !window.EventSource || Date.now() < retryAt) return;
    const stream = new EventSource(config.eventsUrl + `?run_id=${runId}&after=${cursor}`);
    source = stream;
    stream.addEventListener('progress', event => {
      if (source !== stream) return;
      try {
        streamHealthy = true; accept(JSON.parse(event.data));
      } catch (_) { closeStream(); retryAt = Date.now() + 5000; schedule(500); }
    });
    stream.addEventListener('idle', () => { if (source === stream) { closeStream(); schedule(3000); } });
    stream.addEventListener('reconnect', () => {
      if (source === stream) { closeStream(); retryAt = 0; connect(); }
    });
    stream.addEventListener('heartbeat', () => { if (source === stream) lastContact = Date.now(); });
    stream.onerror = () => { if (source !== stream) return; closeStream(); retryAt = Date.now() + 3000; schedule(500); };
  }
  async function poll() {
    if (stopped || polling) { schedule(500); return; }
    if (streamHealthy) { schedule(2000); return; }
    polling = true;
    const startedGeneration = generation;
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 8000);
    let delay = 1000;
    try {
      const response = await fetch(config.statusUrl + `?run_id=${runId}&after=${cursor}`,
        {credentials:'same-origin', cache:'no-store', signal:controller.signal});
      if (!response.ok) throw new Error();
      const packet = await response.json();
      if (startedGeneration !== generation) return;
      accept(packet);
      delay = packet.has_more ? 0 : run?.active ? 500 : 3000;
      if (!packet.has_more) connect();
    } catch (_) {
      if (startedGeneration === generation) {
        el('poll-error').hidden = false;
        document.querySelectorAll('.local-launch').forEach(b => b.disabled = true);
      }
      delay = 2000;
    } finally { clearTimeout(timeout); polling = false; schedule(delay); }
  }
  for (const id of ['update', 'start', 'resume', 'cancel']) {
    el(id).addEventListener('submit', async event => {
      event.preventDefault();
      if (submitting) return;
      const data = new FormData(event.target);
      if (event.submitter?.name) data.set(event.submitter.name, event.submitter.value);
      submitting = true; generation++; closeStream(); clearTimeout(pollTimer);
      el('run').hidden = false; el('poll-error').hidden = true;
      el('request-error').hidden = true;
      render(); el('live').textContent = 'Sending your request…';
      // Observe a committed queue/worker even while systemd launch is replying.
      schedule(0);
      try {
        const url = new URL(event.target.action || location.href, location.href);
        url.searchParams.set('run_id', runId); url.searchParams.set('after', cursor);
        const response = await fetch(url.href, {
          method:'POST', body:data, credentials:'same-origin', headers:{Accept:'application/json'}});
        if (response.redirected) throw new Error('Your session may have expired. Refresh this page to sign in again.');
        const packet = await response.json();
        if (!response.ok) throw new Error(packet.error || 'The request could not be accepted.');
        accept(packet); retryAt = 0;
      } catch (error) {
        el('request-error').textContent = error.message || 'Could not confirm the request. Reconnecting to check its status.';
        el('request-error').hidden = false;
      } finally { submitting = false; render(); schedule(0); }
    });
  }
  el('older').onclick = () => { pageEnd = Math.max(200, (pageEnd ?? history.length) - 200); el('follow').checked = false; timeline(); };
  el('newest').onclick = () => { pageEnd = null; el('follow').checked = true; timeline(); };
  el('follow').onchange = () => { if (el('follow').checked) pageEnd = null; timeline(); };
  window.addEventListener('pagehide', () => { stopped = true; closeStream(); clearTimeout(pollTimer); });
  window.addEventListener('pageshow', () => { stopped = false; schedule(0); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(0); });
  setInterval(tick, 250);
  render(); poll();
})();
