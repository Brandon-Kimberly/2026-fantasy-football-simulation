# Audit Phase 4 — Decision Logic

**Invariant under test:** decisions are optimal given information legitimately available at
decision time.

**Deliverable:** `tests/test_lineup_optimality.py` — 7 tests. 4 pass and lock verified
properties; 3 fail and characterise the defects below.

**Suite:** 154 → 161 tests. No pre-existing test changed behaviour.

**Status:** characterisation only. Nothing is fixed. Triage before remediation, as in Phases 1–3.

**Method note.** Optimality questions were answered by brute force and closed-form cross-checks.
The real-data backtest was not used: it is the gate for a decision-logic change that might touch
baseline computation (the way Phase 2 findings 4 and 5 did), and nothing in this phase's
findings does. If remediation of finding 3 or 4 changes how a team's points are computed, run it
then.

---

## Verified — holds, now locked with tests

| Plan item | Result | Test |
|---|---|---|
| Hungarian assignment is truly optimal, incl. dual-eligibility and FLEX | **1,700 random rosters vs exhaustive search, 0 suboptimal**: 1,500 rosters of 1–6 players (1,025 containing a dual-eligible player) and 200 seven-player FLEX-heavy rosters. Worked example: a WR/DB is placed at DB when that frees a WR slot for a better pure WR | `TestHungarianOptimality` |
| No lookahead leakage | Controlled engine (std_epistemic 0, injuries off, all FA): **49,920 candidate values, all exactly the baseline mean** while realised team scores varied (sd 18.4). The lineup criterion sees `expected_pre` only | `TestNoLookahead` |
| Streamer needs match real holes | The greedy need counter and the Hungarian's unfilled-slot count agree **every week on both fixtures** (480 + 330 team-weeks): no FAAB spent on phantom holes, no unbid holes | `TestStreamerNeedsMatchRealHoles` |
| FAAB bid curve | Bounded by budget and ceiling, scales with aggression and need — already property-tested in Phases 1–2; nothing added | — |

Two things verified by reading and measurement, reported rather than asserted:

- **The 2-week deficit lookahead is a no-op today.** `wk_check in [week_num, min(14, week_num+1)]`
  evaluates the same availability twice: byes are unmodelled (Phase 1 finding 7) and next week's
  injuries are unknown at decision time, so next week's deficit always equals this week's. It is
  harmless, and it will become live the day byes land — at which point the *discard* path
  (finding 4) becomes live too.
- **FAAB has no bite.** Managers spend 2.9–5.9 of 100 per simulated season (bids ≈ draw × agg ×
  needs/2 × deflation ≈ 3.5 each; the competitive ceiling never binds). Nobody ever runs out, so
  `faab_agg` distinguishes managers only through bid *ordering*. Design observation, not a defect.

---

## Findings

### 1. The trade mechanism is effectively dead — `trade_will` has no observable effect

Every trade offer is "the desperate team's best player for the rich team's 6th- and 7th-best".
Reconstructed from the real engine (evaluations are 3 or 4 `get_optimal_score` calls, because
the acceptance test short-circuits):

| fixture | seasons | evaluated | accepted | rich-side gain |
|---|---|---|---|---|
| week01 | 100 | 548 | **0** | median −8.0, **max −3.2** |
| week06 | 100 | 691 | **16 (2.3%)** | median −4.8, max +1.9 |

Why: in a 13-starter lineup, a top-2 team's 6th- and 7th-best players are **starters** (medians
12.7 / 12.2 on week01); the offered player is a **QB 99% of the time** (QBs carry the highest
means, 19.5 median), and the rich team already starts an equal-or-better QB in 49–78% of cases.
Giving two starters for one player that mostly goes to the bench cannot improve the rich team's
optimal score. The desperate side accepts 67% of the time; the rich side almost never.

