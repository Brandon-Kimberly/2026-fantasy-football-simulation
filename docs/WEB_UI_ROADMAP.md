# Web UI roadmap: what to build next

**Status (2026-09-28): waves 1–5 under way; 61 items built, 2 deferred (see Progress).** This is the scoped log of what the web UI
should add, change and remove next. It picks up where `docs/WEB_UI_AUDIT.md` ended: that
audit was about fixing and polishing what existed, and all six of its phases have landed.
This one asks a different question. Given what ESPN, Sleeper, Yahoo, the analysis sites
and the best open-source league tools offer, and given what this project knows that none
of them do, what should the UI become?

Every item below is scoped tightly enough to start from: why it exists, what it copies
and from whom, what is in and out, where the data comes from, which views it serves, and
the tests that should be written first (rule 1). Items that need an engine change, a
sync change or a decision from the owner are marked, and the decisions are collected in
section 3 so none of them happens by accident.

## Progress

Built on `feature/webui-wave1`, each as a red characterisation commit and then its fix.

| Item | What landed |
|---|---|
| UI-E1 | Browser tests: Playwright drives the installed Edge (`tests/test_webui_browser.py`). Against the pre-fix templates the Decisions filter test fails with the original defect. Its first run found a real 404 on Game Day's favicon. |
| UI-F1 | Home reads live scores on load once any game of the week has kicked off. |
| UI-F2 | Sync sources and sync warnings are counted apart ("1 source failed, and the sync raised 30 warnings"). |
| UI-F4 | League splits each record into head-to-head and median, shown only when the weekly actuals reconcile with the league's standings. |
| UI-F6 | The player card shows this week's price beside the season mean, and never calls the season mean a "projection". |
| UI-E4 | Every page reads the odds through `odds_at` / `odds_now`. Home had been blank between Tuesday's sync and that week's simulation. |
| UI-M8 | No 0% or 100% until a game is decided. Also fixed: the count-up animation was painting over a fast live answer. |
| UI-V6 / UI-T2 | "±" is always one standard error and prints through one filter. Every evaluated move shows a verdict in standard errors, and the "moved" filter uses the same line. |
| UI-O3 | "What week N did" on Forecasts, and a one-line version on Home. |
| UI-Q2 | Past results carry the model's own pre-kickoff quote, taken from the same row the Accuracy page scores. |
| UI-O12 | Final wins shown as a range: a strip on Home and a column on League. |
| UI-V7 / R4 | Keyboard-operable sorting and player cards, a table view behind every chart, CSV export. |
| UI-F3 / F9 / F11 / W2 / T5 / P7 | Few lines left explained, no NFL team, a week picker, the waiver clock, the deadline countdown, injuries and practice. |
| deferred | UI-E2 (static assets) and UI-E9 (lighter pages). The churn outweighs a gain a local server does not feel. |
| UI-H1 / H2 / H3 | History (record book, rivalries) and the draft board. Regular season only, re-scored weeks marked. |
| UI-R1 / R2 | The week in review on each played week's Matchups page: awards, movers, the best move, and who had the week. |
| UI-M1 / M2 / M7 | Home knows the week's phase and states a decided result. A pinned score bar. Stale-read warnings. |
| UI-T1 | The trade builder with an instant lineup estimate. |
| UI-O2 / O4 / O11 / F5 | League shows power, the schedule left, the bracket, and pending trades as their sides. |
| UI-P4 | Instant compare for two to four players. A saved full comparison replaces the estimate. |
| UI-L1 | The live lineup callout prices the swap in chance to win. |
| UI-P1 / W1 | The Players page and the Waiver board, with waiver clearing times. |
| UI-A6 | Tool cards lead with their latest answer. |
| UI-A1 / A2 / A3 | Team, player and matchup pages. Every game is live in the current week. Every team link opens its team page. |
| UI-A4 / A5 | League has the season as a grid of fourteen weeks by eight teams: each cell is the opponent, the result once played or the chance to win before, and it opens that game; the run-in is shaded. In the developer view, Records, Jobs, Logs, System and Sync sit under one "More" menu (Decision 6). Every tool that answers a question is linked from the page where the question comes up. The three operations tools stay under System. |
| UI-W3 / T3 / R6 | The waiver board opens with the last run's winning claims (bid and paired-simulation grade; losing bids are not logged, and it says so). The trade builder computes what changes: starters out and in, slot by slot, new bye collisions and depth. A Luck page shows the ledger's five pre-registered measures, built from the files on disk and reached from team pages and the palette, never from Home. The DNP measure is not measurable there, because no per-team starters are on disk. |
| UI-A9 / P5 | Players in the command palette, by name, linking to their pages. The player card draws the distribution strip for a rostered player and Sleeper's injury detail (body part, practice, last update); it measures itself before it places. Not built: "rostered by N of 8", because each player is on one roster or none in this league. |
| UI-P2 | The distribution strip (`render.strip`) on the team roster and the player page: histogram, 10th–90th line, 25th–75th box, mean tick and the weeks played, on one scale per table. Not yet in the hover card or compare. The waiver board has no strips because free agents are not simulated. There is no zero pip: nothing on disk is the chance of a zero in a week played. |
| UI-O1 | One standings helper (`webui/standings.py`) behind League, Home and the team page. It adds all-play, points against, the head-to-head streak, games back of fourth (or the lead over fifth), the odds' move and a proven clinch mark. All-play and points against say when they rest on Sleeper's re-scored weeks. |
| UI-O5 | Strength of schedule is a native grid on each forecast week: implied points per NFL team per week, each roster's average, every cell numbered, and a rest-of-season / next-four / playoff-weeks window. The three static images step aside where it renders. |
| UI-E5 / O6 / O7 / O8 / O9 / O10 | The playoff machine, leverage, the rooting guide, wins needed and clinch markers on `/playoffs`, all filters over the forecast's own simulated seasons. It needed the engine export: Decision 1, which the owner ruled MINOR, with the goldens regenerated alone as 33 added lines. Counts and standard errors come with every number, and anything under 200 seasons is refused. |
| UI-V1 | Three panes at 4K, the owner's choice under Decision 5. CSS only, so below 2200px the page is pixel-identical to before. The left pane is sticky. |

**Found while building, not on the original list.**
- A result the standings contradict. Week 2's box scores give Quantum Ferrets the win over Cosmic Badgers (148.52 to 144.19), but the league's standings record a loss.
  - The standings' season points only add up at the scores before a later stat correction.
  - Pages now flag such a result instead of stating it.
  - **Owner ruling (2026-09-28): the league's record as played decides.** `scripts.as_played_record` rebuilt weeks 1–2 from the frozen snapshot. The rebuild matches every team's banked wins exactly. Every page, Accuracy included, now counts week 2 as the loss. Accuracy's weeks 1–2 *points* are still compared across the scoring change; F83 records that as not yet corrected.
- The Decisions page counted union-merged moves twice: 133 listed for 100 transactions, and the ledger summed the repeats twice. The engine already de-duplicated them; the UI now does too.
- Column sorting never worked. League said "click a column to sort", but no script ever did. One sorter now serves every table.
- Twenty web tests error instead of skipping on a bare install without Flask. They import fixtures inside a Flask probe.
  - This predates the roadmap. The README's no-Flask skip count is right, but its "not a failure" is not.

---

## Contents

