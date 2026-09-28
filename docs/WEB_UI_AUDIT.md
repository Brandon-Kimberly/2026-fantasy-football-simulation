# Web UI audit — 2026-09-27

A measured pass over every page of the web UI in both views, with the bugs found, the
places the layout or organisation falls short, and the things worth adding — each scoped
to a change, an effort, and a test. **No UI change was made for this document.** Team
names throughout are the repository's pseudonyms (H1).

**Status (2026-09-28): Phases 1 to 6 landed, plus a home-page pass on the owner's own
six points (docs/WEB_UI.md W13): the live probability leads and the pre-game one is a
footnote, the two matchup bars share one scale, every starter is shown against his
projection, a scoring feed says what just happened, the season card is full, and the
column widens on a big monitor.**

**Phases 1 to 6** — every bug in §3, B13 included (the
count-up now settles from 90 % of its target over 400 ms, so no wrong-looking number and
the two hero shares stay near 100 throughout). Phase 6 (power features): U11 a manual
theme (system / light / dark) in the settings file, one POST route, stamped on `<html>`,
the dark tokens written once for the OS preference (guarded so an explicit light wins)
and once for the explicit choice and pinned identical; U12 a web-app manifest and an
SVG icon so `syndicatefootball.local` installs as an app; U4 the command palette
(Ctrl/⌘-K: this view's pages, tools and teams, and "compare A vs B" typed straight into
the compare tool) and U14 the `g h` / `g l` / `g f` / `g d` / `g t` / `g g` shortcuts with
`?` for the list; U5 player hover cards on every player cell (`data-player`, one
`/api/player` read, cached per page); U10 Decisions awards (move and ouch of the season
and of the latest week) and per-team timelines in the ledger; U13 a durations line per
tool on Jobs; and page transitions (`@view-transition`, off under reduced motion).
NOT done, deliberately: U2 (what changed since the last sync) and the rest of U13 (SSE log
streaming, record diff) — each is its own session. `tests.test_webui_ninth` pins the
phase (9 tests); scripts by presence only. Phase 5 (live and game day): U3 a job bar on every page while a
job runs and a toast with "Open the answer" when it ends (the page polls the job's JSON
every 3 s only while the bar is up); U6 the next kickoff in the hero from the synced
kickoffs (`glance.kickoff_report`), ticking in the browser; U7 live v2 — game-clock chips
on every starter, the win probability through the day from the board's in-memory
history (`LiveBoard.max_history`, reset each week, never written), every NFL game's
clock and score in the snapshot, a swing animation when the number moves, and
team-colour confetti only when every starter on both sides has played and I am ahead;
U8 head-to-head history with this week's opponent from this season's actuals and the
season archives (`glance.h2h_report`); the hero's avatars clash in; and `/gameday`, a
full-screen dark scoreboard for a TV served in both views (a page, not a fourth view:
the two-view toggle stays as it is). `tests.test_webui_eighth` pins it (11 tests); the
page scripts are pinned by presence only. Phase 4 (charts v2): `render.line_chart` is the one chart
component — round ticks (`nice_ticks`: 1 / 2 / 2.5 / 5 × 10ⁿ, a percentage axis keeps
its 100), x labels that never collide (the first and last always survive; a run of the
same label is written once; Decisions is labelled by date), 11.5 px text with each card
drawn at its rendered width, a hover crosshair with a tooltip naming every series at that
point, team-hue lines with dodged end labels in the team's colour, an optional legend
from the same series list, and `render.sparkline`. Forecasts carries the playoff-odds
and title-odds races (U1, `glance.odds_race`, every team, mine thick) and the home
standings carry each team's odds sparkline and its rank move since the previous forecast
(U9). The `linechart` macro is gone. `tests.test_webui_seventh` pins it (15 tests). Phase 3: the light-theme text tokens
now all pass 4.5:1 (`muted` #6f6e68, `pos` #1f66bd, `neg` #c93a39, `warn` #8f5d12, and
`--warm-ink` / `--gold-ink` for the two accents when they carry text; the gold medal
wears dark text) and `tests.test_webui_sixth` computes the ratios from `base.html` so
they cannot drift; one `:focus-visible` ring for every focusable element; the header is
brand and tabs only, with the clock and view badge in the footer; the status chips and
the League team chips are even grids; and fifteen new sprite symbols give every tool,
record and job its own icon. Two things learned doing
it: headless Chromium will not lay out narrower than ~504 px, so the harness's "phone"
width is 520 and every breakpoint is judged there; and a table that scrolls inside its
own box becomes the sticky containing block, so table headers can only stick above
tablet width — below it they scroll with the table, which is fine. The `?audit=1`
overflow hook and `--overflow WIDTH` probe are in, and every page reports nothing past
the edge at 520.

