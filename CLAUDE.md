# CLAUDE.md

Persistent instructions for this repository. Keep this file short and specific — vague rules get
followed inconsistently.

## What this is

A Monte Carlo simulation of an 8-team IDP fantasy football league (Sleeper). It is a
quantitative modelling project, not a CRUD app: the correctness bar is statistical, not just
"does it run". A change that runs cleanly and silently alters the distribution is a failure.

## Commands

Runtime is **Python 3.10** with the exact pins in `requirements.txt` (`py -3.10 -m pip install -r
requirements.txt`). On this machine plain `python` resolves to the retired Windows Store Python
3.8 -- do not use it: it is pinned to end-of-life numpy/scipy and produced an intermittent native
access violation in the test process (`AUDIT_PLAN.md` R1). Use the launcher:

```bash
py -3.10 -m unittest discover tests      # full suite — 492 tests, must all pass
py -3.10 -m tests.test_golden_master     # reproducibility harness — 15 tests, three scenarios, byte-exact
py -3.10 -m tests.golden_sync            # sync-stage golden: baseline generation from pinned inputs (--regenerate = MAJOR)
py -3.10 -m scripts.weekly_report        # PRIMARY ENTRY POINT: sync -> simulate -> charts -> tools -> HTML+MD digest; fails loud
py -3.10 -m scripts.check_freshness      # has sync run this week, and did it succeed? (OK / DEGRADED / STALE)
py -3.10 -m scripts.run_sync             # pull live data into data/current/ (writes the sync manifest last)
py -3.10 -m scripts.run_simulation       # run the engine
py -3.10 -m scripts.run_season_backtest  # backtest vs the real 2025 season
py -3.10 -m scripts.run_points_backtest  # points-level backtest gate (bias / mean z / coverage), logged per commit
py -3.10 -m scripts.run_player_backtest  # calibrate constants vs real player data
py -3.10 -m scripts.run_windows          # canonical-run windows: open / covered / missed (read-only)
py -3.10 -m scripts.evaluate_move        # paired evaluation of add/drop/waiver; --log-tx; --evaluate-unevaluated
py -3.10 -m scripts.draft_review         # at-draft value review (--season; proxy caveat on the page)
py -3.10 -m scripts.season_retrospective # a completed season in four measurements, no combined verdict
```

The seven decision tools (`scripts/compare_players`, `optimize_lineup`, `matchup_lineup`,
`waiver_targets`, `evaluate_trade`, `find_trades`, `roster_grades`) and their one-line
questions are listed in `README.md`; they read `data/current/` only and never touch the engine
or the season exports.

Expected verdict: `OK (skipped=1, expected failures=3)`. The skip is the live-ingestion test
(`RUN_LIVE_INGESTION_TESTS=1` runs it); the three expected failures are deliberate red
characterisations of tracked open items (`AUDIT_SUMMARY.md`; a fourth, the dead trade
mechanism, flipped to a guard when `AUDIT_PLAN.md` F2 commit 1 landed on 2026-09-01). `espn_api` is in
`requirements.txt`; without it 3 more tests skip cleanly (`skipped=4`) -- expected, not a failure.
`hypothesis` is pinned `<6.120` because 6.165 fails inside its own engine on Python 3.10.0
(verified not to be the example database); revisit on a later 3.10.x.

## Rules of engagement

These are non-negotiable and exist because each was learned the hard way on this codebase.

1. **Write the failing test before the fix.** A test written after a fix proves nothing. If the
   test does not fail against the current (broken) behaviour, it is not a regression test — say
   so plainly rather than presenting it as verified.

2. **Never claim verification you have not performed.** If a test cannot be made to catch a
   given regression, state that as a coverage gap. Do not present passing tests as evidence of
   a property they do not actually test.

3. **Separate characterisation from remediation.** One commit for tests that expose behaviour,
   a second for the fix. This keeps "what was wrong" reviewable independently of "what changed".

4. **Do not refactor what is not covered by intent.** `run_simulation` (571 lines) and
   `export_and_visualize` (492 lines) ARE pinned byte-exactly by the golden master (Phase 0
   is complete; coverage there is execution, not assertion — see F26). Decomposition is
   Phase 8, which stays blocked until the R1 hardware is replaced and Arm D passes 12/12 —
   the golden certifies refactors only on a machine that can be trusted to run it.

5. **Every constant cites a source or is marked unverified.** Numbers in `config.py` carry
   comments explaining their derivation. Preserve that. A new constant with no sourcing comment
   is not acceptable; "unverified, carried over" is acceptable and honest.

6. **Run the full suite before and after every change.** Report the count. Investigate any
   change in the number of tests that run, not just failures.

7. **Prefer finding the real defect over satisfying the test.** If a test fails, diagnose the
   cause before editing either side. Loosening an assertion to make a suite green is a
   regression in disguise.

8. **Closing a finding closes it everywhere, in the same commit.** Resolving,
   retiring, or measuring-and-clearing any finding updates `AUDIT_SUMMARY.md` alongside
   `AUDIT_PLAN.md`, and the status keyword (CLEARED / CLOSED / RESOLVED / BUILT) goes in
   the plan's F-heading so `tests/test_docs` can cross-check. F27 exists because this rule
   did not: the summary went stale on eighteen findings while the plan stayed current.

