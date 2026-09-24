# Changelog

Newest first. Each entry is the release's headline; the full six-section notes (what's
in it, audit counts, hardware/season blockers, backlog, and what the tag does *not*
claim) live on the linked release. MAJOR means the model's predictions changed
materially (see the release policy in `CLAUDE.md`).

## [v8.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v8.0.0) — 2026-09-24 (MAJOR)

**The engine was seeding from a record the league does not recognise.** Sleeper's
`/matchups` endpoint DERIVES a completed week's points rather than storing them — it
recomputes stat lines against the league's *current* scoring settings on every call. After
a mid-season IDP change (F49) that means weeks already played come back re-priced, for
good. The engine summed those recomputed weeks into `actual_wins_banked`, so it believed a
3-1 record where the league had banked 2-2.

Fixed in F84: banked wins and points now come from the standings, **but only when that
record accounts for the completed weeks** — league-wide wins must equal `teams × weeks`
(halved when median scoring is off). That check is load-bearing: the golden fixtures'
standings are stale, so blind trust would have moved every golden onto wrong fixture data.
**Goldens stayed 15/15 byte-identical**, which is the criterion working rather than luck.

**Why MAJOR when the goldens did not move.** Measured on live data, playoff probability
shifts up to **+6.1 points** for a team whose banked record differed. The release policy's
two operational triggers — a golden regeneration, or a sync-time constant — are neither of
them fired here, and this tag adds a third: *an engine input the goldens structurally
cannot see.* The fixtures fall back to the recomputed record, so the change is invisible to
them by construction. Same lesson as F28, different route.

**Only the standings quantities moved.** The Bayesian posterior still reads the recomputed
weekly scores, and that is correct — it asks how good a player is under the rules that
apply in *future* weeks, which is exactly what the re-scored weeks measure. A
green-by-design test pins the blend as untouched.

**Everything else in this tag is capability**, accumulated since v7.0.0 and none of it
touching a projection: the what-to-watch matchup brief (T5), the bye-exposure and
roster-crunch calendar (T6), the odds-history trajectory (R1), the streamer-level study
(C5, measurement only — no constant moved), the tokenising real-name scanner (H1), pending-
trade awareness across the three trade screens (T3), exact clearing prices from recorded
rival bids (T2), FAAB affordability checks (T1), and a test-isolation checker written after
a boundary test of mine overwrote real synced data.

**Findings this release:** F71–F84. Two of them were mine and are recorded as such — a test
that wrote over `data/current/league_schedule.json`, and a real-name leak into an audit doc
that the scanner built this same session caught before it reached history.

## [v7.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v7.0.0) — 2026-09-23 (MAJOR)

**Every probability this model states is now wider, because the old ones were wrong.**
Measured over 240 team-weeks of the real 2025 season, the 80% interval covered 67% of
outcomes and the 50% interval covered 36%. `INTERVAL_INFLATION = 1.41` on `std_aleatoric`
brings that to 0.80 and 0.46, with `sd_z_opt` 1.27 → 1.005.

The factor is 1.41 and not 1.27 for a reason worth stating: aleatoric is only part of the
variance, so inflating it alone cannot scale the total by the full amount. Two backtest
runs pinned the split — aleatoric is 62.5% of team-week variance — which makes the
required factor `sqrt((1.27² − 0.375)/0.625) = 1.408`. Epistemic is untouched and pinned
by a test: it is drawn once per season on purpose, to carry parameter uncertainty into
season outcomes.

**The honest residual, not tuned away:** the 50% band still covers only 0.45. One scale
factor repairs the 80% band and leaves the 50% band narrow, which says the predictive
shape is not Gaussian. That is a shape problem and this was a scale fix.

**Smaller than advertised.** The backlog warned that every champ% would collapse toward
12.5%. Measured at 6 × 300 sims, the three leaders lose 0.3 / 0.7 / 1.4 points and
best-to-worst spread narrows 26.1 → 25.5. A championship is a season aggregate, and
season aggregates are dominated by the once-per-season epistemic draw and by roster
quality. Weekly numbers — where `live_matchup`, `compare_players` and every stated win
probability live — move considerably more.

Also in this tag: **vacated injury volume now reads the Bayesian posterior** rather than
preseason means, closing the open half of F54. When a starter goes down, a backup who has
produced for three weeks now inherits a larger share than one who has not; previously both
carried an identical preseason number. The mean-weighted apportionment *rule* is untouched
and pinned by a test — F24 measured it correct and it stays a deliberate decision.

**One new finding, F62, found by doing the release.** Regenerating the behavioral baseline
reported six "engine behavior changes". At least two cannot be: FAAB bid sizing reads a
budget, a normal draw and a 2025-derived aggression multiplier — no baseline, no variance.
What moved was the *phase* of the shared numpy stream. Measured at ±2.1% standard error on
`faab_spent`, which is larger than the check's own 2% tolerance. The tolerance deliberately
stays where it is; the *report* was corrected to stop asserting a cause it cannot know.

Goldens regenerated deliberately, and all three scenarios moved including `week01` — the
mechanical signature of an init-time constant, where B8 alone moved only the two fixtures
with completed weeks. Suite 997 → 1004.

## [v6.1.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v6.1.0) — 2026-09-23 (MINOR)

Fourteen backlog items worked in order, and what building them kept finding. Six new
findings (F56-F61), none of which were on the backlog: an empty projection payload would
have **silently overwritten every baseline** with `{}` while the manifest said `ok: true`
(F58); a whitelisted missing asset was imputed as healthy whatever the roster said, so an
IR'd player was simulated as available all season and every trade involving his team was
refused (F60); and the sync recorded what WARNED but never what each source **delivered**,
which is the gap F52 hid in for a fortnight (F57).