## 1. How it was done

- **Harness:** `scripts/webui_audit.py` (new, committed with this document). Against a
  server started `--no-real-names --root <sandbox copy>` it toggles each view, walks every
  internal link from `/`, and records per page: HTTP status, time, weight, title, h1
  count, duplicate ids, images without alt, inputs without a label, tables wider than 12
  columns, text leaks (`None`, `nan`, `undefined`, unrendered braces), and broken links.
  With `--screens` it photographs the key pages with headless Edge at desktop (1280),
  phone (400) and dark mode, and captures the console. `--settled` forces
  `prefers-reduced-motion` so the page is captured at rest.
- **Run:** 400 dev pages (the crawl cap; the tree has more raw-file links) and 27 simple
  pages crawled; 144 screenshots; the mode put back where it was.
- **Also:** WCAG contrast computed for every text token on both themes; a static read of
  `base.html`'s CSS interactions; the sandbox happened to be STALE (a copy artefact),
  which exercised the STALE paths the live server rarely shows.

Rerun any time (the sandbox copy is `data/` minus the embed digests and backups):

    py -3.10 -m webui --port 8766 --no-real-names --root <copy>
    py -3.10 -m scripts.webui_audit --base http://127.0.0.1:8766 --out <dir> --screens --settled

Harness limits to know: it can't see CSS layout (that's what the screenshots are for);
`}}` inside JSON views and inputs wrapped by a `<label>` are false positives; and the
count-up/fade animations mean an un-`--settled` capture shows mid-animation numbers.

## 2. Results at a glance

| | dev | simple |
|---|---|---|
| pages crawled | 400 | 27 |
| HTTP failures | 0 | **1** (`/tools/roster_grades` linked from League) |
| console errors / warnings | 0 | 0 |
| pages over 150 KB | 26 (all raw-file views) | 0 |
| slowest page | 858 ms (the 24 MB players-cache view) | — |

Everything renders and nothing crashes. The problems are in what renders: phone layout,
one invisible chart, a handful of text bugs, contrast, and dead polish rules.

## 3. Bugs

Severity: **S1** breaks a page or a view · **S2** visibly wrong · **S3** rough edge.

