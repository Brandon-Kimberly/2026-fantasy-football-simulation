# Scoped backlog — health audit of 2026-09-22

**Written after the F49–F55 week, before any of it is acted on.** Every item below is
scoped so that a model with less context than the author can pick it up cold: what it is,
where the evidence sits, exactly what to change, exactly what *not* to touch, how to know
it is done, and the traps a careful-but-uninformed implementer would fall into.

Nothing in this document has been changed. It is a backlog, not a changelog.

**How to use it.** Pick an item. Read its *Traps* section before touching a file. Follow
`CLAUDE.md` rules 1, 3, 6 and 8 without exception — failing test first, characterisation
and fix as separate commits, full suite before and after with counts, and the finding
closed in `AUDIT_SUMMARY.md` in the same commit as `AUDIT_PLAN.md`. Anything marked
**MAJOR** regenerates baselines or goldens and needs the release-policy dance in
`CLAUDE.md`; do not bundle two MAJORs unless the entry says to.

**Priority key.** P1 = affects a decision this season, correctness. P2 = model
calibration, affects every prediction. P3 = tooling and workflow. P4 = data provenance
and infrastructure. P5 = reporting polish.

---

## Part 0 — State of the team (status, not backlog)

Recorded so a later reader can judge the backlog against where the roster actually was.

| | week 3, corrected data, pre-waivers |
|---|---|
| record | 2-2 (both week-2 legs lost, 149.02 to 150.41 H2H) |
| lineup VORP | **47.2**, first in the league; next best 41.2 |
| starters below replacement | **0** — the only roster in the league at zero |
| champ / playoff / exp wins | 26.0% / 80.3% / 17.32 |
| with pending moves (Mahomes, Butker, Coker) | 44.1% / 97.6% / 21.0 |
| with the DL flip on top | 45.4% / 97.9% / 21.2 |
| FAAB | 82 of 100, 4th of 8; 2025 league pace was 48% spent by week 4 |

**Structural facts a lesser model must not "fix" without reading the linked findings:**

- Caleb Williams is on IR (`Out`, hamstring, week-to-week). **Week 5 is a QB hole if he
  is not back** — Mahomes byes week 5. Stream a QB in week 4 if Williams is not trending.
- One DL (Tuipulotu, bye 7). **Week 7 is a DL hole.** Claim in week 6. Not a pass rusher
  (F49: the sack cut deflates edges ~20%).
- Three LBs (Bolton 12.6, Roquan 12.0, Warner 11.5) for one slot. Roquan is on the block.
  Four rivals have an LB starting below replacement; Cosmic Badgers by 3.0.
- Shakir (7.24, −3.00 vs replacement) is the only true dead weight. He is the drop.
- Every drafted player on the roster is outperforming his preseason projection. There
  are **no** sell-high candidates. Do not go looking for one.
- Pending IDP scoring change (F49): sack 4.0→2.0, QB hit 1.0→0.5. Not yet live. Every
  edge rusher on every roster deflates when it lands. `sync.py:916` reads scoring live;
  no code change is needed.

---

## Part 1 — P1: correctness defects that affect decisions now

### B1. The IDP epistemic rate makes the Bayesian blend nearly inert for an entire position group

**Class:** model / calibration. **Priority:** P1. **Release:** MAJOR (changes
`player_baselines.json` for every IDP; goldens cannot detect it).

**What.** `EPISTEMIC_ERROR_RATES` (`config.py:229`) sets DL/LB/DB to **0.15** against
QB 0.30, RB 0.63, WR 0.55. `_apply_bayesian_updates` (`simulation.py:533–549`) is
precision-weighted: `prior_var = std_epistemic²`, so a small epistemic rate makes the
prior *very* confident and observed games barely move it.

**Evidence, reproduced from the exact formula at `simulation.py:545–547`:**

```
same two observed games [16.5, 41.5], same prior 11.7:
  WR       rate 0.55  posterior 13.72
  RB       rate 0.63  posterior 14.26
  QB       rate 0.30  posterior 12.36
  LB/DL/DB rate 0.15  posterior 11.87   <- ~30% of the movement a WR gets

consistent games [22.7, 29.0], prior 19.6:
  QB       rate 0.30  posterior 22.73
  LB/DL/DB rate 0.15  posterior 21.50
```