1. [How this was put together](#1-how-this-was-put-together)
2. [Where the UI stands](#2-where-the-ui-stands)
3. [Decisions the owner needs to make](#3-decisions-the-owner-needs-to-make)
4. [Guardrails every item inherits](#4-guardrails-every-item-inherits)
5. [How to read an item](#5-how-to-read-an-item)
6. [The index](#6-the-index)
7. [The log](#7-the-log)
   - [F: fixes found in this audit](#f-fixes-found-in-this-audit)
   - [A: information architecture](#a-information-architecture)
   - [M: matchup and game day](#m-matchup-and-game-day)
   - [O: odds, standings and what-if](#o-odds-standings-and-what-if)
   - [L: lineups](#l-lineups)
   - [P: players](#p-players)
   - [W: waivers and FAAB](#w-waivers-and-faab)
   - [T: trades](#t-trades)
   - [H: history and the almanac](#h-history-and-the-almanac)
   - [R: recaps, awards, alerts and sharing](#r-recaps-awards-alerts-and-sharing)
   - [Q: the model's own record](#q-the-models-own-record)
   - [V: visual system and layout](#v-visual-system-and-layout)
   - [E: engineering](#e-engineering)
8. [Deliberately not building](#8-deliberately-not-building)
9. [Suggested order](#9-suggested-order)
10. [Sources](#10-sources)

---

## 1. How this was put together

Four kinds of evidence, all gathered on 2026-09-28.

**The UI as it runs.** The audit harness (`scripts.webui_audit`) crawled a pseudonymous
server in both views: 400 developer-view renders and 27 simple-view renders. None failed
and none took longer than 800 ms; the slowest was a 17 MB raw file at 484 ms. Every main
page was screenshotted at desktop (1280 px), phone (520 px) and dark, and Home again at
2560 px, which is what a 4K monitor gives a browser at common scaling.

**The code and the data.** Every route, template and module was listed. Every file the
engine, the tools and the sync write was checked against what the UI actually reads. That
comparison produced a good share of the items below: the project already holds data that
no page shows.

**The outside endpoints.** Before any item was allowed to depend on outside data, the
endpoint was called: the ESPN scoreboard the live panel already fetches, Sleeper's
trending-adds endpoint, and Sleeper's headshot and team-logo URLs. Results are in the
items that use them.

**Two research passes.** One covered the commercial products (ESPN, Sleeper, Yahoo, MFL,
Fleaflicker, CBS, FantasyPros, Fantasy Life, 4for4, KeepTradeCut, FantasyCalc and others).
The other covered open-source and analytics-media work (LeaguePulse, the kevin-kalish
playoff simulator, league-almanac, TheDadHut's league-history, Krool's analyzer,
uberfastman's weekly report, ffscrapr and ffsimulator, the NYT and FiveThirtyEight odds
interactives, and others). Reddit could not be reached from the research tool, so user
sentiment comes from app-store reviews, review aggregators, vendor forums and vendors'
own admissions. The most useful sources are listed in [section 10](#10-sources).

---

## 2. Where the UI stands

### What it already does better than the competition

- **Every number is a count with an error bar.** No commercial product shows a standard
  error on a win probability. None of the open-source simulators except two model scoring
  properly: the best-known one plays out the rest of the season with literal coin flips.
- **Every transaction is scored before the fact.** The Decisions page prices each move with
  a paired simulation. Every other trade or waiver grader works in hindsight or from
  crowd values.
- **It checks itself.** The Accuracy page scores the model's own committed forecasts. The
  commercial products publish nothing comparable.
- **It is desktop-first.** Sleeper's desktop site is a sore point in its reviews. A tool
  built for a large monitor is the underserved niche, not a compromise.
- **IDP is taken seriously.** Both research passes found IDP poorly served everywhere; one
  analyzer marks it "not modeled".

### Eight structural gaps

These are the patterns behind most of the items, not a list of individual defects.

1. **It is organised around the machine, not the manager.** The navigation is Records,
   Jobs, Logs, Tools. A manager thinks in teams, players, matchups and trades. There is
   no team page, no player page, no page for any game but mine, and no schedule. Every
   competitor has all four.
2. **Answers arrive late.** Every decision tool is a subprocess job. Comparing two players
   takes 1.2 minutes on average and evaluating a move 5.8 minutes, even though the
   exports on disk could answer the common case instantly as an estimate.
3. **Home does not know what day it is.** On Monday morning it still led with the 76.7%
   pre-game number while the matchup stood at 164.0 to 188.8 with one starter left,
   because live data loads only when Refresh is pressed. Nothing marks a week as
   finished, and nothing previews the next one.
4. **Valuable data is never shown.** The playoff bracket file, the 152-pick draft, each
   player's simulated distribution, strength of schedule, injury body part, practice
   participation and depth-chart order all sit on disk unread. The ESPN scoreboard the
   live panel already fetches also carries team colours, logos, Vegas lines, weather,
   stat leaders and records, and all of it is thrown away.
5. **Pictures where interaction belongs.** Each simulated week writes 27 chart images
   (plus 8 tier images), and the UI shows them as static pictures that ignore the
   theme, cannot be hovered, and cannot be read by a screen reader.
6. **Half of a 4K screen is blank.** At 2560 CSS pixels the 1440-pixel column leaves
   about 560 pixels of empty margin on each side.
7. **There is no history.** Seasons 2024 to 2026 are reachable, 2025 is already
   archived, and there is no record book, no rivalry view and no draft board.
8. **The browser is never tested.** The UI has 315 tests across 24 modules, and none of
   them runs a script. The Decisions filters were broken by a CSS rule while the script
   was correct, and nothing caught it.

---

## 3. Decisions the owner needs to make

Some items cannot start until one of these is settled. Each lists the items it unlocks.

### Decision 1: a per-simulation outcome export

**Unlocks:** UI-O6, UI-O7 (the playoff machine), UI-O8, UI-O9, UI-O10, UI-E5.

The most distinctive feature a simulation can offer is the NYT-style what-if: pin some
results and watch the odds move. The web UI must never import the engine, so it cannot
re-simulate; it can only filter simulated seasons the engine already ran. For that the
engine has to write out, for each of its simulated seasons, who won each remaining
head-to-head game, who beat the median each week, the final seeds, and the champion. For
8 teams, 10,000 seasons and about 11 remaining weeks that is roughly 1.5 million bits
plus the seeds, well under a megabyte.

**The catch.** The golden master hashes every payload `export_and_visualize` passes to
`save_json` (stage B) and every argument `run_simulation` passes to it (stage A). Any new
export regenerates the goldens, and the release policy calls any intended regeneration a
MAJOR, even when no prediction changes. Three options:

1. Accept a MAJOR for an additive export. Honest to the letter of the policy, and noisy.
2. Amend the policy: an additive export that changes no existing payload is MINOR, and
   the goldens are regenerated in their own commit with the diff shown to be additions
   only. This mirrors how B30's golden regeneration was proven "trajectories only".
3. Write the export from outside the pinned monoliths. Not obviously possible without
   touching `run_simulation`, which rule 4 protects while R1 stands.

**Owner ruling (2026-09-28): option 2.** CLAUDE.md's release policy now says an export that only
adds a file is MINOR. The export landed on `engine/sim-outcomes-export` as a policy commit, a red
characterisation, the capture, and then the goldens regenerated alone. Every existing hash is
unchanged in all three scenarios, and one entry was added to each.

### Decision 2: new luck measures

**Unlocks:** UI-R7, and any "forecast luck" or "median luck" display.

The luck ledger's five measures were pre-registered on purpose, no combined score is
produced on purpose, and luck stays out of the default pages by the owner's call of
2026-09-21. The research proposes more: forecast luck (actual wins minus the sum of
pre-game win probabilities), median luck, and a schedule-swap matrix. Adding them after
seeing this season's data is exactly the "story instead of a test" the ledger warns
against. Either pre-register them now, with definitions frozen in `docs/LUCK_LEDGER.md`
before they are ever computed, or leave them out.

### Decision 3: player headshots and team logos

**Unlocks:** UI-P6, UI-E7, and the richer versions of UI-M9 and UI-A2.

Sleeper serves both, and both were checked:
`https://sleepercdn.com/content/nfl/players/thumb/<player_id>.jpg` and
`https://sleepercdn.com/images/team_logos/nfl/<abbr>.png` return 200. Loading them in the
browser at view time does not break the rule that the server never fetches at render,
but it sends a request to a third party on every page view and fails offline. Caching
them locally at sync time avoids both.

### Decision 4: log failed waiver claims

**Unlocks:** UI-W4, and the calibration half of UI-W5.

The best FAAB idea in the research is to price a claim at the highest losing bid, the
amount that actually had to be beaten. The decision log keeps completed transactions
only: 133 of them, none with a status field, no failed claims. Capturing losing bids
means extending the sync. That touches the sync-stage golden (`tests.golden_sync`), so
it is its own piece of work.

### Decision 5: the shape of the 4K layout

**Unlocks:** UI-V1.

Either widen the single column (simple, safe), or turn Home into a three-pane dashboard
at very wide viewports: standings and league games on the left, the matchup and live
panel in the centre, the watch list and scoring feed on the right. Fantasy Life's HQ uses
three panes. The second is more useful and more work.

### Decision 6: where the developer pages live

**Unlocks:** UI-A5.

The developer view has eleven tabs. Records, Jobs, Logs, System and Sync could move under
one "Developer" menu, leaving a top bar of Home, Matchups, League, Players, Forecasts,
Accuracy, Decisions and Tools.

### Decision 7: 2024 in the history pages

**Unlocks:** the full versions of UI-H1 and UI-H6.

The 2025 season is archived in `data/logs/season_2025.json`. The 2024 league is reachable
only through `KNOWN_LEAGUE_IDS` because the renewal chain is broken (B20). Bringing it in
needs an ingest like the 2025 one.

### Decision 8: can the UI keep anything of the owner's

**Unlocks:** UI-Q3.

Today the UI writes only its own settings, job logs and backups under `data/local/webui`.
A pick'em against the model would store the owner's picks. That belongs in the same
local directory, never in `data/logs`, but it is still a new kind of write.

---

## 4. Guardrails every item inherits

These are not suggestions; each exists in `CLAUDE.md`, `docs/WEB_UI.md` or a memory note,
and each was learned on this codebase.

- **The web process never imports the engine.** Anything that needs a simulation is a job
  (and runs one at a time, R1) or reads an export.
- **No network at page render.** A page renders from disk. Network reads happen on an
  explicit refresh (the live panel's pattern) or at sync time.
- **Nothing writes under `data/` except Sync.** The UI's own state lives under
  `data/local/webui`.
- **Both views, always** (rule 10). A new page or sentence is plain in both views or sits
  inside `{% if dev %}`, and `tests.test_webui_modes` guards the vocabulary.
- **No real names in any file.** The overlay renames in memory for the owner's eyes only.
  URLs, records, logs and anything written to disk stay pseudonymous. Anything exported
  from the browser for sharing is the owner's own output.
- **Week versus season.** Never answer a week-specific question with a season-level
  baseline, and always label which one a number is.
- **No lookahead.** Lineup judgements use `expected_pre`. Hindsight measures may be shown,
  labelled as hindsight, and never used to rank decisions.
- **Luck is pulled, not pushed**, uses the five pre-registered measures, and has no
  combined score (see Decision 2).
- **FAAB amounts are not priced by the simulation** (F31). A budget delta run through the
  paired evaluation reports roughly zero by construction.
- **Tests first** (rule 1), and say plainly when a test could not have caught the defect.

---

## 5. How to read an item

Each item's tag line reads **priority · size · data · views**.

| field | values |
|---|---|
| priority | **P1** next, **P2** soon, **P3** later |
| size | **S** half a day or less, **M** one to two days, **L** three to five days, **XL** needs an engine or sync change or an owner decision first |
| data | **on disk**: files the app already has under `data/`. **live read**: a read the live panel already makes, or a new public read made only on an explicit refresh. **sync change**: the sync must capture something new. **engine export**: the engine must write something new (see Decision 1). |
| views | **both**, **dev only**, or **both, plain in simple** |

The **Done when** list under each item is the set of tests to write before the code.

IDs use the `UI-` prefix so they cannot collide with the audit's F-numbers, the scoped
backlog's B-numbers, the earlier web UI audit's U-numbers, or `docs/WEB_UI.md`'s
W-sections.

---

## 6. The index

| ID | Item | P | Size | Data |
|---|---|---|---|---|
| UI-F1 | Home leads with a stale pre-game number after kickoff | P1 | S | live read |
| UI-F2 | System counts warnings as failed sources | P1 | S | on disk |
| UI-F3 | A finished week's odds read shows as FAILED | P2 | S | on disk |
| UI-F4 | "Wins" mixes head-to-head and median results | P1 | S | on disk |
| UI-F5 | Pending trades print as a raw key/value dump | P2 | S | on disk |
| UI-F6 | Hover card calls a season mean "projection" | P1 | S | on disk |
| UI-F7 | Orphan cards and chips at 1280 px | P3 | S | none |
| UI-F8 | Standings Title column clips at phone width | P3 | S | none |
| UI-F9 | A player on no NFL team shows a bare dash | P3 | S | on disk |
| UI-F10 | The sync's own warning prints Python `None` | P3 | S | sync change |
| UI-F11 | Compare players' week is a bare number box | P3 | S | none |
| UI-A1 | Team pages | P1 | L | on disk |
| UI-A2 | Player pages | P1 | L | on disk |
| UI-A3 | Matchups page: every game, every week | P1 | L | on disk, live read |
| UI-A4 | Season schedule grid | P2 | M | on disk |
| UI-A5 | Navigation around teams, players and games | P2 | M | none |
| UI-A6 | Tool cards show their latest answer | P2 | S | on disk |
| UI-A7 | Records grouped by run, with headlines and diffs | P2 | M | on disk |
| UI-A8 | Jobs grouped by day and filterable | P3 | S | on disk |
| UI-A9 | Players and teams in the command palette | P2 | S | on disk |
| UI-M1 | Home knows what day of the week it is | P1 | M | on disk, live read |
| UI-M2 | A pinned score bar with players yet to play | P2 | S | live read |
| UI-M3 | Where the matchup is decided, slot by slot | P2 | M | on disk, live read |
| UI-M4 | Every scored IDP category labelled in the feed | P2 | S | live read |
| UI-M5 | Possession and red-zone chips | P3 | M | live read |
| UI-M6 | The other three league games, live | P2 | M | live read |
| UI-M7 | Per-source freshness in the live panel | P2 | S | live read |
| UI-M8 | No 0% or 100% until the result is decided | P1 | S | live read |
| UI-M9 | Game-day TV view, second version | P3 | M | live read |
| UI-M10 | Keys to step between games | P3 | S | none |
| UI-O1 | Standings that separate the two records | P1 | M | on disk |
| UI-O2 | A power rating from the simulation | P2 | S | on disk |
| UI-O3 | What each result cost or bought | P1 | S | on disk |
| UI-O4 | Remaining fantasy schedule strength | P2 | S | on disk |
| UI-O5 | NFL strength-of-schedule grid | P2 | M | on disk |
| UI-O6 | Wins-needed curve | P3 | XL | engine export |
| UI-O7 | The playoff machine | P2 | XL | engine export |
| UI-O8 | Leverage: which games matter most | P2 | L | engine export |
| UI-O9 | Rooting guide | P2 | M | engine export |
| UI-O10 | Clinch, elimination and controls-destiny markers | P3 | L | engine export |
| UI-O11 | Playoff bracket view | P2 | S | on disk |
| UI-O12 | Final wins as a range, not a number | P2 | S | on disk |
| UI-L1 | Lineup advice priced in win probability | P1 | M | on disk |
| UI-L2 | Three lineup objectives side by side | P2 | M | on disk |
| UI-L3 | What is still changeable on Sunday | P2 | S | live read |
| UI-L4 | Decision quality judged before the games | P3 | M | on disk |
| UI-P1 | Players page | P1 | L | on disk, live read |
| UI-P2 | The distribution strip | P1 | M | on disk |
| UI-P3 | How the model has done on each player | P3 | M | on disk |
| UI-P4 | Instant compare | P1 | M | on disk |
| UI-P5 | Hover card, second version | P2 | S | on disk |
| UI-P6 | Headshots and team logos | P3 | S | decision 3 |
| UI-P7 | Injury and practice report | P2 | M | on disk |
| UI-P8 | IDP category projections | P3 | L | sync change |
| UI-W1 | Waiver board | P1 | M | on disk, live read |
| UI-W2 | Waiver clock | P2 | S | on disk |
| UI-W3 | Waiver results card | P2 | S | on disk |
| UI-W4 | Highest-losing-bid ledger | P3 | XL | sync change |
| UI-W5 | Claim-win probability by bid | P3 | XL | new model |
| UI-W6 | FAAB standings and pace | P3 | S | on disk |
| UI-T1 | Trade builder with an instant estimate | P2 | L | on disk |
| UI-T2 | Verdicts stated in standard errors | P1 | S | on disk |
| UI-T3 | Contributing factors, computed | P2 | S | on disk |
| UI-T4 | Process against result for completed trades | P3 | L | on disk, maybe sync change |
| UI-T5 | Trade deadline countdown | P3 | S | on disk |
| UI-T6 | Trade finder ordered by acceptability | P3 | S | on disk |
| UI-H1 | Record book | P2 | M | on disk |
| UI-H2 | Rivalries | P2 | M | on disk |
| UI-H3 | Draft board | P2 | M | on disk |
| UI-H4 | Records only a model can keep | P3 | S | on disk |
| UI-H5 | The season as a story | P3 | M | on disk |
| UI-H6 | Past seasons: champions, standings, brackets | P3 | M | on disk, decision 7 |
| UI-R1 | Weekly recap page | P2 | M | on disk |
| UI-R2 | Weekly awards | P2 | S | on disk |
| UI-R3 | Copy as a Sleeper chat post | P3 | S | on disk |
| UI-R4 | CSV from every table | P3 | S | none |
| UI-R5 | More alert triggers | P2 | M | on disk |
| UI-R6 | Luck, pulled not pushed | P2 | S | on disk |
| UI-R7 | Schedule-swap matrix | P3 | M | decision 2 |
| UI-Q1 | Accuracy, second version | P3 | M | on disk |
| UI-Q2 | What the model said at the time, on every result | P2 | S | on disk |
| UI-Q3 | The owner's picks against the model's | P3 | M | decision 8 |
| UI-Q4 | Reconcile the two calibration reads | P2 | S | on disk |
| UI-V1 | A 4K layout | P1 | M | none |
| UI-V2 | Native charts in place of the week's images | P2 | L | on disk |
| UI-V3 | Chart primitives | P2 | L | none |
| UI-V4 | Team and NFL colours | P3 | S | live read |
| UI-V5 | Compact density | P3 | S | none |
| UI-V6 | One grammar for uncertainty | P1 | S | none |
| UI-V7 | Accessibility pass | P2 | M | none |
| UI-V8 | Early-season states that teach | P3 | S | none |
| UI-E1 | A browser test layer | P1 | L | none |
| UI-E2 | Cacheable static assets | P2 | S | none |
| UI-E3 | A read-only JSON API | P2 | M | on disk |
| UI-E4 | One source for every odds number | P1 | S | on disk |
| UI-E5 | Per-simulation outcome plumbing | P2 | L | engine export |
| UI-E6 | Sandbox root | P3 | M | none |
| UI-E7 | Local image cache at sync | P3 | S | decision 3 |
| UI-E8 | Server-sent events | P3 | M | none |
| UI-E9 | Lighter long pages | P2 | S | none |
| UI-E10 | Audit harness fixes | P3 | S | none |
| UI-E11 | Inline styles into the design system | P3 | S | none |

102 items: 21 at P1, 44 at P2, 37 at P3.

---

## 7. The log

### F: fixes found in this audit

Defects observed during the crawl. Each is small and certain; none needs research.

#### UI-F1 · Home leads with a stale pre-game number after kickoff
`P1 · S · live read · both`

**Why.** On Monday 2026-09-28 at 10:55 the hero read "76.7% to win, pre-game" and the live
panel said "not fetched yet", while the matchup stood at 164.0 to 188.8 with one starter
left, which the live board prices at about 11%. Live data loads only on Refresh or when
auto-refresh has been ticked. The first thing the owner sees is wrong by 65 points.

**Build.**
- When the synced kickoffs show that at least one game of the week has started, the page
  fetches `/api/live` on load. The board's 45-second floor still applies, so reloading
  cannot hammer Sleeper.
- The broader fix, a home page that knows the week's phase, is UI-M1. This item is the
  one-line version that should ship first.

**Done when.**
- The home context exposes whether any kickoff of the current week has passed, tested at
  a time before the first kickoff, between kickoffs, and after the last.
- The page script requests live data on load in that state (presence now; executed once
  UI-E1 exists).

#### UI-F2 · System counts warnings as failed sources
`P1 · S · on disk · dev only`

**Why.** The System page's lede says "30 sources fell back to an older copy". Its own table
shows 11 sources OK and one (Vegas odds) failed. The 30 is the length of the sync's
degraded-warnings list: name collisions, carried baselines, depth-chart disagreements and
FAAB adjustments.

**Build.** Count failed and fallen-back sources from the manifest's `sources`, and count
warnings separately: "1 source failed and the sync raised 30 warnings".

**Done when.** A manifest with one failed source and thirty warnings renders both numbers
correctly, and neither is called the other.

#### UI-F3 · A finished week's odds read shows as FAILED
`P2 · S · on disk · dev only`

**Why.** The Vegas source reads FAILED after every sync that runs once a week's games have
been played, because the odds API has no lines left for games already over. The odds key
was checked on 2026-09-28 and is accepted, with 190 requests left. A timing artifact
wears the same red label as a rejected key.

**Build.** When every synced kickoff of the week precedes the sync's start, show "no lines
left: week N's games are over" in a neutral tone. A real failure (a rejected key, the API
unreachable) keeps the red label.

**Done when.** The three cases render three different messages: a finished week, a
rejected key, and an unreachable API.

#### UI-F4 · "Wins" mixes head-to-head and median results
`P1 · S · on disk · both`

**Why.** The League tile "My record 2–2 · head-to-head plus the median game" and the
standings "W" column both show the combined record. The field is named `h2h_wins` but
holds both kinds of win. In a median league those are two different contests, and a
third-party guide recommends its own analyzer over Sleeper's standings for exactly this
reason.

**Build.** The full fix is UI-O1. At minimum, relabel the tile and column as combined
wins, and show the head-to-head and median records beneath the combined one.

**Done when.** The weekly actuals for a fixture season with known head-to-head and median
results produce three correct records, and no label calls a combined record
"head-to-head".

#### UI-F5 · Pending trades print as a raw key/value dump
`P2 · S · on disk · both`

**Why.** League renders each pending trade by printing its dictionary: bold keys with
underscores replaced, values in a row.

**Build.** A trade card: the two sides, each player with position and season mean, "in
review until" (the league reviews trades for one day), and a link that opens the trade
evaluator pre-filled with the deal.

**Done when.** A fixture pending trade renders as two sides with players, no raw keys
appear, and the evaluator link carries both teams and both player lists.

#### UI-F6 · Hover card calls a season mean "projection"
`P1 · S · on disk · both`

**Why.** The player card reads `mean` from `player_baselines.json`, which is the season
baseline, and labels it "projection". That is the week-versus-season trap the owner
already lost three reads to in one night. The live module already knows the right
precedence: a this-week price from the newest lineup or matchup record wins over the
baseline.

**Build.** Label the baseline "season mean". Where a lineup or matchup record prices the
player for this week, show "this week" as well, with the record's time.

**Done when.** A player priced by a lineup record shows both numbers with correct labels,
and one priced only by the baseline says "season mean" and never "projection".

#### UI-F7 · Orphan cards and chips at 1280 px
`P3 · S · none · both`

**Why.** At 1280 px the League roster chips wrap seven and one, and the Tools grid leaves
Roster calendar and Bid review alone on their rows.

**Build.** Column counts that divide the item counts, or a final row that stretches.

**Done when.** The audit harness's desktop screenshots show no single-item final row on
League or Tools.

#### UI-F8 · Standings Title column clips at phone width
`P3 · S · none · both`

**Why.** At 520 px the home standings push the Title column past the edge; its bars are
cut off. Not a priority while the UI is desktop-first, but it is a one-way door if left.

**Build.** Drop the in-cell bar below a width, or let the table scroll in its own box.

**Done when.** The harness's overflow probe reports nothing past the edge on Home at 520
px, with the standings visible.

#### UI-F9 · A player on no NFL team shows a bare dash
`P3 · S · on disk · both`

**Why.** A rostered player with no NFL team, whitelisted at zero, shows "—" for projection
and −10.3 value over replacement with no explanation.

**Build.** "No NFL team" in place of the dash, and a tooltip explaining that the zero
cancels itself when the player signs.

**Done when.** The fixture's team-less player renders the explanation, not a dash.

#### UI-F10 · The sync's own warning prints Python `None`
`P3 · S · sync change · dev only`

**Why.** A name-collision warning reads "pid 6994 (CB, None)" for a player with no team.
It appears verbatim on 34 developer pages and trips the audit harness's leak probe, which
looks for a literal `None`.

**Build.** Change the wording in `fantasy_sim/sync.py` to "no team". Check whether the sync
golden covers warning text before changing it. UI-E10 covers the harness side.

**Done when.** The warning reads "no team" and the harness reports no `None` on the pages
it flagged.

#### UI-F11 · Compare players' week is a bare number box
`P3 · S · none · both`

**Why.** The week field is a free number input with no bounds and no visible default.

**Build.** A select of the season's weeks with the current one chosen.

**Done when.** The field offers exactly the season's weeks and defaults to the current
week.

---

### A: information architecture

#### UI-A1 · Team pages
`P1 · L · on disk · both`

**Why.** Every competitor has a team page, and this UI has none. A team link today jumps to
an anchor on League. A team is the object a manager thinks in: who they are, how they are
doing, who they play next, and what they have done.

**Seen elsewhere.** ESPN, Sleeper and Yahoo team pages; League Legacy's rule that "every
record ties back to the member, team, matchup or season that earned it".

**Build.** `/team/<slug>`, where the slug comes from the pseudonym so URLs stay
pseudonymous.
- A header: mark, the three records (UI-O1), points for and against, rank, playoff and
  title odds with their season sparkline, and the week-over-week change (UI-O3).
- This week's matchup as a card linking to the game (UI-A3).
- The season schedule: past weeks with the score, the median result, and the pre-game win
  probability quoted at the time (UI-Q2); future weeks with the opponent and the win
  probability from the head-to-head matrix.
- The roster grouped into starters, bench and injured reserve, with this week's price and
  the season mean labelled separately (UI-F6), and value over replacement.
- Every transaction from the decision log with its paired-simulation grade.
- FAAB left, and the head-to-head record against the owner's team (UI-H2).
- For other teams, "Propose a trade" opening the trade builder (UI-T1).

**Not in scope.** Editing anything on Sleeper.

**Data.** Standings, rosters, the schedule, weekly actuals, the latest forecast export,
the decision log and the predictions log, all on disk.

**Done when.**
- All eight fixture teams render a page in both views, and the vocabulary guard passes.
- Every `teamlink` points to the team page.
- The schedule shows the pre-game probability for past weeks from the quoted row only.
- No real name appears in any URL.

**Watch out.** This is the page most likely to grow into a copy of League. League keeps
the comparison across teams; the team page keeps the depth on one.

#### UI-A2 · Player pages
`P1 · L · on disk · both`

**Why.** Hover cards exist, and there is nowhere to go from them. Competitors' player pages
are where research happens, and the complaints about them are about stale news and
projections that "don't make sense" without context.

**Seen elsewhere.** ESPN 2025's player card (game logs, bio, depth chart); Sleeper's
research panel reachable "from anywhere"; Draft Sharks' argument that distribution shape
matters as much as the endpoints.

**Build.** `/player/<player_id>`.
- Identity from the Sleeper players cache: position, NFL team, depth-chart order, age,
  college, height, number.
- Availability: injury status, body part, notes, and practice participation and
  description, each with the cache's update time.
- Ownership in this league, or free agent, or "on waivers until" (UI-W1).
- This week's price and the season mean labelled apart (UI-F6), with the distribution
  strip (UI-P2).
- A week-by-week chart of the pre-game range against what the player actually scored, from the
  projection log and first recorded scores.
- The player's share of simulated championships, from the championship-share export.
- Every transaction involving the player, with its grade.
- Actions: compare (UI-P4), and, for another team's player, propose a trade (UI-T1).

**Data.** Players cache, baselines, `player_variance.json`, the projection log, first
recorded scores, the insights export and the decision log, all on disk.

**Done when.**
- A fixture player with a lineup record renders both prices and the history chart in both
  views.
- The injury block shows the cache's time and says so when the cache is older than the
  last sync.
- Every player name in the UI that carries `data-player` links here.

#### UI-A3 · Matchups page: every game, every week
`P1 · L · on disk, live read · both`

**Why.** Only the owner's own game has a page. "This week's games" on Home is a strip of
four win percentages. ESPN's 2025 app made the whole league's scores a swipeable strip,
and Sleeper's reviews single out how hard it is to follow the league's games live.

**Build.** `/matchups/week-<n>` with a week selector across the season.
- Past weeks: final scores, the median cut, the pre-game quoted probability, and an upset
  flag where the winner was the underdog.
- The current week: live scores and live win probability for all four games, which means
  extending the live snapshot to return every roster the board already reads.
- Future weeks: win probability from the head-to-head matrix.
- A box score per game: both lineups slot by slot, with the price, points and gap per
  player.

**Data.** Weekly actuals and the predictions log on disk; the live panel's existing
Sleeper matchups read returns every roster already.

**Done when.**
- A fixture past week shows four finals with the quoted probabilities and the correct
  upset flags.
- The live snapshot returns all four games, still writes nothing, and still respects the
  45-second floor.
- The page renders in both views.

#### UI-A4 · Season schedule grid
`P2 · M · on disk · both`

**Why.** There is no view of who plays whom across the season.

**Build.** Fourteen weeks by eight teams. Each cell is the opponent and either the result
(score, and the median result) or the win probability. Clicking a cell opens that game
(UI-A3). The owner's row is highlighted, and the run-in weeks 12 to 14 are shaded.

**Data.** `league_schedule.json`, weekly actuals and the head-to-head matrix.

**Done when.** The grid for the fixture schedule is complete, symmetric (a team's opponent
lists that team back), and every cell links to its game.

#### UI-A5 · Navigation around teams, players and games
`P2 · M · none · both`

**Why.** The developer view has eleven tabs, most of them for machinery. Sleeper's 2018
redesign is the cautionary tale: it optimised for the first visit, made repeat tasks
tedious, and had to be reverted.

**Build.** Top level: Home, Matchups, League, Players, Forecasts, Decisions, Tools, plus
Accuracy for the developer view. Records, Jobs, Logs, System and Sync under one
developer menu (Decision 6). Each tool also appears where its question arises: compare
from a player page, trade evaluation from a team page, waiver targets on the waiver
board.

**Done when.** The simple view's top bar is unchanged in meaning, and the vocabulary guard
passes. Every tool is reachable from at least one object page as well as from Tools.

#### UI-A6 · Tool cards show their latest answer
`P2 · S · on disk · both`

**Why.** The Tools page is a menu of questions. The answers already exist as records, and
none is shown. Competitors show the answer; you press a button only to refresh it.

**Build.** Each card shows its newest record's headline ("optimal lineup: 207.9 projected"),
its age ("3 hours ago"), and a button to run it again.

**Done when.** A card for a tool with a record shows the headline and age, and a card
without one says it has not been run yet.

#### UI-A7 · Records grouped by run, with headlines and diffs
`P2 · M · on disk · dev only`

**Why.** Week 3 alone has 15 committed records and 81 archived ones in one flat list. A
weekly digest run writes six records at the same minute, and they appear as six unrelated
rows with a file-size column. This was scoped in the first web UI audit and never built.

**Build.**
- Records written within the same minute become one run with its tools listed.
- A filter by tool.
- The headline of each record in its row, and no size column.
- "Compare with the previous run": the differences between two records of the same tool
  (the unbuilt half of U13).

**Done when.** A fixture weekly run shows as one row, and the diff of two lineup records
lists exactly the slots that changed.

#### UI-A8 · Jobs grouped by day and filterable
`P3 · S · on disk · dev only`

**Why.** Jobs is one long list. Also from the first audit, never built.

**Build.** Group by day, filter by tool and state.

**Done when.** Fixture jobs across two days render under two headings, and each filter
narrows the list correctly.

#### UI-A9 · Players and teams in the command palette
`P2 · S · on disk · both`

**Why.** The palette reaches pages, tools and teams, but not players. The player index at
`/api/players` already exists.

**Build.** Typing a player's name offers that player's page (UI-A2).

**Done when.** A fixture player's name returns that player's page as a palette result.

---

### M: matchup and game day

#### UI-M1 · Home knows what day of the week it is
`P1 · M · on disk, live read · both`

**Why.** The underlying problem behind UI-F1. A fantasy week has four phases, and Home
behaves the same through all of them.

**Build.** Home derives the phase from the synced kickoffs and the weekly actuals:
- **Before lock** (until the week's first kickoff): the preview, the countdown, and the
  lineup check (UI-L1).
- **Live** (from the first kickoff until every starter on both sides has finished): the
  live number leads (already built), and live data loads on its own.
- **Final** (every starter done): the result, win or loss, the final score, the median
  result, and what the result did to the owner's odds (UI-O3). No probability is shown
  for a decided game.
- **Rolled over** (the sync has advanced the week): next week's preview, with last week's
  result as a compact line above it.

**Done when.** Four fixture states render the four phases, and the Final state never
shows a win probability.

**Watch out.** "Every starter done" needs every starter's game clock; if a clock is
unavailable, stay in Live rather than declare a result.

#### UI-M2 · A pinned score bar with players yet to play
`P2 · S · live read · both`

**Why.** The starter tables are long, especially with IDP, and the score scrolls away.
ESPN pins the score while you scroll; Fantasy Life shows players yet to play.

**Build.** A slim bar that sticks while the live panel scrolls: both scores, the live
probability, and "4 of 13 yet to play" for each side.

**Done when.** The bar carries the same numbers as the panel (presence now; executed with
UI-E1).

#### UI-M3 · Where the matchup is decided, slot by slot
`P2 · M · on disk, live read · both`

**Why.** A total probability does not say where it comes from. StatChasers' analyzer
shows where matchups are "actually decided". A simulation can say this exactly.

**Build.** Pair the two lineups slot by slot. For each pair, show the probability that
the owner's player outscores the opponent's, and each slot's share of the variance of
the final margin. Before the games this uses the exported distributions; during them,
the remaining share of each distribution.

**Done when.** For a fixture with known distributions the per-slot probabilities match a
direct calculation, and the variance shares sum to one.

**Watch out.** Slot pairing is a presentation choice, not a real contest. Say so.

#### UI-M4 · Every scored IDP category labelled in the feed
`P2 · S · live read · both`

**Why.** The scoring feed prices any category with the league's own weights, but its
labels were written for common keys. No commercial product breaks IDP points down well.

**Build.** Compare the league's scoring settings against the feed's label map and add a
readable label for every scored key. A starter's row gets its category totals in a
tooltip.

**Done when.** A test lists every key in the fixture scoring settings and fails for any
without a label.

#### UI-M5 · Possession and red-zone chips
`P3 · M · live read · both`

**Why.** Sleeper's field-position view shows how close a rostered player is to scoring.
The ESPN scoreboard the live panel already fetches carries a `situation` block during
live games (possession, down and distance, red zone, the last play). This could not be
verified outside a live game window and must be checked on a Sunday before building.

**Build.** A red-zone marker beside any starter whose team has the ball inside the 20,
and a possession marker on the game strip.

**Done when.** A recorded live scoreboard payload renders the markers, and a payload
without `situation` renders none and raises nothing.

#### UI-M6 · The other three league games, live
`P2 · M · live read · both`

**Why.** On a Sunday the owner watches all four games, not just their own.

**Build.** A compact strip under the hero: the other three games with live scores and
live probabilities. Once the per-simulation export exists (Decision 1), add how each game
moves the owner's odds (UI-O9).

**Done when.** The live snapshot returns all four games (shared with UI-A3), and the strip
renders three of them.

#### UI-M7 · Per-source freshness in the live panel
`P2 · S · live read · both`

**Why.** Stale-looking live data is the most common live-scoring complaint in ESPN and
Sleeper reviews.

**Build.** "Sleeper points 10:42:05 · NFL clocks 10:42:04" under the panel, and a warning
tone when either is more than five minutes old during a live window.

**Done when.** The snapshot carries a time per source, and a fixture with a stale clock
shows the warning.

#### UI-M8 · No 0% or 100% until the result is decided
`P1 · S · live read · both`

**Why.** Sleeper had to rebuild its win probability "to avoid … too-early claims of
certain victory or certain loss". The live estimate here is a Normal approximation, which
reaches 0.0% and 100.0% on rounding long before a game is actually settled.

**Build.** Show "under 0.1%" or "over 99.9%" unless the side behind has no starters left
to play, in which case show the result.

**Done when.** A fixture with a tiny remaining probability and players still to play shows
"under 0.1%", and one with nobody left shows the final result.

#### UI-M9 · Game-day TV view, second version
`P3 · M · live read · both`

**Why.** The TV view is a good start and ignores most of what the scoreboard provides.

**Build.** NFL team colours from the scoreboard's `color` and `alternateColor`, logos
(Decision 3), weather and the DraftKings line per game, and each game's stat leaders. A
three-pane layout at wide viewports.

**Done when.** A recorded scoreboard renders colours, lines and weather for each game.

#### UI-M10 · Keys to step between games
`P3 · S · none · both`

**Why.** ESPN lets you swipe between matchups. On a keyboard that is `[` and `]`.

**Build.** On the Matchups page, `[` and `]` move to the previous and next game, and the
shortcut sheet lists them.

**Done when.** The shortcut sheet lists the keys (executed with UI-E1).

---

### O: odds, standings and what-if

#### UI-O1 · Standings that separate the two records
`P1 · M · on disk · both`

**Why.** In this league half of all wins come from the median game, and the standings fold
them into one number (UI-F4). MFL's rank report and every serious third-party tool show
more.

**Seen elsewhere.** MFL (potential points, efficiency, all-play, power rank); ffwrapped
(head-to-head separate from the combined record); playoffstatus (clinch and elimination
marks); nflplotR (percentage bars inside table cells).

**Build.** One standings component used on Home, League and team pages.
- Columns: combined record, head-to-head W-L, median W-L-T, all-play W-L, points for,
  points against, streak, games back of the last playoff spot, playoff percentage as an
  in-cell bar, title percentage, and the week-over-week change in playoff odds (UI-O3).
- Clinch and elimination markers once they can be computed honestly (UI-O10).
- Column visibility is a choice: Home shows a compact subset, League shows everything.

**Data.** Weekly actuals carry `h2h_win` and `median_win` for every team and week, and
points against comes from the schedule pairs.

**Done when.** A fixture season with known results produces every column correctly,
including a tie against the median. Home, League and a team page render the same numbers
from the same helper.

#### UI-O2 · A power rating from the simulation
`P2 · S · on disk · both`

**Why.** Every open-source power ranking is a hand-weighted formula (one uses 6, 2 and 400
as weights). The simulation already knows how good each team is.

**Build.** The rating is the probability of beating a randomly chosen league opponent
this week: the mean of the team's row in the head-to-head matrix. Show it with its
standard error, a rank interval ("2nd, plausibly 1st to 4th"), and the gap between record
rank and rating rank.

**Done when.** The rating for a fixture matrix equals the row mean, and two teams whose
intervals overlap are shown as overlapping rather than strictly ordered.

**Watch out.** Before this is ever called predictive, backtest it as a predictor of next
week's wins on the Accuracy page.

#### UI-O3 · What each result cost or bought
`P1 · S · on disk · both`

**Why.** myfantasyanalyzer's framing is the most useful sentence in playoff odds: what a
result cost or bought. The per-week exports hold every team's odds for every week.

**Build.** For each team, the change in playoff and title odds from one week's forecast to
the next, beside the result that caused it. On Home, the owner's own change in the Final
phase (UI-M1). On Forecasts, a slope chart of all eight teams from last week to this
week.

**Done when.** Two fixture weeks produce the right deltas, and a week with no earlier
forecast shows no delta rather than a zero.

#### UI-O4 · Remaining fantasy schedule strength
`P2 · S · on disk · both`

**Why.** Standings say where a team is, not what is left.

**Build.** For each team, the mean probability of losing to each remaining opponent from
the head-to-head matrix, against a league-average opponent, with the run-in weeks 12 to 14
shown separately.

**Done when.** A fixture matrix and schedule give the expected values, and a team with no
games left shows nothing.

#### UI-O5 · NFL strength-of-schedule grid
`P2 · M · on disk · both`

**Why.** `strength_of_schedule.json` holds every NFL team's implied total for every
remaining week. The UI shows it only as three static images. LeagueStation's grid is the
model: 32 teams by the remaining weeks, tiered cells, a playoff-window slider.

**Build.** A native heat grid with a text tier in every cell, never colour alone, and the
fantasy playoff weeks 15 to 17 shaded. Clicking a cell shows the number and its rank.

**Not in scope.** An IDP-specific version, which needs opposing offensive volume (plays,
pass attempts). That is not on disk and would need a sync change.

**Done when.** The grid for the fixture file is complete, every cell carries a text tier,
and the three images are no longer needed on the week page.

#### UI-O6 · Wins-needed curve
`P3 · XL · engine export · both`

**Why.** Playoff Computer and the NFL "magic number" both answer "how many do I need?".
The honest version is a curve: the probability of making the playoffs for each final win
total. `win_distributions` gives the win distribution alone, not joined with the playoff
result.

**Build.** From the per-simulation export, the probability of making the playoffs given a
final total of k wins, for each k, with the count of seasons behind each bar.

**Done when.** A synthetic export with a known relationship reproduces it, and bars
backed by fewer than 200 seasons are greyed.

#### UI-O7 · The playoff machine
`P2 · XL · engine export · both`

**Why.** The single most distinctive feature a simulation can offer, and the biggest gap
the open-source research found. The NYT Upshot simulator lets you "choose the outcomes of
just a few games and see how your team's chances grow or shrink". ESPN's Playoff Machine
does it for the NFL. Nobody does it for a fantasy league with median wins and points
tiebreakers, because nobody else simulates points.

**Build.** Needs Decision 1.
- Pick any remaining head-to-head result, and whether any team beats the median in any
  week.
- The page filters the stored simulated seasons to those where every pick happened, then
  shows playoff, seed and title odds for every team, each with its count ("4,812 of
  10,000 seasons match") and its standard error.
- Presets: favourites win, chaos, and the owner's team wins out.
- The previous result stays on screen while a new one computes (a LeaguePulse lesson:
  "showing stale numbers beats showing a spinner").
- Below 200 matching seasons, numbers are greyed and say why.

**Not in scope.** Re-simulating. The web process never imports the engine.

**Done when.**
- A synthetic export with a planted relationship returns the planted conditional odds.
- The count and standard error are shown with every conditioned number.
- Pinning a full week (four games and eight median results) is refused or greyed when it
  leaves too few seasons, rather than shown as precise.

**Watch out.** Each pin roughly halves the sample. One full week of this league cuts
10,000 seasons to about 600. The page must make that visible.

#### UI-O8 · Leverage: which games matter most
`P2 · L · engine export · both`

**Why.** FiveThirtyEight ranked games by how much they swing playoff odds; baseball's
leverage index normalises the swing so 1.0 is typical. A median league adds a second
lever every week.

**Build.** After UI-O7's export:
- For each team and remaining week: playoff odds if it wins versus loses, and if it beats
  versus misses the median.
- The probability-weighted swing, 2 × p × (1 − p) × the difference.
- A leverage index scaled so the season's average game is 1.0.
- A team-by-week grid, and "the biggest games this week" summed across all eight teams.

**Done when.** A synthetic export reproduces a hand-computed swing, and the index averages
1.0 across the season by construction.

#### UI-O9 · Rooting guide
`P2 · M · engine export · both`

**Why.** The kevin-kalish simulator's "rooting interests" answers the Sunday question:
which result in the other games helps me? It re-simulates 3,000 seasons per case. Here it
is a filter over 10,000 with common random numbers for free.

**Build.** For every other game this week: which side to root for, the change in the
owner's odds if each side wins, and the standard error. In a median league, also which
teams near the median the owner should want to score low.

**Done when.** A synthetic export with one decisive game ranks that game first with the
right sign.

#### UI-O10 · Clinch, elimination and controls-destiny markers
`P3 · L · engine export · both`

**Why.** Standings everywhere carry clinch and elimination marks. The trap, which
LeaguePulse falls into, is calling a team "clinched" because it made the playoffs in all
10,000 simulated seasons. An event rarer than one in 10,000 then reads as certain.

**Build.**
- "Controls its own destiny": makes the playoffs in every simulated season where it wins
  out and beats the median out.
- "Over 99.9%" for sim-frequency near-certainty.
- "Clinched" and "eliminated" only when proven by enumerating the remaining results or a
  worst-case bound, not from the simulation's frequency.

**Done when.** A team at 100% of simulations but not provably clinched shows "over 99.9%",
never "clinched".

#### UI-O11 · Playoff bracket view
`P2 · S · on disk · both`

**Why.** `data/current/playoff_bracket.json` has the bracket structure: four teams from
week 15, the seeds and the rounds. No page shows it.

**Build.** Before the playoffs, the projected bracket from the most likely seeding, with
each seed's probability. During them, the real bracket with results. Once Decision 1
lands, each team's title probability given its seed.

**Done when.** The fixture bracket renders both rounds, and a projected bracket labels
itself as projected.

#### UI-O12 · Final wins as a range, not a number
`P2 · S · on disk · both`

**Why.** Home says "expected wins 17.9". `win_distributions` holds every team's p1, p10,
p25, p50, p75, p90 and p99.

**Build.** A percentile strip per team with the wins already banked marked, on Home and
team pages.

**Done when.** A fixture distribution draws the right percentiles, and the banked marker
never sits above the p1.

---

### L: lineups

#### UI-L1 · Lineup advice priced in win probability
`P1 · M · on disk · both`

**Why.** The "the model would field a different lineup" callout prices a swap in
projected points. Yahoo's Assistant GM prices a change in win probability, and its
morning alert fires only when the change would raise it. The matchup-lineups record
already ranks four lineup constructions by their probability of beating the opponent.

**Build.** The callout shows the change in expected points, in the probability of winning
the head-to-head game, and in the probability of beating the median, each with its
standard error, from the newest matchup-lineups record, with the record's time.

**Done when.** A fixture record with a known difference renders all three deltas, and
without a matchup record the callout shows points alone and says why.

#### UI-L2 · Three lineup objectives side by side
`P2 · M · on disk · both`

**Why.** Fantasy Life shows safe and upside lineups; Fantasy Math optimises for win
probability. A simulation can show the three that matter here: the most expected points,
the best chance against this opponent, and the best chance against the median (which
ignores the opponent and can pick differently).

**Build.** Three columns with the players who differ highlighted, and "all three agree"
when they do, which should be most weeks.

**Done when.** A fixture where the objectives disagree highlights exactly the differing
slots. If the median objective is not already in the tool's record, that is a tool
change and its own item.

#### UI-L3 · What is still changeable on Sunday
`P2 · S · live read · both`

**Why.** Sleeper locks each player when that player's game starts. During the Sunday window the
useful question is what can still move, and the lineup record already carries
`locks_active` and `locked_excluded`.

**Build.** During the Live phase, show each unlocked slot's best available alternative,
and grey out the locked ones.

**Done when.** A fixture with some games started shows only the unlocked slots as
changeable.

#### UI-L4 · Decision quality judged before the games
`P3 · M · on disk · both`

**Why.** Every open-source "coaching efficiency" is hindsight: actual points over the
hindsight-best lineup. It punishes correct decisions that did not work out. This project
has the pre-game projections, so it can separate the decision from the dice.

**Build.** For each past week:
- Before-the-games efficiency: expected points of the lineup started, over the expected
  points of the best lineup available beforehand.
- The hindsight gap, labelled as hindsight.
- The difference between them: the part that was not a decision.

The existing decision scorecard tool supplies the start/sit judgements; this surfaces
them on team pages and a lineup history.

**Done when.** A fixture week where the right call lost scores full marks before the games
and a hindsight gap, labelled as such.

---

### P: players

#### UI-P1 · Players page
`P1 · L · on disk, live read · both`

**Why.** There is no way to browse players. Yahoo's Buzz Index and ESPN's Players screen
are the models.

**Build.** `/players`: every player, filterable by position, availability and team, and
sortable.
- Columns: position, NFL team, owner (or "free agent", or "on waivers until …"), this
  week's price with its range, season mean, value over replacement, status, bye, and
  "rostered by N of 8".
- Sleeper's trending adds as a separate column labelled "crowd signal, not the model".
- The free-agent filter is the waiver board (UI-W1).

**Data.** Baselines, rosters, the players cache and the distributions on disk. Trending is
a public read (checked: 200), taken at sync or on an explicit refresh, never at render.

**Done when.** A fixture renders every player, each filter narrows correctly, and the
trending column carries its label and its read time.

#### UI-P2 · The distribution strip
`P1 · M · on disk · both`

**Why.** The project's core idea is that a projection is a distribution, and almost
nothing in the UI draws one. `player_variance.json` holds each rostered player's min, p10,
p25, p50, p75, p90 and max from the simulation.

**Build.** One compact component: a p10 to p90 bar, a p25 to p75 box, a mean tick, a pip
for the chance of scoring zero, and, where history exists, dots for the weeks already
played. Used in rosters, player pages, hover cards, compare and the waiver board. Built
with the dataviz method and a table fallback.

**Done when.** A fixture player's strip places each mark at its value on one scale, and a
bimodal case (a handcuff) is visibly different from a normal one with the same range.

#### UI-P3 · How the model has done on each player
`P3 · M · on disk · both`

**Why.** A per-player calibration read: where each week's actual landed inside the
pre-game range. Sleeper's reviews complain that projections "don't make sense"; this is
the answer, player by player.

**Build.** On the player page, the percentile of each week's actual within that week's
range. On Accuracy, the same across all rostered players as a histogram, which should be
flat if the ranges are right.

**Done when.** Synthetic data drawn from the stated distributions produces a flat
histogram within noise.

**Watch out.** Three weeks per player is noise. Show the count, and never label one
player's handful of weeks as a verdict.

#### UI-P4 · Instant compare
`P1 · M · on disk · both`

**Why.** "Start A or B?" takes 1.2 minutes on average because it always runs the joint
simulation. The exported distributions answer the common case instantly.

**Build.** Compare two to four players at once. Answer immediately from the distributions
as an estimate labelled as one (it ignores same-game correlation), and offer "run the
joint simulation" for the exact probability. When a joint result exists, show it in place
of the estimate.

**Done when.** The estimate for two fixture distributions matches a direct calculation,
and the page always labels which of the two answers it is showing.

#### UI-P5 · Hover card, second version
`P2 · S · on disk · both`

**Why.** After UI-F6 the card is correct but thin, and it is hover-only.

**Build.** The distribution strip (UI-P2), injury body part and practice participation
with times, "rostered by N of 8", and keyboard and tap access (open on focus and Enter).

**Done when.** The card opens from the keyboard (executed with UI-E1) and shows each field
for a fixture player.

#### UI-P6 · Headshots and team logos
`P3 · S · decision 3 · both`

**Why.** Every competitor shows faces and logos, and both are available (checked).

**Build.** After Decision 3: on player pages, rosters, the TV view and hover cards, with
the team mark as a fallback.

**Done when.** A player with no image falls back to the mark, and no image is requested
from a third party if Decision 3 chooses the local cache.

#### UI-P7 · Injury and practice report
`P2 · M · on disk · both`

**Why.** The watch list shows questionable starters. The players cache also knows the body
part, the notes and the week's practice participation, and none of it is shown.

**Build.** A report of every rostered player with a designation: status, body part,
practice participation, notes and their time, and the lineup consequence from the newest
lineup record (the fallback and the points given up).

**Done when.** A fixture designated starter shows the fallback and its cost, and entries older
than the last sync are marked as old.

#### UI-P8 · IDP category projections
`P3 · L · sync change · both`

**Why.** IDP is this league's differentiator and every tool's blind spot. Baselines carry a
mean, not expected tackles and sacks.

**Build.** If Sleeper's projection payload carries per-category IDP expectations, persist
them at sync and show them on player pages and in compare. Verify the payload before
scoping further.

**Done when.** Scoped after the payload check.

---

### W: waivers and FAAB

This league runs FAAB with a budget of 100, processes claims daily at 09:00 Pacific, and
leaves a dropped player on waivers for two days (`docs/WAIVER_MECHANICS.md`).

#### UI-W1 · Waiver board
`P1 · M · on disk, live read · both`

**Why.** Waiver targets is a tool you launch. ESPN's recommended and trending pickups are a
page you open. One ESPN complaint is worth designing against: after a pickup the app
sends you back to your team instead of the list.

**Build.** The Players page filtered to the available (UI-P1), ranked by the newest
waiver-targets record.
- This week's and rest-of-season value, the bid band, and the roster need it fills.
- "Free agent, instant, $0" versus "on waivers until <date> 09:00", from the drop times in
  the decision log and the two-day clearing period.
- Trending as a crowd signal.
- Everything stays on the board after any action.

**Done when.** A player dropped yesterday shows the right clearing time, and one dropped
three days ago shows as a free agent.

#### UI-W2 · Waiver clock
`P2 · S · on disk · both`

**Why.** Claims resolve every morning at 09:00, and the UI never says when.

**Build.** A countdown to the next run beside the kickoff countdown. The league settings
are not cached in `data/current/`, so the time comes from a config constant carrying a
sourcing comment (rule 5), or from a small sync change that caches the settings.

**Done when.** The countdown is right on both sides of 09:00 and across a daylight-saving
change.

#### UI-W3 · Waiver results card
`P2 · S · on disk · both`

**Why.** Sleeper turns waiver processing into an event with a results summary.

**Build.** After each run: who won each claim, the winning bid, and each claim's
paired-simulation grade from the decision log.

**Done when.** A fixture day of claims renders every winner with bid and grade.

#### UI-W4 · Highest-losing-bid ledger
`P3 · XL · sync change · both`

**Why.** The strongest FAAB finding in the research: price a claim at the highest losing
bid, the amount that actually had to be beaten. One study of 754,611 auctions found
winners paid a median of twice the runner-up. The losing bids are not recorded here.

**Build.** After Decision 4: per manager, the total paid above the runner-up, and the real
clearing prices laid against this project's bid bands.

**Done when.** Scoped after Decision 4.

#### UI-W5 · Claim-win probability by bid
`P3 · XL · new model · both`

**Why.** FantasyPros gives three bids; a curve is better. The engine already holds each
rival's measured FAAB behaviour as priors (F31).

**Build.** The probability of winning a claim against each bid amount, from the rivals'
priors, with the recommended bid where more money stops buying much more probability.

**Watch out.** This is a new model, not a display. It prices winning the claim, which F31
leaves unpriced for a reason: it is not the same as what the player is worth. It needs its
own backtest against real clearing prices, which needs UI-W4.

#### UI-W6 · FAAB standings and pace
`P3 · S · on disk · both`

**Why.** Budget left is shown as one number per team.

**Build.** Budget left, spend per week, and "who can outbid me" for a given target: every
team with more budget.

**Done when.** A fixture reproduces each team's pace, and the outbid list is correct.

---

### T: trades

The league's deadline is week 11, and a trade sits in review for one day.

#### UI-T1 · Trade builder with an instant estimate
`P2 · L · on disk · both`

**Why.** The trade evaluator is a form and a 5.8-minute wait. Sleeper's trade centre is
the most praised feature in its reviews: pick players from both rosters and see the deal
take shape.

**Build.** Pick players from both rosters, including from team pages. An instant estimate
appears: the change in expected points by slot and by week, bye collisions, and the cost
of the roster spot a two-for-one frees or consumes. "Run the paired simulation" gives the
real answer in playoff and title odds with standard errors.

**Done when.** The estimate for a fixture deal matches a direct calculation, and the page
always labels whether it shows the estimate or the simulation.

#### UI-T2 · Verdicts stated in standard errors
`P1 · S · on disk · both`

**Why.** A change of 0.8 points with a standard error of 1.1 reads as a win. Fantasy Life
turns trade results into tiers, but uncalibrated ones.

**Build.** One convention for trades, moves and the Decisions page: under two standard
errors reads "indistinguishable from no move", two to four "modest", above four "clear".
Always show both sides. Document these as display conventions in `docs/WEB_UI.md`, not as
statistical claims.

**Done when.** A test covers each tier's boundary, and the same thresholds apply on every
page that shows a paired result.

#### UI-T3 · Contributing factors, computed
`P2 · S · on disk · both`

**Why.** ESPN's trade grades explain themselves with generated text. The explanation here
can be computed.

**Build.** Which starting slots change, the expected points per slot per week, the bye
weeks that collide, and positional depth after the deal.

**Done when.** A fixture trade that creates a bye collision lists it.

#### UI-T4 · Process against result for completed trades
`P3 · L · on disk, maybe sync change · both`

**Why.** The decision log grades every trade before the fact. The research's best idea
here is to add the result beside it: what each side's players actually scored while
started for their new team (TheDadHut's two nets), and a replay of the head-to-head and
median results with the trade undone (LeaguePulse).

**Build.** Two columns, "the decision" and "what happened", never merged into one grade.

**Watch out.** "While started" needs each week's starters for every team. Check whether
the current season's weekly starters are stored before scoping; if not, it is a sync
change.

#### UI-T5 · Trade deadline countdown
`P3 · S · on disk · both`

**Why.** The deadline is week 11, and nothing mentions it.

**Build.** From week 9, a countdown on Home and the Decisions page.

**Done when.** It appears in weeks 9 to 11 and nowhere else.

#### UI-T6 · Trade finder ordered by acceptability
`P3 · S · on disk · both`

**Why.** The trade finder ranks by the owner's gain. A deal the other side loses on will
not happen.

**Build.** Order by the smaller of the two sides' gains, and show the other side's
positional need.

**Done when.** A fixture list reorders as specified.

---

### H: history and the almanac

Seasons 2024 to 2026 are reachable (2024 through `KNOWN_LEAGUE_IDS`, see Decision 7).
The rules below come from league-almanac and LeaguePulse, which both learned them the
hard way: median games never count as head-to-head results, and only the championship
path counts as playoffs.

#### UI-H1 · Record book
`P2 · M · on disk · both`

**Why.** Sleeper's history covers champions and high scores. League Legacy's record book
is the standard: hundreds of records, each linked to the game that set it.

**Build.**
- Game records: highest score, biggest win, closest win, highest losing score, lowest
  winning score.
- Season records: points for, point differential, best head-to-head record.
- Career records across the reachable seasons, with minimums (ten games for a win rate).
- Every record links to its week (UI-A3).

**Done when.** A fixture season produces each record, median games never appear in a
head-to-head record, and every record carries a working link.

#### UI-H2 · Rivalries
`P2 · M · on disk · both`

**Why.** Head-to-head history exists for this week's opponent only.

**Build.** For every pair of teams: the all-time head-to-head record excluding median
games, the average margin, the current streak and the last five meetings. A matrix of all
pairs, and a card on each team page.

**Done when.** A fixture with a known series reproduces it, and median results never count.

#### UI-H3 · Draft board
`P2 · M · on disk · both`

**Why.** The 2026 draft (152 picks) is on disk, and there is no board. `draft_review`
exists as a tool with a proxy caveat.

**Build.**
- The board as rounds by slots, coloured by position, with each pick's current season mean
  and value over replacement.
- Two grades for every pick (from myfantasyanalyzer): the grade on draft day, given what
  was available, and the grade now. Never merged.
- Per-manager tendencies, including when each took their first defensive players, which
  matters in IDP.

**Done when.** The fixture draft renders every pick in its cell, and the two grades appear
side by side.

#### UI-H4 · Records only a model can keep
`P3 · S · on disk · both`

**Why.** Raw margins are what everyone has. This project can add improbability.

**Build.** For the current season, from the weekly exports and the predictions log:
- The least likely win, by the quoted pre-game probability.
- The biggest comeback: the lowest playoff odds at any point for a team that made it.
- The biggest collapse: the reverse.
- The most improbable champion, by the title odds at the start.

These fill in as the season goes.

**Done when.** A fixture season with a planted comeback finds it.

#### UI-H5 · The season as a story
`P3 · M · on disk · both`

**Why.** The odds race shows where every team stood. It does not say why.

**Build.** The race chart annotated with the week's biggest movers (UI-O3) and the
highest-graded moves from the decision log.

**Done when.** The fixture's largest weekly move is annotated on the right week.

#### UI-H6 · Past seasons: champions, standings, brackets
`P3 · M · on disk, decision 7 · both`

**Why.** ESPN keeps "a tally of past winners … full standings, weekly scores and a draft
recap for each year".

**Build.** A page per season: final standings, the champion, the bracket where it is
available, and a link to that season's record entries. The 2025 archive has final
standings and every week's matchups; 2024 needs Decision 7.

**Done when.** The 2025 archive renders a complete season page.

---

### R: recaps, awards, alerts and sharing

#### UI-R1 · Weekly recap page
`P2 · M · on disk · both`

**Why.** Sleeper posts awards every Tuesday; recap tools are a small industry. The digest
exists as a document, not as a page that belongs to the week.

**Build.** Per completed week:
- Every result with its pre-game probability, and upsets marked.
- The odds movers (UI-O3).
- Top performers by surprise: how far each player scored above their own pre-game range,
  differenced against the league like the luck ledger, because the model has a known bias.
- The best-graded move of the week.
- The closest miss against the median.

**Watch out.** Nothing here is labelled luck, and nothing is combined into one score.

**Done when.** A fixture week renders each section, and the surprise ranking is
differenced against the league.

#### UI-R2 · Weekly awards
`P2 · S · on disk · both`

**Why.** The Decisions awards are season-long. Krool's analyzer has thirty-five, with two
guards worth copying: no awards before a game is played, and no loser crowned on a 0–0
tie.

**Build.** Upset of the week (the winner with the lowest pre-game probability), heist (a
win with a score in the bottom fifth of the week), worst beat (a loss with a score in the
top fifth), the biggest odds mover, and the closest median miss.

**Done when.** Each award is correct on a fixture week, and a week with no games played
produces none.

#### UI-R3 · Copy as a Sleeper chat post
`P3 · S · on disk · both`

**Why.** Several recap tools format for league chat with a length check.

**Build.** "Copy for Sleeper" on the recap: plain text within Sleeper's message limit,
split into parts when longer. It copies in the browser, with the owner's overlay applied
if the owner has real names on; nothing is written to disk.

**Done when.** A long recap splits at the limit, and no part exceeds it.

#### UI-R4 · CSV from every table
`P3 · S · none · both`

**Why.** Every record book offers export.

**Build.** A download button on sortable tables, generated in the browser from the table
as displayed.

**Done when.** A fixture table exports with the same rows and headers it shows.

#### UI-R5 · More alert triggers
`P2 · M · on disk · both`

**Why.** The kickoff alert is the only one. Sleeper's alerts for status changes are praised
as "spot-on"; Yahoo's morning lineup alert fires only when a change would help.

**Build.** Beside the kickoff alert, each opt-in and each stating its reason and size:
- A starter's designation changed.
- The model's lineup changed and would move the chance of winning by at least a set
  amount (UI-L1).
- The owner's playoff odds moved by more than two standard errors since the last
  committed run.
- The waiver run is thirty minutes away.

Like the kickoff alert, these work only while a page is open, and the page says so.

**Done when.** Each trigger fires once on a fixture change and not again on reload.

#### UI-R6 · Luck, pulled not pushed
`P2 · S · on disk · both`

**Why.** The luck ledger is a terminal tool. The owner's call is that luck is something you
look up, not a number on Sunday's front page.

**Build.** A Luck page reached from team pages and the palette, never from Home, rendering
the ledger's five pre-registered measures with their standard errors and p-values, and the
ledger's own "not significant yet" message at small n. No new measures (see Decision 2)
and no combined score.

**Done when.** The page renders exactly the five measures, and a test fails if a sixth or
a combined score appears.

#### UI-R7 · Schedule-swap matrix
`P3 · M · decision 2 · both`

**Why.** LeaguePulse's schedule lab replays each team's real scores against every other
team's schedule, and it is the clearest picture of schedule luck available.

**Build.** Only after Decision 2. It overlaps the ledger's `schedule_luck`, so it would
either be pre-registered as a new measure or shown as descriptive only. Median games
excluded, with LeaguePulse's fallback when the borrowed schedule would have a team play
itself.

---

### Q: the model's own record

#### UI-Q1 · Accuracy, second version
`P3 · M · on disk · dev only`

**Why.** Accuracy counts. Once enough weeks exist, it should draw.

**Build.** A reliability diagram (forecast probability against observed frequency), the
Brier score week by week, and the player-level histogram (UI-P3). Everything stays gated
behind the existing sample-size threshold.

**Done when.** Synthetic calibrated forecasts produce a diagonal reliability diagram
within noise.

#### UI-Q2 · What the model said at the time, on every result
`P2 · S · on disk · both`

**Why.** Past results show scores. Fantasy Record Book and League Legacy show receipts;
this project can show the forecast.

**Build.** Every past result card (team pages, matchups, recaps) shows the pre-game
probability from the quoted row, chosen by the same rule as Accuracy: the newest
committed row logged before the week's first kickoff. One shared helper.

**Done when.** The helper and the Accuracy page agree on every fixture week, and a week
without a qualifying row shows nothing rather than a later row.

#### UI-Q3 · The owner's picks against the model's
`P3 · M · decision 8 · both`

**Why.** Yahoo runs a pick-the-winners game inside leagues. Here the opponent is the model.

**Build.** Before kickoff the owner records a probability for each game. After the week,
both are scored with Brier, and a season table keeps the tally.

**Done when.** Scored correctly on a fixture week, and stored only under `data/local/webui`.

#### UI-Q4 · Reconcile the two calibration reads
`P2 · S · on disk · dev only`

**Why.** Two measures seem to point in opposite directions. The Accuracy page finds
team-week misses smaller than the model's stated spread (0.67 of it, with 94% landing in
a range meant to hold 80%): ranges too wide. `docs/LUCK_LEDGER.md` cites the points
backtest's 80% coverage of 0.654: ranges too narrow. They may measure different things,
such as player-level projections against team-week totals, but the page should say which
one it shows and why they differ.

**Build.** An investigation first, then one paragraph on the Accuracy page naming the
scope of each figure.

**Done when.** The paragraph names both scopes, and the two figures are cited from their
sources, not copied.

---

### V: visual system and layout

#### UI-V1 · A 4K layout
`P1 · M · none · both`

**Why.** The owner uses a 4K monitor. At about 2560 CSS pixels the 1440-pixel column
leaves roughly 560 pixels blank on each side, 44% of the width.

**Build.** After Decision 5: a wider container above about 2200 pixels, and either denser
single-column pages or a three-pane Home. All existing breakpoints stay, so nothing
closes the door on phones later.

**Done when.** The harness screenshots Home at 2560 pixels (UI-E10), no content column
leaves more than a set share of the width blank, and the 1280 and 520 pixel screenshots
are unchanged.

#### UI-V2 · Native charts in place of the week's images
`P2 · L · on disk · both`

**Why.** Each simulated week writes 27 chart images (boom and bust and floor and ceiling
for each of the eight teams, three strength-of-schedule charts, trajectories, the
head-to-head heat map, seeding, scoring density and power rankings) plus eight tier
images. They ignore the theme, cannot be hovered, and cannot be read aloud. The JSON
behind most of them is on disk.

**Build.** Replace them one at a time with native charts from the chart primitives
(UI-V3), in the order that retires the most images soonest: strength of schedule (UI-O5),
then boom and bust and floor and ceiling (UI-P2), then the rest. The digest keeps its
images.

**Done when.** Each replacement has a table fallback and renders in both themes, and the
week page drops the image it replaced.

#### UI-V3 · Chart primitives
`P2 · L · none · both`

**Why.** The UI has one line chart, one sparkline and one seed bar. The items above need
more, and they should come from one library rather than per-page code, which the first
charts pass had to consolidate once already.

**Build.** In `webui/render.py`: the distribution strip, a slope chart, a heat grid with
text in every cell, a dot histogram, a fan chart, and a percentage bar for table cells.
Each has hover, a table fallback and both themes, and each palette is run through the
dataviz validator.

**Done when.** Each primitive has a render test on fixture data, and every palette passes
the validator in both themes.

#### UI-V4 · Team and NFL colours
`P3 · S · live read · both`

**Why.** Fantasy teams already have hues. NFL teams have none, and the scoreboard provides
`color` and `alternateColor` for every team.

**Build.** NFL colours wherever an NFL team is the subject (the TV view, the schedule
grid), cached at sync.

**Done when.** A cached colour renders, and a missing one falls back to neutral.

#### UI-V5 · Compact density
`P3 · S · none · both`

**Why.** A 4K monitor fits far more rows than the current padding allows.

**Build.** A compact toggle beside the theme buttons that tightens table rows, remembered
per browser.

**Done when.** The toggle changes row height and nothing else.

#### UI-V6 · One grammar for uncertainty
`P1 · S · none · both`

**Why.** Commercial products define floor and ceiling differently (4for4 uses the 15th and
85th percentiles; this project uses the 10th and 90th), which is a real source of
confusion. The UI should be consistent with itself before it grows.

**Build.** A short style section in `docs/WEB_UI.md`: "±" always means one standard error;
ranges are always the 10th to 90th percentile and say so; verdict tiers follow UI-T2; a
probability shows one decimal unless it is below 1%. A test flags a "±" without a number
after it.

**Done when.** The section exists and the test passes across every page.

#### UI-V7 · Accessibility pass
`P2 · M · none · both`

**Why.** App-store and forum users report that no major fantasy app works well with a
screen reader. Colour-only encodings and hover-only information are the norm, and this UI
has both.

**Build.** Hover content reachable by keyboard and tap; a table view behind every chart;
text or shape alongside every colour encoding; probability bars carrying their number and
standard error in an accessible label.

**Done when.** A browser test (UI-E1) tabs to and opens a player card, and every chart has
a table view.

#### UI-V8 · Early-season states that teach
`P3 · S · none · both`

**Why.** In weeks 1 and 2 several pages are nearly empty, and an empty page reads as
broken. Accuracy already explains what it will show and when.

**Build.** The same for every page that depends on completed weeks: what will appear, and
from which week.

**Done when.** A week-1 fixture renders an explanation, not a blank, on each such page.

---

### E: engineering

#### UI-E1 · A browser test layer
`P1 · L · none · none`

**Why.** 315 tests across 24 modules, and none runs a script. Every script is pinned by its
presence in the served page. The Decisions filters broke on a CSS rule while the script
was right, and only a real browser would have caught it. Nearly every item above adds
script.

**Build.** Playwright for Python against the installed Edge or Chromium, or the DevTools
protocol, which the audit harness already drives. First targets: the Decisions filters,
the palette and shortcuts, the theme toggle, the live panel drawn from a fake
`/api/live`, the kickoff alert against a mocked clock, and the hover card.

**Done when.** The Decisions filter test fails against the commit before the `[hidden]`
fix and passes after it, which is the proof the layer catches what presence tests cannot.

#### UI-E2 · Cacheable static assets
`P2 · S · none · none`

**Why.** Every page re-sends 37.3 KB of CSS, 12.1 KB of script and a 4.7 KB icon sprite,
all inline, with `Cache-Control: no-store` on everything. Home adds 19.7 KB of its own CSS
and 15.8 KB of script.

**Build.** Move them to `/static` with content-hashed names and long caching. Pages and
data stay uncached.

**Done when.** A second page load fetches no stylesheet or script, and a changed asset
gets a new name.

#### UI-E3 · A read-only JSON API
`P2 · M · on disk · none`

**Why.** The interactive items (the playoff machine, the trade builder, the hover cards,
the palette) want data, not HTML.

**Build.** `/api/team/<slug>`, `/api/player/<id>`, `/api/week/<n>` and `/api/odds`, each
read-only and each built on the same helpers as the pages.

**Done when.** Each endpoint returns the same numbers as the page it mirrors, and none
writes or fetches.

#### UI-E4 · One source for every odds number
`P1 · S · on disk · none`

**Why.** LeaguePulse built its odds module because "two pages could show two different
playoff odds for the same league". Here Home reads the newest export, standings compare
two weeks, Forecasts reads all of them, and Accuracy reads the predictions log. They agree
today by care, not by construction.

**Build.** One helper that returns a team's current odds and their source, used by every
page.

**Done when.** A test renders Home, League, Forecasts and a team page from one fixture and
finds the same playoff and title odds on all of them.

#### UI-E5 · Per-simulation outcome plumbing
`P2 · L · engine export · none`

**Why.** The web side of Decision 1.

**Build.** A read-only loader for the export, cached in memory, with a filter function
that returns conditional odds, the count behind them and their standard errors, and a
minimum count below which it refuses.

**Done when.** Synthetic exports with planted relationships return them, and the filter
refuses below the minimum.

#### UI-E6 · Sandbox root
`P3 · M · none · none`

**Why.** Designed as W5 and never built: copy `data/`, run there, throw it away. It would
let the audit harness crawl a copy instead of the live tree, and allow what-if runs by
re-running the engine on altered data if Decision 1 is declined.

**Done when.** A run against the sandbox leaves the real tree's digest unchanged.

#### UI-E7 · Local image cache at sync
`P3 · S · decision 3 · none`

**Why.** The storage half of Decision 3.

**Build.** At sync, fetch headshots for rostered players and all NFL logos into
`data/local`, and serve them from there.

**Done when.** A page with images makes no third-party request.

#### UI-E8 · Server-sent events
`P3 · M · none · none`

**Why.** Job pages poll every two seconds and the job bar every three. The unbuilt half of
U13.

**Build.** Stream job progress and log lines with server-sent events, falling back to
polling.

**Done when.** A job page receives lines without polling, and a dropped stream falls back.

#### UI-E9 · Lighter long pages
`P2 · S · none · none`

**Why.** Decisions is 181 KB with 133 moves on one page. League is 166 KB with eight
twenty-one-row rosters open.

**Build.** Decisions paged by week with the filters kept, and League's benches collapsed
by default (from the first audit).

**Done when.** Both pages drop below 100 KB on the fixture, and the filters still work
across pages.

#### UI-E10 · Audit harness fixes
`P3 · S · none · none`

**Why.** The harness flags a literal `None` inside quoted engine messages (UI-F10), and it
never screenshots at the width the owner actually uses.

**Build.** Ignore `None` inside quoted warnings, and add a 2560-pixel desktop screenshot
to the default set.

**Done when.** A clean crawl reports no false `None`, and the 2560 shots appear.

#### UI-E11 · Inline styles into the design system
`P3 · S · none · none`

**Why.** 80 inline `style` attributes across the templates, each one outside the tokens
and themes.

**Build.** Replace them with classes.

**Done when.** A test counts inline style attributes and fails above zero, with a short
allow-list for dynamic widths.

---

## 8. Deliberately not building

Each of these came up in the research. Each has a reason.

- **League chat, polls and social feeds.** One owner; Sleeper already does this, and its
  reviews complain its news section turned "gross and toxic".
- **Betting promotions and picks.** Sleeper's reviews say it "feels oriented toward
  betting". Vegas lines stay an input to the model and never become a call to action.
- **Crowd trade values** (KeepTradeCut, FantasyCalc, DynastyProcess curves). They are for
  dynasty leagues, and on 3,003 community-judged trades KeepTradeCut agreed with the
  community verdict only 57% of the time when it named a winner. The paired simulation is
  the arbiter here.
- **Hand-weighted power-ranking formulas.** Use UI-O2.
- **A combined luck score, or new luck measures without pre-registration.** See Decision 2
  and the ledger.
- **Hindsight "coaching efficiency" as a measure of skill.** Show it, labelled. Rank on
  UI-L4.
- **Generated recap text.** It needs a contract that it invents no facts, it would name
  real people in text, and it adds nothing a computed recap does not.
- **Novelty metrics built on third-party personal data**, such as arrest records.
- **Achievements for using the app.** They reward clicks, not decisions.
- **Acting on Sleeper from the UI** (claims, trades, lineup changes). The UI stays
  read-only and Sleeper stays where you act.
- **"Clinched" from simulation frequency alone.** See UI-O10.
- **Automatic curation the owner cannot override.** Yahoo's Matchup of the Week cannot be
  changed. Anything this UI picks, such as the biggest game of the week, says why and can
  be overridden.
- **Gold-plating**: animated season videos, 3D trophies, dozens of palettes.
- **Re-simulating in the web process.** The engine is never imported.

---

## 9. Suggested order

Six waves. Each ends with the full suite, the golden master and a crawl, like the earlier
audit phases.

1. **Foundations and fixes.** The browser test layer first (UI-E1), because every later
   wave adds script. Then every UI-F item, one odds source (UI-E4), the uncertainty
   grammar (UI-V6), what each result cost or bought (UI-O3), final wins as a range
   (UI-O12), verdicts in standard errors (UI-T2) and what the model said at the time
   (UI-Q2). Small, certain, and they set conventions the rest depend on.
2. **The objects.** Team pages, player pages, the Matchups page, the schedule grid,
   standings v2 and the power rating, the distribution strip and the second hover card,
   then the navigation built around them (UI-A1 to A5, O1, O2, P2, P5).
3. **Answers without waiting.** Instant compare, the waiver board and players page, the
   lineup callout in win probability, tool cards that show their answers, and the trade
   builder's estimate (UI-P4, P1, W1, L1, A6, T1).
4. **Game day and the big screen.** The week's phases on Home, the pinned bar, freshness,
   honest extremes, the other three games, the 4K layout and the second TV view (UI-M1,
   M2, M6 to M9, V1).
5. **History.** Records grouped by run, the record book, rivalries, the draft board, the
   recap and awards, and luck as a page you open (UI-A7, H1 to H4, R1, R2, R6).
6. **The decisions.** Once Decision 1 is made: the playoff machine, leverage, the rooting
   guide, the markers and the wins-needed curve (UI-O6 to O10, E5). Once Decision 4 is
   made: the losing-bid ledger.

The chart primitives (UI-V3) and native charts (UI-V2) are built as each wave needs them,
not as a wave of their own.

---

## 10. Sources

The research reports cite more; these are the ones items above depend on.

**Open-source and indie**
- LeaguePulse, the closest peer (engine, calibration, replay, schedule lab, the odds
  module): https://github.com/ebrown-32/LeaguePulse
- kevin-kalish/fantasy-playoff-simulator (rooting interests, calibration metrics):
  https://github.com/kevin-kalish/fantasy-playoff-simulator
- league-almanac (record-book rules, median exclusion):
  https://github.com/miamiyankee13/league-almanac
- TheDadHut/league-history (trade nets, luck, fun stats):
  https://github.com/TheDadHut/league-history
- Krool/FantasyFootballAnalyzer (awards and their guards, draft grading):
  https://github.com/Krool/FantasyFootballAnalyzer
- uberfastman/fantasy-football-metrics-weekly-report (the common metric vocabulary):
  https://github.com/uberfastman/fantasy-football-metrics-weekly-report
- The FAAB auction study (754,611 auctions):
  https://github.com/RockChalkJay/draft-day-clj/pull/101
- myfantasyanalyzer, playoff odds and draft grades:
  https://myfantasyanalyzer.com/playoff-odds/ · https://myfantasyanalyzer.com/draft-grades/
- ffscrapr standings: https://ffscrapr.ffverse.com/reference/ff_standings.html

**Commercial**
- ESPN 2025 features: https://www.espn.com/fantasy/football/story/_/id/45844949/2025-fantasy-football-where-play-new-features-espn
- Sleeper's rebuilt win probability: SleeperHQ's post on X, June 2022 (the status URL is
  left out: its 19-digit id is Sleeper-shaped and trips the F37 identifier guard)
- Sleeper's 2018 navigation reversal: https://sleeper.com/blog/upcoming-redesign/
- Sleeper's median game: https://support.sleeper.com/en/articles/3971690-extra-game-each-week-against-league-median
- Yahoo Assistant GM: https://sports.yahoo.com/fantasy/article/introducing-assistant-gm-a-smart-new-feature-exclusive-to-yahoo-fantasy-plus-subscribers-125543697.html
- Fleaflicker playoff scenarios: https://www.fleaflicker.com/forums/site-announcements/topics/new-feature-playoff-scenarios-13927
- LeagueStation strength-of-schedule grid: https://www.leaguestation.com/draft-kit/strength-of-schedule
- StatChasers matchup analyzer: https://statchasers.com/fantasy-football-matchup-analyzer/
- Fantasy Life HQ: https://www.fantasylife.com/articles/fantasy/welcome-to-fantasy-hq-powered-by-xfinity-for-2026
- League Legacy record book: https://leaguelegacy.io/features/fantasy-league-record-book
- Trade-calculator comparison (3,003 trades): https://statsguyfantasy.com/methodology/comparing-trade-calculators

**Media**
- NYT Upshot-style playoff simulator, described: https://www.seahawks.com/news/tuesday-round-up-calculate-nfl-playoff-chances-with-ny-times-playoff-simulator
- ESPN NFL Playoff Machine: https://www.espn.com/nfl/playoffs/machine
- Leverage index: https://www.baseball-reference.com/about/wpa.shtml

**Checked directly on 2026-09-28**
- The ESPN scoreboard the live panel reads: team `color`, `alternateColor` and `logo`;
  per-competitor `records` and `leaders`; per-game `odds` (DraftKings); per-event
  `weather`; `broadcasts` and `venue`. `situation` could not be checked outside a live
  game.
- Sleeper trending adds (`/v1/players/nfl/trending/add`): 200.
- Sleeper headshots and team logos: 200.
