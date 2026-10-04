/* Only the toolbar-selected Chase tab can be observed or operated. */
let port = null, selected = null, busy = false, activation = 0, pending = null;
let lastDownload = null;
const allowed = new Set(['https://secure.chase.com', 'https://www.chase.com', 'https://chase.com']);
function origin(url) { try { return new URL(url.replace(/^blob:/, '')).origin; } catch (_) { return ''; } }
browser.tabs.onActivated.addListener(() => { activation++; });
browser.tabs.onUpdated.addListener((id, change) => { if (selected?.id === id && change.url) activation++; });
browser.tabs.onRemoved.addListener(id => { if (selected?.id === id) selected = null; });
browser.downloads.onCreated.addListener(item => {
  if (pending && Date.parse(item.startTime) >= pending.started - 1000) {
    pending.items.push(item.id);
    lastDownload = {source_origin: origin(item.url), source_scheme: String(item.url || '').split(':')[0], has_referrer: !!item.referrer,
      referrer_origin: origin(item.referrer || ''), state: item.state, network_correlated: false, dom_correlated: false};
  }
});
// Retain metadata for candidate exports only, never headers, cookies or bodies.
browser.webRequest.onHeadersReceived.addListener(details => {
  if (!pending || details.tabId !== pending.tabId || details.timeStamp < pending.started ||
      !allowed.has(origin(details.url)) || details.statusCode < 200 || details.statusCode >= 300) return;
  const headers = details.responseHeaders || [];
  const attachment = headers.some(h => h.name.toLowerCase() === 'content-disposition' && /attachment/i.test(h.value || ''));
  const qfx = headers.some(h => h.name.toLowerCase() === 'content-type' && /(?:qfx|ofx)/i.test(h.value || ''));
  if ((attachment || qfx) && pending.sources.size < 20) pending.sources.add(details.url);
}, {urls: [...allowed].map(o => o + '/*')}, ['responseHeaders']);
async function tab() {
  if (!selected) throw new Error('select_chase_tab');
  const t = await browser.tabs.get(selected.id);
  if (!allowed.has(origin(t.url)) || !t.active) throw new Error('chase_tab_not_active');
  return t;
}
async function call(method, argument) {
  const t = await tab();
  await browser.tabs.executeScript(t.id, {file: 'content.js'});
  const [result] = await browser.tabs.executeScript(t.id, {
    code: `globalThis.pacioliChase[${JSON.stringify(method)}](${JSON.stringify(argument ?? null)})`});
  return result;
}
async function image(data) {
  const img = new Image(); img.src = data; await img.decode();
  const scale = Math.min(1, 1600 / img.width, 1200 / img.height);
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(img.width * scale); canvas.height = Math.round(img.height * scale);
  canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height);
  const encoded = canvas.toDataURL('image/jpeg', .85).split(',')[1];
  if (encoded.length > 3 * 1024 * 1024) throw new Error('screenshot_too_large');
  return encoded;
}
async function execute(cmd) {
  if (cmd.op === 'ping') return {ready: !!selected, version: 2};
  if (cmd.op === 'diagnostics') return {download: lastDownload};
  if (cmd.op === 'prepare') {
    const t = await browser.tabs.get(selected?.id).catch(() => null);
    if (!selected || !t || !allowed.has(origin(t.url))) throw new Error('select_chase_tab');
    // Keep Pacioli usable when the operator starts from another tab in this window.
    if (!t.active) await browser.windows.create({tabId: t.id, focused: false});
    await tab();
    return {ready: true};
  }
  if (cmd.op === 'release') { pending = null; if (selected) await call('release'); return {released: true}; }
  if (cmd.op === 'observe') {
    const t = await tab(), revision = activation;
    const observed = await call('observe');
    if (['form','overview','limit'].includes(observed.kind)) {
      try {
        if (origin(t.url) !== selected.origin) throw new Error('capture_permission_required');
        let capture;
        try { capture = await browser.tabs.captureVisibleTab(t.windowId, {format: 'jpeg', quality: 85}); }
        catch (_) { throw new Error('capture_permission_required'); }
        await tab();
        if (revision !== activation) throw new Error('chase_tab_changed');
        await call('check', observed.token);
        observed.image = await image(capture);
      } finally { await call('cleanup').catch(() => {}); }
    }
    return observed;
  }
  if (cmd.op === 'act') {
    if (cmd.action === 'download_qfx') {
      if (pending) throw new Error('download_already_pending');
      const t = await tab();
      pending = {started: Date.now(), items: [], origin: origin(t.url), tabId: t.id, sources: new Set()};
      try { return await call('act', cmd); } catch (error) { pending = null; throw error; }
    }
    if (pending) throw new Error('download_already_pending');
    return await call('act', cmd);
  }
  if (cmd.op === 'download') {
    if (!pending) throw new Error('no_download_pending');
    if (pending.items.length > 1) { pending = null; throw new Error('ambiguous_download'); }
    if (!pending.items.length) return {state: 'waiting'};
    const [item] = await browser.downloads.search({id: pending.items[0]});
    const correlated = !!item && pending.sources.has(item.url);
    const domCorrelated = !!item && item.url.startsWith('blob:') && origin(item.url) === pending.origin &&
      (await call('downloadSource', item.url)).matched === true;
    if (lastDownload) { lastDownload.network_correlated = correlated; lastDownload.dom_correlated = domCorrelated; }
    if (!item || !allowed.has(origin(item.url)) ||
        !(correlated || domCorrelated || origin(item.referrer || '') === pending.origin))
      throw new Error('download_provenance_unavailable');
    if (item.state === 'interrupted') throw new Error('download_interrupted');
    if (item.state !== 'complete') return {state: 'waiting'};
    if (!/\.(qfx|ofx)$/i.test(item.filename) || item.fileSize > 10 * 1024 * 1024)
      throw new Error('invalid_qfx_download');
    const result = {state: 'complete', id: item.id, path: item.filename};
    pending = null; await call('release'); return result;
  }
  throw new Error('unsupported_operation');
}
function connect() {
  if (port) return;
  port = browser.runtime.connectNative('lan.pacioli.chase');
  port.onDisconnect.addListener(() => { port = null; pending = null; browser.browserAction.setBadgeText({text: '!'}); });
  port.onMessage.addListener(async cmd => {
    if (busy) { port?.postMessage({id: cmd.id, error: 'bridge_busy'}); return; }
    busy = true;
    try { port?.postMessage({id: cmd.id, result: await execute(cmd)}); }
    catch (error) { port?.postMessage({id: cmd.id, error: /^[a-z_]{3,48}$/.test(error.message) ? error.message : 'bridge_operation_failed'}); }
    finally { busy = false; }
  });
}
browser.browserAction.onClicked.addListener(t => {
  if (!allowed.has(origin(t.url))) { browser.browserAction.setBadgeText({text: '!'}); return; }
  if (busy || pending) return;
  selected = {id: t.id, windowId: t.windowId, origin: origin(t.url)}; activation++;
  connect(); browser.browserAction.setBadgeText({text: 'ON'});
});
