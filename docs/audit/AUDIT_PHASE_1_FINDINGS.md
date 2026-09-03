# Audit Phase 1 — Conservation and Invariants

**Invariant under test:** nothing is created or destroyed that shouldn't be.

**Deliverable:** `tests/test_invariants.py` — 26 tests (5 of them property-based via Hypothesis).

**Suite:** 84 → 110 tests. 19 of the new tests pass and lock invariants that hold; 7 fail and
characterise the defects below. No pre-existing test changed behaviour.

---

## Method

Every quantity was checked against the running engine on Phase 0's committed fixtures, not
read off the source. `run_simulation` is a single ~445-line method, so its internals were
observed through the seams that already exist — the extracted helpers (`_solve_optimal_assignment`,
`_apportion_vacated_volume`, `_record_vacated_volume`, `_compute_faab_bid`) wrapped with
pass-through recorders, plus the 17 arguments handed to `export_and_visualize`, plus the real
`save_json` payloads. Nothing was refactored to make it observable; that is Phase 8's job.

`test_instrumentation_does_not_perturb_the_engine` asserts the wrappers change no number, so
the runs measured here are numerically identical to production ones. That test is also what
surfaced Finding 6 — it failed for a reason that had nothing to do with instrumentation.

The week01 / week06 fixture pair does the real work. Four of the six defects are **invisible at
week 1 and appear only once part of the season is in the books.** Production is at week 1 right
now, so those four are latent and will activate on the first sync after week 2.

---

## Invariants that hold (now locked with tests)

| Property | Result |
|---|---|
| Lineups fill exactly 13 slots | 3,594 / 3,594 assignments, both fixtures |
| No player in two slots; no ineligible slot assignment | 0 violations |
| Injury clocks stay in [0, 16], monotone decreasing | max observed exactly 16, no negatives |
| Vacated volume: apportioned ≤ vacated | exact equality, ratio 1.000000 |
| Playoff / Champ / Toilet shares | 400.000000000 / 100.000000000 / 100.000000000 |
| League-wide decisions conserved per season | exactly 112.0 in every simulation, zero variance |
| `all_play` total == H2H matrix total | 9,408 == 9,408 |
| Cumulative win trajectories never decrease | 0 decreasing steps |
| FAAB never sized against a negative balance | min remaining 5.46 |
| Team weekly total == sum of its 13 starters | max deviation 0.03, inside the 0.07 rounding bound |

The vacated-volume conservation fix from earlier work holds up under property testing across
arbitrary position groups and injury patterns, including the degenerate cases.

The apparent "17 of 128 team-weeks disagree" in the audit log was a false alarm: the log rounds
each starter and the team total to 2dp independently, so reconstruction error is bounded by
13 × 0.005 + 0.005 = 0.07. Observed max was 0.03. No defect.

---

## Findings

### 1. H2H "Any Given Sunday" matrix is deflated mid-season — **FIXED**

`export_and_visualize`, line ~1061:

```python
win_pct_matrix = pd.DataFrame.from_dict(h2h, orient='index') / (total_sims * 14) * 100
```

The numerator counts only the weeks actually simulated (`14 - (current_week - 1)`); the
divisor is hardcoded to 14. For any pair of teams, `P(A beats B) + P(B beats A)` must be 100%
less the tie rate.

| fixture | weeks simulated | observed pair sum |
|---|---|---|
| week01 | 14 | **100.00%** |
| week06 | 9 | **64.29%** — exactly 9/14 |

Every cell of the exported heatmap is scaled by `weeks_simulated / 14`. This is the same class
of silent deflation the all-play fix was written to remove; that fix corrected the numerator and
left the divisor.

**The replacement denominator was determined empirically, not assumed.** Three candidate windows
were tested against the actual accumulation for a run starting at week `W`:

| quantity | week01 implied weeks | week06 implied weeks | window |
|---|---|---|---|
| `h2h` matrix total | 14.0000 | **9.0000** | regular-season simulated |
| `all_play` total | 14.0000 | **9.0000** | regular-season simulated |
| `pts_against` | 14.0000 | **9.0000** | regular-season simulated |
| `global_weekly_scores` cols written | 14 | **9** | regular-season simulated |
| `wins` (decisions) | 14.0000 | **9.0000** | regular-season simulated |
| `global_season_points` | **15.97** | **10.96** | *all* simulated weeks, playoffs included |