| # | Sev | Where | What | Fix | Test |
|---|---|---|---|---|---|
| B1 | **S1** | every table page at phone width | Digits and names break mid-word ("3/7/2/./9", "Turb/o/Llam/as"); the body is wider than the screen on Home and League. Cause: `td { overflow-wrap: anywhere }` and `td.nm` no longer `nowrap` let the table shrink instead of scroll; something else forces the page wider than 400 px. | `.num, td.nm, .row .t { white-space: nowrap }`; `.scroller { overflow-x: auto }` at every width with a soft fade edge; `.wrap { overflow-x: clip }` as a guard; stack the hero's number row and the games list below 500 px; standings as a card list below 500 px. | Harness phone shots become the gate; add an `?audit=1` hook that writes `documentElement.scrollWidth` into the DOM so `--dump-dom` can assert "no body overflow" automatically. |
| B2 | **S1** | Decisions › Who has helped themselves | The bars don't render. The bar is a `<span class="b">` with a height, so it collapses; only the number shows, and the value column wraps into two lines. | Make `.hbar > *` block-level (or use `div`s); widen the value column to 9 rem. | Modes/polish test: `class="b"` element has non-zero rendered height is not unit-testable — assert the markup uses the block element and pin with a screenshot. |
| B3 | S2 | Decisions | Dropped players without a projection show "Nick Bosa **None** · —/wk". | In `glance._pl` fall back to `""`; template hides an empty position and the "/wk" when mean is missing. | `test_webui_fourth`: a drop with no projection renders without "None". |
| B4 | S2 | Home › Watch list | "COSTS **−-0.2**" — a hard-coded minus in front of a signed number; and when the fallback projects *more* than the starter (this case) "costs" is the wrong word. | Use `|signed`; label the column "If out" → "Change"; colour positive green. | Home test with a negative `give_up`. |
| B5 | S2 | Home | The h3 hint renders as part of the heading: "THIS WEEK'S GAMES WIN PROBABILITY, FROM THE SAME PREDICTION LOG". `h2 .hint` is styled; `h3 .hint` is not. | Add the `h3 .hint` rule (same as h2's). | Polish test: hint span not uppercase inside h3. |
| B6 | S2 | every page | The gradient page title never appears: `h1 > :first-child:not(.pill)` can only match an element, and every h1 begins with a text node. The rule is dead. | Wrap the title text in a `<span class="t">` in the templates (or use `h1` background-clip directly and keep pills opaque). | Assert the span is present on every page in both views. |
| B7 | S2 | every table page | `th { position: sticky; top: 0 }` slides under the sticky app header (`.top`, z-index 30) while scrolling; and `#t-…` anchors (League team links) land under the header. | `th { top: var(--head-h) }` with the header height as a token; `[id] { scroll-margin-top: calc(var(--head-h) + 12px) }`. | Screenshot with scroll (harness `--scroll` option) or accept as visual review. |
| B8 | **S1** | System, tool pages (STALE) | The STALE lede is a wall of every degraded warning: `fr.reasons` mixes the one STALE cause with the 27 degraded lines. Recurs every week between sync and simulation. | Split reasons in `glance.freshness_report` into `stale_reasons` / `degraded`; the lede shows only the STALE cause(s); the degraded list stays folded as it is for DEGRADED. Same on tool pages (`<li>` dump). | Home/modes tests with a STALE fixture: lede under 300 chars, degraded folded. |
| B9 | S2 | Tool forms | `|capitalize` lowercases the rest of the label: "Player a", "Player b". Help lines duplicate the default: "simulations; default 2000 · default 2000". | A `sentence` filter (upper-case first letter only); print the default only when the help doesn't already name it. | Polish test on `/tools/compare_players`. |
| B10 | **S1** | Simple view › League | "Grade every roster" links to `/tools/roster_grades`, a dev-only tool → 404 for a simple-view user (the crawl's one failure). | Wrap dev-only tool links in `{% if dev %}` (also "Weekly digest" already is). Add `SIMPLE_PAGES` link-check to the modes test: every link on a simple page returns 200. | Modes test: crawl links from each simple page. |
| B11 | S3 | Sync | "Read from the Windows User scope (the Windows User scope)". | Print the source only when it differs from the default wording. | — |
| B12 | S2 | Jobs | JSON-output tools (live matchup, luck ledger, market sweep…) show nothing in **Answer** although their job page renders tables; labels repeat the tool name ("Luck ledger · Luck ledger · 2026"); pre-rename jobs show raw labels. | "Open answer" for every OK job; strip the tool name from the label; humanise old labels through `tool_title`. | Fourth-pass test on `/jobs`. |
| B13 | S3 | Home | The count-up shows a wrong number for ~0.7 s (24.3% on the way to 76.7%) and the two hero numbers don't sum to 100 mid-animation. | Count from a nearby value (e.g. 90% of target) over 400 ms, or count only the headline number. | Judgement call — see §6. |
| B14 | S2 | every chart | Y ticks aren't round (21/16/11/5/0; −1/12/25/38/51); x labels collide at the end ("wk 13 wk 14") and repeat on Decisions ("wk 1" ×4); chart text is 10 px and the Forecasts trend cards are close to unreadable. | A tick function (1/2/5 × 10ⁿ), drop the forced last label when it collides, label Decisions by date, minimum 11.5 px chart text and taller cards. | `line_chart` unit tests for tick values and label collision. |
| B15 | S3 | Week › Where each team finishes | The team mark wraps above the name (first column squeezed by eight seed columns); colour scale is /100 so most cells are faint. | `td.nm { white-space: nowrap }` (B1), scale the tint to the column max. | — |
| B16 | S3 | League rosters | Rows with a status pill are taller than their neighbours. | `.st { line-height: 1; }` and `vertical-align: middle` on the cell. | — |
| B17 | S2 | League › Raw files (dev) | The players cache renders as a **24 MB** HTML page. | Files over 1 MB: offer download only, no pretty-print. | Route test with a large JSON fixture. |
| B18 | S3 | Home chip, Records titles | Raw window ids: "run3_tuesday", "run1 pre kickoff". | A `window_title` map: "Tuesday run", "Pre-kickoff run", "Sunday run". | Polish test. |
| B19 | S3 | Home, League | Orphan chips (5 status chips → 4 + 1; 8 team chips → 7 + 1). | Status chips as a 5-column grid at ≥ 1100 px; team nav as a wrapping grid with equal widths. | — |
| B20 | S3 | header (dev) | The clock/host/mode line wraps to its own row under ten tabs. | Move the clock and view badge to the footer; one-row header. | — |
| B21 | S2 | all (light theme) | Contrast: `muted` 3.4:1 (used at 12 px everywhere), `pos` 4.2:1, `neg` 3.7:1, `warm` 3.0:1, `gold` 2.5:1; white on the gold and warm gradient pills 2.7 / 3.2:1. Dark theme passes except `rule`. | `--muted` → `#6f6e68` (5.0:1); text-safe variants `--gold-ink #8a6a12`, `--warm-ink #b85f1c`; gradients for fills and pills only, never small text. | `tests/test_webui_modes` contrast check over the token table (pure Python, no browser). |
| B22 | S2 | Waiver targets record (a simple-view tool) | 16-column table. | Two tables: the eight that decide (name, pos, NFL, VORP, this week, bid, band, status) and a folded "more" (tier, mean, p90, P(0), old bid, bye, fills, incumbent). | Render spec test: max 9 columns in the primary table. |
| B23 | S2 | keyboard users | No `:focus-visible` style on links, chips, cards, tabs; the tools card has two tab stops (cover link + title link). | One global `:focus-visible` rule; make the card itself the link (or `tabindex=-1` on the title). | — |
| B24 | S3 | Log page | 494 KB at n=200 because every row embeds its JSON in a `<details>`. | Load the JSON on open (data attribute rendered on demand) or cap n at 100 by default. | Route test on size. |

## 4. Layout and organisation

- **Home.** The hero is tall for what it says; the live panel reserves a block before
  anything has been fetched. Collapse the live block to one line until the first refresh
  (or auto-fetch when a game window is open). The stat cards work; the small chart in the
  right card needs to be larger (B14). The five status chips are five different concerns
  — a compact status strip with consistent icon discs reads better than pills.
- **Header.** Ten tabs plus a clock line is dense. Keep one row: brand + tabs; clock and
  view badge to the footer. (Simple view already fits one row.)
- **Tools.** Every card is the same shape with no icon; add an icon per tool, the last
  run ("Done · 2 h ago"), and a "Run again" shortcut with the last settings.
- **Tool form.** Four full-width group cards for five fields is mostly whitespace; put
  WHO and WHEN side by side and HOW MUCH/OPTIONS folded by default in dev.
- **Records.** Six records from one run read as six unrelated rows. Group by run
  (timestamp) into a card — "Sep 27, 10:08 am · canonical digest + 5 records" — with tool
  icons; drop the size column; add a tool filter. The "→ json" raw link needs a real label.
- **Jobs.** Group by day; running-job banner site-wide (see U3); answer links for all.
- **Decisions.** The "roster had moved on" sentence repeats on almost every row — make it
  an icon with a tooltip; paginate by week with the newest open; widen the date column so
  "10:32 am" doesn't wrap.
- **Forecasts.** Mostly empty with three runs; this is the page for the odds race (U1).
- **League.** The strongest page. Fold each roster's bench by default; team chips as a
  grid; "Propose a trade" as a button.
- **Week.** Add "since last week" deltas beside playoff/title odds; keep the heat-maps.
- **System.** Fine once B8 lands; cards instead of stacked sections would balance it.

## 5. Useful additions

| # | What | Why | Views | Effort |
|---|---|---|---|---|
| U1 | **Playoff-odds race** — all eight teams across canonical runs (one line each, team colours, mine emphasised) on Forecasts; the same for title odds | the page is empty and this is the season's story | both | S |
| U2 | **What changed since the last sync** — rosters, statuses, lines, projections diffed against the backup | the question after every sync; the backups make it free | dev (+ a "news" version for simple) | M |
| U3 | **Site-wide job bar** — a thin progress bar at the top of every page while a job runs, a toast with "Open the answer" when it finishes | today you have to sit on the job page | both | S |
| U4 | **Command palette** (Ctrl/⌘-K): pages, tools, teams, "compare A vs B" | fastest way around a UI you use daily | dev | M |
| U5 | **Player hover cards** in every table: projection, VORP, status, bye, "compare with…" | the roster tables are where questions start | both | M |
| U6 | **Next kickoff countdown / "lineups lock in 2h 14m"** in the header from the synced kickoffs | the one number that matters on Sunday morning | both | S |
| U7 | **Live panel v2** — per-player live points with game-clock chips, a win-probability line over the day (in-memory snapshots), possession/red-zone if the scoreboard offers it | you said you'd use it to monitor scoring | both | M |
| U8 | **Head-to-head history** vs this week's opponent, from the season logs | context the hero lacks | both | S |
| U9 | **Rank-change arrows** (▲2) in standings vs the previous forecast; per-team sparklines of playoff odds | trend, not just level | both | S |
| U10 | **Decisions awards** — best and worst move of the week/season, per-team timelines | makes the ledger a story | both | S |
| U11 | **Manual theme** (light / dark / system) persisted in the settings file | dark is currently OS-only | both | S |
| U12 | **PWA manifest + icons** so `syndicatefootball.local` installs as an app with its own window and icon | you're using it daily; it should feel like an app | both | S |
| U13 | **Dev: jobs duration chart, SSE log streaming instead of 2 s polling, record diff (lineup vs lineup)** | developer ergonomics | dev | M |
| U14 | **Keyboard shortcuts** (g h / g l / g t, ? for help) | pairs with U4 | dev | S |

## 6. Fun and visual

- **Fix what didn't land:** gradient titles (B6), the count-up (B13 — count from 90 % of
  the target, or from the previous value when there is one, so the number never reads
  wrong).
- **Icons.** A set of ~14 more symbols (lineup, matchup, waiver, trade, compare, calendar,
  sweep, luck, health, scorecard, bid, odds, live, sync) used on tool cards, section
  headings and records — the single biggest "professional" gain for the cost.
- **Page transitions.** `@view-transition { navigation: auto }` gives a cross-fade between
  pages in one CSS rule; honours reduced motion.
- **Charts v2.** One SVG chart component (line, race, bars) with hover crosshair and
  tooltip, animated draw, gradient areas, round ticks, ≥ 11.5 px text; replaces the
  per-template maths.
- **Hero.** Team avatars with a "vs" clash on load; a win-probability swing animation
  when the live number moves; team-colour confetti on a final win (and only then).
- **Gameday / kiosk mode.** A full-screen live scoreboard for a TV: big numbers, dark,
  auto-refresh, every game's clock. Works as a fourth "view" behind the same toggle.
- **Season progress bar** in the header (week 3 of 14, playoffs marked).
- **Team-colour theming.** Your team's hue as the accent across the UI (the palette is
  already per-team); a per-user brand when the engine is hosted.
