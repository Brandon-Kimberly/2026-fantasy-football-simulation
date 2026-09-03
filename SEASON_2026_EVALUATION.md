# Season 2026 evaluation criteria

**Written and committed 2026-09-02, seven days before the first 2026 kickoff
(2026-09-09, 5:20 PM PT).** No 2026 game has been played. This document exists so the
model is judged against a standard set in advance, not one retrofitted to the outcome —
whatever happens, these are the criteria, and they do not move after the fact.

## What counts as success

1. **Quoted probabilities match realized frequencies within stated error.** This is the
   model's actual claim — not "who wins," but "how often." Every canonical weekly report
   quotes matchup win probabilities and P(≥ median) with standard errors; success is
   those probabilities being right *as frequencies* over the season, within the error the
   small sample implies (~56 matchup-weeks is not many; the reliability bins will be
   wide, and that width is part of the stated error, not an excuse appended later).
2. **Points-for finishes in the top third of the league.** Operationalized now so it
   cannot be argued later: finishing 1st–3rd of 8 in regular-season points-for counts
   (8/3 = 2.67; 3rd is the boundary and is declared in, tonight, before any game).
   Points-for is the record-free measure of whether the model's weekly decisions —
   lineups, waivers, the depth moves — actually produced points.
3. **The interval-coverage gap does not worsen.** Pre-season baseline, recorded from
   F25 (docs/AUDIT_PLAN.md): team-week dispersion is understated by a factor bracketed
   at **r ≈ 1.15–1.34, best estimate ~1.2** — meaning a quoted 80% is realistically
   ~74–78%. The 2026 quoted-vs-realized measurement replacing that bracket with a
   *smaller* number (or confirming ~1.2 with the harness artifacts gone) is success on
   this criterion; a materially larger number is failure.

## What counts as neither success nor failure

**Final record. Playoff berth. Championship.** The 2025 season retrospective is the
reason this is stated in advance: the 2025 team went **4–10 on the second-lowest
points-for** (1,721.32), with schedule luck worth about one win (all-play expected 5.14
vs 4 actual, the league's worst), while Femboy Cats went **6–8 on fewer points**
(1,717.70). Fourteen games in an 8-team league cannot distinguish a good model from a
lucky one on record alone. A championship will not validate this model, and a losing
record will not refute it — the calibration numbers will do both jobs.

## What is in scope versus out of scope

**In scope — injuries, breakouts, falloffs are the model's job, not its excuses.**
Absence is priced explicitly (F4/F5/F6): the forward onset/duration model realized
**14.6% simulated absence vs 14.7% real** on 2025 data. `EPISTEMIC_ERROR_RATES` is
precisely the parameter that means "this player may be much better or worse than
projected," drawn once per simulated season by design. A well-calibrated model does not
predict *who* gets hurt or *who* breaks out; its intervals contain those outcomes at the
stated rate. If a star's injury or a rookie's breakout blows the calibration, that is a
model failure under criterion 1, full stop.

**Out of scope — league rulings, not football.** Holdouts, suspensions, and
Commissioner-Exempt listings are administrative absences whose length is set by rulings,
not healing. The live example is named now: **Josh Jacobs**, rostered with `NA` status,
whose return is simulated on `ABSENCE_RETURN_HAZARD_STEADY` = 0.16/week — a hazard
measured on 2025 IR/PUP/Sus/DNR returns and **carried over, unverified, for this
status** (F17 tracks the data point his return will provide). Errors traceable to that
class of absence are noted, not scored.

## How each criterion is measured, and when

The instruments already exist; nothing needs building to run this evaluation:

- **Criterion 1** — `data/logs/predictions_2026.jsonl` records every canonical run's
  ex-ante quoted probabilities (last-canonical-row-wins, git-tracked, machine-loss-proof).
  F25's quoted-vs-realized calibration becomes measurable at **~week 5–6** (a tagged
  milestone per the release policy) and sharpens all season.
- **Criterion 2** — league standings; trivially readable at any point, final in week 14.
- **Criterion 3** — the same quoted-vs-realized measurement, compared against the
  bracket recorded above; the points-backtest gate's optimal-lineup target
  (`scripts/run_points_backtest`) provides the harness-side cross-check.
- **The season post-mortem** — `scripts/season_retrospective.py --season 2026` in
  January: schedule luck, lineup efficiency, absences, and high-scorer losses, reported
  separately with no combined verdict, exactly as done for 2025. It reads the slot list
  from the persisted season bundle, so 2026 requires no code changes.

This file is a one-time pre-commitment. It is not updated during the season; the
January evaluation cites it as written.