`h2h` and `all_play` are incremented inside `run_simulation`'s `if week_num <= 14:` block, so
they span weeks `W..14` and exclude the weeks 15–16 playoff rounds. The correct divisor is
`REGULAR_SEASON_WEEKS - (current_week - 1)` — neither the hardcoded 14 nor a 16-week span.
week01 cannot distinguish these (both give 14); week06 pins it at exactly 9.0000.

That last row is the sole place where a 16-week basis is mixed with the 14-week one used
everywhere else. It is Finding 5.

**Fixed** together with 2–4: one derived `weeks_simulated` in `export_and_visualize`.

### 2. `schedule_luck_index` is not zero-sum mid-season — **FIXED (with a caveat)**

```python
true_win_pct = all_play[t] / (total_sims * 14 * 7)
actual_exp_pct = np.mean(wins[t]) / 28.0
```

`luck_rating` is a zero-sum quantity — one team's easy schedule is another's hard one — so the
ratings must sum to zero.

| fixture | Σ luck_rating | Σ true_win_pct (should be 4.0 for 8 teams) |
|---|---|---|
| week01 | **0.00** | 4.0000 |
| week06 | **+142.86** | 2.5714 |

At week 6 every single team is reported as lucky, which cannot happen. Three hardcoded constants
are involved: `14` (weeks), `7` (opponents, = n_teams − 1), and `28.0` (max decisions, which also
assumes `MEDIAN_SCORING_ENABLED`; under the season backtest's `False` the ceiling is 14, not 28).
All three are now derived.

**Open caveat — the fix restores the invariant but not full correctness.** Correcting the
divisors does make `luck_rating` sum to zero (Σ`actual_exp_pct` = 4.0000 exactly for 8 teams, so
the two terms cancel). But on a mid-season run the terms still cover **different spans**:
`actual_exp_pct` is a full-season win rate, because `wins[]` carries the banked results of
already-completed weeks, while `true_win_pct` is an all-play rate over only the weeks this run
simulated. Making them genuinely comparable needs historical all-play recomputed from
`weekly_actuals` — a real feature, not a divisor change. Recorded here and in a code comment
rather than papered over.

### 3. `avg_points_against_per_game` divides by 14 regardless of weeks played — **FIXED**

Same root cause. At week 6 it reports 113.75 where the true per-game figure is 176.94 — a 36%
understatement of a number labelled "per game."

### 4. `weekly_score_percentiles` are diluted by unplayed weeks — **FIXED**

`global_weekly_scores` is allocated as a full `(total_sims, 14)` array but written to only for
weeks the simulation runs. On a mid-season run the leading columns keep their initialised zeros,
and the exported statistics are computed over the flattened array:

| fixture | structural zeros | reported mean | true mean | reported `p10_floor` |
|---|---|---|---|---|
| week01 | 0.0% | 180.48 | 180.48 | 138.40 |
| week06 | **35.7%** | 109.64 | 170.55 | **0.00** |

`p10_floor` is exactly 0.00 for every team — a "10th-percentile scoring floor" that says a team
has a 10% chance of scoring nothing. The KDE density chart is fit over the same zeros, so its
bandwidth and shape are distorted too (the zero spike itself is hidden only because `xlim` starts
at 60). The chart's median-cut baseline is dragged by the same zeros:

| fixture | `avg_median_cut` as coded | over played weeks only |
|---|---|---|
| week01 | 178.41 | 178.41 |
| week06 | **112.82** | 175.50 — 35.7% low |

### 5. `Expected_Points` includes the playoff weeks — **FIXED**

`sim_points` accumulates for all 8 teams through weeks 15 and 16, but only 4 teams play a
semi-final and 2 play the final. `Expected_Points` is reported beside `Expected_Wins`, which
covers the 14-week regular season.

Every team is credited with roughly two extra weeks of scoring (+327 to +379, about 12%),
including the four eliminated at week 14 and the team that finished last. Seeding is unaffected —
the week-14 tiebreak reads `sim_points` before the playoff weeks are added — so this is a
reporting defect, not a standings one.

**Fixed** by gating the `sim_points` accumulation on `week_num <= REGULAR_SEASON_WEEKS`.
`week_scores` still carries weeks 15–16, since the playoff rounds are decided on them.

The "seeding is unaffected" claim above was asserted from reading the code; the golden deltas
then confirmed it empirically. Of `stage_a`'s 17 outputs, **exactly one moved** — `points`.
`wins`, `trajectories`, `seed_matrix`, `b_playoffs`, `b_champs`, `b_toilets`, `h2h`, `all_play`,
`pts_against`, `champ_players`, `global_weekly_scores` and `audit_log` are all byte-identical, so
no standing, seed, playoff berth or championship outcome changed. In the exports, only
`syndicate_comprehensive_matrix` moved; `syndicate_insights`, `live_season_forecast` and
`model_learning_report` are untouched.

The size of the move also cross-checks: the mean per-team reduction is −353.73 (week01) and
−346.87 (week06), against the +327…+379 and +314…+389 excesses measured before the fix. It
removes the playoff-week contribution and nothing else.

### 6. A config constant is mutated in place by running the engine — **FIXED**

`FantasySimulationEngine.__init__`:

```python
self.baselines[p_name] = SIM_CONFIG["KNOWN_MISSING_ASSETS"][p_name]
```

This binds the config module's own dict object into `self.baselines` rather than a copy.
`_apply_bayesian_updates` then writes posterior values directly into entries of `self.baselines`,
so the whitelisted player's sourced constants are overwritten in `config.py`'s live state for the
rest of the process.

Observed on the week06 fixture, repeating construction of the engine:

| run | `mean` | `std_epistemic` |
|---|---|---|
| documented value | 6.500 | **1.170** |
| 1 | 7.067 | 0.514 |
| 2 | 7.171 | 0.250 |
| 3 | 7.209 | 0.156 |
| 5 | 7.283 | 0.156 |

Three consequences, ascending:

- A constant whose provenance is documented in `config.py` silently stops holding the documented
  value. (CLAUDE.md rule 5 exists precisely to keep constants sourced.)
- **Results become order-dependent.** The same fixture gives different answers depending on what
  ran before it in the same process — the exact property the Phase 0 harness exists to guarantee.
  Verified: running `tests.test_invariants` before `tests.test_golden_master` fails all six
  golden-master tests. The suite is green today only because `test_golden_master` sorts first
  alphabetically and `SCENARIOS` happens to put week01 (which has no completed weeks, so
  `_apply_bayesian_updates` returns before mutating anything) ahead of week06. **That ordering is
  load-bearing by accident.** This belongs on the Phase 0 gap list as a third entry.
- **The corruption compounds.** Each run treats the previous run's posterior as its prior and
  re-applies the same five weeks of evidence, so uncertainty collapses on repetition rather than
  converging — 87% off the documented `std_epistemic` after three runs, built entirely on
  double-counted evidence. Both backtest harnesses run the engine in a loop and are exposed.

Production runs the simulation once per process, so the live week-1 forecast is not affected.

**Fixed** by deepcopying the whitelist entry at the point of imputation. The mutation only ever
landed on the config module, never on the loaded baselines the engine actually simulates from, so
this moved no exported number — verified: all 12 golden-master tests unchanged and green, in both
module orderings.

### 7. The bye-week mechanism is entirely dead code

`sync.py` line 325: `"bye": player.get("team_bye", 0)`.

Sleeper's `/players/nfl` payload has no `team_bye` key. Checked against the committed
`data/sleeper_players_cache.json`: **12,225 of 12,225 entries lack it**, and no key containing
"bye" appears anywhere — not at the top level, not inside `metadata`. The default fires for every
player on every sync, so all 964 entries in `player_baselines.json` carry `bye: 0`. Week numbers
start at 1, so the engine's three `week_num == p_info.get('bye')` guards (streamer-need scan,
injury pass, scoring pass) can never fire.

