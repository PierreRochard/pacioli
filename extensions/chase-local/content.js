/* Isolated-world bridge. No page script, arbitrary selector or coordinate execution. */
(() => {
  if (globalThis.pacioliChase?.version === 10) return;
  if (globalThis.pacioliChase?.dispose) globalThis.pacioliChase.dispose();
  else globalThis.pacioliChase?.cleanup();
  const origins = new Set(['https://secure.chase.com', 'https://www.chase.com', 'https://chase.com']);
  let snapshot = null, markers = [], sequence = 0, downloadCapture = null, restoreBlob = null;
  function watchBlobs() {
    restoreBlob?.();
    if (typeof exportFunction !== 'function' || !window.wrappedJSObject) return;
    const pageURL = window.wrappedJSObject.URL;
    const original = pageURL.createObjectURL;
    const wrapper = exportFunction(function(blob) {
      const url = original.call(pageURL, blob);
      if (downloadCapture && Date.now() - downloadCapture.created <= 60000 &&
          typeof url === 'string' && url.startsWith('blob:') && new URL(url).origin === location.origin &&
          downloadCapture.urls.size < 5) downloadCapture.urls.add(url);
      return url;
    }, window);
    pageURL.createObjectURL = wrapper;
    const timer = setTimeout(() => restoreBlob?.(), 60000);
    restoreBlob = () => {
      clearTimeout(timer);
      if (pageURL.createObjectURL === wrapper) pageURL.createObjectURL = original;
      restoreBlob = null;
    };
  }
  function recordDownload(event) {
    if (!downloadCapture || Date.now() - downloadCapture.created > 60000) return;
    const anchor = event.composedPath().find(el => el.tagName === 'A' && el.download);
    if (!anchor || !/\.(qfx|ofx)$/i.test(anchor.download) || !anchor.href.startsWith('blob:')) return;
    try {
      if (new URL(anchor.href).origin === location.origin && downloadCapture.urls.size < 5)
        downloadCapture.urls.add(anchor.href);
    } catch (_) {}
  }
  document.addEventListener('click', recordDownload, true);
  function release() { restoreBlob?.(); downloadCapture = null; cleanup(); return true; }
  function dispose() { release(); document.removeEventListener('click', recordDownload, true); }
  function downloadSource(url) {
    return {matched: !!downloadCapture && Date.now() - downloadCapture.created <= 60000 && downloadCapture.urls.has(url)};
  }
  const text = el => {
    let words = String(el?.getAttribute('aria-label') || el?.innerText || el?.textContent || '').trim().split(/\s+/);
    // Chase renders some option labels twice (visual and accessibility text).
    while (words.length > 1 && words.length % 2 === 0 &&
           words.slice(0, words.length/2).join(' ') === words.slice(words.length/2).join(' '))
      words = words.slice(0, words.length/2);
    return words.join(' ').slice(0, 180);
  };
  const accountOverview = () => /^#\/dashboard\/overview(?:$|[/?])/.test(location.hash);
  const accountNumbers = value => value.match(/(?<!\d)\d{4}(?!\d)/g) || [];
  const isQfx = value => /\bqfx\b|\bquicken\s+web\s+connect\b/i.test(value) &&
    !/\b(csv|qif|qbo|quickbooks)\b/i.test(value);
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none' &&
      (!el.checkVisibility || el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}));
  };
  // Firefox exposes closed component roots to its isolated extension world.
  const componentRoot = el => el.shadowRoot || el.openOrClosedShadowRoot;
  function all(selector, root = document) {
    const found = [...root.querySelectorAll(selector)];
    for (const el of root.querySelectorAll('*')) if (componentRoot(el)) found.push(...all(selector, componentRoot(el)));
    return found;
  }
  function cleanup() { markers.forEach(el => el.remove()); markers = []; }
  function shadowHosts(el) {
    const result = [];
    for (let host = el.getRootNode().host; host; host = host.getRootNode().host)
      result.push(host.tagName.toLowerCase());
    return result;
  }
  function receivesPointer(el, x, y) {
    let hit = document.elementFromPoint(x, y);
    for (let depth = 0; hit && componentRoot(hit) && depth < 20; depth++) {
      const next = componentRoot(hit).elementFromPoint(x, y);
      if (!next || next === hit) break;
      hit = next;
    }
    // Follow composed parents so nested shadow controls are checked all the
    // way down. A sibling overlay inside the same host must still be refused.
    for (let node = hit; node; node = node.parentElement || node.getRootNode().host)
      if (node === el) return true;
    return false;
  }
  function state() {
    if (!origins.has(location.origin)) throw new Error('unapproved_origin');
    const body = document.body.innerText.slice(0, 50000);
    // Never capture a login, verification frame or password-manager surface.
    if (/\/logout\/?$/i.test(location.pathname) ||
        /we['\u2019]ve signed you out|you (?:have been|were) signed out|your session (?:has )?expired/i.test(body) ||
        all('input[type="password"],input[autocomplete="one-time-code"],iframe').some(visible) ||
        /verify your identity|identity verification|approve.{0,30}passkey|enter.{0,20}(verification|security) code/i.test(body))
      return {kind: 'authentication', controls: []};
    // The card form always explains its 1,200-transaction limit. That help
    // text is not a failed export; a limit response also offers a range reset.
    const controls = collect();
    if (controls.some(c => c.kind === 'change_range') &&
        /exceeds?.{0,30}(1,?200|transaction limit)|more than 1,?200|maximum.{0,20}1,?200/i.test(body))
      return {kind: 'limit', controls};
    if (/something went wrong|could not complete your request|try again later|temporarily unavailable/i.test(body))
      return {kind: 'error', controls: []};
    return {kind: /Your downloaded account activity file is ready/i.test(body) && controls.some(c => c.kind === 'open_export') ? 'download_complete' :
      accountOverview() && controls.some(c => c.kind === 'open_account') ? 'accounts' :
      controls.some(c => c.kind === 'download') ? 'form' :
      controls.some(c => ['open_export','open_activity'].includes(c.kind)) ? 'overview' : 'unknown', controls};
  }
  function collect() {
    const candidates = all('button,a,select,input,mds-select,[role="button"],[role="link"],[role="combobox"],[role="option"],mds-select-option');
    const result = [], seen = new Set();
    for (const el of candidates) {
      if (!visible(el) || seen.has(el)) continue;
      // Custom selects contain implementation radio inputs; act on the option.
      if (el.tagName === 'INPUT' && !['text','date'].includes(el.type)) continue;
      const label = text(el), identifier = (el.id + ' ' + (el.name || '')).toLowerCase();
      const accountTitle = String(el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 180);
      const labelled = el.labels ? [...el.labels].map(text).join(' ') : '';
      const name = `${label} ${labelled} ${identifier}`;
      let kind = null;
      if (el.matches('[role="option"],mds-select-option')) kind = 'option';
      else if (/downloadActivityAccounts|select-account_options|account.*selector/i.test(name)) kind = 'account';
      else if (accountOverview() && el.matches('a,button') &&
               /\((?:\.{3,}|\u2026|[\u2022*]{2,})\s*\d{4}\)/.test(accountTitle)) kind = 'open_account';
      else if (el.tagName === 'A' && el.id === 'heading' && /^See all transactions(?:\s|$)/i.test(label)) kind = 'open_activity';
      // Chase's enabled activity icon retains a misleading no-activity aria
      // label. Identify only this known button on the account summary route;
      // opening the form still cannot authorize downloading any data.
      else if (el.tagName === 'BUTTON' && el.id === 'quick-action-download-activity-tooltip' &&
               /^#\/dashboard\/summary\//.test(location.hash)) kind = 'open_export';
      else if (/^Download (?:account|other) activity$/i.test(label)) kind = 'open_export';
      else if (el.id === 'downloadButton' || /^(download|download qfx|export qfx)$/i.test(label)) kind = 'download';
      else if (/^(change date range|edit date range)$/i.test(label)) kind = 'change_range';
      else if (/showing-file|file.?type|file.?format/i.test(name)) kind = 'format';
      else if (/showing-activity|date.?range|activity.?period/i.test(name)) kind = 'range';
      else if (el.tagName === 'INPUT' && /(^|\W)(from|start)(\W|date)/i.test(name)) kind = 'from';
      else if (el.tagName === 'INPUT' && /(^|\W)(to|end)(\W|date)/i.test(name)) kind = 'to';
      if (!kind) continue;
      if (kind === 'option' && !label) continue;
      // Prefer the actionable button over its custom-element container.
      if (el.matches('mds-select') && (el.querySelector('button') || el.shadowRoot?.querySelector('button'))) continue;
      const r = el.getBoundingClientRect();
      const displayed = kind === 'open_account' ? accountTitle : el.tagName === 'SELECT' ? el.selectedOptions[0]?.textContent.trim() :
        el.tagName === 'INPUT' ? el.value : label;
      const item = {kind, label, value: String(displayed ?? '').slice(0, 180),
        tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
        shadow_hosts: shadowHosts(el),
        enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true',
        box: [r.x, r.y, r.width, r.height].map(x => Math.round(x)), el};
      if (el.tagName === 'SELECT') item.options = [...el.options].map(o => ({label: o.textContent.trim(), value: o.value}));
      result.push(item); seen.add(el);
    }
    if (result.length > 80) throw new Error('too_many_controls');
    return result;
  }
  function fingerprint(s) {
    return JSON.stringify([location.href, innerWidth, innerHeight, scrollX, scrollY, s.kind,
      s.controls.map(({el, ...rest}) => rest)]);
  }
  function observe() {
    cleanup();
    let s = state();
    // Bring the actual form into view before creating any observation IDs.
    const target = s.controls.find(c => c.kind === 'download') || s.controls.find(c => c.kind === 'open_export');
    if (target && (target.box[1] < 0 || target.box[1] + target.box[3] > innerHeight)) {
      target.el.scrollIntoView({block: 'center'}); s = state();
    }
    const token = crypto.randomUUID(), created = Date.now();
    const base = 10 + (++sequence % 1000) * 100;
    s.controls.forEach((c, index) => { c.id = base + index; });
    // IDs are intentionally excluded from the DOM fingerprint.
    const fp = fingerprint({...s, controls: s.controls.map(({id, ...c}) => c)});
    snapshot = {token, created, fp, controls: s.controls, kind: s.kind};
    if (['form', 'overview', 'limit'].includes(s.kind)) for (const c of s.controls) {
      if (c.box[1] < 0 || c.box[1] + c.box[3] > innerHeight) continue;
      const tag = document.createElement('span');
      tag.textContent = String(c.id);
      tag.style.cssText = `position:fixed;left:${Math.max(0,c.box[0])}px;top:${Math.max(0,c.box[1]-14)}px;z-index:2147483647;background:#ffe344;color:#000;border:1px solid #000;font:16px sans-serif;pointer-events:none;padding:1px 3px;`;
      document.documentElement.append(tag); markers.push(tag);
    }
    return {token, kind: s.kind, content_version: 10, width: innerWidth, height: innerHeight,
      controls: s.controls.map(({el, ...c}) => c)};
  }
  function current(token) {
    cleanup();
    if (!snapshot || snapshot.token !== token || Date.now() - snapshot.created > 45000 ||
        fingerprint(state()) !== snapshot.fp) throw new Error('stale_observation');
    return snapshot;
  }
  function unique(s, kind) {
    const found = s.controls.filter(c => c.kind === kind);
    if (found.length !== 1) throw new Error('ambiguous_control');
    return found[0];
  }
  function verifyDownload(s, task) {
    const acct = unique(s, 'account'), fmt = unique(s, 'format');
    if (!/^\d{4}$/.test(task.last4) || JSON.stringify(acct.value.match(/(?<!\d)\d{4}(?!\d)/g)) !== JSON.stringify([task.last4]) ||
        !isQfx(fmt.value)) throw new Error('export_mismatch');
    for (const [kind, value] of [['from', task.start], ['to', task.end]]) {
      const raw = unique(s, kind).value;
      const normalized = raw.match(/^(\d{4})-(\d\d)-(\d\d)$/);
      const actual = normalized ? `${normalized[2]}/${normalized[3]}/${normalized[1]}` : raw;
      if (actual !== value) throw new Error('export_mismatch');
    }
  }
  function act(command) {
    const s = current(command.token);
    const c = s.controls.find(x => x.id === command.target_id);
    if (!c || !c.enabled || !c.el.isConnected || !visible(c.el)) throw new Error('invalid_target');
    const permitted = {open_account: ['open_account'], open_activity: ['open_activity'],
      open_export: ['open_export'], download_qfx: ['download'], select_account: ['account'],
      select_qfx_format: ['format'], set_date_range: ['from','to','range'], reduce_date_range: ['change_range'], choose_option: ['option']};
    if (!permitted[command.action]?.includes(c.kind)) throw new Error('action_target_mismatch');
    if (command.action === 'open_account' && (!accountOverview() ||
        !/^\d{4}$/.test(command.task.last4) ||
        JSON.stringify(accountNumbers(c.value)) !== JSON.stringify([command.task.last4])))
      throw new Error('account_not_allowed');
    if (command.action === 'download_qfx') verifyDownload(s, command.task);
    c.el.scrollIntoView({block: 'nearest', inline: 'nearest'});
    const rect = c.el.getBoundingClientRect();
    if (!receivesPointer(c.el, rect.x + rect.width/2, rect.y + rect.height/2)) throw new Error('control_obscured');
    if (command.action === 'choose_option') {
      const value = command.value;
      if (typeof value !== 'string' || value !== c.label ||
          !(isQfx(value) || /custom|specific date|date range/i.test(value) ||
            (/^\d{4}$/.test(command.task.last4) && value.includes(command.task.last4)))) throw new Error('option_not_allowed');
    }
    if (c.el.tagName === 'SELECT') {
      const option = [...c.el.options].filter(o => command.value === o.value);
      if (option.length !== 1) throw new Error('option_not_allowed');
      if (c.kind === 'format' && !isQfx(option[0].textContent)) throw new Error('option_not_allowed');
      if (c.kind === 'account' && !option[0].textContent.includes(command.task.last4)) throw new Error('option_not_allowed');
      c.el.value = option[0].value;
      c.el.dispatchEvent(new Event('input', {bubbles: true, composed: true})); c.el.dispatchEvent(new Event('change', {bubbles: true}));
    } else if (c.el.tagName === 'INPUT') {
      if (!['from','to'].includes(c.kind) || !/^\d\d\/\d\d\/\d{4}$/.test(command.value) ||
          command.value !== command.task[c.kind === 'from' ? 'start' : 'end']) throw new Error('date_not_allowed');
      const v = c.el.type === 'date' ? command.value.slice(6)+'-'+command.value.slice(0,2)+'-'+command.value.slice(3,5) : command.value;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(c.el, v);
      c.el.dispatchEvent(new Event('input', {bubbles: true, composed: true})); c.el.dispatchEvent(new Event('change', {bubbles: true}));
    } else {
      if (command.action === 'download_qfx') {
        downloadCapture = {created: Date.now(), urls: new Set()}; watchBlobs();
      }
      c.el.click();
    }
    snapshot = null;
    return {acted: true};
  }
  globalThis.pacioliChase = {version: 10, observe, act, cleanup, release, dispose, downloadSource,
    check: token => { current(token); return true; }};
})();
