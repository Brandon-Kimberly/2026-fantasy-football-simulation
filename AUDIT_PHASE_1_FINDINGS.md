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

### 1. H2H "Any Given Sunday" matrix is deflated mid-season — **latent now, active from week 2**

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

### 2. `schedule_luck_index` is not zero-sum mid-season

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

### 3. `avg_points_against_per_game` divides by 14 regardless of weeks played

Same root cause. At week 6 it reports 113.75 where the true per-game figure is 176.94 — a 36%
understatement of a number labelled "per game."

### 4. `weekly_score_percentiles` are diluted by unplayed weeks

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
at 60).

### 5. `Expected_Points` includes the playoff weeks — **affects every run, including week 1**

`sim_points` accumulates for all 8 teams through weeks 15 and 16, but only 4 teams play a
semi-final and 2 play the final. `Expected_Points` is reported beside `Expected_Wins`, which
covers the 14-week regular season.

Every team is credited with roughly two extra weeks of scoring (+327 to +379, about 12%),
including the four eliminated at week 14 and the team that finished last. Seeding is unaffected —
the week-14 tiebreak reads `sim_points` before the playoff weeks are added — so this is a
reporting defect, not a standings one.

### 6. A config constant is mutated in place by running the engine — **most serious**

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

**Also found while checking this:** `depth_chart_order` *is* present on 12,193 cache entries
(1,812 non-null). That is the field Phase 7 wants for replacing mean-weighted vacated-volume
apportionment in the handcuff case — it is already being downloaded, just not persisted.

---

## Remediation status

Not started. This commit is characterisation only, per CLAUDE.md rule 3.

Findings 1–4 share one root cause and one fix: derive the denominator from the weeks actually
simulated instead of hardcoding 14 (and derive `7` and `28.0` from league size and the median
scoring flag). Finding 5 is a scope decision about what `Expected_Points` should mean. Finding 6
is a one-line copy. Finding 7 needs a real bye-week source, since Sleeper does not supply one.

Fixing 1–5 changes exported numbers, so the week06 golden hashes will need regenerating in a
separate commit with the reason recorded.