- **Bars and tiles.** Animate from the previous value, not zero; value labels inside
  wide bars; tiles with gradient numbers; consistent icon discs.
- **Empty states.** A small field-line illustration and one sentence instead of a muted
  line.
- **Motion budget.** Stagger at most four elements; nothing longer than 600 ms; reduced
  motion stays a hard off switch (it is).

## 7. Proposed order

| Phase | Contents | Effort | Gate |
|---|---|---|---|
| 1 · Bug sweep | B2 B3 B4 B5 B6 B8 B9 B10 B11 B12 B16 B18 B11 | one session | tests above + modes guard |
| 2 · Phone and tables | B1 B7 B15 B17 B22 B24 + the `?audit=1` overflow hook | one session | harness phone shots, no body overflow on every simple page |
| 3 · Contrast, focus, header, icons | B19 B20 B21 B23 + icons + one-row header | one session | contrast test over tokens; focus screenshot |
| 4 · Charts v2 | B14 + U1 + U9 + the chart component | one–two sessions | `line_chart` tests; screenshots |
| 5 · Live and gameday | U3 U6 U7 U8 + hero animations + kiosk view | one–two sessions | live tests (no network); modes guard for the fourth view |
| 6 · Power features | U2 U4 U5 U10 U11 U12 U13 U14 + page transitions | as wanted | per feature |

Every phase is built for both views (CLAUDE.md rule 10) and lands behind the modes guard.

## 8. What this audit does not claim

It ran on a sandbox copy that was STALE where the live server is DEGRADED, so the STALE
findings (B8) were seen here and inferred for the live server; the crawl capped at 400
dev pages (raw-file links dominate the remainder); CSS layout was judged from 144
screenshots at three widths, not from every page; and no user other than the owner has
used the simple view yet — its vocabulary is guarded by test, its usability is not.
