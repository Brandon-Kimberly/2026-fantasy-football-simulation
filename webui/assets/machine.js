// webui/assets/machine.js -- the playoff machine on the public site (2026-09-30).
// The public site is static, so it cannot ask webui/outcomes.py for each set of picks. This does
// the same count in the browser, from the forecast's simulated seasons (playoffs/outcomes.json):
// the share of seasons in which every pick happened, refused under doc.min matching seasons, each
// share with sqrt(p(1-p)/n) as its standard error. tests/test_public_machine.py holds it to the
// Python numbers for the same picks, in a real browser.
(function () {
  var form = document.getElementById('pm-static');
  if (!form) return;
  var res = document.getElementById('pm-result');
  var data = null, base = null, touched = false;

  fetch(form.dataset.src)
    .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
    .then(function (doc) {
      data = decode(doc);
      base = conditional([]);
      update();
      form.dataset.ready = '1';
    })
    .catch(function () { form.dataset.ready = 'failed'; });

  // ---- the export, decoded as webui.outcomes.Outcomes decodes it
  function decode(doc) {
    var T = doc.teams.length, n = doc.seasons.length, hexn = doc.median_hex || 1;
    var d = { T: T, n: n, teams: doc.teams, min: doc.min, spots: doc.spots, games: {}, med: {},
              rank: new Int16Array(n * T), champ: new Int16Array(n), index: {} };
    doc.teams.forEach(function (t, i) { d.index[t] = i; });
    d.rank.fill(T);
    d.champ.fill(-1);
    var parts = doc.seasons.map(function (r) { return r.split(';'); });
    var codes = parts.map(function (p) { return p[0] ? p[0].split('|') : []; });
    doc.weeks.forEach(function (w, wi) {
      var g = (doc.matchups[String(w)] || []).length;
      var games = new Int8Array(n * g), med = new Uint8Array(n * T);
      var ok = n > 0 && codes.every(function (c) { return (c[wi] || '').length === g + hexn; });
      if (ok) {
        for (var s = 0; s < n; s++) {
          var c = codes[s][wi], bits = parseInt(c.slice(g), 16);
          for (var gi = 0; gi < g; gi++) games[s * g + gi] = c.charCodeAt(gi) - 48;
          for (var t = 0; t < T; t++) med[s * T + t] = (bits >> t) & 1;
        }
      } else {
        games.fill(-1);
      }
      d.games[w] = { g: g, a: games };
      d.med[w] = med;
    });
    parts.forEach(function (p, s) {
      var seeds = p.length > 1 && p[1] ? p[1].split(',') : [];
      seeds.forEach(function (x, r) {
        var ti = parseInt(x, 10);
        if (ti >= 0 && ti < T) d.rank[s * T + ti] = r;
      });
      if (p.length > 2 && p[2] !== '') d.champ[s] = parseInt(p[2], 10);
    });
    return d;
  }

  // ---- webui.outcomes.conditional
  function conditional(picks) {
    var d = data, n = d.n, T = d.T, keep = new Uint8Array(n), m = 0, s;
    keep.fill(1);
    picks.forEach(function (p) {
      if (p.kind === 'g') {
        var G = d.games[p.w], want = p.v === 'a' ? 1 : 0;
        if (!G || p.i >= G.g) return;
        for (s = 0; s < n; s++) if (keep[s] && G.a[s * G.g + p.i] !== want) keep[s] = 0;
      } else {
        var M = d.med[p.w], wantm = p.v === '1' ? 1 : 0;
        if (!M || p.i >= T) return;
        for (s = 0; s < n; s++) if (keep[s] && M[s * T + p.i] !== wantm) keep[s] = 0;
      }
    });
    for (s = 0; s < n; s++) m += keep[s];
    var refused = m === 0 || m < d.min, teams = {};
    d.teams.forEach(function (t, ti) {
      if (refused) { teams[t] = null; return; }
      var po = 0, ch = 0, seeds = [0, 0, 0, 0];
      for (var s2 = 0; s2 < n; s2++) {
        if (!keep[s2]) continue;
        var r = d.rank[s2 * T + ti];
        if (r < d.spots) po++;
        if (d.champ[s2] === ti) ch++;
        if (r < 4) seeds[r]++;
      }
      var p = po / m, c = ch / m;
      teams[t] = { playoff: p, playoff_se: Math.sqrt(p * (1 - p) / m), champ: c, champ_se: Math.sqrt(c * (1 - c) / m),
                   seeds: seeds.map(function (k) { return k / m; }) };
    });
    return { n: m, total: n, refused: refused, teams: teams };
  }

  // ---- the picks the form holds
  function picks() {
    var out = [];
    new FormData(form).forEach(function (v, k) {
      var mt = /^([gm])(\d+)\.(\d+)$/.exec(k);
      if (!mt || v === '') return;
      out.push({ kind: mt[1], w: parseInt(mt[2], 10), i: parseInt(mt[3], 10), v: v });
    });
    return out;
  }

  // ---- the filters the server's page prints with (webui.render fpct / fse / fsigned)
  function pct(v, nd) { return (v * 100).toFixed(nd === undefined ? 1 : nd) + '%'; }
  function se(v) { var f = Math.abs(v * 100); return '± ' + (f < 1 ? f.toFixed(2) : f.toFixed(1)); }
  function num(k) { return k.toLocaleString('en-US'); }

  function update() {
    var ps = picks(), cur = ps.length ? conditional(ps) : base, live = ps.length > 0 && !cur.refused;
    res.dataset.n = String(cur.n);
    res.dataset.refused = cur.refused ? '1' : '0';
    var box = res.querySelector('.pm-count');
    if (box) {
      box.classList.toggle('refused', cur.refused);
      var p = box.querySelector('p');
      if (p) {
        p.innerHTML = !ps.length ? 'The odds across all ' + num(cur.total) + ' simulated seasons, ± one standard error. Pick results to see how they change.'
          : cur.refused ? '<b>' + num(cur.n) + ' of ' + num(cur.total) + ' seasons match</b>: too few to say. The odds need at least ' + data.min + ' matching seasons, and each pick keeps roughly half of them. Change a pick to see them again.'
          : '<b>' + num(cur.n) + ' of ' + num(cur.total) + ' seasons match</b> every pick. Each number is the share of those seasons, ± one standard error.';
      }
      var chips = box.querySelector('.pm-picks');
      if (chips && touched) chips.remove();
    }
    var head = res.querySelector('table.pm-table thead tr');
    if (head) {
      head.innerHTML = '<th>Team</th><th class="num">Playoffs</th>' + (live ? '<th class="num">Change</th>' : '') +
        '<th class="num">Title</th><th class="num">Seed 1</th><th class="num">2</th><th class="num">3</th><th class="num">4</th>';
    }
    res.querySelectorAll('table.pm-table tr[data-team]').forEach(function (tr) {
      var t = tr.dataset.team, x = cur.teams[t], b = base.teams[t];
      while (tr.cells.length > 1) tr.deleteCell(1);
      if (!x) {
        var dash = tr.insertCell(-1);
        dash.className = 'num dim';
        dash.colSpan = live ? 7 : 6;
        dash.textContent = '—';
        tr.dataset.playoff = '';
        tr.dataset.champ = '';
        return;
      }
      tr.dataset.playoff = String(x.playoff);
      tr.dataset.champ = String(x.champ);
      var c = tr.insertCell(-1);
      c.className = 'num';
      c.innerHTML = '<b>' + pct(x.playoff) + '</b> <span class="se">' + se(x.playoff_se) + '</span>';
      if (live) {
        var dlt = (x.playoff - b.playoff) * 100, cd = tr.insertCell(-1);
        cd.className = 'num' + (dlt >= 0.05 ? ' pos' : (dlt <= -0.05 ? ' neg' : ''));
        cd.textContent = Math.abs(dlt) < 0.05 ? '0.0' : (dlt > 0 ? '+' : '') + dlt.toFixed(1);
      }
      var ct = tr.insertCell(-1);
      ct.className = 'num';
      ct.innerHTML = pct(x.champ) + ' <span class="se">' + se(x.champ_se) + '</span>';
      x.seeds.forEach(function (sv) { var cs = tr.insertCell(-1); cs.className = 'num seed'; cs.textContent = pct(sv, 0); });
    });
    var note = res.querySelector('.pm-change-note');
    if (live && !note) {
      note = document.createElement('p');
      note.className = 'small muted pm-change-note';
      note.textContent = 'Change is in percentage points, against the odds with no picks.';
      res.appendChild(note);
    } else if (!live && note) {
      note.remove();
    }
  }

  form.addEventListener('submit', function (e) { e.preventDefault(); });
  form.addEventListener('change', function () { if (data) { touched = true; update(); } });
  form.addEventListener('reset', function () { setTimeout(function () { if (data) { touched = true; update(); } }, 0); });
})();
