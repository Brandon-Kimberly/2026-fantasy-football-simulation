# Audit Phases 5 + 6 — Season Mechanics and Outputs

**Phase 5 invariant:** league rules are implemented as written.
**Phase 6 invariant:** what is exported equals what was computed.

**Deliverable:** `tests/test_season_mechanics.py` — 12 tests. 9 pass and lock verified rules and
export consistency; 3 fail and characterise the defects below.

**Suite:** 161 → 173 tests. No pre-existing test changed behaviour.

**Status:** characterisation only. Nothing is fixed. Triage before remediation, as in every phase.

---

## The rules, from the source of truth

Sleeper league settings, fetched live 2026-08-28 (`/league/1310010483033522176`):

| setting | value | engine |
|---|---|---|
| `num_teams` | 8 | 8 ✓ |
| `playoff_teams` | 4 | top 4 seeds ✓ |
| `playoff_week_start` | 15 | weeks 15 (1v4, 2v3) and 16 (final) ✓ |
| `playoff_round_type` | 0 (one week per round, no reseeding) | no reseeding ✓ |
| `league_average_match` | **1** | median matchup on → 2 decisions per team per week ✓ |
| `trade_deadline` | week 11 | engine trades in weeks 6–10 ✓ |
| roster | 13 starters + 6 BN + 2 IR | `REQUIRED_STARTING_SLOTS` = 13 ✓ |

