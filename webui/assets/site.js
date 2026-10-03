// webui/assets/site.js -- the shared script, moved out of base.html (UI-E2, 2026-09-30).
// Served at /assets/site.<content hash>.js with a year's caching (webui/assets.py); it runs
// where the inline block did, after table.js and the page's window.PALETTE.
// UI-E8: follow a running job -- the server-sent event stream where the browser has one, the
// old polling of /jobs/<id>.json (every `pollMs`) when it does not or the stream drops.
// `apply(status)` gets every update; watching stops once the job has ended.
window.watchJob = function (jid, apply, pollMs, pollOnly) {
  var url = '/jobs/' + encodeURIComponent(jid), done = false;
  function poll() {
    if (done) return;
    fetch(url + '.json', { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) { setTimeout(poll, pollMs * 2); return; }
        apply(j);
        if (j.state !== 'RUNNING') { done = true; return; }
        setTimeout(poll, pollMs);
      }).catch(function () { setTimeout(poll, pollMs * 2); });
  }
  // pollOnly: the job bar on every page polls -- a browser allows six connections per host, and a
  // stream held by every open tab would stall page loads (audit 2026-09-29)
  if (pollOnly || !window.EventSource) { setTimeout(poll, pollMs); return; }
  var es = new EventSource(url + '/events');
  es.onmessage = function (ev) {
    var j;
    try { j = JSON.parse(ev.data); } catch (e) { return; }
    apply(j);
    if (j.state !== 'RUNNING') { done = true; es.close(); }
  };
  es.onerror = function () { es.close(); if (!done) setTimeout(poll, pollMs); };
};
(function () {
  // UI-V5: the compact toggle -- table rows only, remembered in this browser (storage may be blocked: then it lasts the page)
  var db = document.getElementById('density'), root = document.documentElement;
  if (!db) return;
  db.setAttribute('aria-pressed', root.dataset.density === 'compact' ? 'true' : 'false');
  db.addEventListener('click', function () {
    var on = root.dataset.density !== 'compact';
    if (on) { root.dataset.density = 'compact'; } else { delete root.dataset.density; }
    db.setAttribute('aria-pressed', on ? 'true' : 'false');
    try { if (on) { localStorage.setItem('syn-density', 'compact'); } else { localStorage.removeItem('syn-density'); } } catch (e) {}
  });
})();
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // numbers settle onto their value from just below it (B13: never a wrong-looking number); bars and rings grow to their value
  document.querySelectorAll('[data-count]').forEach(function (el) {
    var target = parseFloat(el.getAttribute('data-count')), nd = parseInt(el.getAttribute('data-nd') || '0', 10);
    var suffix = el.getAttribute('data-suffix') || '', prefix = el.getAttribute('data-prefix') || '';
    if (isNaN(target)) return;
    if (reduce) { el.textContent = prefix + target.toFixed(nd) + suffix; return; }
    var t0 = null, dur = 400, from = 0.9 * target;
    // a script that takes the number over drops data-count, and the count-up yields to it
    function step(t) { if (!el.hasAttribute('data-count')) return; if (!t0) t0 = t; var k = Math.min(1, (t - t0) / dur); k = 1 - Math.pow(1 - k, 3); el.textContent = prefix + (from + (target - from) * k).toFixed(nd) + suffix; if (k < 1) requestAnimationFrame(step); }
    requestAnimationFrame(step);
  });
  requestAnimationFrame(function () {
    document.querySelectorAll('[data-width]').forEach(function (el) { el.style.width = el.getAttribute('data-width') + '%'; });
    document.querySelectorAll('[data-dash]').forEach(function (el) { el.style.strokeDashoffset = el.getAttribute('data-dash'); });
  });
  // charts: a crosshair and a tooltip naming every series at the nearest point (the svg carries the data)
  var tip = document.createElement('div'); tip.className = 'viztip'; tip.style.display = 'none'; document.body.appendChild(tip);
  document.querySelectorAll('svg.viz[data-xs]').forEach(function (svg) {
    var xs = svg.getAttribute('data-xs').split(',').map(parseFloat), labels = JSON.parse(svg.getAttribute('data-labels') || '[]');
    var unit = svg.getAttribute('data-unit') || '', nd = parseInt(svg.getAttribute('data-nd') || '1', 10);
    var lines = Array.prototype.map.call(svg.querySelectorAll('polyline[data-vals]'), function (p) {
      return { name: p.getAttribute('data-name'), vals: JSON.parse(p.getAttribute('data-vals')), col: p.getAttribute('data-col') };
    });
    if (!xs.length || !lines.length) return;
    var xh = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    xh.setAttribute('class', 'xh'); xh.setAttribute('y1', svg.getAttribute('data-top')); xh.setAttribute('y2', svg.getAttribute('data-bottom'));
    xh.style.display = 'none'; svg.appendChild(xh);
    svg.addEventListener('pointermove', function (ev) {
      var r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal, mx = (ev.clientX - r.left) * vb.width / r.width, best = 0;
      xs.forEach(function (x, i) { if (Math.abs(x - mx) < Math.abs(xs[best] - mx)) best = i; });
      xh.setAttribute('x1', xs[best]); xh.setAttribute('x2', xs[best]); xh.style.display = '';
      var rows = lines.filter(function (l) { return l.vals[best] !== null && l.vals[best] !== undefined; })
                      .sort(function (p, q) { return q.vals[best] - p.vals[best]; });
      tip.textContent = '';
      var h = document.createElement('b'); h.textContent = labels[best] || ''; tip.appendChild(h);
      rows.forEach(function (l) {
        var row = document.createElement('span'), nm = document.createElement('em'), sw = document.createElement('i'), v = document.createElement('strong');
        sw.style.background = l.col; nm.appendChild(sw); nm.appendChild(document.createTextNode(l.name)); nm.style.fontStyle = 'normal';
        v.textContent = Number(l.vals[best]).toFixed(nd) + unit; row.appendChild(nm); row.appendChild(v); tip.appendChild(row);
      });
      tip.style.display = '';
      var tw = tip.offsetWidth, th = tip.offsetHeight, left = ev.clientX + 14, top = ev.clientY + 14;
      if (left + tw > window.innerWidth - 8) left = ev.clientX - tw - 14;
      if (top + th > window.innerHeight - 8) top = ev.clientY - th - 14;
      tip.style.left = left + 'px'; tip.style.top = top + 'px';
    });
    svg.addEventListener('pointerleave', function () { tip.style.display = 'none'; xh.style.display = 'none'; });
  });
  // U3: while a job runs, poll it from any page; when it ends, a toast with "Open the answer"
  var bar = document.getElementById('jobbar'), toast = document.getElementById('toast');
  function escT(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function showToast(html, ms) {
    if (!toast) return;
    toast.innerHTML = html + ' <button type="button" class="ghost" aria-label="dismiss">×</button>';
    toast.hidden = false;
    toast.querySelector('button').addEventListener('click', function () { toast.hidden = true; });
    if (ms) setTimeout(function () { toast.hidden = true; }, ms);
  }
  if (bar) {
    var jid = bar.getAttribute('data-id'), jurl = bar.getAttribute('data-url'), jlabel = bar.getAttribute('data-label') || 'The job';
    window.watchJob(jid, function (j) {
      if (j.state === 'RUNNING') return;
      bar.remove();
      if (j.state === 'OK') showToast('<span><b>' + escT(jlabel) + '</b> finished.</span> <a href="' + escT(jurl) + '">Open the answer</a>', 30000);
      else showToast('<span><b>' + escT(jlabel) + '</b> did not finish.</span> <a href="' + escT(jurl) + '">See what happened</a>', 30000);
    }, 3000, true);
  }
  // U4 / U14: the command palette and the shortcuts
  var PALETTE = window.PALETTE || [];   // the page's own list, inline in base.html
  var pal = document.getElementById('pal'), palQ = document.getElementById('pal-q'), palList = document.getElementById('pal-list'), keys = document.getElementById('keys');
  var palItems = [], palIdx = 0;
  function typing(ev) { var t = ev.target; return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable); }
  function palRender() {
    var q = palQ.value.trim(), ql = q.toLowerCase();
    var vs = q.match(/^compare\s+(.+?)\s+(?:vs\.?|v|versus)\s+(.+)$/i);
    palItems = [];
    if (vs) palItems.push({ k: 'do', t: 'Compare ' + vs[1] + ' vs ' + vs[2], h: '/tools/compare_players?a=' + encodeURIComponent(vs[1].trim()) + '&b=' + encodeURIComponent(vs[2].trim()) });
    PALETTE.forEach(function (it) { if (!ql || (it.t + ' ' + (it.d || '')).toLowerCase().indexOf(ql) >= 0) palItems.push(it); });
    palItems = palItems.slice(0, 8);
    if (palPlayers.q === ql) palItems = palItems.concat(palPlayers.items).slice(0, 12);   // UI-A9
    palIdx = 0;
    palList.innerHTML = palItems.map(function (it, i) { return '<li class="' + (i === 0 ? 'on' : '') + '" data-i="' + i + '"><span class="k">' + escT(it.k) + '</span><span>' + escT(it.t) + '</span>' + (it.d ? '<span class="d">' + escT(it.d) + '</span>' : '') + '</li>'; }).join('');
  }
  // UI-A9: players come from the player index as the query is typed; an answer to an older
  // query is dropped, so the list never shows players for letters since deleted
  var palPlayers = { q: null, items: [] }, palSeq = 0;
  function palFetchPlayers() {
    if (window.SITE.static) return;                                // no player search without a server
    var q = palQ.value.trim(), ql = q.toLowerCase(), my = ++palSeq;
    if (q.length < 2 || /^compare\s/i.test(q)) { palPlayers = { q: null, items: [] }; return; }
    fetch('/api/players?owner=all&q=' + encodeURIComponent(q), { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || my !== palSeq) return;
        palPlayers = { q: ql, items: (d.players || []).filter(function (p) { return p.pid; }).slice(0, 6).map(function (p) {
          return { k: 'player', t: p.name, h: '/player/' + encodeURIComponent(p.pid), d: [p.pos, p.nfl, p.owner || 'free agent'].filter(Boolean).join(' · ') };
        }) };
        palRender();
      }).catch(function () {});
  }
  function palOpen() { if (!pal) return; keys.hidden = true; pal.hidden = false; palQ.value = ''; palRender(); palQ.focus(); }
  function palGo() { var it = palItems[palIdx]; if (it) window.location.href = window.siteUrl(it.h); }
  if (pal) {
    palQ.addEventListener('input', function () { palRender(); palFetchPlayers(); });
    palQ.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); palIdx = (palIdx + (ev.key === 'ArrowDown' ? 1 : palItems.length - 1)) % Math.max(1, palItems.length); Array.prototype.forEach.call(palList.children, function (li, i) { li.classList.toggle('on', i === palIdx); }); }
      else if (ev.key === 'Enter') { ev.preventDefault(); palGo(); }
    });
    palList.addEventListener('click', function (ev) { var li = ev.target.closest('li'); if (li) { palIdx = parseInt(li.getAttribute('data-i'), 10); palGo(); } });
    pal.addEventListener('click', function (ev) { if (ev.target === pal) pal.hidden = true; });
    keys.addEventListener('click', function (ev) { if (ev.target === keys) keys.hidden = true; });
  }
  var GO = { h: '/', m: '/matchups', l: '/league', f: '/forecasts', d: '/decisions', t: '/tools', g: '/gameday' };
  if (window.SITE.static) { delete GO.t; delete GO.g; }
  var pendingG = 0;
  document.addEventListener('keydown', function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'k') { ev.preventDefault(); palOpen(); return; }
    if (ev.key === 'Escape') { if (pal) pal.hidden = true; if (keys) keys.hidden = true; if (pcard) pcard.hidden = true; return; }
    if (typing(ev) || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (ev.key === '?') { ev.preventDefault(); if (keys) { keys.hidden = !keys.hidden; if (pal) pal.hidden = true; } return; }
    if (ev.key === 'g') { pendingG = Date.now(); return; }
    if (pendingG && Date.now() - pendingG < 1500 && GO[ev.key]) { pendingG = 0; window.location.href = window.siteUrl(GO[ev.key]); return; }
    pendingG = 0;
  });
  // U5: a card for any player name (data-player) after a short hover -- or, UI-V7, when a
  // player link takes keyboard focus; Escape closes it
  var pcard = document.getElementById('pcard'), pcTimer = null, pcCache = {};
  function playerHref(id) { return window.siteUrl('/player/' + encodeURIComponent(id)); }
  function pcShow(el, d) {
    var st = d.status ? d.status : (d.on_ir ? 'IR' : 'healthy');
    pcard.innerHTML = '<div class="n">' + (d.img ? '<img class="face" src="' + escT(d.img) + '" alt="">' : '') + escT(d.name) + '</div><div class="row"><span>' + escT(d.pos || '?') + ' · ' + escT(d.nfl || 'FA') + '</span><span>' + escT(d.owner || 'free agent') + '</span></div>' +
      (d.week_mean != null ? '<div class="row"><span>week ' + escT(d.week) + ' <span class="muted">· ' + escT(d.week_source) + '</span></span><b>' + Number(d.week_mean).toFixed(1) + '</b></div>' : '') +
      '<div class="row"><span>season mean</span><b>' + (d.mean == null ? '—' : Number(d.mean).toFixed(1)) + '</b></div>' +
      (d.vorp != null ? '<div class="row"><span>value over replacement</span><b>' + (d.vorp > 0 ? '+' : '') + Number(d.vorp).toFixed(1) + (d.tier ? ' · tier ' + escT(d.tier) : '') + '</b></div>' : '') +
      '<div class="row"><span>status</span><b>' + escT(st) + '</b></div>' +
      ((d.body_part || d.practice) ? '<div class="row"><span>' + escT(d.body_part || 'injury') + (d.practice ? ' · practice ' + escT(d.practice) : '') + '</span>' + (d.updated ? '<span class="muted">' + escT(String(d.updated).slice(0, 10)) + '</span>' : '') + '</div>' : '') +
      (d.bye ? '<div class="row"><span>bye</span><b>week ' + escT(d.bye) + '</b></div>' : '') +
      (d.strip ? '<div class="strip" title="one week of points: 10th to 90th percentile, the middle half boxed, the mean ticked">' + d.strip + '</div>' : '') +
      (d.pid ? '<a href="' + playerHref(d.pid) + '">Player page</a> · ' : '') + '<a href="/tools/compare_players?a=' + encodeURIComponent(d.name) + '">Compare with…</a>';
    pcard.style.visibility = 'hidden'; pcard.hidden = false;                   // measure before placing: the card grew (UI-P5)
    var r = el.getBoundingClientRect(), h = pcard.offsetHeight, left = Math.min(r.left, window.innerWidth - 276), top = r.bottom + 8;
    if (top + h > window.innerHeight) top = Math.max(8, r.top - h - 8);
    pcard.style.left = Math.max(8, left) + 'px'; pcard.style.top = top + 'px'; pcard.style.visibility = '';
  }
  if (pcard && !window.SITE.static) {                           // the hover card asks the server
    function pcFor(el) {
      clearTimeout(pcTimer);
      pcTimer = setTimeout(function () {
        var name = el.getAttribute('data-player');
        var still = function () { return el.matches(':hover') || document.activeElement === el; };
        if (pcCache[name]) { if (still()) pcShow(el, pcCache[name]); return; }
        fetch('/api/player?name=' + encodeURIComponent(name), { headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.ok ? r.json() : null; }).then(function (d) { if (d) { pcCache[name] = d; if (still()) pcShow(el, d); } }).catch(function () {});
      }, 260);
    }
    document.addEventListener('mouseover', function (ev) {
      var el = ev.target.closest && ev.target.closest('[data-player]');
      if (el) pcFor(el);
    });
    document.addEventListener('focusin', function (ev) {
      var el = ev.target.closest && ev.target.closest('a[data-player]');
      if (el) pcFor(el);
    });
    document.addEventListener('focusout', function (ev) {
      if (ev.target.closest && ev.target.closest('a[data-player]')) setTimeout(function () { if (!pcard.contains(document.activeElement)) pcard.hidden = true; }, 150);
    });
    document.addEventListener('mouseout', function (ev) {
      var el = ev.target.closest && ev.target.closest('[data-player]');
      if (!el) return;
      clearTimeout(pcTimer);
      setTimeout(function () { if (!pcard.matches(':hover') && !el.matches(':hover')) pcard.hidden = true; }, 200);
    });
    pcard.addEventListener('mouseleave', function () { pcard.hidden = true; });
  }
  // UI-V7: a sortable header is a keyboard control too -- Tab to it, Enter or Space sorts
  document.querySelectorAll('th[data-key]').forEach(function (th) { th.setAttribute('tabindex', '0'); });
  document.addEventListener('keydown', function (ev) {
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches && ev.target.matches('th[data-key]')) { ev.preventDefault(); ev.target.click(); }
  });
  // UI-R4: any sortable table downloads as CSV, the rows as shown (sorted, filtered)
  document.querySelectorAll('table').forEach(function (t) {
    if (!t.querySelector('th[data-key]') || !t.tBodies[0]) return;
    var b = document.createElement('button');
    b.type = 'button'; b.className = 'ghost csv'; b.textContent = 'Download CSV';
    b.addEventListener('click', function () {
      function cell(c) { var s = c.textContent.replace(/\s+/g, ' ').trim(); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; }
      var rows = [Array.prototype.map.call(t.tHead.rows[0].cells, cell).join(',')];
      Array.prototype.forEach.call(t.tBodies[0].rows, function (r) { if (!r.hidden) rows.push(Array.prototype.map.call(r.cells, cell).join(',')); });
      var a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob(['\ufeff' + rows.join('\r\n')], { type: 'text/csv;charset=utf-8' }));
      a.download = (document.title.split('·')[0].trim().toLowerCase().replace(/[^a-z0-9]+/g, '-') || 'table') + '.csv';
      document.body.appendChild(a); a.click(); a.remove();
    });
    var host = t.closest('.scroller') || t;
    host.parentNode.insertBefore(b, host.nextSibling);
  });
  // tables whose headers carry data-key sort on click: numbers high to low first, text A to Z,
  // a second click reverses; a cell's data-sort wins over its text. The markup and the arrow
  // styles shipped in c329cca with no script behind them -- found 2026-09-28.
  document.addEventListener('click', function (ev) {
    var th = ev.target.closest && ev.target.closest('th[data-key]');
    if (!th || (ev.target.closest('a, button, input'))) return;
    var table = th.closest('table'), body = table && table.tBodies[0];
    if (!body) return;
    var i = Array.prototype.indexOf.call(th.parentNode.children, th), num = th.getAttribute('data-type') === 'number';
    var desc = th.classList.contains('sort-desc') ? false : (th.classList.contains('sort-asc') ? true : num);
    Array.prototype.forEach.call(th.parentNode.children, function (h) { h.classList.remove('sort-asc', 'sort-desc'); h.removeAttribute('aria-sort'); });
    th.classList.add(desc ? 'sort-desc' : 'sort-asc');
    th.setAttribute('aria-sort', desc ? 'descending' : 'ascending');
    function key(r) {
      var c = r.cells[i];
      var v = !c ? '' : (c.hasAttribute('data-sort') ? c.getAttribute('data-sort') : c.textContent.trim());
      if (!num) return String(v).toLowerCase();
      var f = parseFloat(v);
      return isNaN(f) ? null : f;
    }
    var rows = Array.prototype.slice.call(body.rows);
    rows.sort(function (a, b) {
      var x = key(a), y = key(b);
      if (num && (x === null || y === null)) return x === y ? 0 : (x === null ? 1 : -1);   // blanks last, both ways
      var c = num ? x - y : (x < y ? -1 : (x > y ? 1 : 0));
      return desc ? -c : c;
    });
    rows.forEach(function (r) { body.appendChild(r); });
  });
  // the sticky header's height, so sticky table headers and anchor targets sit below it
  var top = document.querySelector('header.top');
  function headH() { if (top) document.documentElement.style.setProperty('--head-h', top.offsetHeight + 'px'); }
  headH(); window.addEventListener('resize', headH);
  // an alert on the page itself -- and, where the browser allows one, a system notification too.
  // Browsers refuse notification permission on an http address that is not localhost, which is
  // how this site is usually opened; the page alert works everywhere (owner report 2026-09-30).
  window.pageAlert = function (title, body, tag) {
    var host = document.querySelector('.alertstack');
    if (!host) { host = document.createElement('div'); host.className = 'alertstack'; host.setAttribute('role', 'status'); host.setAttribute('aria-live', 'polite'); document.body.appendChild(host); }
    var t = document.createElement('div'); t.className = 'alertcard';
    var b = document.createElement('b'); b.textContent = title; t.appendChild(b);
    if (body) { var s = document.createElement('span'); s.textContent = body; t.appendChild(s); }
    var x = document.createElement('button'); x.type = 'button'; x.setAttribute('aria-label', 'Dismiss'); x.textContent = '\u00d7'; t.appendChild(x);
    function close() { t.classList.add('out'); setTimeout(function () { t.remove(); }, 260); }
    x.addEventListener('click', close);
    setTimeout(close, 15000);
    host.appendChild(t);
    try {
      if (window.Notification && Notification.permission === 'granted' && document.hidden) {
        var n = new Notification(title, { body: body || '', tag: tag || title }); n.onclick = function () { window.focus(); n.close(); };
      }
    } catch (e) {}
  };
  // ---- polish (2026-09-30): the moving parts of the style layer. It all stands down under reduced
  // motion or in an automated browser, so every test reads the page at rest.
  (function () {
    var still = (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) || navigator.webdriver;
    var topbar = document.querySelector('header.top');
    function onScroll() { if (topbar) topbar.classList.toggle('scrolled', window.scrollY > 8); }
    onScroll(); window.addEventListener('scroll', onScroll, { passive: true });
    // a light under the pointer on cards and tiles (not in an automated browser: it adds a child)
    if (!navigator.webdriver) document.querySelectorAll('.tile, .card, .chip, .mg, .og').forEach(function (el) {
      if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
      el.classList.add('spot');
      var g = document.createElement('span'); g.className = 'glow'; g.setAttribute('aria-hidden', 'true'); el.insertBefore(g, el.firstChild);
      el.addEventListener('pointermove', function (ev) {
        var r = el.getBoundingClientRect();
        el.style.setProperty('--mx', (ev.clientX - r.left) + 'px'); el.style.setProperty('--my', (ev.clientY - r.top) + 'px');
      });
    });
    // a ripple where a button is pressed
    document.addEventListener('pointerdown', function (ev) {
      var b = ev.target.closest && ev.target.closest('button, .btn');
      if (!b || still) return;
      var r = b.getBoundingClientRect(), d = Math.max(r.width, r.height), c = document.createElement('span');
      c.className = 'ripple'; c.style.width = c.style.height = d + 'px';
      c.style.left = (ev.clientX - r.left - d / 2) + 'px'; c.style.top = (ev.clientY - r.top - d / 2) + 'px';
      b.appendChild(c); setTimeout(function () { c.remove(); }, 650);
    });
    // a thin progress bar while the next page loads
    var prog = null;
    function going() { if (!prog) { prog = document.createElement('div'); prog.className = 'navprog'; document.body.appendChild(prog); } requestAnimationFrame(function () { prog.classList.add('go'); }); }
    document.addEventListener('click', function (ev) {
      var a = ev.target.closest && ev.target.closest('a[href]');
      if (!a || ev.defaultPrevented || ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || a.target || a.hasAttribute('download')) return;
      var u = new URL(a.href, location.href);
      if (u.origin !== location.origin || (u.pathname === location.pathname && u.hash)) return;
      going();
    });
    document.addEventListener('submit', function (ev) { if (!ev.defaultPrevented) going(); });
    window.addEventListener('pageshow', function () { if (prog) prog.classList.remove('go'); });
    if (still) return;
    // a tile's number settles onto its value from just below it, as [data-count] numbers do
    // (B13: never a wrong-looking number); one that carries data-count, or markup, is left alone
    function settle(el) {
      if (el.hasAttribute('data-count') || el.dataset.settled || el.children.length) return;
      var txt = el.textContent, m = /^(\s*[^\d\-+]*?)([+\-]?)(\d[\d,]*\.?\d*)(.*)$/s.exec(txt);
      if (!m || /\d/.test(m[4])) return;
      var target = parseFloat(m[3].replace(/,/g, '')), dec = (m[3].split('.')[1] || '').length, t0 = null, dur = 500;
      if (!isFinite(target) || target === 0) return;
      el.dataset.settled = '1';
      function fmt(v) { var s = v.toFixed(dec); return m[3].indexOf(',') >= 0 ? Number(s).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) : s; }
      function step(ts) { if (t0 === null) t0 = ts; var k = Math.min(1, (ts - t0) / dur), e = 1 - Math.pow(1 - k, 3);
        el.textContent = k < 1 ? m[1] + m[2] + fmt(target * (0.9 + 0.1 * e)) + m[4] : txt; if (k < 1) requestAnimationFrame(step); }
      requestAnimationFrame(step);
    }
    // lines draw themselves in
    function draw(svg) {
      svg.querySelectorAll('path, polyline').forEach(function (p) {
        var cs = getComputedStyle(p);
        if (cs.fill !== 'none' || cs.stroke === 'none' || !p.getTotalLength) return;
        var len = p.getTotalLength(); if (!(len > 4)) return;
        p.style.strokeDasharray = len; p.style.strokeDashoffset = len; p.classList.add('drawn');
        requestAnimationFrame(function () { requestAnimationFrame(function () { p.style.strokeDashoffset = 0; }); });
        p.addEventListener('transitionend', function () { p.style.strokeDasharray = ''; p.style.strokeDashoffset = ''; }, { once: true });
      });
    }
    var inView = function (el) { var r = el.getBoundingClientRect(); return r.top < window.innerHeight && r.bottom > 0; };
    var io = 'IntersectionObserver' in window ? new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        var el = e.target; io.unobserve(el);
        if (el.classList.contains('pre')) { el.classList.add('pre-in'); el.classList.remove('pre'); }
        el.querySelectorAll('.tile .v').forEach(settle);
        el.querySelectorAll('svg.viz, svg.spark').forEach(draw);
      });
    }, { rootMargin: '0px 0px -8% 0px' }) : null;
    document.querySelectorAll('.wrap > section, .wrap > .tiles, main > section, .head ~ section').forEach(function (el) {
      if (!io) return;
      if (!inView(el)) el.classList.add('pre');
      io.observe(el);
    });
    document.querySelectorAll('.tiles, .hero').forEach(function (el) { if (io && inView(el)) io.observe(el); });
  })();
  document.querySelectorAll('[data-theme-set]').forEach(function (b) {
    var cur = document.documentElement.dataset.theme || 'system';
    b.classList.toggle('on', b.getAttribute('data-theme-set') === cur);
    b.addEventListener('click', function () {
      var t = b.getAttribute('data-theme-set');
      if (t === 'system') { delete document.documentElement.dataset.theme; } else { document.documentElement.dataset.theme = t; }
      try { if (t === 'system') { localStorage.removeItem('syn-theme'); } else { localStorage.setItem('syn-theme', t); } } catch (e) {}
      document.querySelectorAll('[data-theme-set]').forEach(function (x) { x.classList.toggle('on', x === b); });
    });
  });
  // ask for system notifications only where the browser can grant them; a refusal changes nothing
  window.askNotify = function () {
    try { if (window.Notification && window.isSecureContext && Notification.permission === 'default') Notification.requestPermission(); } catch (e) {}
  };
  // a table wider than its column gets a sideways scrollbar (.xscroll) instead of losing its
  // right-hand columns to the page's clip; re-measured on resize
  function fit() {
    document.querySelectorAll('.scroller').forEach(function (s) {
      s.classList.remove('xscroll');
      var t = s.firstElementChild && s.querySelector('table');
      if (t && t.getBoundingClientRect().width > s.clientWidth + 1) s.classList.add('xscroll');
    });
  }
  fit(); window.addEventListener('resize', fit);
})();