Seven new tools — `market_sweep`, `trade_leverage`, `data_health`, `bid_review`,
`stat_corrections`, `reprice`, `decision_scorecard`, plus `live_matchup --tail` and
`find_trades --exhaustive`. Four are scratchpad scripts promoted with their bugs fixed:
the market sweep named the wrong man to drop on three positions at once, the bait script
joined draft picks **by name** across 220 colliding names, and the tail audit priced a
finished week against today's projections.

Three tools found errors in themselves the day they shipped. `data_health` called a real
21.5 market line a fallback; `--tail` labelled a cold opponent "hot"; `decision_scorecard`
picked its pre-kickoff record by sorting file paths, so `archive/` beat an earlier
top-level run. Each was caught by running the thing, not by reading it.

**The measurement that mattered most is a negative result.** B13 asked for a bid heuristic
scored against the clearing price. On all 26 logged 2026 claims the correlation between a
player's VORP and his winning bid is **−0.136** (F61): this league does not bid on model
value, 20 of 26 claims went to players below replacement, and the biggest bid of the
season ($25) went to a player at −2.57. No VORP-shaped rule can be scored against those
prices, so v2 ships beside v1, both labelled unvalidated, and B14's ledger is the route to
settling it rather than another argument.

Two weekly numbers moved for the reader without the model moving at all: the live tracker
was treating the head-to-head and median legs as independent when both turn on the same
score — the double-loss probability was understated **2.4×** (13.3% against 5.5%) — and
the trade screen may no longer print a "their gain" figure as though it were a
measurement. Engine goldens byte-identical throughout; 695 → 982 tests.

## [v6.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v6.0.0) — 2026-09-22 (MAJOR)

Two silent data faults, found three days apart, both of which had been quietly starving
the model of the evidence it was built to consume. The ESPN half of the projection blend
had been dead since week 2 — a missing `week` argument meant espn_api returned the
inactive dummy league's scoring period, so `stats.get(1)` worked and every week after
found nothing (F52). And the Bayesian posterior had only ever reached **157 of ~1,140
players**, because it was fed from matchup payloads that by design contain only rostered
players — so every free agent was ranked on a frozen preseason prior while your own
roster was ranked on a corrected one, and replacement level was computed three lines
before the blend that should inform it (F54).

Both are fixed and the baselines regenerated, which is why this is MAJOR: the goldens
could not have detected either one. Also ships the luck ledger (F53), five
pre-registered measurements of schedule, opponent, close-game, absence and scoring luck,
and records the mid-season IDP scoring change as an evaluation boundary (F49).

## [v5.1.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v5.1.0) — 2026-09-14 (MINOR)

The first week of live season operation, and what it cost. Week 1's automated canonical
run did everything right and then failed on a cosmetic step (F40), which cried wolf on
the desktop watcher (F41) and — in a second, unrelated tool the same week — silently hid
the week's primary pre-registered record from the archive (F42), because both treated
"the run succeeded" as a proxy for "the run produced something". New tool
`scripts.live_matchup` (F43) answers the question the week actually asked: banked points
are certain and only the remaining game clock carries variance. Week 1 measured against
both canonical quotes, with three hypotheses pre-registered into F25 for the week 5-6
calibration check — including the first measurement of the odds feed as an input with
its own error. Engine goldens byte-identical; the model itself was not touched.

## [v5.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v5.0.0) — 2026-09-05 (MAJOR)

League-identity pseudonymization (F37): fictional team names throughout, roster-id
keys, league IDs moved to environment/secrets (a committed Sleeper ID resolves to real
identities through the public API). Goldens regenerated on the renamed fixtures — and
the behavioral baseline regenerated with zero drift, measuring the rename as
behavior-inert. The pre-registered season evaluation re-locked with a dated names-only
note before any game was played. The owner's local reports keep an env-gated real-name
legend that never reaches logs or published artifacts.

## [v4.1.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v4.1.0) — 2026-09-05 (MINOR)

Season-operations automation and the showcase polish: the canonical-window watcher and
scheduled log capture on GitHub Actions, fully gated unattended canonical runs (F36 —
allowlist gate, remediation issues with verbatim commands, provenance-stamped rows),
the behavioral-plausibility harness, a consolidated methods document with a measured
naive-baseline comparison (engine MAE 22.24 vs 26.55 projections-only), and the sample
report rebuilt as a Pages build product instead of a committed 9 MB blob. Engine
goldens byte-identical throughout.

## [v4.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v4.0.0) — 2026-09-03 (MAJOR)

FAAB behavior calibrated to the real league: bid sizes fitted to the 99 attributed 2025
claims, an upgrade-bidding channel, and a two-parameter per-manager model — simulated
spending moved from ~31% of real to inside the pre-declared [650, 800] band. Trade
evaluator records FAAB transfers as explicitly unpriced. Also: the weekly report's
visual redesign, and F30's capture-rate measurement (measured and held).

## [v3.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v3.0.0) — 2026-09-02 (MAJOR)

K and IDP players gain a real epistemic signal: both projection sources' stat lines
scored under this league's own settings, with the disagreement driving uncertainty.
The sync stage gets its own byte-exact golden, closing the blind spot v2.0.0 exposed.

## [v2.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v2.0.0) — 2026-09-02 (MAJOR)

First measured IDP variance constants (DL/LB/DB derived from full-NFL 2025 stats,
replacing placeholder fallbacks) and K re-fit under the league's current kicker rules.
The engine goldens were byte-identical through the change — the finding that sync-time
constants sit upstream of what they pin, now written into the release policy itself.

## [v1.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v1.0.0) — 2026-09-02

The audited baseline: Phases 0–7 complete, the golden master, the real-data backtest
gate, and the audit trail that defines this project. Tagged at the F27 commit.
