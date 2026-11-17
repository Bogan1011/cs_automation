const fmtTime = iso => iso ? new Date(iso).toLocaleString() : 'never';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// Theme toggle (remembered per browser)
document.getElementById('theme')?.addEventListener('click', () => {
  const dark = matchMedia('(prefers-color-scheme: dark)').matches;
  const cur = document.documentElement.dataset.theme || (dark ? 'dark' : 'light');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('theme', next); } catch (e) {}
});

// Smooth count-up for numbers
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
function countUp(root = document) {
  root.querySelectorAll('[data-n]').forEach(el => {
    const to = Number(el.dataset.n), from = Number(el.dataset.from || 0);
    if (reduceMotion || isNaN(to) || from === to) { el.textContent = isNaN(to) ? '–' : to.toLocaleString(); return; }
    const t0 = performance.now(), dur = 700;
    const step = now => {
      const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 4);
      el.textContent = Math.round(from + (to - from) * e).toLocaleString();
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}
const prevValues = {};
const num = (key, v) => { const from = prevValues[key] ?? 0; prevValues[key] = v ?? 0; return v == null ? '–' : `<span data-n="${v}" data-from="${from}">${from}</span>`; };

function toast(html, ms = 0) {
  const m = document.getElementById('msg'); if (!m) return;
  m.innerHTML = ''; void m.offsetWidth; m.innerHTML = html;
  clearTimeout(toast._t); if (ms) toast._t = setTimeout(() => m.innerHTML = '', ms);
}
function busy(b, label) { b.disabled = true; b.dataset.label = b.innerHTML; b.innerHTML = `<span class="spin"></span>${label}`; }
function idle(b) { b.disabled = false; b.innerHTML = b.dataset.label; }

// "17:00:00, 5th Nov (7 days ago)" in the viewer's local time
const ord = d => d + (d % 10 === 1 && d !== 11 ? 'st' : d % 10 === 2 && d !== 12 ? 'nd' : d % 10 === 3 && d !== 13 ? 'rd' : 'th');
function ago(ms) {
  const m = Math.floor(ms / 60000), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d >= 1) return d === 1 ? '1 day ago' : `${d} days ago`;
  if (h >= 1) return h === 1 ? '1 hour ago' : `${h} hours ago`;
  return m <= 1 ? 'just now' : `${m} min ago`;
}
function seenText(iso) {
  const t = new Date(iso); if (isNaN(t)) return '';
  const now = new Date(), time = t.toLocaleTimeString('en-GB', {hour12: false});
  const date = `${ord(t.getDate())} ${t.toLocaleString('en-GB', {month: 'short'})}` + (t.getFullYear() !== now.getFullYear() ? ` ${t.getFullYear()}` : '');
  return `${time}, ${date} (${ago(now - t)})`;
}
document.querySelectorAll('.seen[data-t]').forEach(el => { const s = seenText(el.dataset.t); if (s) { el.textContent = s; el.title = new Date(el.dataset.t).toString(); } });
document.querySelectorAll('.ts').forEach(el => { const t = el.textContent.trim(); if (t && t !== 'never') el.textContent = fmtTime(t); });

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (r.status === 401) { location.href = '/login'; throw new Error('login'); }
  return r;
}