Consequence: real bye weeks are not modelled at all. Rosters are never short-handed by a bye,
streamer demand never spikes in weeks 5–14, and FAAB is never spent covering one.

`tests/test_sync.py` already covers this line and passes — because its fixture supplies a
`team_bye` field of its own invention. That is how the gap survived: the test proves the code
reads the field correctly, not that the field exists.

**Coverage consequence:** the Phase 1 invariant "a player on bye never scores and never absorbs
vacated volume" **could not be verified end to end** and is recorded as an open gap, not as
passing. Note that `_apportion_vacated_volume` has no bye awareness even in principle — it is
never told which week it is — so if byes are ever ingested, a player on bye will be counted in the
apportionment denominator and his share destroyed. That should be fixed in the same change that
makes byes live.

**FIXED — bye modelling, 2026-08-28 (branch `audit/phase-7-calibration`, steps 1–6).** Sync derives each
team's bye from the NFL schedule it already fetches (`config.derive_bye_weeks`: the one usable week a
team appears in no pairing; 32/32 teams for both 2026 and 2025, weeks 5–14), records it in
`nfl_schedule.json["_meta"]["byes"]`, and stamps every baseline's `bye` from its team; the engine
reads only `_meta.byes` (single derivation point). The three guards are live; the golden fixtures
carry byes (962/964 baselines) and `TestByeWeekLiveness` is inverted into two consistency guards.
The prediction above about `_apportion_vacated_volume` turned out to be wrong in a useful way: an
onset is only recorded for a player whose team plays that week, and pools are per real NFL team,
so every pool's team and every recipient is playing — pinned on the real engine by
`tests/test_byes.py::TestByeAndInjuries::test_vacated_volume_never_touches_a_team_on_bye` (no DET
pool in DET's bye week, no recipient on bye in any week, and DET contingency does pay out in week 7
so the check is not vacuous). Real-2025 effect of byes alone: points bias +1.1% → −1.8%, cover80
0.62 → 0.65 (bye-modelling step 5a, AUDIT_PLAN.md).