`MEDIAN_SCORING_ENABLED = True` is therefore correct for 2026 (and `False` for the 2025 backtest
remains the deliberate decision CLAUDE.md records). "Toilet bowl" in the exports is the last
regular-season seed; Sleeper has no consolation bracket configured. Standings tiebreak is total
points (Sleeper's default), which is what the engine sorts on.

---

## Verified — holds, now locked with tests

| Property | Result | Test |
|---|---|---|
| 2 decisions per team per week when median is on | every weekly trajectory increment ∈ {0, 0.5, 1, 1.5, 2}; league-wide **exactly 8 per week, every week, every sim** (only {0, 1, 2} observed — no ties occurred) | `TestDecisionsPerWeek` |
| Seeding by wins, then points | `seed_matrix` equals the ranking recomputed per sim from the exported `wins` and `points` on both fixtures; the points tiebreak was exercised 17–28 times per run | `TestSeedingAndBracket` |
| Playoff berths / last place | `b_playoffs` = seeds 1–4 exactly; `b_toilets` = seed 8 exactly | same |
| One champion per sim, from the field | Σ champions = sims; a team with no playoff appearances has no championship | same |
| Week indexing, regular season | `current_week` 13 and 14 simulate exactly 2 and 1 remaining regular-season weeks | `TestWeekIndexingEntryPoints` |
| Exports = computation | `Expected_Wins/Points`, `Playoff_Pct`, `Champ_Pct`, all seven win percentiles, `expected_cumulative_wins_by_week`, `finishing_seed_probabilities`, the H2H matrix (row = winner, ÷ sims × weeks simulated, NaN diagonal), `highest_single_week_score_observed` / team / week — all agree with direct recomputation to 1e-9 on both fixtures | `TestExportsMatchComputation` |
| Orientation of every `to_dict` / `.loc` in `export_and_visualize` | `summary_df.to_dict('records')`, `win_pct_matrix.to_dict('index')`, `seed_df.loc[summary_df['Team']]` — covered by the checks above; the Phase 1 transposition class has no siblings | same |

Schedule-luck decomposition and `all_play_wins` were verified and fixed in Phase 1 (findings
1–3) and are not re-derived here; the open span caveat on `luck_rating` stands.

---

## Findings

### 1. The engine crashes on any playoff or post-season `current_week` — **INTERIM FIX (explicit refusal); graceful seeding tracked as F3**

| `current_week` | outcome |
|---|---|
| 13, 14 | runs; simulates exactly the remaining regular-season weeks |
| **15** | `IndexError: list index out of range` — `top4` is only populated by the week-14 seeding block, which never runs |
| **16** | `KeyError: None` — `w1`/`w2` are `None`, so the final looks up `week_scores[None]` |
| **17** | `UnboundLocalError: week_num` — the week loop never executes and the post-loop assert reads its variable |

`sync_all` writes `current_week` straight from Sleeper's `/state/nfl`, which reports 15–18 during
and after the playoffs. So the first sync in playoff week 1 turns every forecast into a stack
trace. There is no path that seeds the bracket from *banked* standings (the information is in
`weekly_actuals` / `league_standings`), and no explicit refusal. **Severity: high, latent** —
activates in week 15. Fix options: seed the bracket from banked standings when
`current_week ≥ 15` and simulate only the remaining playoff rounds; or refuse with a `ValueError`
that says so. Either moves no golden hash (both fixtures are regular-season).

**Immediate half fixed; graceful version tracked as `AUDIT_PLAN.md` F3.** `run_simulation` now
refuses with a `ValueError` for `current_week > 14` that names the limitation and F3. The
entry-point test asserts the refusal (and flips to "these weeks run" when F3 lands). No golden
movement.

### 2. A banked H2H tie is truncated in the forecast record — **FIXED**

```python
'actual_wins_banked': int(self.actual_h2h_wins[t] + self.actual_median_wins[t])
'approximate_magic_number': max(0, 16 - int(self.actual_h2h_wins[t] + self.actual_median_wins[t]))
```

Sleeper records an H2H tie, and `sync._extract_weekly_h2h_results` stores it as 0.5. `int()`
drops it: a team with banked 2.5 exports `actual_wins_banked: 2` while `expected_final_wins`
in the same record keeps the half (10.5), so `banked + expected_future ≠ expected_final`
(2 + 8.0 vs 10.5). The magic number is off by one for the same team. **Severity: low** (ties are
rare; the record is internally inconsistent when they happen). Moves no hash on the fixtures
(no banked ties).

**Fixed.** `actual_wins_banked` and `approximate_magic_number` are now the float the engine
holds. The forecast payload's golden hash moved in both scenarios with every moment identical
(int 2 → float 2.0 in the canonical rendering); `stage_a` is byte-identical.

### 3. `is_mathematically_eliminated` is a Monte Carlo zero, not a proof

```python
'is_mathematically_eliminated': bool(p_prob == 0.0)
```

On week06 — 8 regular-season weeks and 16 decisions still available to every team — the flag
names **one** team at 16 sims and **three** at 2 sims. A mathematical property of the season
cannot depend on how many seasons were simulated. At the production size (10,000 sims) a true
0/10,000 is a ~0.03% chance, which is still not elimination. **Severity: low-medium** (a
false claim in a headline field; the fix is either rename to `no_playoff_appearances_in_sample`
or compute a real sufficient condition from banked decisions). Moves no `stage_a` hash; moves
`stage_b`/`stage_c` in week06 only if the value changes there.

### 4. Playoff ties advance the *lower* seed — **FIXED**

```python
w1 = s1 if week_scores.get(s1, 0) > week_scores.get(s4, 0) else s4
champ = w1 if week_scores.get(w1, 0) > week_scores.get(w2, 0) else w2
```

Strict `>` sends a tied semi-final to seed 4 (over seed 1) and a tied final to the seed-2 side.
Sleeper advances the higher seed on a tie. Measure-zero with continuous scores — never observed
— but the direction is wrong and it is one character. **Severity: low.** Not tested (inline,
unreachable without a contrived engine); reported.

**Fixed.** The rule is extracted as `_playoff_winner(a, b, week_scores, seed_order)`: higher
score wins, an exact tie goes to the earlier seed. Tested directly (semi-final, final, argument
order). The golden master confirms the extraction changed no outcome — `stage_a` byte-identical
in both scenarios.

### 5. A score exactly on the median awards five median wins — reported

`if score >= median_cut` with an 8-team median (mean of 4th and 5th): if the 4th and 5th tie
exactly, both are `>=` and five teams get the median win, breaking the "exactly 8 decisions per
week" rule. `sync_all` computes the banked `median_win` with the same `>=`, so at least the two
agree. Measure-zero in the engine; possible in real data (identical team scores). **Severity:
low.** Reported.

### 6. `approximate_magic_number = 16 − banked` is an unsourced constant — reported

16 of 28 decisions as the playoff lock is a heuristic with no derivation (the trajectory chart
draws the same `axhline(16)`). It is labelled *approximate*, which is honest; it is recorded here
so Phase 7 can either derive it from the seed distribution or drop it. **Severity: low.**

### 7. Housekeeping — the stale PNG

`data/Week_1_Scoring_Density_KDE.png` is not produced by any current path;
`weekly_scoring_density_path` writes `Week_1_Weekly_Scoring_Density.png`. `data/` is entirely
gitignored, so this is a local orphan from before the rename: delete it. No code change.

---

## Triage table

| # | Finding | Severity | Blast radius | Moves hashes |
|---|---|---|---|---|
| 1 | Engine crashes for `current_week` ≥ 15 — **refuses cleanly now; F3 makes it run** | High, latent (week 15) | every playoff-week forecast | none |
| 2 | Banked H2H tie truncated; forecast record inconsistent — **fixed** (float) | Low | teams with a tie on record | forecast payload, representation only |
| 3 | `is_mathematically_eliminated` is a sample zero | Low-medium | headline forecast field | week06 `stage_b/c` at most |
| 4 | Playoff ties advance the lower seed — **fixed** (`_playoff_winner`, tested) | Low (measure-zero) | — | none (verified) |
| 5 | Exact-median tie awards 5 median wins | Low (measure-zero) | — | none |
| 6 | Magic number 16 unsourced | Low | labelled approximate | — |
| 7 | Orphan PNG in `data/` — **deleted locally** | — | local only | — |

None of these touch baseline computation or the weekly draw; the backtest gate does not apply.