function startOverview(pollSeconds) {
  async function load() {
    try {
      const data = await (await api('/api/overview')).json();
      render(data.clients, data.latest_patterns || {});
      document.getElementById('updated').textContent = 'Updated ' + new Date().toLocaleTimeString() + ' · data refreshes every ' + Math.round(pollSeconds / 60) + ' min';
    } catch (e) { document.getElementById('updated').textContent = 'Could not load data: ' + e.message; }
  }
  function render(clients, latest) {
    const sum = k => clients.reduce((a, c) => a + ((c.summary || {})[k] || 0), 0);
    document.getElementById('totals').innerHTML = [
      ['Clients', clients.length, false, '#clients'], ['Endpoints', sum('total'), false, '/endpoints'],
      ['Standard Endpoint Protection', sum('sep'), false, '/endpoints?protection=SEP'],
      ['Server & Workload Protection', sum('swp'), false, '/endpoints?protection=SWP'],
      ['Offline', sum('offline'), true, '/endpoints?filter=offline'], ['Not seen 7+ days', sum('stale'), true, '/endpoints?filter=stale'],
      ['Patterns up to date', sum('up_to_date'), false, '/endpoints?filter=patterns_latest'], ['Patterns outdated', sum('outdated'), true, '/endpoints?filter=patterns_outdated'], ['Agents needing update', sum('behind_latest'), true, '/endpoints?filter=agent_behind'],
    ].map(([l, v, b, h], i) => `<a href="${h}" class="kpi ${b && v ? 'bad' : (i === 1 ? 'accent' : '')}"><b>${num('t' + i, v)}</b><span>${esc(l)}</span></a>`).join('');

    document.getElementById('clients').innerHTML = clients.map(c => {
      const s = c.summary || {};
      const status = c.last_poll_ok === false ? '<span class="pill fail"><i></i>Poll failed</span>' : c.last_poll_ok ? '<span class="pill live"><i></i>Live</span>' : '<span class="pill"><i></i>Waiting for first poll</span>';
      const pct = s.total ? Math.round(100 * (s.online || 0) / s.total) : 0;
      const n = k => num(c.id + k, s[k]);
      const L = q => `/endpoints?client=${c.id}${q}`;
      return `<div class="card client" onclick="if(!event.target.closest('a'))location.href='/clients/${c.id}'">
        <h3><a href="/clients/${c.id}">${esc(c.name)}</a> <span class="tag">${esc(c.region)}</span></h3>
        <div class="muted">${status} · ${fmtTime(c.last_poll_at)}</div>
        ${c.last_poll_ok === false ? `<div class="err" style="font-size:12px">${esc((c.last_error || '').slice(0, 160))}</div>` : ''}
        <div class="health" title="${pct}% online"><span style="width:${pct}%"></span></div>
        <div class="muted" style="font-size:12px">${s.total ? pct + '% online' : '&nbsp;'}</div>
        <div class="nums">
          <a href="${L('')}"><b>${n('total')}</b><span>Endpoints</span></a>
          <a href="${L('&protection=SEP')}"><b>${n('sep')}</b><span>Standard EP</span></a>
          <a href="${L('&protection=SWP')}"><b>${n('swp')}</b><span>Server &amp; Workload</span></a>
          <a href="${L('&filter=offline')}" class="${s.offline ? 'bad' : ''}"><b>${n('offline')}</b><span>Offline</span></a>
          <a href="${L('&filter=patterns_outdated')}" class="${s.outdated ? 'bad' : ''}"><b>${num(c.id + 'pb', s.outdated)}</b><span>Patterns outdated</span></a>
          <a href="${L('&filter=agent_behind')}" class="${s.behind_latest ? 'bad' : ''}"><b>${n('behind_latest')}</b><span>Agents need update</span></a>
        </div></div>`;
    }).join('') || '<p class="muted">No clients yet. Add one with <code>python -m app.cli add-client</code>.</p>';

    document.querySelector('#compare tbody').innerHTML = clients.map(c => {
      const s = c.summary || {};
      return `<tr><td><a href="/clients/${c.id}">${esc(c.name)}</a></td><td>${esc(c.region)}</td>` +
        [['total', ''], ['sep', '&protection=SEP'], ['swp', '&protection=SWP'], ['online', '&filter=online'], ['offline', '&filter=offline'],
         ['stale', '&filter=stale'], ['up_to_date', '&filter=patterns_latest'], ['outdated', '&filter=patterns_outdated'],
         ['behind_latest', '&filter=agent_behind']].map(([k, q]) => `<td class="num">${s[k] == null ? '–' : `<a class="cnt" href="/endpoints?client=${c.id}${q}">${s[k]}</a>`}</td>`).join('') +
        `<td>${esc(['SEP', 'SWP'].filter(k => (s.latest_versions || {})[k]).map(k => k + ' ' + s.latest_versions[k]).join(' · ') || '–')}</td><td>${fmtTime(c.last_poll_at)}</td></tr>`;
    }).join('');
    const PN = {SEP: 'Standard Endpoint Protection', SWP: 'Server & Workload Protection'};
    const prots = Object.keys(latest).sort();
    const box = document.getElementById('latest');
    box.innerHTML = prots.map(p => `<div class="lpcol"><h3 class="subhead">${esc(PN[p] || p)}</h3>` +
      Object.keys(latest[p]).sort().map(k => `<div class="lp"><span>${esc(k)}</span><b>${esc(latest[p][k])}</b></div>`).join('') + '</div>').join('');
    box.hidden = box.previousElementSibling.hidden = !prots.length;
    countUp();
  }
  load();
  setInterval(load, 60000);
}

