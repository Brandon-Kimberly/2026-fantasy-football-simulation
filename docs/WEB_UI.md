# Web UI — design and phased backlog (scoped 2026-09-26)

**Status (2026-09-27).** W1, W2 and W3 are built on `feature/web-ui` — commits `2b429bf`
(read-only viewer, suite 1444→1480), `b9e5717` (tool launcher, →1513) and `b37bfec`
(engine runs, →1521) — each with the goldens 15/15, `check_test_isolation` CLEAN and
`scan_real_names` CLEAN, and each hand-verified against the real tree. All three are
**MINOR pending**: no tag, no version bump, no CHANGELOG entry yet — that is the owner's
release sitting, per `CLAUDE.md`. W4 stands as the decision recorded below; W5 is unscheduled.
Everything after this paragraph is the design as written before the code, kept so the
implementation can be judged against it.

This document is the design a session implements, written so that a model with less
context than the author can pick each phase up cold: what it is, what it builds on,
exactly what to change, exactly what *not* to touch, how to know it is done, and the
traps a careful-but-uninformed implementer would fall into. It follows the conventions of
`SCOPED_BACKLOG.md`.

The goal is a **localhost-only** web interface for reading this project's outputs and,
later, invoking its existing entry points. Single user, no auth, no deployment, no public
hosting. `CLAUDE.md` is binding throughout; nothing here overrides it.

---

## 0. The decisions, on one screen

| Question | Decision | Why (the constraint that forces it) |
|---|---|---|
| Does the web process import the engine? | **Never.** It imports an allowlist of import-safe modules and nothing else; a subprocess guard test pins this. | `fantasy_sim.simulation` truncates `data/current/syndicate_warnings.log` at import (F10). `fantasy_sim.decisions` imports it. A long-lived process that imports either clobbers a real file on every start. |
| How do tools run? | **As subprocesses of the exact CLI the owner already types** (`<this interpreter> -m scripts.<tool> …`, `cwd=root`). The UI is a launcher, not a second code path. | No new path into the engine means the golden argument is trivial and today's tool behaviour is preserved byte-for-byte. |
| CWD? | The server **never `chdir`s**. It binds one absolute `root` at construction and joins every relative `storage` path onto it through one `resolve()` helper. Subprocesses get `cwd=root`. | `storage.DATA_DIR = "data"` is CWD-relative. Process-global `chdir` is unsafe in a threaded server and untestable in-process. |
| Concurrency? | **One engine subprocess at a time**, enforced three ways: an in-process lock, a pid lock file, and a pre-launch scan for other engine processes. A non-zero exit or kill is reported **VOID**, never partial. | R1: a native memory fault under parallel load; a crashed run is void. |
| Real names? | Pseudonyms everywhere on disk, in URLs, and in logs. Real names are an **in-memory overlay fetched at startup** and substituted into response bodies only. | H1/F37: no real identity in any repo file, ever. F48: the opinion lives in the entry point, never the library. |
| Framework? | **Flask + Jinja2**, in `requirements-web.txt`, tests skipping cleanly when absent. | Autoescaping is a safety property here; a sync server matches a single-flight lock; the hypothesis/espn_api/pyyaml skip precedent already exists. |
| Sync from the browser? | **Not exposed.** The status page shows freshness and the exact terminal command. | C3: a sync with a dead key destroys real data with no restore, and the server's environment can hold a stale key. |
| Localhost? | Bound to `127.0.0.1`, **not configurable**, plus a `Host`-header check and a CSRF token on every POST. | A localhost server with no origin check is reachable from any web page the owner visits (DNS rebinding / CSRF). In this project that means an attacker-triggered engine run, or league data read out. |

---

## 1. What already exists that this builds on

Nothing below is new. The UI is a reader and a launcher over these surfaces.

**On disk, all under `data/` (blanket-gitignored except the named logs):**

| Location | What it holds | Written by |
|---|---|---|
| `data/current/*.json` | sync outputs: baselines, rosters, standings, schedules, Vegas, pending trades, `sync_manifest.json`, `league_state.json` | `scripts.run_sync` only |
| `data/weeks/week_NN/` | the engine's per-week exports: `live_season_forecast_week_N.json`, `syndicate_comprehensive_matrix_week_N.json`, `model_learning_report_week_N.json`, `syndicate_insights_week_N.json`, the audit log, `positional_tiers.json`, `player_variance.json`, `strength_of_schedule.json`, ~12 PNG charts, `tiers/`, `boom_bust/`, `floor_ceiling/` | `scripts.run_simulation`, the chart builders |
| `data/decisions/week_NN/` | **canonical** tool records and weekly digests (`.md` + `.html`); `archive/` beneath it holds everything exploratory (36 files in week 3 at scoping time) | the seven decision tools, `scripts.weekly_report`, `scripts.gameday` |
| `data/decisions/adhoc/` | `compare_*`, `move_*`, `trade_*` records (63 at scoping time) | `compare_players`, `evaluate_move`, `evaluate_trade` |
| `data/decisions/season/` | draft review, retrospectives | season one-offs |
| `data/results/week_NN/weekly-report-<run_id>/` | downloaded runner artifacts, localized in place | `scripts.localize_reports` |
| `data/logs/*.jsonl` | the append-only season logs (decision, predictions, bids, designations, FAAB adjustments, provenance, projections, first scores) | sync, the tools, the runner |
| `data/local/` | the owner's untracked private files: `env.sh`, `identity_map.json`, `owner_team_map.json` | the owner |

**In the library, import-safe (verified 2026-09-26: no top-level import of `simulation`,
`decisions` or `sync`):** `config`, `storage`, `freshness`, `run_windows`, `weekly_report`,
`positional_tiers`, `luck_ledger`, `odds_history`, `scorecard`, `player_variance`,
`data_health`, `pending`, `draft_review`, `league_chain`, `corrections`, `reprice`,
`player_ids`. Of these the UI needs:

- `weekly_report.real_names_enabled()`, `real_name_overlay()`, `localize_names(text,
  overlay, kind)`, `PRIVATE_MARKER` — the F37/F48 overlay machinery, exactly as the digest
  uses it. `localize_names` is idempotent (it checks for the marker), so applying it to an
  already-localized file is safe.
- `freshness.check(offline=True)` → `(status, reasons, details)`; `logs_git_state(...)`.
- `run_windows.load_kickoffs()`, `compute_windows(...)`, `watch_verdict(...)`.
- `positional_tiers._TABLE_CSS`, `_TABLE_JS` — the sortable-table pattern the digest reuses.
- `storage`'s path helpers, used **only for their relative strings** (see §2.2).

**Not import-safe, never imported by the web process:** `simulation` (the F10 handler),
`decisions`, `sync` (network + writes), and everything that imports them —
`behavior_check`, `bid_ledger`, `leverage`, `market`, `matchup_watch`, `roster_calendar`,
`season_retrospective`, `streamer_study`, `strength_of_schedule`, `swaps`. `storage`
imports `matplotlib.pyplot`; the entry point sets `MPLBACKEND=Agg` before any import
(precedent: `scripts/draft_review.py`).

**Existing HTML surface.** `scripts.weekly_report` already renders a full HTML digest
(`--embed` inlines the charts). The UI serves those files; it does not re-render reports.

---

## 2. Process model: a reader and a launcher

### 2.1 The web process never imports the engine

`webui/` is a top-level package beside `scripts/` — like `scripts/`, not registered in
`pyproject.toml`'s `packages`, so `fantasy_sim` stays free of web dependencies and the
coverage ratchet (scoped to `fantasy_sim/*`) is untouched. Entry point: `py -3.10 -m webui`.

The package imports from `fantasy_sim` **only the allowlist in §1**. The guard is a test,
not a convention:

> `tests/test_webui_imports.py` runs a **subprocess** (the suite's own process has already
> imported `simulation` thirty times over, so `sys.modules` in-process proves nothing) with
> `cwd` = a temp dir holding a sentinel `data/current/syndicate_warnings.log`. It imports
> `webui` and every `webui.*` module, then asserts (a) none of `fantasy_sim.simulation`,
> `fantasy_sim.decisions`, `fantasy_sim.sync` is in `sys.modules`, and (b) the sentinel file
> is byte-identical. A second test **characterises the hazard itself** — importing
> `fantasy_sim.simulation` in the same setup DOES truncate the sentinel — so the guard is
> shown to be testing something real, not vacuously passing. That characterisation is of
> documented behaviour (F10) and is not a defect to fix.

Write the guard **first** (rule 1): a `webui` that reaches for `fantasy_sim.decisions` to
compute a VORP fails it.

### 2.2 Root binding, not CWD

`storage.DATA_DIR = "data"` and every helper returns a relative path. The server:

- takes `root` (default: the checkout containing `webui/`, i.e. `Path(__file__).resolve().parents[1]`;
  `--root PATH` overrides it for a sandbox copy), resolves it to an absolute path once, and
  binds it to the app object;
- routes every file access through `resolve(rel)` = `os.path.realpath(os.path.join(root,
  rel))`, which **refuses** any result outside `root/data`, any path containing `..`, and
  anything under `data/local/` (that is where `env.sh` and the identity map live);
- serves only allowlisted shapes: `data/weeks/week_\d{2}/…`, `data/decisions/(week_\d{2}(/archive)?|adhoc|season)/…`,
  `data/current/<known basename>`, `data/logs/<known basename>`, `data/results/…`, with
  extensions in `{json, jsonl, png, html, md, txt}`. No directory listings of arbitrary paths;
  listings are explicit routes over explicit directories.

"A request can never be served against the wrong tree" is then a property of construction:
one app, one absolute root, one chokepoint, and a test that instantiates the app on a temp
root and asserts every route reads from it and nothing else (see W1 acceptance).

### 2.3 The import-time log truncation (F10)

`fantasy_sim/simulation.py` calls `logging.basicConfig(... FileHandler(SYNDICATE_WARNINGS_LOG_FILE,
mode='w') ...)` at module level. Two consequences the design absorbs rather than fixes:

1. The **web process** must not import it (§2.1). It also must not import anything that
   configures the root logger differently; Flask's own logging is left at defaults.