This is why, on 2026-09-22, Devin Lloyd (16.5, 41.5) posted a corrected 11.90 against a
prior of 11.73, Van Ness (13.0, 27.2) posted 5.65 against 5.55, and Budda Baker (9.8,
22.0) posted 8.85 against 8.54 — while Mahomes (22.7, 29.0) moved 19.58 → 22.70. The
model learns about quarterbacks and does not learn about linebackers.

**Why it matters.** `CLAUDE.md` says the IDP constants are "less rigorously sourced" and
F28 "moved DL/LB/DB off 1.5" — i.e. 0.15 is a carried number, not a measured one. An
unverified constant is silently setting the learning rate for three of the eight starting
slots. Whether 0.15 is *right* is unknown; that it is *consequential* is now measured.

**Scope.**
1. Write the characterisation test first: an IDP and a WR with identical priors and
   identical observed games; assert the posteriors differ by the ratio implied by the
   rates. This pins the *mechanism* (it is not a bug) so the later change is reviewable.
2. Measure, do not guess. Use `scripts.run_player_backtest` (it exists for exactly this)
   on real 2025 IDP player-weeks: fit the epistemic rate that maximises held-out
   predictive likelihood for DL, LB, DB separately. `docs/audit/` has the method for how
   the offensive rates were derived; follow it.
3. If the fitted rate is materially above 0.15, change `config.py:231` with the
   derivation in the comment (rule 5), re-sync, regenerate goldens, MAJOR tag.
4. If the fitted rate is *near* 0.15, close as measured-and-cleared and leave it.

**Acceptance.** A `docs/audit/` entry showing the fitted rate per position with n and a
held-out score; `config.py` comment cites it; goldens regenerated iff the value changed.

**Traps.**
- Do NOT raise the rate because "it feels low." That is exactly what rule 5 forbids.
- The precision weighting is *correct*. Two wildly different scores genuinely are weak
  evidence. The question is only whether the prior's stated confidence is calibrated.
- `actual_var` has a floor of `0.5 * prior_var` at `simulation.py:543`. With a tiny
  prior_var that floor is tiny too; do not "fix" the floor as a proxy for this item.
- This interacts with F49: the sack cut will change IDP variance structure. Fit on
  2025 data (pre-change scoring) and note the boundary.

---

### B2. The cheap trade screen's acceptance rule disagrees badly with the paired simulation

**Class:** tools. **Priority:** P1. **Release:** MINOR.

**What.** `find_trade_targets` and every ad-hoc screen this week scored a swap by
*optimal starting lineup + 0.1 × bench* (`decisions.py`, `_package` / `val`). The paired
simulation (`evaluate_trade` / `evaluate_add_drop`) measures actual championship odds.
They disagreed, repeatedly, and always in the same direction:

| swap | cheap screen | paired sim |
|---|---|---|
| Warner + Bolton → Walker + Coker | +1.54 for Cosmic Badgers | **−16.07 playoff%** |
| Hooker for Bishop | +0.82 (mean) | **−1.87 champ%** |
| Lloyd for Bolton | +3.66 (mean) | Lloyd measured *worse* than Bolton |
| Van Ness for Tuipulotu | +1.63 (mean) | Van Ness 3.70 *below* Tuipulotu |

Four recommendations went to the owner on screen numbers and were reversed by the sim.

**Why it matters.** The screen is what `find_trades --evaluate N` uses to *choose which
N* to simulate, and what the weekly report's trade section prints. A screen that
misranks is worse than no screen: it filters the good candidates out before the sim sees
them.

**Scope.**
1. Characterise: a test fixture where the screen says both sides gain and the paired sim
   says one side loses (the Walker shape — the giving side loses its RB1 and the screen's
   0.1× bench weight cannot express what that does to injury cover across a season).
2. Change `find_trade_targets` to (a) never print a "their gain" figure from the screen
   without a paired-sim confirmation, and (b) when `--evaluate` is used, choose
   candidates by *my* screen gain only and let the sim decide the counterparty's side.
3. Add a "screen-vs-sim disagreement" line to the trade-targets output whenever they
   differ in sign, so the disagreement is visible rather than silent.

**Acceptance.** The report's trade section carries paired-sim deltas for anything it
recommends, or says "unsimulated" beside it. Test asserts the disagreement case is
labelled.

**Traps.**
- The screen is not *wrong* for a single-week question. It is wrong as a proxy for a
  season, because bench weight (0.1×) does not capture injury cover or bye cover.
  Do not "fix" the weight; replace the use.
- Paired sims are slow (~1 min per arm on this machine, R1 forbids parallel). Budget
  accordingly; `--evaluate 3` is the practical ceiling.