function startClient(clientId) {
  const tbody = document.querySelector('#eps tbody');
  let rows = [...tbody.querySelectorAll('tr[data-p]')];
  const $ = id => document.getElementById(id);
  const search = $('search'), none = $('none'), title = $('viewtitle'), shown = $('shown'), csv = $('csv');
  const PROT = ['SEP', 'SWP', 'Sensor only'];
  let view = 'all', st = '', page = 0, size = 50, sortKey = '', sortDir = 1;
  const pref = (() => { try { return JSON.parse(localStorage.getItem('eps-pref') || '{}'); } catch (e) { return {}; } })();
  if (pref.size != null) { size = pref.size; $('size').value = String(size); }
  const save = () => { try { localStorage.setItem('eps-pref', JSON.stringify({size})); } catch (e) {} };
  const inView = r => view === 'all' || (PROT.includes(view) ? r.dataset.p === view : r.dataset.f.split(' ').includes(view));
  const vkey = v => v.split(/[.\-]/).map(x => x.padStart(8, '0')).join('.');

  function render(resetPage) {
    if (resetPage) page = 0;
    const q = search.value.trim().toLowerCase();
    const match = rows.filter(r => inView(r) && (!st || r.dataset.st === st || (st === 'off' && r.dataset.st === 'stale')) && (!q || r.textContent.toLowerCase().includes(q)));
    const pages = size ? Math.max(1, Math.ceil(match.length / size)) : 1;
    page = Math.min(page, pages - 1);
    const from = size ? page * size : 0, to = size ? from + size : match.length;
    const visible = new Set(match.slice(from, to));
    rows.forEach(r => { r.hidden = !visible.has(r); });
    shown.textContent = match.length.toLocaleString();
    none.hidden = match.length > 0 || !rows.length;
    $('range').textContent = match.length ? `${(from + 1).toLocaleString()}–${Math.min(to, match.length).toLocaleString()} of ${match.length.toLocaleString()}` : '';
    $('pageno').textContent = `Page ${page + 1} of ${pages}`;
    $('prev').disabled = page === 0; $('next').disabled = page >= pages - 1;
  }
  function sortBy(k) {
    sortDir = sortKey === k ? -sortDir : (k === 'seen' || k === 'comp' ? -1 : 1); sortKey = k;
    const num = k === 'seen' || k === 'comp';
    rows.sort((a, b) => {
      let x = a.dataset[k] || '', y = b.dataset[k] || '';
      if (num) return (Number(x) - Number(y)) * sortDir;
      if (k === 'agent' || k === 'sensor') { x = vkey(x); y = vkey(y); }
      return x.localeCompare(y, undefined, {numeric: true}) * sortDir;
    });
    rows.forEach(r => tbody.insertBefore(r, none));
    document.querySelectorAll('#eps th.sort').forEach(th => th.dataset.dir = th.dataset.k === k ? (sortDir > 0 ? 'asc' : 'desc') : '');
    render(true);
  }
  document.querySelectorAll('#eps th.sort').forEach(th => th.addEventListener('click', () => sortBy(th.dataset.k)));
  let t; search.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => render(true), 120); });
  addEventListener('keydown', e => { if (e.key === '/' && document.activeElement !== search) { e.preventDefault(); search.focus(); } });
  $('size').addEventListener('change', e => { size = Number(e.target.value); save(); render(true); });
  const top = () => document.querySelector('.inv-main').scrollIntoView({behavior: reduceMotion ? 'auto' : 'smooth', block: 'start'});
  $('prev').addEventListener('click', () => { page--; render(); top(); });
  $('next').addEventListener('click', () => { page++; render(); top(); });

  // iOS-style segmented control with sliding thumb (status filter)
  const seg = $('st'), thumb = document.createElement('span');
  thumb.className = 'thumb'; seg.prepend(thumb);
  const moveThumb = b => { thumb.style.left = b.offsetLeft + 'px'; thumb.style.width = b.offsetWidth + 'px'; };
  seg.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
    seg.querySelectorAll('button').forEach(x => x.classList.remove('on'));
    b.classList.add('on'); moveThumb(b); st = b.dataset.s; render(true);
  }));
  requestAnimationFrame(() => moveThumb(seg.querySelector('button.on')));
  addEventListener('resize', () => moveThumb(seg.querySelector('button.on')));

  document.querySelectorAll('.inv-row[data-k]').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('.inv-row.on').forEach(x => x.classList.remove('on'));
    b.classList.add('on'); view = b.dataset.k; title.textContent = b.dataset.label;
    const p = new URLSearchParams({client: clientId});
    if (PROT.includes(view)) p.set('protection', view); else if (view !== 'all') p.set('filter', view);
    csv.href = '/endpoints.csv?' + p; render(true);
    if (innerWidth < 900) top();
  }));
  document.querySelector('#tabs a.on')?.scrollIntoView({inline: 'center', block: 'nearest'});
  render(true);

  document.getElementById('refresh').addEventListener('click', async e => {
    const b = e.currentTarget; busy(b, 'Syncing…');
    try {
      const d = await (await api(`/api/clients/${b.dataset.id}/refresh`, {method: 'POST'})).json();
      if (d.ok) location.reload(); else { toast('Sync failed: ' + esc(d.error), 8000); idle(b); }
    } catch (err) { toast('Sync failed: ' + esc(err.message), 8000); idle(b); }
  });
  document.getElementById('report').addEventListener('click', async e => {
    const b = e.currentTarget; busy(b, 'Building report…');
    try {
      const d = await (await api(`/api/clients/${b.dataset.id}/report`, {method: 'POST'})).json();
      if (d.ok) toast('✓ Draft created in Outlook. ' + (d.web_link ? `<a href="${esc(d.web_link)}" target="_blank" rel="noopener">Open the draft</a>` : 'Check your Drafts folder.'), 15000);
      else toast(esc(d.error) + (d.download ? ` <a href="${esc(d.download)}">Download the PDF instead</a>` : ''), 15000);
    } catch (err) { toast('Report failed: ' + esc(err.message), 8000); }
    idle(b);
  });

  // Keep the page live: reload every 5 minutes unless the user is typing in search
  setInterval(() => { if (document.activeElement !== search) location.reload(); }, 300000);
}