9. **Every phase branches from `main`, never from another phase's branch.** After a phase
   merges, delete its branch. If `git log <new-branch> --oneline` shows commits from a
   different `audit/phase-N-*` branch, the branch point was wrong — stop and re-branch from
   `main` before doing any work.

## Deliberate decisions — do not "fix" these

Each of these looks like a defect and is not. Changing any of them requires explicit discussion.

- `SIM_CONFIG['MEDIAN_SCORING_ENABLED'] = False` in the season backtest. The 2025 season really
  was pure H2H; the flag exists so a historical season is simulated under the rules that applied.
- `ESPN_BLEND_ELIGIBLE_POSITIONS` excludes K and IDP. Sleeper and ESPN scoring for those
  positions could not be matched, so blending them would corrupt the disagreement signal that
  drives epistemic uncertainty.
- `VACATED_VOLUME_CAPTURE_RATE = 0.65` is explicitly **not** rigorously derived. It is carried
  over and documented as such. Do not silently re-tune it; if you have a real source, say so.
- Mean-weighted vacated-volume apportionment was long suspected backwards in the handcuff
  case, and F24 (2026-09-03) **measured it as correct**: on 8 real 2025 lead-RB absences,
  mean-weighting ties depth weighting and matches observed inheritance concentration, and in
  the one live chart-vs-mean disagreement the CHART was wrong (Commissioner Exempt listing).
  Do not switch to `depth_chart_order` weighting; the sync depth watchdog surfaces live
  disagreements for human judgment.
- `INJURY_RATES` for TE/QB/DL/LB/DB are less rigorously sourced than RB/WR. This is documented
  in `config.py`. Improving them requires real position-specific data, not interpolation.
- `MANAGER_PROFILES` are deliberately excluded from data-driven calibration — per-manager sample
  size is far too small, and letting an optimiser tune them would let it compensate for errors
  elsewhere in the model.
- `FantasySimulationEngine` is deliberately one class. Its methods share substantial state;
  splitting it is a real architectural change, not a tidy-up.

## Release policy

Semantic versions, tied to what this repo already enforces (baseline: v1.0.0, tagged at
the F27 commit, 2026-09-03):

- **MAJOR** -- the model's predictions change materially. Operationally: **any intended
  golden-master regeneration, OR any change to sync-time constants that alter
  `player_baselines.json`** (`VOLATILITY_CONSTANTS`, `EPISTEMIC_ERROR_RATES`,
  `BASE_STREAMER_MEANS`, the blend weights) -- **which the goldens cannot detect**: the
  engine consumes `std_aleatoric` baked in at sync time, so a sync-time recalibration
  regenerates nothing while changing every prediction (learned from F28, whose golden
  deltas were byte-identical). A commit doing either says "MAJOR pending" in its
  message, and the tag lands with the release notes, not the commit.
- **MINOR** -- capability added, goldens byte-identical (new tools, report sections, CI,
  coverage).
- **PATCH** -- fixes and docs that move neither.
- **Season milestones, regardless of code**: cut a tag (at least PATCH) at week 5-6
  (F25's quoted-vs-realized calibration first measurable), week 11 (trade deadline),
  week 15 (playoffs), and season end (F7/F8/F18/F19 unblock together) -- an addressable
  snapshot at each evidence milestone is what makes "the model said X at the deadline"
  checkable later.

The reminder lives in `scripts.run_windows` (the scheduled read-only status tool), not in
a test: a commit-time gate on a release-time act would fail every commit between a golden
regeneration and its tag and teach itself to be ignored. It flags a pending MAJOR
(goldens changed since the latest tag), arrived milestone weeks (two-week window, then
quiet), and -- because GitHub's release UI tags server-side -- says `git fetch --tags`
when the clone sees no tags rather than misreporting "untagged".

**Release-note template** (v1.0.0 is the baseline; it folds sections 3-5 into one "not
done" block, which is acceptable -- the split below is preferred because "blocked on X"
and "not blocked but not done" are different honesty claims):

1. **What's in it** -- user-visible changes only; MAJOR/MINOR items named.
2. **What the audit found** -- counts copied from AUDIT_SUMMARY.md's grand-total row (the
   guarded single source; tests/test_docs ties the README to the same row).
3. **Blocked on hardware** -- R1 state, verbatim from its AUDIT_PLAN entry.
4. **Blocked on season data** -- the F-numbers and their unlock weeks.
5. **Not blocked, not done** -- the honest backlog.
6. **What this tag does not claim** -- standing caveats (IDP constants underived,
   the interval-dispersion bracket, coverage = execution on the monoliths, and whatever
   else is true at tag time).

## Statistical conventions

- Aleatoric variance is redrawn weekly. Epistemic variance is drawn **once per simulated season**
  and held fixed — this correctly propagates parameter uncertainty to season-level outcomes.
  Do not "simplify" this into a single draw.
- Lineups are chosen on `expected_pre` (pre-game expectation), never on realised `final_score`.
  Any change that lets realised outcomes influence lineup selection is lookahead leakage and is
  a serious bug.
- Vacated injury volume must be conserved: total apportioned never exceeds total vacated.

## The audit

`docs/AUDIT_PLAN.md` is the working spec. Phases are organised by property class (conservation,
orientation, invariance, bounds, liveness) rather than by file, because every defect found so far
came from asking a property question rather than reading code linearly.

Work one phase per session. Record findings as you go. Do not start Phase 8 (engineering /
decomposition) before Phase 0 (reproducibility harness) is complete.