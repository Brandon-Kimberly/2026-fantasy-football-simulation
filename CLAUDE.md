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
py -3.10 -m unittest discover tests      # full suite — 232 tests, must all pass
py -3.10 -m scripts.run_sync             # pull live data into data/
py -3.10 -m scripts.run_simulation       # run the engine
py -3.10 -m scripts.run_season_backtest  # backtest vs the real 2025 season
py -3.10 -m scripts.run_player_backtest  # calibrate constants vs real player data
```

Expected verdict: `OK (skipped=1, expected failures=4)`. The skip is the live-ingestion test
(`RUN_LIVE_INGESTION_TESTS=1` runs it); the four expected failures are deliberate red
characterisations of tracked open items (`AUDIT_SUMMARY.md`). `espn_api` is in
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

4. **Do not refactor what is not covered.** `run_simulation` (~445 lines) and
   `export_and_visualize` (~333 lines) have no golden-master test. Until Phase 0 of
   `AUDIT_PLAN.md` is complete, any behaviour-preserving claim about them is unfalsifiable.

5. **Every constant cites a source or is marked unverified.** Numbers in `config.py` carry
   comments explaining their derivation. Preserve that. A new constant with no sourcing comment
   is not acceptable; "unverified, carried over" is acceptable and honest.

6. **Run the full suite before and after every change.** Report the count. Investigate any
   change in the number of tests that run, not just failures.

7. **Prefer finding the real defect over satisfying the test.** If a test fails, diagnose the
   cause before editing either side. Loosening an assertion to make a suite green is a
   regression in disguise.

8. **Every phase branches from `main`, never from another phase's branch.** After a phase
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
- Mean-weighted vacated-volume apportionment is **known to be backwards in the handcuff case**
  (a true backup carries a low projection precisely because he sits behind the starter). The
  correct fix is ingesting Sleeper's `depth_chart_order`, not adjusting the weights by feel.
- `INJURY_RATES` for TE/QB/DL/LB/DB are less rigorously sourced than RB/WR. This is documented
  in `config.py`. Improving them requires real position-specific data, not interpolation.
- `MANAGER_PROFILES` are deliberately excluded from data-driven calibration — per-manager sample
  size is far too small, and letting an optimiser tune them would let it compensate for errors
  elsewhere in the model.
- `FantasySimulationEngine` is deliberately one class. Its methods share substantial state;
  splitting it is a real architectural change, not a tidy-up.

## Statistical conventions

- Aleatoric variance is redrawn weekly. Epistemic variance is drawn **once per simulated season**
  and held fixed — this correctly propagates parameter uncertainty to season-level outcomes.
  Do not "simplify" this into a single draw.
- Lineups are chosen on `expected_pre` (pre-game expectation), never on realised `final_score`.
  Any change that lets realised outcomes influence lineup selection is lookahead leakage and is
  a serious bug.
- Vacated injury volume must be conserved: total apportioned never exceeds total vacated.

## The audit

`AUDIT_PLAN.md` is the working spec. Phases are organised by property class (conservation,
orientation, invariance, bounds, liveness) rather than by file, because every defect found so far
came from asking a property question rather than reading code linearly.

Work one phase per session. Record findings as you go. Do not start Phase 8 (engineering /
decomposition) before Phase 0 (reproducibility harness) is complete.