function startHome() {
  document.getElementById('syncall')?.addEventListener('click', async e => {
    const b = e.currentTarget; busy(b, 'Syncing all clients…');
    toast('Syncing every client. This can take a minute or two.');
    try {
      const d = await (await api('/api/sync', {method: 'POST'})).json();
      const bad = (d.results || []).filter(r => r.result.startsWith('FAILED'));
      if (!bad.length) { toast('✓ All clients synced.', 2500); setTimeout(() => location.reload(), 700); return; }
      toast(bad.map(r => `<b>${esc(r.client)}</b>: ${esc(r.result)}`).join('<br>'), 15000);
    } catch (err) { toast('Sync failed: ' + esc(err.message), 8000); }
    idle(b);
  });
}

// Simple search + paging for a server-rendered table (rows marked data-row)
function startPagedTable(id) {
  const $ = x => document.getElementById(x);
  const rows = [...document.querySelectorAll(`#${id} tbody tr[data-row]`)];
  const search = $('search'), none = $('none');
  let page = 0, size = 50;
  try { const v = localStorage.getItem('rows-' + id); if (v != null) { size = Number(v); $('size').value = v; } } catch (e) {}
  function render(reset) {
    if (reset) page = 0;
    const q = search.value.trim().toLowerCase();
    const match = rows.filter(r => !q || r.textContent.toLowerCase().includes(q));
    const pages = size ? Math.max(1, Math.ceil(match.length / size)) : 1;
    page = Math.min(page, pages - 1);
    const from = size ? page * size : 0, to = size ? from + size : match.length;
    const vis = new Set(match.slice(from, to));
    rows.forEach(r => r.hidden = !vis.has(r));
    if (none) none.hidden = match.length > 0 || !rows.length;
    $('range').textContent = match.length ? `${from + 1}–${Math.min(to, match.length)} of ${match.length}` : '';
    $('pageno').textContent = `Page ${page + 1} of ${pages}`;
    $('prev').disabled = page === 0; $('next').disabled = page >= pages - 1;
  }
  let t; search.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => render(true), 120); });
  $('size').addEventListener('change', e => { size = Number(e.target.value); try { localStorage.setItem('rows-' + id, e.target.value); } catch (x) {} render(true); });
  $('prev').addEventListener('click', () => { page--; render(); scrollTo({top: 0, behavior: reduceMotion ? 'auto' : 'smooth'}); });
  $('next').addEventListener('click', () => { page++; render(); scrollTo({top: 0, behavior: reduceMotion ? 'auto' : 'smooth'}); });
  addEventListener('keydown', e => { if (e.key === '/' && document.activeElement !== search) { e.preventDefault(); search.focus(); } });
  render(true);
}

