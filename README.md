# Fantasy Football Monte Carlo Simulation

A quant-grade simulation engine and weekly decision-support kit for an 8-team IDP Sleeper
league. Every player's weekly score is a probability distribution, not a point projection;
10,000 simulated seasons run forward from the current week through a Gaussian copula, a
two-variance (aleatoric + epistemic) model, an empirical absence model, and the league's real
schedule, producing calibrated playoff and championship odds -- and, on top of that, seven
tools that turn the same machinery into answers to the questions a manager actually has each
week. The model was validated by a documented, phase-by-phase audit (see
[Validation and audit trail](#validation-and-audit-trail)); the constants are measured, not
guessed, and every one of them says where it came from.

## Weekly use -- one command

```bash
py -3.10 -m scripts.weekly_report            # sync -> simulate -> charts -> grades -> lineup -> matchup -> waivers
py -3.10 -m scripts.weekly_report --full     # ... plus the trade-target finder (--evaluate N runs tool 2 on the top N)
py -3.10 -m scripts.weekly_report --embed    # inline the charts (portable single HTML, ~15-20 MB)
py -3.10 -m scripts.check_freshness          # one glance: has sync run this week, and did it succeed?
```

No arguments are needed for the common case: the team comes from `config.MY_TEAM`, the week
from the sync. The orchestrator chains, in-process and in order: `sync_all` -> the Monte Carlo
simulation -> positional tiers, strength of schedule and the win-trajectory chart -> the
league-wide "this week" outlook -> roster grades -> the lineup optimizer -> the opponent-aware
matchup tool -> waiver targets. It writes one consolidated digest to `data/decisions/week_NN/` (`--canonical`) or `week_NN/archive/` (default), as
both **`weekly_report_week{N}_{stamp}.html`** (sortable tables, every chart of the week inlined
in the section it belongs to, a collapsible assumed-optimal lineup per team) and
**`…md`** for quick console reading.
`--embed` inlines every chart as a data URI -- portable but large by design (several MB, vs ~60 KB normally); embedded digests are named `*_embed.html` so the size is visible before opening.

**It fails loud.** The first step that raises -- or a gate that finds the previous step did not
leave its data (no sync manifest from *this* run; no simulation export newer than the step) --
stops the chain, writes the digest with a `FAILED AT STEP …` banner and *no* downstream
sections, and exits 1. Nothing downstream ever runs on stale or partial data. A sync that
tolerated failures (a projection source down, a stale line, a rostered player with no
projection) is not a failure, but its `DEGRADED` list is the first thing in the digest, every
week it persists.

## Decision tools

Each is a script under `scripts/` and a function in `fantasy_sim.decisions`. They read
`data/current/` only, write their JSON records to `data/decisions/`, and never touch the
season exports. The engine is not modified; the one extraction made for them
(`_weekly_score_from_z`) is pinned byte-identical by the golden master.

| Question | Tool |
|---|---|
| Start A or B this week? -- real P(A > B) from the players' *joint* simulated distributions (copula, shared environment and injury state), not a mean comparison; a free agent is sampled through the engine's own transform | `py -3.10 -m scripts.compare_players "Player A" "Player B" [--light]` |
| What lineup does the engine's own rule set for my real roster, and by how much? -- each starter's p10/p50/p90 and the margin over the best bench alternative | `py -3.10 -m scripts.optimize_lineup` |
| Against *this week's specific opponent*, should I play safe or swing for variance? -- four lineup constructions (max-mean, safe, correlated stack, P-maximising local search) on one joint sample of both rosters, each with P(beat opponent) and P(beat median) | `py -3.10 -m scripts.matchup_lineup [--no-cross]` |
| Who should I claim, and what should I bid? -- my real roster gaps against the free-agent pool, ranked by value over replacement, with tiers, a week distribution, an (unverified, labelled) bid heuristic, and P(beats my incumbent) | `py -3.10 -m scripts.waiver_targets [--positions RB,WR]` |
| Is this specific trade good for me? -- two paired full simulations (with and without, same seeds) and the real Champ%/Playoff% delta for both sides and every bystander, with paired SEs | `py -3.10 -m scripts.evaluate_trade --team-a … --a-gives … --team-b … --b-gives …` |
| Who should I be trading for, and who wants what I have? -- the other seven rosters scanned for buried bench players who would start for me (with a give-back their side can accept) and for my surplus that has a buyer | `py -3.10 -m scripts.find_trades [--evaluate N]` |
| How good is each roster, really? -- every rostered player's tier and VORP rolled up per position and overall, and a league table ranked by lineup VORP (no letter grades) | `py -3.10 -m scripts.roster_grades [--team …]` |

## Or run the pieces individually

```bash
py -3.10 -m scripts.run_sync                    # pull live data into data/current/ and write the sync manifest
py -3.10 -m scripts.run_simulation              # the Monte Carlo engine: exports + charts + boom/bust + floor/ceiling
py -3.10 -m scripts.run_positional_tiers        # statistically-derived tiers per position (PNG + sortable HTML tables)
py -3.10 -m scripts.run_strength_of_schedule    # NFL-team and fantasy-roster schedule heatmaps
py -3.10 -m scripts.run_win_trajectory          # expected wins over the simulated season, all teams
py -3.10 -m scripts.run_windows                 # this week's three canonical-run windows: which are open, covered, or missed (read-only)
py -3.10 -m scripts.draft_review                # at-draft value review of an ingested draft (--season; PROXY caveat on the page)
py -3.10 -m scripts.evaluate_move               # paired evaluation of an add/drop or waiver (--log-tx <id>, --bid N,
                                                #   --evaluate-unevaluated [--mine-only] [--limit N] for the logged backlog)
py -3.10 -m scripts.season_retrospective        # a completed season's record in four measurements (schedule luck, lineup
                                                #   efficiency, absences, high-scorer losses), no combined verdict
py -3.10 -m scripts.run_season_backtest         # win-total / playoff backtest vs the real 2025 season
py -3.10 -m scripts.run_points_backtest         # points-level backtest (bias, mean z, coverage), logged with commit + interpreter
py -3.10 -m scripts.run_player_backtest         # variance / correlation / epistemic constants vs real player-week data
```

## How the model works

**Data pipeline** (`fantasy_sim.sync`): real per-player weekly projections from Sleeper,
blended with a second, independent projection source (a dedicated ESPN league mirroring this
scoring), real Vegas totals and spreads, NFL team defensive strength derived from completed-game
results with empirical-Bayes shrinkage, byes derived from the live schedule, and each player's
current availability (Sleeper injury status, the league's IR slot). Every sync appends the
projections it used to `data/logs/projection_log.jsonl` (the one artifact that cannot be
refetched, tracked in git) and writes `sync_manifest.json` last, so a manifest's presence means
the sync completed and its `degraded` list says what it tolerated.

**Simulation engine** (`fantasy_sim.simulation`): each player's weekly score is lognormal with
separately modelled aleatoric (week-to-week) and epistemic (projection) variance -- epistemic
drawn *once per simulated season* and held, aleatoric redrawn weekly. Same-NFL-team players are
correlated through a Gaussian copula with measured coefficients. Lineups are chosen on pre-game
expectation by the Hungarian algorithm (never on realised scores -- lookahead is a bug here).
Injuries follow an onset hazard and a two-component duration mixture; players out *now* enter
on a measured return hazard; vacated volume is conserved. Waivers, FAAB and a live trade
mechanism run stochastically through the season; the 4-team playoff is simulated -- or seeded
from banked standings when the run starts inside it.

**Backtesting** (`fantasy_sim.backtest_season`, `backtest_player`, `scripts.run_points_backtest`):
the season-level and points-level backtests reconstruct as-of-week inputs from this league's
real 2025 season in an isolated working directory and run the *actual, unmodified* engine
against them; the player-level backtest checks the variance, correlation and epistemic
constants directly against real player-week data. Each states its own limitations in its
docstring (including a documented defensive-scoring confound in the 2025 season).

## Project structure

```
fantasy_sim/
├── config.py                 # every constant, each with its derivation or marked unverified; league IDs, MY_TEAM
├── storage.py                # every path this project reads or writes, named once; save_json / save_chart
├── clients/
│   ├── sleeper.py            # Sleeper player database (one-day TTL cache)
│   └── espn.py               # ESPN league client (second projection source)
├── sync.py                   # data ingestion pipeline + the sync manifest
├── simulation.py             # FantasySimulationEngine -- the Monte Carlo core and the season exports
├── freshness.py              # OK / DEGRADED / STALE assessment of the data on disk
├── decisions.py              # the seven decision tools + the league-wide weekly outlook
├── weekly_report.py          # the orchestrator: fail-loud runner, gates, Markdown + HTML digest
├── positional_tiers.py       # statistically-derived tiers, per-position charts and sortable tables
├── player_variance.py        # boom/bust and floor/ceiling reports from the engine's per-player accumulator
├── strength_of_schedule.py   # schedule heatmaps from the engine's own environment model
├── win_trajectory.py         # expected-wins-over-week chart from the exports
├── backtest_season.py        # season-level backtest against real 2025 outcomes
└── backtest_player.py        # player-level calibration checks; projection-error derivation (F7)

scripts/                      # 17 thin CLI entry points (weekly_report is the primary one); probes/ = R1 machine-fault probes
tests/                        # 27 test modules + golden_master.py (the reproducibility harness); 474 tests
data/                         # runtime output, three buckets (see storage.py):
├── current/                  #   sync's snapshot of the world as of the last sync (overwritten each sync) + the manifest
├── weeks/week_NN/            #   one directory per simulated week: exports, charts, tiers, SoS, audit log
├── decisions/                #   decision-tool records and the weekly digests:
│   ├── week_NN/              #     canonical runs (--canonical: the scheduled Tue/Sun reports)
│   │   └── archive/          #     everything else -- exploratory and mid-week runs (the default)
│   ├── season/               #     draft reviews, season retrospectives
│   └── adhoc/                #     compare/evaluate output tied to a moment, not a report run
└── logs/                     #   append-only, season-spanning: projection_log.jsonl and points_backtest.jsonl -- tracked in git
```

## Setup

Runtime is **Python 3.10** with the exact pins in `requirements.txt`:

```bash
py -3.10 -m pip install -r requirements.txt
```

(`py -3.10` is deliberate: on the original machine plain `python` resolved to an end-of-life
3.8 that produced an intermittent native fault in the test process -- `AUDIT_PLAN.md` R1.)

Two credentials are read from environment variables, never hardcoded:

| Variable | Required for |
|---|---|
| `ODDS_API_KEY` | **Required for any correct in-season forecast.** Before the 2026-09-09 gate the engine runs on a hand-verified week-1 table; after it, this key is the only source of real lines. Without it every team gets a flat 21.5 total and no opponent -- the sync manifest, `check_freshness` and the digest's DEGRADED block all say so loudly, but the forecast is matchup-blind, not correct. Free tier at the-odds-api.com. |
| `ESPN_S2`, `ESPN_SWID` | Only if the dedicated ESPN league is private. Without ESPN the blend and its disagreement-driven epistemic term simply do not apply. |

## Testing

```bash
py -3.10 -m unittest discover tests      # expected: Ran 474 tests ... OK (skipped=1, expected failures=3)
py -3.10 -m tests.test_golden_master     # the reproducibility harness: 15 tests, three scenarios, byte-exact hashes
```

The skip is the live-ingestion test (`RUN_LIVE_INGESTION_TESTS=1` runs it); the three expected
failures are deliberate red characterisations of tracked open items. The golden master hashes
`run_simulation`'s complete output and every JSON export for three committed fixture scenarios
(preseason, mid-season, inside the playoffs); any change to the engine either leaves them
byte-identical or is regenerated with the deltas explained in the commit.

## Validation and audit trail

This is the part of the project to read first if you want to know whether the numbers can be
trusted. The model was audited phase by phase, organised by property class (conservation,
orientation, invariance, bounds, liveness) rather than by file, and every finding, fix and
deliberate non-fix is recorded:

- **`AUDIT_SUMMARY.md`** -- the whole arc on one page: Phases 0-7, what was found, fixed, and left
  open, with the numbers.
- **`AUDIT_PHASE_0_FINDINGS.md` … `AUDIT_PHASE_7_FINDINGS.md`** -- seven phase reports
  (reproducibility harness; conservation and invariants; the statistical core; data ingestion
  integrity; decision logic; season and playoff mechanics + outputs; calibration).
- **`AUDIT_PLAN.md`** -- the working spec, with the tracked follow-ups **F1-F17** (each with
  Origin / Scope / Acceptance criterion / When, and its outcome when closed) and the R1
  machine-fault investigation.

Concretely: **474 tests** with every regression test written to fail before its fix; a
**three-scenario golden master** that makes any behaviour change in the engine falsifiable
byte-for-byte; a **real-data backtest gate** on this league's 2025 season (points bias, mean z,
coverage, logged per commit and interpreter) that every correlation- or scoring-adjacent change
must pass; constants measured on real player-week data (variance, epistemic error,
copula correlations re-confirmed league-wide on 391 players / 714 pooled pair-weeks); and the
disagreements with an external audit recorded as measured, resolved findings rather than
opinions (F13: tail dependence and game-script correlation, measured and not adopted; the
80-point cap, re-measured on playoff equity and kept).

## Adapting this to your own league

**The single most important thing first: the constants were measured in *this* league's scoring
system and need re-deriving for yours.** `VOLATILITY_CONSTANTS`, `EPISTEMIC_ERROR_RATES`,
`SIM_CONFIG['CORRELATIONS']`, the injury onset/duration/return parameters and the replacement
levels were all calibrated on real player-week data scored *this way* (`backtest_player`, F13,
F4/F5) and validated against *this* league's 2025 history. Change the scoring or the roster
format and those numbers are no longer sourced for your league -- the honest path is to re-run
`scripts.run_player_backtest` against your own season's data, re-establish the backtest gate,
and only then trust the outputs. Editing the IDs below without doing that produces a
professional-looking forecast whose constants belong to someone else's league.

**Straightforward, config-only edits** (`fantasy_sim/config.py`):
- `LEAGUE_ID`, `MY_TEAM`, `TEAM_NAME_MAP` (Sleeper display names -> your team labels; every
  roster resolves through it, unmapped users become "Unknown"), `ESPN_LEAGUE_ID` +
  `ESPN_S2`/`ESPN_SWID` (optional second source), `ODDS_API_KEY`.
- `MANAGER_PROFILES` (per-team FAAB aggression and trade willingness; deliberately uncalibrated,
  measured under F14 as outcome-inert -- neutral values are a fine start), `KNOWN_MISSING_ASSETS`
  and `DUAL_ELIGIBILITY` (league-specific players).

**Format assumptions that are *not* drop-in** -- these are in the code, not just the config:
- **8 teams.** The trade block ranks standings as `[0:2]` rich / `[4:8]` desperate; the
  median-beat decision, all-play and seeding assume the league size; `TEAM_NAME_MAP` and
  `MANAGER_PROFILES` are eight entries.
- **14-week regular season, 4-team playoff in weeks 15-16** (`REGULAR_SEASON_WEEKS`, `top4`,
  the bracket seeding and the week-17 refusal).
- **Roster structure.** `REQUIRED_STARTING_SLOTS` (13: QB, 2 RB, 2 WR, TE, 3 FLEX, K, DL, LB, DB)
  drives the Hungarian assignment, the streamer scan, replacement levels and VORP;
  `ACTIVE_ROSTER_LIMIT = 19` in the trade evaluator; `BASE_STREAMER_MEANS` per slot.
- **IDP.** DL/LB/DB slots with Sleeper's raw positions normalised (DE/DT/NT -> DL, CB/S/FS/SS ->
  DB). The IDP variance, epistemic and injury constants are *less rigorously sourced* than the
  offensive ones (documented in `config.py`), and the ESPN blend excludes K and IDP by design.
  A non-IDP league removes slots and loses nothing statistically; a new position needs new
  constants with sources.
- **Hybrid H2H + weekly-median scoring** (`SIM_CONFIG['MEDIAN_SCORING_ENABLED']`): two decisions
  per week, 28 max. The 2025 backtest runs with it *off* because that season was pure H2H --
  the flag exists precisely so a season is simulated under the rules that applied to it.
- **2026-specific reference data:** `WEEK_1_VERIFIED_VEGAS` (a hand-verified preseason table
  that expires at the odds gate), `NFL_TEAMS`, and bye weeks derived from the live schedule.

## Notable engineering details

- **Two-variance model:** aleatoric variance redrawn weekly; epistemic drawn once per simulated
  season and held, so parameter uncertainty propagates to season-level outcomes instead of
  averaging away.
- **Absence, not a hole:** a rostered player with no projection but a Sleeper absence status
  (IR / PUP / Commissioner Exempt / the league IR slot) is carried at his last data-sourced mean
  and enters on the measured return hazard -- never a hand-typed healthy baseline.
- **Lineups on expectation, never on outcomes:** every lineup in every simulated week is chosen
  on pre-game expectation; letting realised scores in would be lookahead leakage.
- **Real backtesting infrastructure:** as-of-week inputs reconstructed into an isolated
  working directory and run through the actual engine, not a parallel reimplementation.
- **Failing loud beats failing silent:** the sync manifest, the freshness check and the
  orchestrator's gates exist because an earlier defect (a test fixture silently truncating real
  production data on every suite run, `AUDIT_PLAN.md` F11) was found by accident. Nothing in
  the pipeline may now look like success while being partial.