Consequence: `MANAGER_PROFILES[...]['trade_will']` — one of the two manager parameters, and
one CLAUDE.md deliberately excludes from calibration — does nothing measurable. **Severity:
medium** (a modelled behaviour that does not occur; no distributional harm, but the model claims
something it doesn't do). Fixing it (any offer structure that can be favourable to both sides)
moves `stage_a` in any scenario where a trade completes.

### 2. A completed trade shrinks the rich team's roster by one

```python
tent_d = [p for p in d_list if p != p1] + [p2, p3]; tent_d.sort(...); dropped = tent_d.pop()
tent_r = [p for p in r_list if p not in [p2, p3]] + [p1]
```

The desperate side receives two, gives one, drops one: conserved. The rich side gives two,
receives one, drops nothing: **−1 per trade**, permanently. Observed on all 16 completions in
100 week06 seasons (19 → 18) and reproduced on a crafted league where the trade is favourable to
both sides. A rich team that trades repeatedly walks its roster toward 13 and below, at which
point it starts injecting streamers — which, per finding 3, may *help* it. Also left behind:
the traded players' entries stay in `sim_meta[r_team]` (stale, harmless). **Severity: medium
today** (few trades complete), **high if finding 1 is fixed** (trades become frequent).

### 3. A won streamer is valued by league-wide bid rank, not by position — and beats real starters — **FIXED**

```python
available_streamers = [max(4.0, 12.0 - (i * 0.5)) for i in range(...)]   # by bid rank
won_streamers[t_name].append(available_streamers[i])
```

The highest bid in the league gets a 12.0-point streamer, the next 11.5, and so on — for
whatever slot happens to be empty. Against the fixture's real replacement levels:

| position | replacement level | unbid fallback | rank-1 streamer | streamer − fallback |
|---|---|---|---|---|
| QB | 17.7 | 14.2 | 12.0 | −2.2 |
| RB | 11.2 | 9.0 | 12.0 | +3.0 |
| WR | 10.5 | 9.0 | 12.0 | +3.0 |
| TE | 7.7 | 7.5 | 12.0 | **+4.5** |
| K | 10.7 | 8.6 | 12.0 | +3.4 |
| DL | 8.4 | 7.5 | 12.0 | **+4.5** |
| LB | 10.5 | 8.4 | 12.0 | +3.6 |
| DB | 8.8 | 8.0 | 12.0 | **+4.0** |

A rank-1 streamer out-projects **105 of the 156 rostered players**. With ~1 bid per week league-
wide, the rank-1 value is what a team with a hole usually gets: a roster hole at DB/DL/TE/K is
an *upgrade* over a real starter for ~3.5 FAAB. Verified in the audit log: `STREAMER_DB_0` at
12.0 against a DB replacement level of 8.8, `STREAMER_TE_*` at 12.0 against 7.7. **Severity:
medium-high**: it rewards the thing streaming is meant to penalise, and it interacts with
finding 2 (a shrinking roster becomes a streamer factory). Fixing it moves `stage_a` wherever a
streamer starts — most sims on both fixtures.

**Fixed.** A won streamer is capped at its position's replacement level at the point it fills a
slot (where the position is known) — but **only where that replacement level was computed from
real players**. `_calc_replacement_levels` now records which positions had data; a position
absent from the baseline pool keeps the ladder value rather than being pinned to the unverified
4.0 default.

That qualifier came from the backtest, run as insurance although the change touches no baseline
computation. The first version (cap unconditionally) moved the real-2025 points bias from +1.1%
to **−5.2%** (mean z +0.33, ~5 SE) — not because the cap is wrong, but because the 2025 rosters
have no DB/DL/LB players at all (team-DEF era), so every IDP slot is a streamer every week and
the cap pinned all of them to 4.0. The tightened version leaves the backtest at **+1.1% → +1.1%**
(cp3 +1.19 → +1.24 pts; the rest identical). Production-like effect, isolated at 400 seasons on
the week01 fixture: weekly team mean 175.71 → 174.95, **−0.43%**. The 30-season golden
summaries show +1.3% at week01 — that is RNG reshuffle (the trade block's `rand()` calls depend
on the standings, which the cap perturbs from week 6 on), not the cap; see the golden-master
docstring on summary size vs. interpretive precision.

### 4. Latent: a won streamer is discarded if the hole is next week's, and the FAAB is still spent

`won_streamers` is rebuilt empty every week, but needs are `max(this week, next week)` and the
bid is paid this week. Today the two are always equal (the lookahead is a no-op, above), so
this never fires — the bids-equal-holes test proves it. The moment byes are modelled, a team
whose hole is next week pays this week, receives nothing, and pays again next week. **Severity:
low now, medium once byes land.** Recorded so the bye work re-checks it; it is the same class as
the two absence-blocked Phase 2 findings.

### 5. Minor, reported only

- The desperate side always offers its single highest-mean player, which is nearly always its
  QB (finding 1's mechanism); there is no position-awareness in offer construction.
- `sim_meta[r_team]` keeps `p2`/`p3` after a trade; `sim_meta[d_team]` keeps `p1`. Stale
  entries, read by nothing. Cosmetic.
- Weeks 15–16: non-playoff teams keep bidding and simulating lineups. Cosmetic, no effect on
  outputs (regular-season quantities stop at week 14 since Phase 1).

---

## Triage table

| # | Finding | Severity | Blast radius | Moves hashes |
|---|---|---|---|---|
| 1 | Trades effectively never complete; `trade_will` inert | Medium | manager model claims; nothing distributional today | `stage_a`, any scenario with a completed trade |
| 2 | Rich roster −1 per completed trade | Medium → high if 1 is fixed | roster size, streamer injection | `stage_a`, same |
| 3 | Streamer value by bid rank beats real starters — **fixed** (capped at data-derived replacement level; backtest neutral, −0.43% production-like) | Medium-high | any team-week with a hole | `stage_a`, both |
| 4 | Won streamer discarded when the hole is next week (latent) | Low → medium with byes | FAAB, streamers | none today |
| 5 | Cosmetic notes | — | — | — |

Findings 1 and 2 belong together: fixing 1 without 2 makes 2 frequent. Finding 3 is the one with
present-day distributional effect. None of the three touches baseline computation, so the
backtest gate does not apply unless remediation reaches into scoring.