// Agent installer: card -> Windows/Linux sheet -> copy / email / open the OneDrive link
function startInstaller(clientName, reportTo) {
  const dlg = document.getElementById('pick'), $ = id => document.getElementById(id);
  let card = null;
  const showOs = () => { $('step-os').hidden = false; $('step-link').hidden = true; };
  document.querySelectorAll('.icard').forEach(c => c.addEventListener('click', () => {
    card = c; $('pick-title').textContent = c.dataset.title; showOs();
    dlg.showModal ? dlg.showModal() : dlg.setAttribute('open', '');
  }));
  document.querySelectorAll('.osbtn').forEach(b => b.addEventListener('click', () => {
    const os = b.dataset.os, url = card.dataset[os] || '', label = b.textContent.trim();
    $('pick-os').textContent = label; $('pick-url').value = url;
    $('step-os').hidden = true; $('step-link').hidden = false;
    $('nolink').hidden = !!url;
    ['copy', 'mail', 'open'].forEach(id => $(id).classList.toggle('disabled', !url));
    $('copy').disabled = !url;
    $('open').href = url || '#';
    const subject = `${clientName}: Trend Micro ${card.dataset.title} installer (${label})`;
    const body = `Hi,\n\nPlease use the script below to install the ${card.dataset.title} on your ${label} machines:\n\n${url}\n\nRun it with administrator/root rights. Let us know once done or if you face any issue.\n\nRegards,\nEventus Security SOC`;
    $('mail').href = url ? `mailto:${encodeURIComponent(reportTo || '')}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}` : '#';
  }));
  $('back').addEventListener('click', showOs);
  $('copy').addEventListener('click', async () => {
    const url = $('pick-url').value; if (!url) return;
    try { await navigator.clipboard.writeText(url); }
    catch (e) { $('pick-url').select(); document.execCommand('copy'); }
    $('copy').textContent = '✓ Copied'; setTimeout(() => $('copy').textContent = 'Copy link', 1600);
  });
  dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close(); });
}