**Also found while checking this:** `depth_chart_order` *is* present on 12,193 cache entries
(1,812 non-null). That is the field Phase 7 wants for replacing mean-weighted vacated-volume
apportionment in the handcuff case — it is already being downloaded, just not persisted.

### 8. `power_rankings_baseline_pts` is labelled as a starting-lineup score but is not one

Found while auditing the adjacent point-based exports for the same 14-vs-16-week basis mixing.
It does **not** have that problem — it is a per-week projected figure, correctly so, and is not
derived from `global_season_points`.

It does carry a label/content mismatch. `get_optimal_score` returns
`optimal_starting_lineup + bench * 0.1`, but the JSON key is `power_rankings_baseline_pts` and
the chart's x-axis reads "Optimal Valid Starting Lineup Baseline (Projected Points)". Measured on
week01: Femboy Cats' true starters-only optimum is 166.8 against a reported 173.1 — a 3.6% bench
uplift folded into a number presented as starters. The bench term is deliberate (it rewards
depth), but the label does not say so.

Minor, and Phase 6's territory rather than Phase 1's. Reported, not fixed.

---

## Remediation status

| # | Status | Moves hashes |
|---|---|---|
| 1 | Fixed | week06 `stage_b` + `stage_c` |
| 2 | Fixed, one caveat open (span mismatch) | week06 `stage_b` + `stage_c` |
| 3 | Fixed | week06 `stage_b` + `stage_c` |
| 4 | Fixed | week06 `stage_b` + `stage_c` |
| 5 | Fixed | both scenarios, `stage_a` (`points` only) + `stage_b` + `stage_c` |
| 6 | Fixed | none |
| 7 | **Fixed** — byes derived from the NFL schedule at sync (32/32 teams), engine guards live, vacated-volume non-interaction pinned | `stage_a`, both scenarios (fixtures now carry byes) |
| 8 | Open — reported only, Phase 6 | n/a |

Findings 1–4 shared one root cause and one fix: a single `weeks_simulated` derived once in
`export_and_visualize`, with `7` and `28.0` derived from league size and the median-scoring flag.
`REGULAR_SEASON_WEEKS` was consolidated into `config.py` from `backtest_season.py`, which had
defined it locally — the engine now reads it too, and a season length disagreeing between the
engine and the backtest is precisely the drift `config.py` exists to prevent.

Verified footprint of the 1–4 fix: **week01 unchanged in all three stages** (the divisor is
correct by coincidence at week 1), **week06 `stage_a` unchanged in all 17 outputs** — the engine
itself is untouched — and exactly two week06 payloads moved,
`syndicate_comprehensive_matrix_week_6.json` and `syndicate_insights_week_6.json`. Element counts
are identical, so no field was added or removed. The insights `min` moving from 2.00 to −7.95 is
the zero-sum property being restored: previously every team's `luck_rating` was positive.
