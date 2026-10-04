/* Isolated-world bridge. No page script, arbitrary selector or coordinate execution. */
(() => {
  if (globalThis.pacioliSchwab?.version === 9) return;
  globalThis.pacioliSchwab?.dispose?.();
  const origins = new Set(['https://client.schwab.com', 'https://www.schwab.com']);
  let downloadCapture = null, restoreBlob = null, markers = [];
  // Words a structure probe may reveal. Everything else becomes '*', and any
  // token containing a digit becomes '#': names, tickers, nicknames and
  // amounts never leave the page.
  const VOCABULARY = new Set(`
    a about account accounts action actions activity add all amount an and apply as at available back
    balance balances bank basis brokerage btn button by calendar cancel cash center change checking
    checkbox choose clear close closed collapse combobox confirm container content continue cost csv
    current custom dashboard date dates day days default description details detail dialog document
    documents done download dropdown edit end equity export exports field fields file files filter
    filters for format forms from gain gains go header help hide history home icon ii in include
    individual input investments ira item items joint json label last link list loss losses log logout
    long lot lots main market menu message messages modal month months more move nav navigation new next
    no of off ok on open option options or out overview page pending per period picker portfolio
    positions preferences previous primary print profile quantity radio range realized refresh reports
    research roth save search secondary security select selected service settings short show sort start
    statement statements status submit summary symbol tab tabs tax term the this through time to today
    toggle total trade transactions transfer transfers type unrealized update value view week with xml
    year years ytd`.trim().split(/\s+/));
  // Short all-caps words look like tickers even when they spell a UI word.
  const ACRONYMS = new Set(['CSV', 'JSON', 'XML', 'IRA', 'YTD', 'OK']);
  function mask(value, limit = 80) {
    const words = String(value || '').trim().split(/\s+/).filter(Boolean).map(word => {
      if (/\d/.test(word)) return '#';
      const letters = word.replace(/^[^A-Za-z]+|[^A-Za-z]+$/g, '');
      if (/^[A-Z]{1,5}$/.test(letters) && !ACRONYMS.has(letters)) return '*';
      const bare = word.toLowerCase().replace(/^[^a-z]+|[^a-z]+$/g, '');
      if (!bare) return /^[()\[\]\-\/:.,&|+…]+$/.test(word) ? word : '*';
      return VOCABULARY.has(bare) ? bare : '*';
    });
    return words.join(' ').replace(/(?:\* ?){2,}/g, '* ').trim().slice(0, limit);
  }
  const tokens = value => mask(String(value || '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[-_.:\/]+/g, ' '), 60);
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
    if (!anchor || !/\.(json|csv|xml)$/i.test(anchor.download) || !anchor.href.startsWith('blob:')) return;
    try {
      if (new URL(anchor.href).origin === location.origin && downloadCapture.urls.size < 5)
        downloadCapture.urls.add(anchor.href);
    } catch (_) {}
  }
  document.addEventListener('click', recordDownload, true);
  function cleanup() { markers.forEach(el => el.remove()); markers = []; return true; }
  function release() { restoreBlob?.(); downloadCapture = null; cleanup(); return true; }
  function dispose() { release(); document.removeEventListener('click', recordDownload, true); }
  function downloadSource(url) {
    return {matched: !!downloadCapture && Date.now() - downloadCapture.created <= 60000 && downloadCapture.urls.has(url)};
  }
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
  function shadowHosts(el) {
    const result = [];
    for (let host = el.getRootNode().host; host; host = host.getRootNode().host) result.push(host.tagName.toLowerCase());
    return result;
  }
  function composedClosest(el, selector) {
    for (let node = el; node; node = node.parentElement || node.getRootNode().host)
      if (node.matches?.(selector)) return node;
    return null;
  }
  function authentication() {
    const body = document.body.innerText.slice(0, 50000);
    return all('input[type="password"],input[autocomplete="one-time-code"]').some(visible) ||
      /verify your identity|security code|two-step verification|you (?:have been|were) logged out|session (?:has )?(?:ended|expired)/i.test(body);
  }
  const CONTROLS = 'a,button,select,input,textarea,[role="button"],[role="link"],[role="tab"],[role="menuitem"],' +
    '[role="menuitemradio"],[role="option"],[role="combobox"],[role="radio"],[role="checkbox"],[role="listbox"],' +
    '[aria-haspopup]';
  const DATA = 'table,tr,[role="grid"],[role="table"],[role="row"],[role="gridcell"],[role="cell"]';
  function probeElements() {
    const found = [], seen = new Set();
    for (const el of all(CONTROLS)) {
      if (seen.has(el) || !visible(el)) continue;
      seen.add(el);
      if (el.type === 'password' || el.type === 'hidden' || composedClosest(el, DATA)) continue;
      if (found.length >= 200) break;
      found.push(el);
    }
    return found;
  }
  // Discovery only: open pages and dialogs by an exact masked label from a fixed,
  // non-transactional list. Trade, transfer and settings controls cannot match.
  const DISCOVERY_LABELS = new Set(['export', 'positions', 'realized gain / loss', '* history', 'close', 'cancel']);
  function discoverClick(cmd) {
    if (!origins.has(location.origin) || authentication()) throw new Error('invalid_target');
    if (!DISCOVERY_LABELS.has(cmd.label)) throw new Error('invalid_target');
    const el = probeElements()[cmd.index];
    if (!el || mask(el.getAttribute('aria-label') || el.textContent || '') !== cmd.label || el.disabled)
      throw new Error('invalid_target');
    el.scrollIntoView({block: 'center'});
    el.click();
    return {acted: true};
  }
  function probe() {
    if (!origins.has(location.origin)) throw new Error('unapproved_origin');
    const page = {origin: location.origin, path: tokens(location.pathname), hash: tokens(location.hash),
      title: mask(document.title)};
    if (authentication()) return {...page, kind: 'authentication', controls: []};
    const controls = [], seen = new Set();
    let skipped = 0;
    for (const el of all(CONTROLS)) {
      if (seen.has(el) || !visible(el)) continue;
      seen.add(el);
      if (el.type === 'password' || el.type === 'hidden') continue;
      if (composedClosest(el, DATA)) { skipped++; continue; }
      if (controls.length >= 200) break;
      const r = el.getBoundingClientRect();
      const labelled = el.labels ? [...el.labels].map(l => l.textContent || '').join(' ') : '';
      const control = {tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || null,
        type: el.tagName === 'INPUT' ? el.type : null, id: tokens(el.id), name: tokens(el.getAttribute('name')),
        classes: tokens([...el.classList].slice(0, 4).join(' ')),
        label: mask(el.getAttribute('aria-label') || el.textContent || (el.type !== 'text' && el.value)),
        labelled: mask(labelled), placeholder: mask(el.getAttribute('placeholder')),
        enabled: !el.disabled && el.getAttribute('aria-disabled') !== 'true',
        checked: ['radio', 'checkbox'].includes(el.type) ? !!el.checked : el.getAttribute('aria-checked'),
        expanded: el.getAttribute('aria-expanded'), popup: el.getAttribute('aria-haspopup'),
        in_dialog: !!composedClosest(el, '[role="dialog"],dialog,[aria-modal="true"]'),
        shadow: shadowHosts(el), box: [r.x, r.y, r.width, r.height].map(v => Math.round(v / 10) * 10)};
      if (el.tagName === 'SELECT') control.options = [...el.options].slice(0, 25).map(o => mask(o.text));
      controls.push(control);
    }
    // Styled radio groups hide the native input behind its label: inside open
    // dialogs, report choice inputs and labels even when not visible.
    const dialogChoices = [];
    for (const dialog of all('[role="dialog"],dialog,[aria-modal="true"]').filter(visible))
      for (const el of all('input[type="radio"],input[type="checkbox"],label,legend', dialog)) {
        if (dialogChoices.length >= 60) break;
        dialogChoices.push({tag: el.tagName.toLowerCase(), type: el.type || null, hidden: !visible(el),
          value: el.tagName === 'INPUT' ? mask(el.value) : '', label: mask(el.textContent),
          for: tokens(el.htmlFor || ''), id: tokens(el.id), name: tokens(el.getAttribute('name')),
          checked: el.tagName === 'INPUT' ? !!el.checked : null, shadow: shadowHosts(el)});
      }
    const headings = all('h1,h2,h3,[role="heading"]').filter(visible).slice(0, 20).map(h => mask(h.textContent));
    const customTags = [...new Set(all('*').map(el => el.tagName.toLowerCase()).filter(t => t.includes('-')))].slice(0, 60);
    return {...page, kind: 'structure', headings, custom_elements: customTags, controls, dialog_choices: dialogChoices,
      skipped_data_controls: skipped, dialogs: all('[role="dialog"],dialog,[aria-modal="true"]').filter(visible).length,
      iframes: all('iframe').filter(visible).length, truncated: controls.length >= 200};
  }
  // Recognized controls only. Each kind is tied to Schwab's own ids or a single
  // vocabulary label; nothing is found by coordinates or free-form selectors.
  const labelOf = el => mask(el.getAttribute('aria-label') || el.textContent || '');
  const raw = el => String(el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\s+/g, ' ');
  // Schwab's export dialogs, one component per page.
  const DIALOGS = {history: 'app-export-modal', rgl: 'app-export-overlay', positions: 'app-export-confirmation'};
  const inDialog = el => Object.values(DIALOGS).some(tag => composedClosest(el, tag));
  const inNav = el => shadowHosts(el)[0] === 'responsive-meganav';
  const FORMATS = ['json', 'csv', 'xml'];
  const DATE_RANGES = ['today', 'last # days', 'current month', 'previous month', 'last # months',
    'current year', 'previous year', 'previous calendar year', 'all', 'custom date range'];
  const ACCOUNT_NUMBER = /(?:\.{3}|…|[xX*]{2,})\s?(\d{3,4})(?!\d)/;
  const PAGES = {history: /^(?:transaction )?history$/i, rgl: /^realized gain ?\/ ?loss$/i, positions: /^positions$/i};
  const RECOGNIZERS = {
    nav: el => el.tagName === 'A' && inNav(el) && Object.values(PAGES).some(pattern => pattern.test(raw(el))),
    open_export: el => el.tagName === 'BUTTON' && !inDialog(el) && /export.?button/i.test(el.id) && labelOf(el) === 'export',
    date_range: el => el.tagName === 'SELECT' && !inDialog(el) && /date.?range.?select/i.test(el.id),
    tx_format: el => !!composedClosest(el, DIALOGS.history) && el.tagName === 'INPUT' && el.type === 'radio' &&
      FORMATS.includes(mask(el.value)),
    rgl_details: el => !!composedClosest(el, DIALOGS.rgl) && el.tagName === 'INPUT' && el.type === 'radio' &&
      /details.*radio/i.test(el.id),
    export_submit: el => el.tagName === 'BUTTON' && ((composedClosest(el, DIALOGS.history) || composedClosest(el, DIALOGS.rgl)) &&
      labelOf(el) === 'export' || composedClosest(el, DIALOGS.positions) && labelOf(el) === 'ok'),
    export_cancel: el => el.tagName === 'BUTTON' && inDialog(el) && labelOf(el) === 'cancel',
    account: el => el.tagName === 'BUTTON' && !inDialog(el) && /^account/i.test(el.id) && el.getAttribute('aria-haspopup'),
    // History applies a new date range only when its own Search button is pressed.
    apply_search: el => el.tagName === 'BUTTON' && !inDialog(el) && !inNav(el) && labelOf(el) === 'search' &&
      (/search.?button/i.test(el.id) && !!composedClosest(el, 'app-transaction-history') || !!composedClosest(el, 'app-rgl')),
    // Items of Schwab's account selector menu that name a masked account number.
    // The opened selector lists one link per account (id account-…-header-N-account-M).
    account_option: el => ACCOUNT_NUMBER.test(raw(el)) && !inDialog(el) && !inNav(el) && (
      el.tagName === 'A' && /^account.*header.*account/i.test(el.id) ||
      !!composedClosest(el, 'sdps-account-selector,alto-ng-accountselector') && !/^account/i.test(el.id) &&
      el.matches('li,[role="option"],[role="menuitem"],[role="menuitemradio"],[role="radio"]') &&
      !el.querySelector('button,a,[role="option"]')),
  };
  // A dialog's hidden radios stay in the page after it closes; they count only
  // while its visible submit button is showing.
  const HIDDEN_OK = new Set(['tx_format', 'rgl_details']);
  let snapshot = null;
  function pageKind() {
    for (const [kind, tag] of [['history', 'app-transaction-history'], ['rgl', 'app-rgl'], ['positions', 'app-holdings']])
      if (all(tag).length) return kind;  // Angular removes the previous page's component.
    return null;
  }
  function recognized() {
    const found = [];
    for (const el of all('a,button,select,input,li,[role="option"],[role="menuitem"],[role="menuitemradio"],[role="radio"]')) {
      if (composedClosest(el, DATA)) continue;
      for (const [kind, test] of Object.entries(RECOGNIZERS))
        if (test(el) && (HIDDEN_OK.has(kind) || visible(el))) found.push({el, kind});
    }
    const open = found.some(f => f.kind === 'export_submit');
    return found.filter(f => open || !HIDDEN_OK.has(f.kind));
  }
  function describe(f, i) {
    const control = {id: i, kind: f.kind, label: labelOf(f.el),
      enabled: !f.el.disabled && f.el.getAttribute('aria-disabled') !== 'true'};
    if (f.kind === 'nav') control.value = Object.entries(PAGES).find(([, pattern]) => pattern.test(raw(f.el)))[0];
    if (HIDDEN_OK.has(f.kind)) { control.value = f.kind === 'tx_format' ? mask(f.el.value) : 'details'; control.checked = !!f.el.checked; }
    if (f.kind === 'date_range') {
      control.options = [...f.el.options].map(o => mask(o.text));
      control.value = mask(f.el.selectedOptions[0]?.text || '');
    }
    return control;
  }
  function accountDigits(found) {
    const selectors = found.filter(f => f.kind === 'account');
    if (selectors.length !== 1) return null;
    const match = raw(selectors[0].el).match(ACCOUNT_NUMBER);
    return match ? match[1] : null;
  }
  function observe(task) {
    if (!origins.has(location.origin)) throw new Error('unapproved_origin');
    if (authentication()) return {kind: 'authentication', controls: []};
    const found = recognized();
    snapshot = {token: crypto.randomUUID(), created: Date.now(), elements: found.map(f => f.el)};
    const controls = found.map(describe);
    const page = pageKind();
    const dialog = controls.some(c => c.kind === 'export_submit');
    const digits = accountDigits(found);
    const loading = all('sdps-spinner,[role="progressbar"]').some(visible);
    const empty = all('app-empty-table,sdps-empty-state').some(visible);
    const result = {kind: page ? page + (dialog ? '_export' : '') : 'unknown', token: snapshot.token, controls,
      results: loading ? 'loading' : empty ? 'empty' : 'ready',
      account_match: task?.account_digits && digits ? digits.endsWith(task.account_digits) || task.account_digits.endsWith(digits) : null,
      capture: dialog};
    // Only the registration step asks for the selected account's trailing digits.
    if (task?.register_account === true) result.account_digits = digits;
    found.forEach((f, i) => {
      if (f.kind !== 'account_option') return;
      const optionDigits = raw(f.el).match(ACCOUNT_NUMBER)?.[1] || null;
      if (task?.account_digits) controls[i].matches = !!optionDigits && optionDigits === task.account_digits;
      // Listing all accounts for registration returns digits and the account type words.
      if (task?.list_accounts === true)
        Object.assign(controls[i], {digits: optionDigits, type: raw(f.el).replace(ACCOUNT_NUMBER, '').replace(/\d/g, '').trim().slice(0, 60)});
    });
    return result;
  }
  function check(token) {
    if (!snapshot || snapshot.token !== token || Date.now() - snapshot.created > 45000) throw new Error('stale_observation');
    return true;
  }
  const PERMITTED = {navigate: 'nav', open_export: 'open_export', choose_date_range: 'date_range',
    choose_format: 'tx_format', choose_details: 'rgl_details', download_export: 'export_submit', cancel_export: 'export_cancel',
    apply_search: 'apply_search', open_accounts: 'account', choose_account: 'account_option'};
  function act(cmd) {
    if (cmd.action === 'discover_click') return discoverClick(cmd);
    check(cmd.token);
    const el = snapshot.elements[cmd.target_id];
    const kind = el && Object.entries(RECOGNIZERS).find(([, test]) => test(el))?.[0];
    if (!el || !el.isConnected || el.disabled || PERMITTED[cmd.action] !== kind ||
        (!HIDDEN_OK.has(kind) && !visible(el))) throw new Error('invalid_target');
    if (cmd.action === 'navigate' && !(PAGES[cmd.value] && PAGES[cmd.value].test(raw(el)))) throw new Error('invalid_target');
    if (cmd.action === 'choose_account' &&
        !(/^\d{3,4}$/.test(cmd.value || '') && raw(el).match(ACCOUNT_NUMBER)?.[1] === cmd.value))
      throw new Error('invalid_target');
    if (cmd.action === 'choose_format' && !(FORMATS.includes(cmd.value) && mask(el.value) === cmd.value))
      throw new Error('invalid_target');
    if (cmd.action === 'choose_date_range') {
      if (!DATE_RANGES.includes(cmd.value)) throw new Error('invalid_target');
      const options = [...el.options].filter(o => mask(o.text) === cmd.value);
      if (options.length !== 1) throw new Error('invalid_target');
      el.value = options[0].value;
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      snapshot = null;
      return {acted: true};
    }
    if (cmd.action === 'download_export') { downloadCapture = {created: Date.now(), urls: new Set()}; watchBlobs(); }
    el.scrollIntoView?.({block: 'center'});
    el.click();
    snapshot = null;
    return {acted: true};
  }
  globalThis.pacioliSchwab = {version: 9, probe, observe, act, check, cleanup, release, dispose, downloadSource,
    _watchBlobs: watchBlobs, _mask: mask};
})();