---

### B3. Lineup tools ignore game locks mid-week and report unreachable lineups

**Class:** tools. **Priority:** P1. **Release:** MINOR.

**What.** `optimize_lineup`, `matchup_lineup` and the weekly report's matchup section
re-solve the whole week as if nothing had kicked off. On Sunday 2026-09-20 at 08:37 PT
the optimizer wanted Tykee Smith at DB and McConkey in a FLEX — but Bishop and Shakir had
played Thursday and were locked. Its "expected total 204.6" was unreachable. The weekly
report the same weekend said P(win) 72.1% while the live tracker said 57.8%; the gap was
Thursday's banked result plus a lineup that could no longer be set.

**Scope.**
1. `scripts.live_matchup.game_clocks()` already returns per-team `(frac, label)`. Expose
   it as a helper `locked_players(week)` returning the set of pids whose game has started.
2. `optimize_lineup` takes `--respect-locks` (default ON when `week == current_week` and
   any game has started): locked starters are pinned, locked bench players are excluded
   from alternatives, and the header says how many slots were pinned.
3. Weekly report: when run mid-week, the matchup section prints the live tracker's
   numbers with a one-line banner, not a fresh-week solve.

**Acceptance.** Test: a fixture with one played starter and one played bench player;
optimizer output pins the former and never proposes the latter. Weekly report mid-week
carries the "N players locked, live view" banner.

**Traps.**
- The pre-kickoff optimizer is correct and must not change. Only the mid-week behaviour
  is wrong. Gate on `game_clocks`, not on the day of the week.
- `game_clocks` hits ESPN. Keep the seam injectable (`fetch=`) so tests stay hermetic.

---

### B4. `Questionable` is invisible to every tool, and no tool says so

**Class:** model / tools. **Priority:** P1. **Release:** MINOR (surfacing) — a
haircut would be MAJOR and is NOT recommended.

**What.** `INITIAL_ABSENCE_STATUSES = ('IR','PUP','Out','Sus','DNR','NA')`.
`Questionable` is in no absence set anywhere; `_initial_absence_clock` returns 0 for a
Questionable player exactly as for a healthy one. Every projection, every VORP, every
win probability treats a flak-jacketed receiver with a broken rib as fully available.
Verified 2026-09-20 (F50/F51 work): the Bishop "Questionable" flag changed nothing in the
sim because it never reached it.

**Why this is scoped as *surface it*, not *model it*.** The Sleeper projection the
baseline derives from already reflects expected usage for a Questionable player, so a
second haircut would double-count. And in-week availability is a decision the owner
hedges by hand (F51). The defect is that the tools do not *tell* the owner which of the
numbers they are looking at carry that unpriced risk.

**Scope.**
1. Every tool that prints a starter or a recommendation prints the injury designation
   beside the name when it is non-null. `optimize_lineup` already carries `p_zero`; add a
   `flag` column.
2. `weekly_report` gets a "Questionable starters" block listing each with his best
   bench fallback by week expectation.
3. `live_matchup` prints the Questionable count for both rosters beside the win
   probability, with the F51 one-liner: "margin is trustworthy only when these are
   comparable."

**Acceptance.** Fixture with one Questionable starter; every affected tool's output
contains the designation. No baseline, no sim path touched.

**Traps.** Do not add `Questionable` to `INITIAL_ABSENCE_STATUSES`. That is a modelling
change with no evidence base and it would move every prediction.

---

### B5. The projection log carries no provenance, so January's mandated partition cannot be reconstructed from the log alone

**Class:** data provenance. **Priority:** P1 (deadline: before the January analysis).
**Release:** PATCH.

**What.** `data/logs/projection_log.jsonl` rows carry `synced_at` and nothing else that
identifies the code that wrote them: no commit, no schema version, no blend flag.
Week 2 has **24 distinct sync stamps**, some from before F52 (ESPN dead) and some after.
F49 requires criterion 1 to partition at the IDP scoring change; F52/F54 add a *second*
boundary that does not coincide. Right now both partitions have to be rebuilt by matching
timestamps against `git log`.

**Scope.**
1. Add `git_commit` (short hash), `schema_version` (an int in `config.py`), and
   `espn_present` (bool: did the blend fire this sync) to every row `sync.py` writes at
   line ~658. The `evaluate_move` records already carry `git_commit`; copy the pattern.
