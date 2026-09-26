# Web UI — design and phased backlog (scoped 2026-09-26)

**Scoping only. No feature code exists yet.** This document is the design a later session
implements, written so that a model with less context than the author can pick each phase
up cold: what it is, what it builds on, exactly what to change, exactly what *not* to
touch, how to know it is done, and the traps a careful-but-uninformed implementer would
fall into. It follows the conventions of `SCOPED_BACKLOG.md`.

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

Decision: the status page shows freshness and prints `py -3.10 -m scripts.run_sync` with
the H5 note. Revisit only if the owner asks, and then as its own scoped item with the
registry read specified.

### W5 (optional, later) — Sandbox root

`--root` already allows serving a copy. A "sandbox run" button — copy `data/` to a temp
root, run a tool or the report there, show the result, discard — is the exact technique
`tests/golden_sync.py` and this session's non-canonical report used, needs no engine
change, and would let the owner ask "what if I add X" without touching production data.
Scoped separately when W3 has been used for a few weeks.

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