2. **Tool subprocesses** import it, so each tool run overwrites that mirror log in `root`
   — which is exactly what happens today when the owner runs the same tool by hand. The
   UI does not change this and the doc says so on the status page ("this file holds the
   last process that imported the engine; per-run warnings are in the audit JSON").

Do not "fix" `simulation.py`'s logging. It is engine-adjacent, documented, and out of scope.

### 2.4 Tools run as subprocesses — today's CLI, exactly

Every tool invocation is `[sys.executable, "-m", "scripts.<tool>", *args]` with
`cwd=root`, `env=os.environ.copy()`, `shell=False`, stdout+stderr to a job log. Rules:

- `sys.executable` of the web process, which must itself be Python 3.10 (assert at
  startup; refuse to run on anything else). This closes the `python` → Store 3.8 trap.
- The tool name comes from an **allowlist** (per phase, §4); arguments are built from a
  per-tool form schema into an argv list — free-text values (player names) are single argv
  items, never interpolated into a shell string.
- No tool is ever launched with `--canonical` unless the form has an explicit, unchecked-by-
  default box and the page shows the `run_windows` verdict beside it. Canonical is a
  deliberate act (the `decisions_week_path` docstring), not a default.

Because tools write only under `data/decisions/` (verified 2026-09-26 for all seven tools,
`matchup_watch`, `roster_calendar`, `gameday`, `compare_players`, `evaluate_move`) and never
under `data/current/`, a tool run cannot damage sync outputs. `gameday` calls
`os.startfile` to open a browser; the UI does not launch `gameday` (it *is* the browser).

### 2.5 Concurrency: one engine process, enforced three ways (R1)

Simplest safe rule, and the one adopted: **every tool subprocess runs under the same
single-flight lock**, whether or not it is "heavy". Classifying tools by sampling depth
would be a second place the R1 assumption lives; the lock is the only place.

1. **In-process:** a `threading.Lock` acquired non-blocking around `Popen`; a second
   request while held gets HTTP 409 and a page saying what is running and since when.
2. **Cross-process:** `data/local/webui/engine.lock` created `O_EXCL` with `{pid,
   started_at, argv}`; stale if the pid is dead. Covers a second server instance.
3. **Pre-launch scan:** before `Popen`, list running interpreters whose command line
   contains `scripts.` or `fantasy_sim` (via `tasklist`/`wmic` on Windows, `ps` elsewhere;
   `psutil` optional). If any is found — including the owner's own terminal run, or the
   test suite — **refuse** and show the list. This is heuristic and says so.

**VOID semantics.** A job that exits non-zero, is killed, or whose lock is found stale on
server restart is shown as **VOID** with the R1 sentence verbatim ("a crashed run is void;
re-run alone"), never as "finished with errors". The job page must never re-launch on
reload (POST → redirect → GET).

**During a suite or golden run the server must be down or idle.** The pre-launch scan
refuses in that direction; nothing can refuse in the other (a test run does not know about
the server). Document it on the status page and in README.

### 2.6 Real names: a runtime overlay, and nothing on disk

- `webui/__main__.py` mirrors `scripts/weekly_report._default_to_real_names()`:
  `os.environ.setdefault("SHOW_REAL_TEAM_NAMES", "1")` unless `GITHUB_ACTIONS`. The library
  default stays OFF (F48), so the suite never reaches the network.
- At startup the app calls `weekly_report.real_name_overlay()` once and keeps the dict **in
  memory**. Failure → empty overlay → pseudonyms (the library's own behaviour). An
  optional offline fallback may read `data/local/identity_map.json` the way
  `scripts.localize_reports.real_mapping()` does; it is read, never copied.
- **URLs, form values, query strings, job argv, job logs and server access logs carry
  pseudonyms only.** Team selectors post the fictional name; the overlay is applied to the
  rendered body (`localize_names(html, overlay, kind="html")`, or a Jinja filter for
  JSON-derived tables). Werkzeug's access log prints request lines — pseudonymous by this
  rule.
- No template cache on disk, no static build step, no server-side page cache, no
  screenshot/export feature that writes HTML anywhere under the repo. If an "export" is
  ever wanted it writes under `data/local/` only and `resolve()` refuses to *serve* it back
  as a repo path.
- Tests: with the flag unset, every route renders pseudonyms and no `PRIVATE_MARKER`;
  with a **fake overlay injected** (no network — patch `real_name_overlay` to return a
  fictional→fictional map), the marker appears and substitution happens; and
  `scripts.check_test_isolation` stays clean. `scan_real_names` runs before every push.

### 2.7 Secrets and identifiers

`SLEEPER_LEAGUE_ID`, `ESPN_LEAGUE_ID`, `SLEEPER_LEAGUE_ID_2025`, `SLEEPER_LEAGUE_ID_2024`,
`ODDS_API_KEY` are inherited by subprocesses and **never** read for rendering. Test: start
the app with canary values in those variables and assert no response body, header, or job
log line contains any canary. `data/local/env.sh` is never parsed. `data/local/` is never
served (§2.2).

### 2.8 Localhost as an invariant, not a default

- `app.run(host="127.0.0.1", port=…)`; there is no `--host` option and a test asserts the
  CLI rejects one.
- Every request's `Host` header must be `127.0.0.1:<port>` or `localhost:<port>`; anything
  else is 400. This defeats DNS rebinding, which is otherwise a real read path to league
  data on a localhost service.
- Every POST carries a per-launch CSRF token (random at startup, embedded in forms,
  checked server-side). Without it a page the owner happens to visit can launch an engine
  run.

---

## 3. Framework and dependencies

**Flask + Jinja2** (Werkzeug's development server, `threaded=True`), pinned in
`requirements-web.txt`. Not in `requirements.txt`: that file is pinned exactly because the
goldens are byte-locked to its numeric stack.

Why Flask and not the alternatives, against *this* project's constraints:

- **stdlib `http.server`**: no autoescaping. Player names contain apostrophes and real
  team names are arbitrary user-chosen strings substituted into HTML at response time; hand-
  escaping every interpolation is exactly the bug class Jinja exists to remove. No test
  client, no routing; W2's forms and W3's job pages would re-implement Flask, worse.
- **FastAPI/uvicorn**: async buys nothing for one user, and pydantic + uvicorn are a
  larger dependency surface than Flask. A sync request model matches a single-flight lock.
- **Django**: far too heavy for six pages and a launcher.

**Test policy** follows the existing optional-dependency precedent (`hypothesis`,
`espn_api`, `pyyaml`): `tests/test_webui_*.py` probe `import flask` and `skipTest`
cleanly without it, so `py -3.10 -m unittest discover tests` stays runnable from a bare
`requirements.txt` install. CI installs `requirements-web.txt` so the web tests run there;
`ci.yml`'s ruff step adds `webui` to its paths. The expected local verdict changes from
`skipped=1` to `skipped=1+N` when Flask is absent — document it beside the espn_api note.

**The browser layer (2026-09-28, roadmap UI-E1).** Presence tests pin a script by its
text in the served page, which is why the Decisions filters shipped broken: the script was
right and a class's `display` beat the browser's rule for `hidden`. `tests/test_webui_browser.py`
serves the fixture tree on a loopback port and drives the installed Edge through Playwright,
asserting on what is DISPLAYED. Run against the templates before `b36f933`, its filter test
fails in both views with the original defect ("4 not less than 4"); on the fix it passes.
Any new script behaviour gets a test here, not only a presence pin.

---

## 4. Phased backlog

Each phase is independently shippable to `main` as a **MINOR** release (capability added,
goldens byte-identical — and the goldens are still run, because rule 6 does not care that
the diff "obviously" cannot move them). Every phase's done-when includes:

- full suite before and after with the count; **README.md (count + badge + module
  count) and CLAUDE.md (count) updated in the same commit** — `tests/test_docs` pins both;
- `py -3.10 -m tests.test_golden_master` 15/15; `py -3.10 -m tests.golden_sync` unchanged;
- `py -3.10 -m scripts.check_test_isolation` clean;
- `py -3.10 -m scripts.scan_real_names` clean (before the push, with `SHOW_REAL_TEAM_NAMES`
  set in that shell);
- ruff clean on `fantasy_sim scripts tests webui`;
- CHANGELOG headline, `pyproject.toml` version, `CITATION.cff` version **and date** — the
  docs guards pin all three to the tag.

### W1 — Read-only viewer (MINOR)

**Scope.** Zero engine invocation, zero writes to `root`. Pages:

- `/` **status** — `freshness.check(offline=True)` verdict and reasons; the sync manifest's
  `degraded` list; `run_windows` windows and coverage (`load_kickoffs` falls back to a live
  ESPN fetch only when the synced schedule lacks kickoffs — acceptable, and shown as such);
  `logs_git_state` (uncommitted / unpushed log rows); the exact terminal commands for sync
  and a canonical report; the R1 sentence.
- `/weeks`, `/weeks/<NN>` — the forecast table from `live_season_forecast_week_N.json`
  (banked record and points, expected final wins, playoff probability ± SE, magic number),
  every chart as `<img>`, links to the JSON exports, the audit log's warnings list.
- `/decisions/<NN>`, `/decisions/adhoc` — canonical records and digests, then `archive/`,
  grouped by tool and sorted newest-first; `.html` served inline, `.md` as preformatted
  text (no Markdown dependency in W1), `.json` pretty-printed.
- `/current` — standings, rosters (name, position, NFL team, `mean`, designation from the
  baselines — **no VORP: replacement levels need the engine**, see §5), pending trades with
  the T3 advisory note, FAAB remaining.
- `/logs/<name>` — the last N rows of each tracked log, newest first, with the overlay.
- `/file/<path>` — raw allowlisted files (§2.2).

**Order of work (rule 1).** (1) `tests/test_webui_imports.py` — the two subprocess tests
of §2.1, red because `webui` does not exist. (2) `webui/` skeleton: `__main__.py`,
`app.py` (factory `create_app(root, overlay=None)`), `paths.py` (`resolve`, allowlists),
`names.py` (overlay + filter), `requirements-web.txt`. (3) Pages, each with a route test
on a temp root populated from `tests/fixtures/`. (4) README section "Web UI (local)",
CLAUDE.md commands block, ci.yml install + ruff paths.

**Done when.** The §4 common list, plus: the import guard is green and its characterisation
twin is green; a test walks every route against a temp root and asserts (a) the tree is
byte-identical afterwards, (b) no `data/local/` path is reachable, (c) a `..` path is 400;
the canary-secrets test (§2.7) passes; the `Host`-header test passes; `--host` is refused;
with the flag unset no response contains `PRIVATE_MARKER`, with a fake overlay every
pseudonym is substituted; `py -3.10 -m webui` on the real tree serves week 3 and the
week-3 canonical digest, checked by hand once.

### W2 — Tool launcher (MINOR)

**Scope.** The job runner (§2.4, §2.5) and forms for the tools that read `data/current/`
and write only their own record under `data/decisions/`:

| Tool | Notes |
|---|---|
| `optimize_lineup`, `matchup_lineup`, `waiver_targets`, `roster_grades`, `find_trades` | week tools; `--canonical` behind the explicit box |
| `compare_players` | runs a reduced simulation — same lock, no special case |
| `evaluate_trade`, `evaluate_move` | two paired **full** simulations; minutes; same lock |
| `matchup_watch`, `roster_calendar`, `live_matchup`, `trade_leverage` | read-only briefs |
| `check_freshness` (online), `run_windows --json`, `odds_history`, `luck_ledger`, `decision_scorecard`, `data_health`, `bid_review` | status/measurement tools |

Excluded on purpose: `gameday` (opens a browser), `run_sync` (W4), `run_simulation` and
`weekly_report` (W3), every backtest and study (milestone tools, not weekly ones),
`migrate_identity`, `localize_reports --fetch` (needs `gh` auth), `scan_real_names`.

**Job model.** `data/local/webui/jobs/<id>/meta.json` + `stdout.log`; `/jobs/<id>` shows
state (RUNNING / OK / VOID), elapsed, the log tail, and — parsed from the tool's own
`logged -> …` line or the newest file it wrote — a link into W1's decision browser.

**Done when.** The §4 common list, plus tests that: two concurrent launches of a fake tool
yield one RUNNING and one 409; a fake tool exiting non-zero shows VOID and the R1 sentence;
a stale lock file with a dead pid is reported VOID on startup; every launch has `cwd ==
root` and `shell is False` (patch `subprocess.Popen` and inspect the call); the argv for
each tool form is an exact list (no string joins) for a name containing a space and an
apostrophe; the tool allowlist rejects an unlisted module name; the CSRF token is required
on every POST; job logs never contain a canary secret; `optimize_lineup` launched from the
UI on the real tree writes exactly one record under `data/decisions/week_NN/archive/` and
nothing else changes (`check_test_isolation --dir data` pattern, run by hand).

### W3 — Engine runs (MINOR)

**Scope.** `scripts.run_simulation` and `scripts.weekly_report --skip-sync [--full]
[--embed] [--evaluate N]` through the same job model. The launch page shows the
`run_windows` verdict, the freshness verdict, and refuses to launch when freshness is STALE
(the orchestrator would refuse anyway; the UI says so first). `--canonical` behind the
explicit box, with the window name it would be filed under.

**Done when.** The §4 common list; the W2 lock tests re-run against these two tools; a test
that a reload of `/jobs/<id>` never re-launches; a hand-run `weekly_report --skip-sync`
from the UI produces a digest that W1's browser lists and renders, and the
`syndicate_warnings.log` note on the status page is accurate after it.

### W4 — Sync: not exposed (decision, not a phase)

`scripts.run_sync` rewrites every file in `data/current/` and is the only writer of the
model's inputs. H5 makes a *rejected* key (401/403) stop before writing, but the server
inherits its launching shell's environment, which on this machine has held a pre-rotation
key; a fetch that fails for any non-401 reason still degrades to the flat fallback, which is
C3's data-destroying path. Reading the User-scope registry value from the server to inject
a fresh key is possible (`winreg`, HKCU\Environment) and would make the UI the *only* place
that logic lives — a second copy of an H5 decision, which this repo's rules reject.

Decision (2026-09-24): the status page shows freshness and prints `py -3.10 -m
scripts.run_sync` with the H5 note. Revisit only if the owner asks, and then as its own
scoped item with the registry read specified.

**Reopened 2026-09-27 at the owner's request** ("three trades were just processed; the
rosters and everything downstream are stale"), with the registry read specified and two
more safeguards, as `webui/sync.py` and the Sync page:

1. *Preflight.* The key is read from the Windows User scope (`winreg`,
   HKCU\Environment) first and this process's environment only as a fallback; the page
   says which. It is probed against the-odds-api (the same verdicts as
   `fantasy_sim.sync.verify_odds_key`, copied because that module is one the web
   process must never import) and the launch goes ahead only on `ok`. Rejected, absent
   and unreachable all stop with nothing written and nothing launched; the fallback
   sync stays a deliberate terminal act (`--allow-fallback`). The page renders without
   probing -- the probe spends an API request, so it runs only on launch.
2. *Backup.* Every file in `data/current/` is copied to
   `data/local/webui/backups/<stamp>/` (local, never served, never tracked) with a
   manifest before the launch; a Restore button copies one back, refused while a job
   runs; the ten newest are kept. This is the restore C3 never had.
3. *Injection.* The verified key reaches the child through its environment only
   (`JobRunner.launch(env=...)`); it never enters the job record, the log, or a page.
   The sync re-verifies it itself (H5), so a stale shell value cannot be used twice.

Two modes: *Sync* (`scripts.run_sync`) and *Sync & refresh* (`scripts.weekly_report`
WITHOUT `--skip-sync`, the repository's primary entry point exactly as a hand run:
non-canonical, filed under the week's archive). The tool registry still does not offer
`run_sync`; the guarded page is the only way. What a sync touches: `data/current/`
(rewritten by design) and the rows it APPENDS to the season logs; never a log rewrite,
never `data/weeks/`, `data/decisions/` or the predictions log.

### W8 — Two views: dev and simple (2026-09-27)

The owner's words: the site is a developer's view now -- files, jobs, logs, syncs,
verdict codes -- and "if other people used this they would never want to know any of
that"; long term the engine is hosted somewhere and the UI is simple enough for anyone.

One codebase, two views, every page rendered in both:

- **dev** -- everything W1--W7 built: every file and raw link, the jobs list, logs,
  System, Sync, Records, the engine runs, every tool with every setting, the command
  preview, the audit codes in method notes, the header's localhost/real-names line and
  the private banner.
- **simple** -- Home, League, Forecast, Decisions, and the tools that answer a
  manager's question (`webui.tools.SIMPLE_TOOLS`), asking only who and when. A tool's
  answer (the job page) is for everyone but shows no command, log, exit code or job id;
  a record opens as tables with no raw JSON; states read Running / Done / Didn't
  finish; method notes lose their audit codes (`render.simplify`); no page names a
  file, a sync, the engine, or a verdict code.

The mode is server-side (`webui.settings`, data/local/webui/settings.json) so every
request agrees; the footer toggles it through one POST route (`/mode`, CSRF), and
`py -3.10 -m webui --mode simple` sets the default before a setting exists. Today the
server is localhost-only, so "only the owner can switch" is true by construction; when
the engine is hosted for other people, `/mode` is the one route to put behind the
owner's login -- everything else already keys off the stored mode.

**The rule, from here on: every change to the UI is made for both views.** It is
enforced, not remembered: `tests.test_webui_modes` renders every simple page on the
full fixture tree and fails on any developer vocabulary (`DEV_TERMS`) in the visible
text, and checks the dev-only pages and tools stay 404 in the simple view. A new page
goes into `SIMPLE_PAGES` or `DEV_ONLY_PREFIXES`; a new tool into `SIMPLE_TOOLS` or
not; a new sentence with a file name, a code or the word "sync" goes inside
`{% if dev %}`. Macros are imported `with context` so `dev` reaches them.

### W9 — A local name, and the vibrant layer (2026-09-27)

**`http://syndicatefootball.local/` on this machine.** The bind stays 127.0.0.1 (section
2.8 is unchanged: there is still no `--host`, and argparse abbreviations are off so
`--host` cannot alias `--hostname`); what changes is the Host check, which accepts a
name the owner configures on top of the two built-in ones. Two steps, both local:

1. Once, as Administrator, point the name at the loopback in the hosts file:
   `Add-Content C:\Windows\System32\drivers\etc\hosts "127.0.0.1 syndicatefootball.local"`
2. Start the server with the name (and port 80 to drop the `:8765`):
   `py -3.10 -m webui --hostname syndicatefootball.local --port 80`

`.local` rather than the bare word because browsers treat a single label as a search
term; a hosts-file line is the only resolution, so nobody else's machine can reach it
and the DNS-rebinding defence holds (an unconfigured name is still refused with 400).

**The vibrant layer.** Gradient tokens (`--g-brand`, per-colour gradients, glows, one
shadow) drive: a gradient brand mark and h1s, a sticky blurred header with an animated
gradient underline on the active tab, two soft colour glows behind the page, tiles with
a coloured corner disc that lift and glow on hover, gradient pills for the live
states, gradient buttons (pill-shaped, glow on hover, press feedback), gradient bars,
medal ranks (gold/silver/bronze) in every standings table, chart areas that fade
through an SVG gradient and lines that draw themselves in with dots that pop, a hero
with a drifting violet glow, a gradient ring and split bar, and a red live badge. Every
motion sits under `prefers-reduced-motion: reduce`, and both views carry the same
layer (tests.test_webui_modes pins it in each).

### W5 (optional, later) — Sandbox root

`--root` already allows serving a copy. A "sandbox run" button — copy `data/` to a temp
root, run a tool or the report there, show the result, discard — is the exact technique
`tests/golden_sync.py` and this session's non-canonical report used, needs no engine
change, and would let the owner ask "what if I add X" without touching production data.
Scoped separately when W3 has been used for a few weeks.

### W6 — Live scoreboard (built 2026-09-27; read-only, off-disk)

The landing page shows the points banked so far in my matchup, each starter's game
state, and an updated chance to win, with a refresh button and an auto-refresh toggle
(every three minutes, remembered per browser). `webui/live.py` reads two public
endpoints -- Sleeper's matchups for the week and ESPN's scoreboard -- and holds the
result in the server process's memory (`LiveBoard`, one snapshot, never more often
than 45 s). It writes nothing: not under `data/`, not to a log, not to a cache file.
The engine reads `data/current/` (written only by sync) and the season logs (written
only by the tools), so the model's inputs, its predictions log and its later evaluation
are untouched by any number of refreshes -- `tests/test_webui_live` asserts the tree
digest is identical across repeated refreshes. Page renders never fetch; only
`/api/live` does, and the default board is disabled on a runner or without a league id.

The number it quotes is a quick estimate and says so on the panel: banked points plus
the unplayed fraction of each starter's pre-game expectation (this week's lineup or
matchup record where one exists, else the baseline mean), Normal-approximated. F50 pins
the engine's own live number to `scripts.live_matchup`, which imports the engine; the
panel links to that tool, and the three small formulas it shares are copied with their
docstrings rather than imported.

Team avatars (owner's eyes only) ride the same path as real names: `Overlay.avatars`
is fetched in memory when real names are on, rendered as `<img src>`, never written,
and off whenever the overlay is off.

### W7 — Readable addresses, the Decisions tab, VORP on the League page (2026-09-27)

Every list links to a readable address and the file paths keep working underneath:
`/records/week-3/optimal-lineup/2026-09-24-165331` (archive runs under `/archive/`,
compare records keep the A-vs-B, digests keep the run name and end in `/markdown` for
the md twin), `/jobs/optimal-lineup/2026-09-26-000000-000001`, `/logs/decision-log`,
`/forecasts/week-3`, `/league`. One function (`render.pretty_url`) builds them and the
resolver matches an entry by the same function, so there is one mapping to keep.

The **Decisions** tab joins the decision log to itself: each `type` row (a move) to the
`record_type: evaluation` row `evaluate_move --evaluate-unevaluated` wrote for it, by
transaction id. It shows every move with its effect on the team that made it (playoff
and title percentage points, ± the batch spread), sums each team's own moves into a
ledger, and charts my cumulative effect. Nothing is computed that the tools did not
write; a skipped evaluation (roster drift) says so rather than showing a number.

The League page's VORP comes from the newest `roster_grades` record that carries
`rosters` -- `scripts.roster_grades` writes per-player detail for every team since this
date -- never from a replacement level recomputed in the UI (that would be a second
definition of the engine's number). Until one has been run the column says so.

The `--json` tools (live matchup, luck ledger, odds history, data health, bid review,
run windows, decision scorecard) always run `--json` from the UI and their document
renders as tables on the job page; the text form is one click away in the log.

### W10 — Charts v2 (2026-09-28)

One chart component, `render.line_chart`, draws every line chart in the UI, and the
templates pass it series and labels only (the `linechart` macro that did its own maths
is gone). It picks round ticks (`nice_ticks`), keeps x labels from colliding while
always writing the first and last, writes a run of identical labels once, leaves
headroom for the last value label, and emits the data (`data-labels`, `data-xs`, each
polyline's `data-vals` and colour) that the hover script in `base.html` reads to draw a
crosshair and a tooltip naming every series at the nearest point. A series may carry a
team `hue` (from `glance.TEAM_HUES`) and then draws in that colour at a lightness token
(`--line-l`) set per theme; end labels of a race dodge each other and wear the team's
colour, with a surface-coloured halo so they read over a line. Pass the width the card
will render at — a 640-wide drawing scaled into a 330 px card is the unreadable-text bug
B14 named. `render.sparkline` is the table-cell version.

The Forecasts page draws the playoff-odds and title-odds races from `glance.odds_race`
(every team's odds across the season's forecast exports, ordered by the latest, mine
marked), and the home standings show each team's odds sparkline and its move in odds
rank since the previous week's forecast. Both views; nothing new is computed — the
numbers are the exports' own.

### W11 — Live and game day (2026-09-28)

**The job bar.** Every page checks `runner.current()` at render; while a job runs, a
thin animated bar sits at the top and the page polls `/jobs/<id>.json` every three
seconds until the state changes, then shows a toast — "Open the answer" for OK, "See
what happened" for VOID. No job, no polling. Both views.

**The next kickoff.** `glance.kickoff_report` reads the kickoffs the sync persisted
(`nfl_schedule._meta.kickoffs`) and the hero says when the next one is, how many games
start then and how many are still ahead; the browser ticks the countdown. Never a
network read — no kickoffs on disk means no countdown.

**Head-to-head.** `glance.h2h_report` finds every meeting with this week's opponent:
this season from `league_schedule.json` paired with `weekly_actuals.json`, earlier
seasons from `data/logs/season_<year>.json` (roster map + matchups). The record, the
last result and the full list; nothing is computed that those files do not hold.

**Live v2.** The snapshot now carries `games` (every NFL game's teams, scores, clock and
state) beside the two rosters, and `LiveBoard` keeps a bounded in-memory `history` of
the win probability, projections and time of each read, reset when the week changes.
It is still memory only: W6's guarantee that a refresh writes nothing under `data/`
holds (tests.test_webui_eighth digests the tree around a run of refreshes). The panel
draws game-clock chips, the day's line, a swing when the number moves, and confetti in
the team's colour only on a final win — every starter on both sides played, and ahead.

**`/gameday`.** A standalone dark page for a TV: the two banked scores large, the chance
to win, every starter with a chip, every game with its score and clock, refreshed each
minute through `/api/live?refresh=1` (the board's 45 s floor still applies). It is a page
in both views, not a third view; the toggle and the mode gate are unchanged.

### W12 — The power features (2026-09-28)

**Theme.** `settings.json` carries `theme` (system / light / dark) beside `mode`; `/theme`
is the one POST route and every page stamps the choice on `<html data-theme>`. The dark
tokens live twice in `base.html` — under the OS media query, guarded with
`:root:not([data-theme="light"])`, and under `:root[data-theme="dark"]` — and
tests.test_webui_ninth parses both blocks and fails if they differ. Style through the
tokens, never inside either block.

**Install as an app.** `/manifest.webmanifest` and `/icon.svg`; the browser's install
prompt gives the local name its own window and icon. Both views.

**Palette and shortcuts.** The context processor hands every page `palette`: the pages
of THIS view's nav, the tools this view may launch, the teams — so the simple view's
palette cannot reach a developer page. Ctrl/⌘-K opens it; `compare A vs B` typed there
opens the compare tool pre-filled (its fields are `a` and `b`). `g` then a letter jumps
between pages; `?` lists them. Keys are ignored while typing in a field.

**Player cards.** Any cell with `data-player` (the datatable macro's player column, the
League rosters) shows a card after a short hover, from one `/api/player` read: position,
NFL team, owner, projection, bye, status, and VORP when the newest roster_grades record
carries the player; "Compare with…" opens the compare tool with A filled.

**Awards and timelines.** `decisions_report` adds `awards` (the (move, team) pair with the
largest and smallest playoff effect, season and latest week) and `timelines` (each
team's own moves added up in order). Sums of the tools' numbers, nothing new.

**Durations.** The Jobs page draws the last twelve finished runs per tool as a line.

### W13 - The home page, in the owner's own words (2026-09-28)

Six things the owner asked for, and what each turned into.

**What is true NOW leads.** The hero's big number is the pre-game probability only until
the first live read lands; then it becomes the live one, its label reads "to win, now",
and the pre-game figure moves into the footnote line beside the expected totals. One
trap worth knowing: the layout's count-up animation runs AFTER a page's own script, so
anything that rewrites a `data-count` element on load must drop the attribute first or
the animation walks the number back to what the server rendered.

**Both bars, one scale.** The two matchup bars are drawn against the larger of the two
projected totals rather than each against its own, so their lengths compare. Two bars of
identical length for different point totals was the bug.

**Every starter, against his projection.** Each row carries the pre-game projection, the
points so far, and the gap between them -- measured against the share of his game that
has actually been played (`expected x (1 - frac)`), so a man at half-time is judged on
half a game. Above, below and not yet started are three different colours, and the two
rosters sit side by side on one fixed column set.

**A scoring feed.** `LiveBoard` keeps the previous snapshot; `live.diff_updates` turns
two consecutive reads into one entry per starter whose points moved, and
`live.stat_parts` prices the change in his stat line with the LEAGUE'S OWN scoring
weights -- "Jalen Coker +3.4 (+2.9 29 rec yds, +0.5 catch)". Two reads are needed, so the
feed starts empty and fills as the day goes. Its sources are one more Sleeper endpoint
each refresh (`/stats/nfl/regular/<season>/<week>`, the season from the sync manifest)
and the league object once per process for the weights; either failing costs the
breakdown, never the feed or the snapshot. Still memory only -- nothing under `data/`.

**The season card.** The ring sat alone in a box as tall as the chart beside it, and the
first fill -- bars under two of the numbers -- was worse than the gap: `.mini` is a bar
component that had no `display` of its own, so outside a flex parent it laid out as an
inline box with no height and its fill escaped the track. It is a block now, wherever it
is used.

What the card carries instead is the one graphic it was missing: **where I finish**, the
finishing-seed distribution from the week's export as a single stacked bar, shading away
from the top seed, with the playoff cut marked and a legend giving each seed its share
and the chance of missing. `glance.seed_report` READS the cut off the numbers rather
than assuming a league size: the running total of the seeds meets the forecast's own
playoff probability at the number of spots (45.34 + 26.19 + 15.50 + 7.05 = 94.08 against
a stated 94.1, so four). If the two never agree to within a point -- a different format,
a partial export -- no cut is claimed and the whole distribution is drawn in one neutral
colour. Under it sit four plain figures with no bars at all: title odds, expected wins,
to clinch, banked.

**Width.** The column widens to 1440 px at viewports of 1500 px and up. This is read on a
4K monitor; the narrow breakpoints are untouched, so the phone layout is still there for
when it matters.

### W14 - What the model would change (2026-09-28)

The first thing in this UI that asks for a decision instead of reporting one. The
live read already knows the starters Sleeper has me fielding; the newest
optimal-lineup record already knows the ones the tool would field; nothing compared
them. `live.lineup_plan` reads the record, `live.lineup_diff` is the pure comparison,
and the result rides on the snapshot as `plan`.

It is a set difference with the points at stake attached, and one piece of honesty on
top: a man whose NFL game has kicked off cannot be moved, so he is reported and marked
`locked` rather than advised, and `actionable` is true only when there is at least one
unlocked man to start AND one to bench. The benched man is priced at the RECORD'S
number for him where it has one -- that is the number the advice is being measured
against -- with his live row as the fallback. Agreement is silence: when the two slates
match, the panel does not render at all.

The comparison can only live here. `live_rosters.json` carries rosters but no starters,
so the lineup I am ACTUALLY fielding is knowable from the live read and nowhere else;
that is why the panel needs the live scoreboard connected, and why it says nothing
when the optimiser has not been run for the week.

### W15 - What changed since the last sync (2026-09-28, U2)

The Sync page already took a full copy of `data/current/` before every sync it
launched. `sync.changes` reads one of those copies back and says what moved: who
changed hands (added, dropped, or traded from one roster to another), who picked up or
cleared a designation, whose projection moved by half a point or more, and what the
standings did. Rostered players only for projections, biggest movers first, capped --
the free-agent pool moves every sync and is noise.

The backup is read straight off disk, because the path chokepoint refuses `data/local/`
by design; the live side goes through the root like everything else. Nothing is
written and nothing is recomputed -- a sync that changed nothing shows nothing, which
is what it says against a tree whose week is already over.

The newest backup is the default comparison point, and every row of the backups table
offers itself as an alternative (`/sync?from=<name>`), so "what did I miss while I was
away" can span several syncs rather than only the last one.

### W16 - The kickoff alert (2026-09-28)

The one place this UI speaks first. A checkbox beside auto-refresh asks for the
browser's notification permission (localhost is a secure context, so it is available
over plain HTTP), and from then on the countdown that already ticks in the page fires
one notification thirty minutes before the next kickoff: how many starters are
questionable, and whether the model would field a different lineup and by how much.

It is opt-in, it is remembered in `localStorage`, and a fired alert is recorded against
its own kickoff so a reload cannot repeat it. It only works while a page is open --
there is no service worker and no server-side push -- and the control's own tooltip
says so rather than letting the owner assume otherwise.

### W17 - Accuracy: the model's own track record (2026-09-28)

The project's claim is that every probability is a count and the success criteria were
hashed before a game was played. Nothing in the UI ever showed whether the
probabilities came true. `/accuracy` does, from two files already on disk: the
predictions log and the weekly actuals.

Two rules keep it honest rather than flattering. Only the QUOTED forecast is scored --
the newest canonical `week_predictions` row logged BEFORE the week's first kickoff,
because a row logged after the games began knows too much; a week whose only canonical
row came later is skipped and SAID to be skipped, never quietly replaced. And a week
with no result yet is not scored at all.

What it counts: Brier and hit rate on the matchup calls (a true 50/50 is not counted as
a call); bias, mean absolute error and z on the points, where z divides each miss by the
spread the record itself stated (`sd_total`), so the dispersion figure is the model's
own claim being checked rather than one invented here; and the same treatment for the
beat-the-median calls. `ENOUGH_WEEKS` is 5 (F25: first measurable at weeks 5-6) and
until then the page leads with how thin the sample is and calls the numbers counts
rather than conclusions.

It is the OWNER'S page: the model's own report card is dev-only, out of the simple view's
navigation and refused by the mode gate like every other developer page.

### One rule for hiding things (2026-09-28)

Scripts here hide rows by setting the `hidden` attribute, and the browser's own rule
for that (`[hidden] { display: none }`) loses to any class that sets a display. The
Decisions filters set `hidden` on rows styled `display: grid`, so clicking a filter
changed nothing on screen; the same bug had already been patched four times, once per
component, for the palette, the shortcut sheet, the player card and the toast. There is
now one rule in `base.html` -- `[hidden] { display: none !important; }` -- and those four
patches are gone. A page that hides something does not have to remember this again.

### Accessibility, export, and small fixes (2026-09-28, roadmap UI-V7, R4, F3, F9, F11, W2, T5, P7)

- **Keyboard and screen readers (UI-V7).** Sortable headers take focus and sort on Enter. The
  player card opens on keyboard focus and Escape closes it. Every line chart carries a "view as
  table". League's roster names are player-page links.
- **CSV (UI-R4).** Every sortable table downloads its rows as shown.
- **Small fixes.**
  - A lines fetch after most of the week's kickoffs reads "few left … expected", not "failed" (F3).
  - "No NFL team" replaces a bare dash (F9).
  - Compare's week is a pick-list (F11).
  - Home shows the next waiver run (W2) and, in weeks 9–11, the trade-deadline countdown (T5).
  - An injury and practice report comes straight from Sleeper's cache, on Home and every team
    page (P7).
- **Deferred on purpose.**
  - Cacheable static assets (E2): moving the shared CSS and script out of base.html would break
    the many tests that read them in place, for a gain a local server does not feel.
  - Lighter long pages (E9): League and Players are about 190 KB and render in under 0.1 s.

### History, the draft board, and the week in review (2026-09-28, roadmap UI-H1/H2/H3, R1/R2)

- **`/history` (`webui/history.py`).** A record book, my record against every team, and a
  rivalry grid, all from **regular-season games only**: the 2025 archive cannot tell the
  championship path from consolation games, and a median game is never a head-to-head result.
  2026 results are as the league counted them. Every game in a week Sleeper now re-scores is
  marked, not only games whose result flipped (`results.rescaled_weeks`); the team pages and
  week cards say "re-scored points" too.
- **`/draft`.** Each season's picks, round by team, coloured by position, with the player's
  average now and whether the drafting team still has the player. It is labelled hindsight,
  not a grade.
- **The week in review (`webui/recap.py`) on a played week's Matchups page.** Awards, the odds
  movers, the best-graded move, and "who had the week". The surprise is measured against the
  league's average surprise that week, so the model's bias does not take the prize. It uses no
  luck language and no combined score.

### Game day, the trade builder, and League's second pass (2026-09-28, roadmap wave 4 and more)

- **Home knows what day it is (UI-M1).** `home_report` carries the week's phase and last week's
  result as the league counted it: "Last week (week 2): lost to … (re-scored box score; the
  league's result stands) · the model had 71%". A decided game shows its result ("Won ·
  final 188.8–164.0"), not a probability.
- **A pinned score bar (UI-M2)** stays under the header while the starter tables scroll. **Stale
  reads are flagged (UI-M7):** more than five minutes old while the game is undecided. With live
  scores not connected, the simple view now says so plainly. The script used to write the
  developer's SLEEPER_LEAGUE_ID instruction into it, and the vocabulary guard, which reads
  server HTML, could not see script-written text.
- **The trade builder (UI-T1, `/trade`, `webui/trade.py`).** Tick players on both sides and see
  each side's best lineup week by week, before and after. It is an exact assignment to the 13
  starting slots via scipy, with byes and IR respected. It also shows new empty-slot weeks and
  the drop forced at the 19-man limit (pinned equal to the engine's constant by a test). One
  button sends the deal to the paired simulation.
- **League, second pass (UI-O2, O4, O11, F5).** A power rating from the head-to-head matrix
  (no rank interval: the matrix carries no standard errors), the chance in the games left,
  the bracket "if the season ended today" with each seed's chance of that seed, and pending
  trades shown as their sides.

### Answers without waiting (2026-09-28, roadmap wave 3)

- **Instant compare (UI-P4).** The compare page answers as soon as two names are in, for up
  to four players (`webui/compare.py`). Each player is priced the way the live panel prices a
  starter, as independent Normals: P(A > B) for a pair, and each player's chance of the top
  score for more. It is labelled as an estimate that ignores same-game links and the chance a
  player sits. A saved full comparison for the same pair and week replaces it.
- **Lineup advice in chance to win (UI-L1).** `live.lineup_stakes` takes the game-plan record's
  margin (mean and sd) and shifts it by the points a swap is worth. The live callout says
  "about +2.5 points of chance to win the game and +3.0 to beat the median", as an estimate,
  not a fresh simulation.
- **Players and the waiver board (UI-P1, W1).** `/players` and `/waivers`
  (`webui/players_page.py`). Each player's standing comes from the decision log's drops and
  docs/WAIVER_MECHANICS.md: on waivers until the first 09:00 PT run two days after the drop,
  otherwise a free agent. The board is ranked by the newest waiver-targets record.
- **Tool cards show their latest answer (UI-A6)**, and "Not run yet" where a record-writing
  tool has none.
- **Found on the way:** column sorting had never worked. League said "click a column to sort",
  but no script had ever sorted (`c329cca` shipped the markup and styles alone); one
  delegated sorter in base.html now serves every `th[data-key]`. Also, the simple view's tools
  guard 404'd the compare panel's own fragment.

### Team, player and matchup pages (2026-09-28, roadmap UI-A1, A2, A3)

`/team/<slug>`, `/player/<id>` and `/matchups/week-<n>` are read-only views built in
`webui/objects.py` on the helpers every other page uses. Odds come through odds_now, results
through webui.results (as the league played them), the model's pre-kickoff quote through
accuracy.quoted_week, and moves through decisions_report, so no number can disagree with the
same number elsewhere. URLs are built from pseudonyms; every team link opens the team page, and
the player card links to the player page. The ~20 MB players cache is trimmed and held in
memory per file modification. For the current week, the live snapshot now carries every game
(`league`), and Matchups draws them once play has started. Past weeks show team totals only:
per-player box scores for completed weeks are not on disk.

**Found while building them:** `decisions_report` did not de-duplicate union-merged
decision-log rows, though the engine's readers always have. The Decisions page listed 133
moves for 100 transactions and its ledger counted the repeats twice. It now keeps the first
row per transaction, as the engine does.

### Three panes at 4K (2026-09-28, roadmap UI-V1; the owner chose three panes over a wider column)

At 2200 CSS px and wider -- a 4K monitor at the usual 125-150% scaling gives a browser 2560
to 3072 -- Home becomes three panes: standings and this week's games on the left, sticky so
they stay in view beside a long live panel; the matchup and the live panel (scoring feed
included) in the centre; the season outlook, the watch list and the expected-wins chart on
the right. It is CSS only: one wrapper div that is a plain block below the breakpoint, and
a grid with `display: contents` on the two old row containers above it. Below 2200px the
page is pixel-identical to before (1280 and 1920 compared screenshot to screenshot), so
nothing is closed off for a phone later. The scoring feed stays in the centre because the
live panel draws it as part of itself. `tests.test_webui_browser.TestThreePaneHome`
measures the panes' boxes at 2560 and the stacking at 1280 and 1920.

### One grammar for uncertainty (2026-09-28, roadmap UI-V6 / UI-T2)

- **"±" means one standard error, on every page.** It prints only through the `se` filter
  (`render.fse`): two decimals below 1, one above, so the same 0.25 no longer reads ± 0.25
  on Home and ± 0.3 on Forecasts. A guard in `tests.test_webui_twelfth` fails on any
  literal "±" in a template or the renderer outside that one function.
- **A spread is not a standard error.** The weekly-score column on a forecast page shows
  "sd 21", with a tooltip saying what it is; it used to print "± 21" beside standard errors.
- **Ranges are the 10th to 90th percentile** unless labelled otherwise.
- **A paired result is stated in standard errors** (`render.verdict`): under 2 "no
  measurable change", 2 to 4 "a modest gain/loss", above 4 "a clear gain/loss". Every
  evaluated move on Decisions carries its verdict, and the "Measurably moved the odds"
  filter draws the same line. These tiers are a DISPLAY convention, not a significance
  test; the move is priced by the paired evaluation, and the tier only says how far
  outside its own noise the number sits.
- **A chance to win is never shown as certain until it is decided** (UI-M8): "over 99.9%"
  or "under 0.1%" while anyone on either side has a game left.

### One standings table (2026-09-28, roadmap UI-O1)

`webui/standings.py` builds every standings row, and League, Home and the team page render
from it, so a number means the same thing everywhere. League shows every column: wins,
head-to-head, median, all-play, points for and against, the head-to-head streak, games back,
playoff odds with their move since the last forecast, title odds, final wins, VORP and budget.
- **Games back.** Games back of fourth, or, for a team in the top four, its lead over fifth.
  Level on wins is still decided on points, the league's tiebreak, and the page says "on
  points" rather than implying a tie.
- **Head-to-head and median** appear only when the week-by-week results add up to the
  league's total (UI-F4).
- **All-play and points against** come from Sleeper's box scores. The as-played record
  carries results only, so for re-scored weeks (F83) the page says these two columns use
  the re-scored points.
- **Clinch marks.** A clinch or elimination mark appears when the current forecast wrote its
  per-season record and the remaining schedule proves it (UI-O10).

### The playoff machine (2026-09-28, roadmap Decision 1; UI-E5, O6, O7, O8, O9, O10)

The owner ruled an additive engine export MINOR (CLAUDE.md, release policy). The engine now
writes one line per simulated season: every remaining game, every team against every
week's median, the final seeds and the champion
(`weeks/week_NN/sim_outcomes_week_N.json`). Only the seeds were captured before. The
capture reads values the loop already computed and draws nothing, so every existing golden
hash stayed byte-identical. `tests.test_sim_outcomes` checks that the record rebuilds the
engine's own wins, seed matrix and title rates.

`webui/outcomes.py` reads that file. The web process still never re-simulates: every number
on `/playoffs` is a share of the forecast's own seasons.

- **The machine (O7).** Pin any remaining result, or any team against the median, and every
  team's playoff, title and seed odds are recomputed over the matching seasons.
  - The count is shown ("600 of 1,000 seasons match"), and so is a standard error on each
    number: binomial, because each simulated season is an independent draw.
  - Below 200 matching seasons the numbers are refused, not greyed. Each pin keeps roughly
    half the seasons, and the page says so.
  - The result swaps in place and the old one stays on screen while the new one loads. The
    address follows, so a view can be shared or reloaded.
  - Presets:
    - favourites win this week;
    - upsets this week;
    - the owner wins out;
    - a played week "as it was played", offered when the forecast predates the result.
- **Leverage (O8).** For each team and week, the playoff odds if it wins versus loses, and
  if it beats versus misses the median.
  - The swing is 2 × p × (1 − p) × the difference. That is the expected size of the move
    once the result is known.
  - An index scales every swing by the season's average game swing, so the average game is
    1.0 by construction, and a median result reads on the same scale.
  - "The biggest games of the week" sums each game's swing over all eight teams.
- **Rooting guide (O9).** For every other game this week: the owner's odds if each side
  wins, the difference, and its standard error. Tiers follow `render.verdict`, so inside
  two standard errors the page says "either". The same is shown for each other team
  against the median.
- **Wins needed (O6).** The playoff odds for each final win total, with the count of
  seasons behind each bar.
  - A final total is banked wins plus the remaining games plus the median results. Banked
    wins come from the same week's forecast file.
  - Bars behind fewer than 200 seasons are hatched, and a table view carries the same
    numbers.
- **Markers (O10).**
  - **Clinched and eliminated** come only from a bound on the remaining schedule. A tie in
    wins counts against the team, because the points tiebreak is still to be played.
    Simulation frequency never decides them.
  - **"Over 99.9%" and "under 0.1%"** are frequency near-certainty, and need at least
    3,000 seasons. By the rule of three, 0 in 3,000 bounds the rate near 0.1%.
  - **"Controls its destiny"** is the same kind of bound: if the team wins out and beats
    every median, fewer than four others can still match its total.

**What it does not claim:**
- The bounds are conservative. A team can be mathematically in before the page says
  "clinched", but never the other way.
- From week 15 the export's seeds are the engine's banked ranking. Sleeper's bracket can
  override the playoff field, and the machine does not model that override.
- The Tk trap. `tests.test_webui_outcomes` runs a real (sandboxed) engine on the Agg
  backend. On Windows' default Tk backend, the engine's first figure starts an interpreter
  whose objects a later browser test's server thread finalises, and that test times out.
  The older engine tests, run in the full suite's order, have not hit this; the hazard is
  latent there.

---

## 5. What cannot be done without touching the engine, the goldens, or the gate

Stated up front so nobody discovers it mid-phase.

1. **Fresh VORP / replacement levels / week expectations in W1.** They come from
   `FantasySimulationEngine()` via `fantasy_sim.decisions`, which imports the engine. W1
   renders *recorded* tool outputs (`roster_grades_*.json`); a live number is a W2 job.
2. **Progress inside a running simulation.** A progress bar would need hooks in
   `run_simulation`, which is golden-pinned. The job page shows elapsed time and the log
   tail instead.
3. **Cancelling a run cleanly.** Killing the subprocess mid-run is treated as VOID (R1);
   the per-week exports may be partial, and the page says to re-run alone. No engine change.
4. **The warnings-log truncation.** `simulation.py`'s module-level `basicConfig` (F10) is
   avoided for the web process and left as-is for tools. Changing it is engine-adjacent.
5. **Manager-facing wording, real names in URLs, deployment.** Policy, not engineering:
   forbidden by H1/F37 and §2.8, not by any code limit.
6. **Sync's stale-key hazard** belongs to sync (H5/C3), not the UI, and is why W4 is a
   decision rather than a phase.

Nothing in W1–W3 requires an engine, golden, behaviour-baseline or gate change. If an
implementer finds otherwise, stop and report (the prompt's instruction, repeated here).

The one engine change the UI has needed so far is the per-simulation outcome export
(roadmap Decision 1). It went to the owner first and was ruled MINOR because it only adds a
file. The goldens were regenerated in a commit of their own, whose diff is 33 added lines
and nothing else.

---

## 6. Traps

- **`python` is the retired Store 3.8.** Use `py -3.10`; launch subprocesses with
  `sys.executable`; assert 3.10 at startup.
- **The suite pins the test count in three places** (README badge, README sentence,
  CLAUDE.md) and the module count in README. Every test file added moves all of them.
- **Every `scripts/*.py` must be mentioned as `scripts.<name>` in README.** The entry point
  is `webui/__main__.py` precisely so this guard is not tripped by accident — but README
  still documents `py -3.10 -m webui` in its own section.
- **Do not edit `fantasy_sim/weekly_report.py` or `positional_tiers.py`.** Both are
  renderer sources whose changes trigger the Pages deploy and are pinned by
  `test_every_renderer_source_triggers_the_pages_deploy`. Import from them; never modify.
- **Default arguments bind paths at definition time.** Patching
  `fantasy_sim.storage.SOME_FILE` in a test does not redirect a function whose signature is
  `def f(path=SOME_FILE)` (B28). The web layer avoids this class entirely by taking `root`
  as a constructor argument and never relying on patched module constants.
- **`git init -b` is unsupported on this machine's git** (F87). Any test that builds a
  repo uses `git init` + `git checkout -b`, and fails on any non-zero git exit.
- **In-process `sys.modules` proves nothing about imports** — the suite has already
  imported the engine. Import guards run in a subprocess.
- **The blanket `data/*` ignore needs nested exceptions** (see `.gitignore`). Job files
  under `data/local/webui/` need no exception — they must stay untracked.
- **Never run the suite, the goldens, or a hand tool while a job is running** (R1). The
  pre-launch scan refuses one direction; the owner refuses the other.
- **Werkzeug's reloader forks a second process.** Run with `use_reloader=False`, or the
  lock file and the pre-launch scan see the server's own twin.
- **`load_kickoffs` can reach ESPN** when the synced schedule lacks `_meta.kickoffs`. The
  status page labels the source, as `run_windows` does; tests stub it.
- **Digests on disk already contain real names** (a hand-run `weekly_report` defaults the
  flag on; runner artifacts are localized by `localize_reports`). Serving them is fine on
  localhost; **copying or re-saving them anywhere is not**, and `localize_names` is
  idempotent so applying the overlay again is harmless.
- **CRLF.** If any file the docs guards hash is ever touched, write with `newline="\n"`.

---

## 7. Branch and merge protocol

- `feature/web-ui` is branched from `main` (rule 9). W1 ships from it via a PR (the
  `ci` required check); the branch is then deleted, and W2/W3 each branch fresh from
  `main` (`feature/web-ui-w2`, …). No branch lives longer than one phase.
- **`main` receives automated commits several times a day** — `data-capture` (daily),
  `canonical-run` (eight crons a week), `evaluate-moves` (daily) — all to `data/logs/`.
  Merge `main` **into** the branch before every meaningful push; **never rebase** the
  branch over them. `.gitattributes` union-merge rules cover the append-only `.jsonl` logs
  so those merges are clean; for any `data/logs/*.json` bundle that conflicts, take
  `main`'s copy (the automated capture is authoritative).
- A phase PR touches: `webui/`, `tests/test_webui_*.py`, `requirements-web.txt`,
  `README.md`, `CLAUDE.md`, `.github/workflows/ci.yml`, `CHANGELOG.md`, `pyproject.toml`,
  `CITATION.cff`, this document's status line. Nothing under `fantasy_sim/`, `tests/fixtures/`
  (except new web fixtures under `tests/fixtures/webui/`), or `data/`.

---

## 8. What this document does not claim

- That the UI adds predictive value. It adds *access*; the model's measured standing at
  scoping time (a well-centred points model, a probability layer with no demonstrated edge
  yet, an open interval-width question awaiting weeks 5–6) is unchanged by any of this.
- That the pre-launch process scan is airtight. It is a heuristic over command lines and
  is labelled as such on the page.
- That W4 is permanently closed. It is closed until the owner reopens it with the registry
  read specified.
- That the phases are small. W1 is a few hundred lines plus tests; W2 is the concurrency
  layer and is where the care goes; W3 is mostly W2 re-pointed. The estimates are honest
  guesses, not commitments.