2. Do NOT rewrite historical rows. Add a one-off script `scripts/annotate_projection_log`
   that stamps `schema_version=0` on existing rows and infers `espn_present` from
   `espn_mean is not None`. Run it once, commit the result, never again.
3. Write the two boundary dates into `docs/LUCK_LEDGER.md`'s sibling, or a new
   `docs/EVALUATION_BOUNDARIES.md`, with the commit hashes.

**Acceptance.** Every new row has the three fields; a test asserts it. The boundaries
doc exists and is linked from `SEASON_2026_EVALUATION.md`'s neighbourhood (do NOT edit
that file — it is hashed and CI-guarded).

**Traps.** `SEASON_2026_EVALUATION.md` must not be touched. The projection log is
append-only by design; the annotation script must be idempotent and must not reorder.

---

### B6. Six remaining silent-fallback sites in the data layer (the F52 class)

**Class:** data. **Priority:** P1. **Release:** PATCH.

**What.** `grep` finds six `except Exception:` / bare `except:` blocks in `sync.py` and
`clients/*.py` that resolve to `pass`, `continue` or `return {}`. F52 was exactly this
shape: a successful call returning empty was indistinguishable from data, for a
fortnight. F36 made one of them loud; F52 made another; F55 records the weather one.

**Scope.**
1. Enumerate them (the grep is in the commit that wrote this document's session; rerun
   `grep -n "except Exception:\s*$\|except:\s*$" -A1 fantasy_sim/sync.py fantasy_sim/clients/*.py`).
2. For each: either (a) log a `WARNING` naming the source and the fallback taken, or
   (b) if the fallback is genuinely harmless, add a comment saying *why* it is harmless
   and which finding measured that.
3. Add to the sync manifest a `sources` block: `{name: {ok, rows, fallback}}` for every
   external call. `check_freshness` reads it and reports any source with `rows == 0`.

**Acceptance.** `check_freshness` output lists every source with a row count; a test
patches one fetch to return `{}` and asserts a DEGRADED verdict names it.

**Traps.** Some of these are inside loops over 32 teams; one warning per source per
sync, not per team, or the 129 routine notices become 1,000.

---

## Part 2 — P2: model calibration

### B7. Prediction intervals are ~27% too narrow, and nothing is scheduled to fix it

**Class:** model. **Priority:** P2. **Release:** MAJOR.

**What.** `data/logs/points_backtest.jsonl` (latest entry, commit `f07181c`):
`cover80 = 0.654` against nominal 0.80, `cover50 = 0.375` against 0.50,
`sd_z_opt = 1.2683`. F25 brackets the understatement at r ≈ 1.15–1.34 and tracks it.
Every probability the model quotes — win%, champ%, "1 in 83" — is sharper than the data
supports. The owner was told a 1.2% last-place figure that is more honestly ~3–4%.

**Scope.** Two candidate fixes; the entry must choose one and say why.
- **(a) Global inflation.** Multiply every `std_aleatoric` at engine init by a single
  `INTERVAL_INFLATION` constant derived from the backtest's `sd_z_opt`. Cheap, honest,
  and it is what the backtest already computes. Cite the backtest entry in the comment.
- **(b) Source fix.** Find *which* variance is understated (per-player aleatoric?
  environment draw's 0.10 sd at `decisions.py:759`? missing same-game correlation in
  the score draw?). Harder, better.

Recommend (a) now with (b) as a follow-on, because (a) fixes every quoted probability
this season and (b) needs the F25 week-5/6 data to attribute.

**Acceptance.** Re-run `run_points_backtest` after the change: `cover80` within
0.78–0.82 and `mean_z` unchanged in sign. Goldens regenerated. `sd_z_opt` in the new
entry near 1.0.

**Traps.**
- Inflating variance moves *every* win probability toward 50% and every champ% toward
  12.5%. The owner's current 44% will read lower. That is the honest number; do not
  soften it.
- This is sync-time or init-time — the goldens will not detect a constant change
  (learned from F28). Regenerate them anyway, deliberately, and say so.
- Do this AFTER B1 if B1 changes IDP rates, or the two will be confounded.

---

### B8. The open half of F54: vacated-volume apportionment still reads pre-blend means

**Class:** model. **Priority:** P2. **Release:** MAJOR.

