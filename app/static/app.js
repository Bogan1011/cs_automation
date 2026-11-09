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
    const names = Object.keys(latest).sort();
    const box = document.getElementById('latest');
    box.innerHTML = names.map(k => `<div class="lp"><span>${esc(k)}</span><b>${esc(latest[k])}</b></div>`).join('');
    box.hidden = box.previousElementSibling.hidden = !names.length;
    countUp();
  }
  load();
  setInterval(load, 60000);
}

function startClient() {
  const rows = [...document.querySelectorAll('#eps tbody tr[data-p]')];
  let prot = '';
  const search = document.getElementById('search'), issues = document.getElementById('issues');
  function filter() {
    const q = search.value.toLowerCase();
    rows.forEach(r => {
      const show = (!prot || r.dataset.p === prot) && (!issues.checked || r.dataset.issue === '1') && (!q || r.textContent.toLowerCase().includes(q));
      r.style.display = show ? '' : 'none';
    });
  }
  search.addEventListener('input', filter);
  issues.addEventListener('change', filter);
  // iOS-style segmented control with sliding thumb
  const seg = document.getElementById('prot'), thumb = document.createElement('span');
  thumb.className = 'thumb'; seg.prepend(thumb);
  const moveThumb = b => { thumb.style.left = b.offsetLeft + 'px'; thumb.style.width = b.offsetWidth + 'px'; };
  seg.querySelectorAll('button').forEach(b => b.addEventListener('click', () => {
    seg.querySelectorAll('button').forEach(x => x.classList.remove('on'));
    b.classList.add('on'); moveThumb(b); prot = b.dataset.p; filter();
  }));
  requestAnimationFrame(() => moveThumb(seg.querySelector('button.on')));
  addEventListener('resize', () => moveThumb(seg.querySelector('button.on')));
  document.querySelectorAll('.kpi b').forEach(b => { const v = Number(b.textContent); if (!isNaN(v)) { b.innerHTML = `<span data-n="${v}">0</span>`; } });
  countUp();

  document.getElementById('refresh').addEventListener('click', async e => {
    const b = e.currentTarget; busy(b, 'Refreshing…');
    try {
      const d = await (await api(`/api/clients/${b.dataset.id}/refresh`, {method: 'POST'})).json();
      if (d.ok) location.reload(); else { toast('Refresh failed: ' + esc(d.error), 8000); idle(b); }
    } catch (err) { toast('Refresh failed: ' + esc(err.message), 8000); idle(b); }
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