**What.** F54 moved `_calc_replacement_levels()` after `_apply_bayesian_updates()`.
`_build_pass_catcher_hierarchy()` and `_build_nfl_position_groups()` (`simulation.py`
init, the two lines above the replacement call) still run *before* the blend. So when a
starter goes down, the injured volume is apportioned across teammates using their
**preseason** means, not their corrected ones. A backup who has been producing gets the
same share as one who has not.

**Scope.**
1. Characterise: fixture where two backups have identical priors and one has posted big
   observed weeks; assert the vacated share is currently identical (it is), then after
   the fix favours the producer.
2. Move both builders after the blend. That is the entire code change.
3. Re-run `scripts.run_behavior_check` — F24 measured mean-weighting as correct on 8 real
   2025 lead-RB absences; this change must not break that. If the F24 rates move
   materially, stop and report rather than ship.

**Acceptance.** `run_behavior_check` drift within its committed tolerance; goldens
regenerated; F24's measured inheritance concentration still matches.

**Traps.** F24 is a deliberate decision in `CLAUDE.md`. This item changes the *inputs*
to that mechanism, not the mechanism. If a lesser model finds itself editing the
weighting rule, it has gone wrong.

---

### B9. The model-health verdict threshold is an unsourced literal

**Class:** config hygiene. **Priority:** P2 (low effort). **Release:** PATCH.

**What.** `simulation.py:538`:
`'Calibrated & Learning' if mae < 18.0 else 'High Variance / Volatile'`. The `18.0`
cites nothing. Current value with two weeks banked: **29.64 → "High Variance"**. Whether
that is alarming or expected at n=2 is unknowable without knowing where 18.0 came from.

**Scope.** Move it to `config.py` as `TEAM_MAE_HEALTH_THRESHOLD` with either a derivation
(the 2025 backtest's team MAE was 22.36 — is 18.0 meant to be below that?) or the
literal words "UNVERIFIED, carried over". Rule 5.

**Acceptance.** No bare numeric threshold in `_apply_bayesian_updates`; the constant has
a comment.

---

### B10. Injury risk is positional only; there is no player-specific durability signal

**Class:** model. **Priority:** P2. **Release:** MAJOR if adopted; the *study* is PATCH.

**What.** `INJURY_RATES` keys by position. Every WR has the same onset hazard. The model
has no way to know McConkey has carried a rib injury for three weeks, or that a player
has been listed Questionable four times. The owner's strongest trade argument this week
(McConkey → Coker) was information the model structurally lacks.

**Scope — study first, adopt only on evidence.**
1. Feature: count of `Questionable`/`Doubtful` designations in the trailing N weeks, per
   player, from the player cache history (this needs the cache snapshotted weekly — see
   B21; without it the study cannot run on 2026, only on whatever 2025 designations
   `season_2025.json` preserved).
2. Test: does designation-count predict subsequent DNP (0.0 weeks) above the positional
   base rate? Fit on 2025, hold out 2026.
3. Adopt as a multiplier on the positional hazard only if the lift is significant and
   survives holdout. Cite in `config.py`.

**Acceptance.** A `docs/audit/` entry with the fitted lift and n. Adoption is a
separate, later decision.

**Traps.** Do not proxy durability from *scores*. A low score is not an injury. Use
designations only.

---

### B11. The live tracker treats the two weekly legs as independent when they share the same score

**Class:** tools. **Priority:** P2. **Release:** MINOR.

**What.** `live_matchup` prints "expected wins 1.17 of 2 (2-0 ~34%, 0-2 ~17%; legs
treated as independent)". They are not independent: my head-to-head result and my median
result both depend on *my* score. A big day wins both; a bad day loses both. The 2-0 and
0-2 probabilities are both understated.

**Scope.** `median_leg()` already draws the full league jointly (Monte Carlo over all
eight rosters). Extend it to return, per draw, whether I beat my opponent AND whether I
beat the median, then report the joint 2-0 / 1-1 / 0-2 from the same draws. One function,
no new model.

**Acceptance.** Test: a roster with certain outcomes (nothing left to play) reports
exactly one of P(2-0)/P(0-2) = 1.0. A roster where H2H and median are decided by the same
uncertain player reports P(2-0) + P(0-2) > the independent product.

---

## Part 3 — P3: tools and workflow

### B12. Promote the four ad-hoc audit scripts that earned their keep this week

**Class:** tools. **Priority:** P3. **Release:** MINOR each.

Each of these was written in the session scratchpad, run repeatedly, and produced the
decisions. They should be real tools with tests, listed in the README (the docs guard
`test_every_script_is_documented_in_the_readme` will enforce that).

| scratchpad script | becomes | what it does |
|---|---|---|
| `sweep3.py` | `scripts/market_sweep` | every starting slot vs best free agent, **engine values only**, ranked upgrades, dead-weight list |
| `bait.py` | `scripts/trade_leverage` | sell-high (draft pick + preseason vs corrected) and leverage (every rival's below-replacement slot vs my surplus) |
| `howbad.py` | `live_matchup --tail` | per-player and team z-score of the day so far, clock-adjusted; "1 in N" |
| `health.py` | `scripts/data_health` | every source PASS/DEGRADED/FAIL with row counts (pairs with B6) |

**Scope per tool.** Port the logic into `fantasy_sim/` with the computation pure
(dicts in, dicts out) and the fetching in `scripts/`. Tests against hand-computed
fixtures. README one-liner. Real-name overlay via `real_name_overlay()`, never printed on
a runner.

**Traps — read these, they are the whole reason the week went the way it did.**
- **Never hand-blend.** `engine.baselines[name]['mean']` is the corrected number after
  F54. Every count-weighted `(4*prior + obs)/6` the author computed this week was wrong
  for volatile players. The tools must read the engine, not recompute it.
- **Never key by name.** `DeVonta Smith`, `Justin Jefferson`, `Lamar Jackson` each
  collide with a defender in the cache. Key by `entry['player_id']`. The first version
  of the sweep silently scored the wrong DeVonta Smith.
- `market_sweep` must compare against the *slot-losing* starter (the Nth-best at a
  position with N slots), and the *drop* it names must be the worst player at the
  position, which are different people.

---

### B13. Bid sizing ignores competition and the marginal value over the fallback

**Class:** tools. **Priority:** P3. **Release:** MINOR.

**What.** `evaluate_move --bid` prints a market comparable (median bid/VORP = 7.69 from
the decision log). That heuristic priced Roquan at ~$22; the owner bid $15; nobody else
bid. The same heuristic would have priced Mahomes at ~$35; the *right* bid was $15–18,
because the fallback (Shough) was worth +7.63 of Mahomes' +12.60 and only three rivals
carried one QB, all healthy.

**Scope.** A `suggest_bid_v2` that takes: paired-sim value of the claim, paired-sim value
of the best fallback, the number of rivals whose starting slot at that position is below
replacement, and those rivals' FAAB and `faab_agg`. Output a bid range with the reasoning
printed (marginal over fallback × a competition factor). Keep the old heuristic printed
beside it, labelled, until a season of claims lets B14's ledger compare them.

**Acceptance.** On the logged 2026 claims to date, v2's suggested bid is closer to the
clearing price than the old heuristic on more than half of them.

**Traps.** The decision log's clearing prices are censored (you only see the winning bid).
Do not treat "nobody else bid" as "the true price was $1"; treat it as "≤ my bid".

---

### B14. A claim-outcome ledger, so bid heuristics can be scored instead of argued about

**Class:** process. **Priority:** P3. **Release:** MINOR.

**What.** Every waiver claim this week was priced by intuition after the Roquan overpay.
There is no record of *suggested bid vs actual bid vs winning bid vs outcome*. Without it
B13 cannot be evaluated and the next overpay will be argued the same way.

**Scope.** `evaluate_move --log-tx` already appends evaluations to the decision log. Add
`suggested_bid`, `bid_placed`, `won` (bool), `winning_bid_if_visible`. A
`scripts/bid_review` prints the running calibration.

---

### B15. `find_trade_targets` is need-driven and misses best-available; merge with the exhaustive scan

**Class:** tools. **Priority:** P3. **Release:** MINOR.

**What.** The built-in finder returned "no buy-side candidates" three times this week
while the exhaustive 1-for-1/2-for-2 scan (`all_swaps.py`, `trades2.py`) found the
Coker deal, the DL flip, and the Roquan leverage. The finder only looks at *their bench
player who starts at my weakest slot*; it never sees "their starter I could displace with
a piece they need more."

**Scope.** Add a `--exhaustive` mode that enumerates 1-for-1 and 2-for-2 across all seven
rosters on engine values, filters where both sides gain on the screen, then hands the top
N to the paired sim (B2's rule). Cap at 2-for-2; 3-for-3 is combinatorially useless.

**Traps.** The first exhaustive scan silently skipped Cosmic Badgers because a hardcoded
19-man roster cap rejected their 20-man roster. Compare against `len(their_roster)`,
never a literal.

---

### B16. Document the waiver-claim mechanics that decide whether a claim can even land

**Class:** docs. **Priority:** P3. **Release:** PATCH.

**What.** Three mechanics cost the owner real time this week and are written nowhere:
- **On-waivers vs free-agent**: a player dropped within `waiver_clear_days` (2) needs a
  FAAB bid resolved at the 9 AM PT daily run; anyone else is an instant $0 add. The UI
  tell is a bid box vs an "Add" button.
- **Only-one-lands**: with a full roster, give every alternative claim the *same* drop;
  the first success removes the drop and the rest fail on roster space. With an open IR
  slot, no drop is needed and the open slot does the same job.
- **IR eligibility**: `reserve_allow_out=1`, `reserve_allow_doubtful=1`, two slots.

**Scope.** A short `docs/WAIVER_MECHANICS.md` and a pointer from the README.

---

### B17. A `resolve_pid()` helper that refuses ambiguous names, for every ad-hoc script

**Class:** tools. **Priority:** P3. **Release:** PATCH.

**What.** `sync.resolve_player_keys` handles collisions correctly for the baselines.
Nothing protects a script that builds `{name: pid}` from the cache with `setdefault` —
which is what every scratchpad script this week did, and one of them scored the CB
DeVonta Smith's 0.0 as the WR's. `fantasy_sim.decisions.resolve_player` exists; check
whether it raises on ambiguity. If it does not, make it.

**Acceptance.** `resolve_player("DeVonta Smith")` raises or returns the rostered pid with
a warning; a test covers the three known collisions.

---

## Part 4 — P4: data provenance and infrastructure

### B18. Weather is fetched every sync and read by nothing — see F55

Already filed with the full offseason measurement plan. Listed here only so this backlog
is complete. Do not start it before the season ends; do fix the three data faults (amount
not probability, game-time not daily max, loud on failure) *before* the study.

### B19. Stat corrections are invisible: no first-recorded-score snapshot

**Class:** data. **Priority:** P4. **Release:** MINOR.

**What.** `weekly_actuals.json` is regenerated on every sync, so a Tuesday stat
correction overwrites its own evidence. The owner asked how often corrections flip a
result; the repo cannot answer. It also means the January evaluation's *realized* side
can shift underneath the quoted side.

**Scope.** On the first sync after a week completes, write
`data/logs/first_recorded_scores.jsonl` with `{week, pid, points, recorded_at}` for
every starter, once, never overwritten. A `scripts/stat_corrections` diff against the
current values.

**Acceptance.** After one full week, the file has one row per starter and the diff tool
runs. Corrections, if any, are listed with magnitude.

### B20. The league renewal chain is broken at 2025; known league IDs belong in config

**Class:** infra. **Priority:** P4. **Release:** PATCH.

**What.** The 2025 league's `previous_league_id` is `None`, so any tool that walks the
chain (`luck_ledger --all`, `season_retrospective`) silently stops one season short.
2024 is reachable only by id. Right now that ID lives in a scratchpad script and a
`--league-id` flag.

**Scope.** `config.py`: `KNOWN_LEAGUE_IDS = {"2024": "...", "2025": "...", "2026": ...}`
read from env the way the others are (F37 made league IDs env-only; follow it —
`SLEEPER_LEAGUE_ID_2024`). Chain-walkers consult it as a fallback and *warn* when the
chain and the map disagree.

### B21. Weekly snapshot of the player cache, for the injury-designation history B10 needs

**Class:** data. **Priority:** P4. **Release:** MINOR.

**What.** `sleeper_players_cache.json` holds today's `injury_status` only. B10's study
needs "how many times was this player Questionable in the trailing N weeks," which does
not exist for 2026 and is only partially recoverable for 2025.

**Scope.** On each canonical sync, append `{week, pid, injury_status, injury_body_part}`
for every *rostered* player to `data/logs/designations.jsonl`. Tiny file, append-only.

### B22. A tool that prices a pending scoring change before it lands

**Class:** tools. **Priority:** P4. **Release:** MINOR.

**What.** F49's repricing (every IDP under sack 2.0 / QB hit 0.5) was a one-off
scratchpad script. It found Tuipulotu was the league's biggest loser (−19.5%) and that
the owner should refuse any trade bringing him a pass rusher. That analysis is not
repeatable by anyone else.

**Scope.** `scripts/reprice --scoring '{"idp_sack": 2.0, "idp_qb_hit": 0.5}'` recomputes
every rostered player's projection under the alternative settings from Sleeper's
projected stat lines (the stats endpoint carries them), and prints per-player and
per-team deltas. `sync.py:916` already reads scoring live, so once the change is real
this tool becomes a no-op; its value is in the window *before*.

---

## Part 5 — P5: reporting

### B23. The weekly report's matchup section is a pre-kickoff view and says so nowhere

Covered by B3. Listed for completeness: the fix is the mid-week banner, not a new
section.

### B24. A decision scorecard in the post-week report

**Class:** reporting. **Priority:** P5. **Release:** MINOR.

**What.** Twice this week the owner asked "did we make bad calls?" and the answer was
built ad hoc: every start/sit with a real alternative, the pre-kickoff margin, P(alt
wins), and the result — with **unresolved** calls (alternative not yet played) marked as
such rather than scored. The week-1 version showed 5 of 8 "decisions" were the *same*
Watson decision counted five times. That deduplication is the useful part.

**Scope.** After the week closes, `weekly_report` (or `season_retrospective --week N`)
prints: decision, started, alternative, margin, P(alt), outcome, *and groups rows by
alternative* so one bench player appearing in five slots reads as one decision.

**Traps.** Score only against the *pre-kickoff* lineup record
(`data/decisions/week_NN/lineup_*.json`), never against a re-solved lineup. Lookahead
here would be the exact leakage `CLAUDE.md`'s statistical conventions forbid.

### B25. The report should list Questionable starters with their fallbacks

Covered by B4. The report is the highest-value place for it.

---

## Part 6 — tests and process

### B26. Sweep for tests that mock the exact boundary they exist to verify

**Class:** tests. **Priority:** P3. **Release:** PATCH (findings).

**What.** F50 and F52 had the same shape: correct, passing tests that patched the
function whose *input* was wrong, then asserted arithmetic on the input the test itself
supplied. `test_live_matchup` handed `team_states` a baselines dict and re-used its
numbers; `test_espn_subscores` patched `fetch_espn_projection_data` wholesale. Neither
could catch a bad request, because the request never ran.

**Scope.** `grep -n "patch(" tests/*.py` and, for every patch of a function in
`sync.py`, `clients/`, or `live_matchup.py` that *fetches or extracts*, ask: is there a
test anywhere that exercises the real function's contract (what it asks for, what it
returns when empty)? List the ones without. File as one finding with the list; fix the
top three.

**Traps.** The goal is not to remove mocks — hermetic tests are a design requirement
(`CLAUDE.md` environment section). The goal is one *additional* test per boundary that
pins the request, the way F52's `test_free_agents_is_asked_for_the_week_being_synced`
does.

### B27. Known and unchanged: R1 hardware; golden coverage is execution not assertion (F26)

Listed so nobody re-derives them. R1: one engine process at a time, a crashed run is
void, Phase 8 stays blocked. F26: the monoliths are pinned byte-exactly but not
asserted; decomposition waits on R1.

---

## Ordering, if one model does all of it

1. **B5, B6, B9, B16, B17, B20** — provenance and hygiene, all PATCH, no baselines
   touched, a day's work total, and they de-risk everything after.
2. **B4, B3, B11** — surfacing and lock-awareness. MINOR, no model change, high
   decision value this season.
3. **B12** — promote the four tools. Do this before B13/B15, which build on them.
4. **B2, B15, B13, B14** — the trade/bid tooling, in that order.
5. **B1 study → B7 → B8** — the three MAJORs, strictly in that order so they are not
   confounded, each with its own goldens regeneration and tag. B1's *study* can run
   any time; its *adoption* waits for the number.
6. **B19, B21, B22, B24** — data capture and reporting, any order.
7. **B10, B18** — offseason studies. Do not start before the season ends.

---

## What this document does not claim

It does not claim the model is badly calibrated in direction — `mean_z` is +0.10 and
`bias` −2.12, both small. It claims the *intervals* are narrow (B7) and that one
position group's *learning rate* is set by an unverified number (B1). It does not claim
any of the week's waiver calls were wrong on the corrected data — Mahomes, Butker, the
Coker trade and the DL flip all survived paired simulation. It claims the *process* that
produced the four reversed calls (Lloyd, Van Ness, Baker, Hooker) was hand arithmetic
that the tools should never have required (B12, B2).

And it does not claim the owner is unlucky. F53 is pre-registered precisely so that
question gets answered by the data and not by whoever is writing the document.
