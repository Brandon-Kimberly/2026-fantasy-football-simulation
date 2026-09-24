# Scoped backlog 2 — post-B1 audit of 2026-09-23

**Written at the close of the session that worked `SCOPED_BACKLOG.md` to completion, before
any of it is acted on.** Every item below was found by *using* the tools on real decisions
that evening — six trade evaluations, a waiver run, a league re-evaluation, a matchup brief
— rather than by reading code. Each carries the evidence that surfaced it, exactly what to
change, what not to touch, how to know it is done, and the trap a careful-but-uninformed
implementer would fall into.

Nothing in this document has been changed. It is a backlog, not a changelog.

**How to use it.** Same as the first backlog. Follow `CLAUDE.md` rules 1, 3, 6 and 8
without exception — failing test first, characterisation and fix as separate commits, full
suite before and after with counts, and any finding closed in `AUDIT_SUMMARY.md` in the same
commit as `AUDIT_PLAN.md`. Anything marked **MAJOR** needs the release-policy dance. Two
standing rules from the owner override everything here:

- **No real team name or username in any file, ever.** Chat output only. The overlay is
  fetched live and never written. `H1`'s scanner exists to enforce this.
- **`ODDS_API_KEY` in the shell may be stale.** Read it from the User scope before any sync
  (`[Environment]::GetEnvironmentVariable('ODDS_API_KEY','User')` in PowerShell) and verify
  it returns 200 first. A sync with a dead key writes the 21.5 fallback over good data, and
  `data/current/` is not in git — there is nothing to restore. `C3` fixes the overwrite.

**Priority key.** P1 = affects a decision this season, correctness. P2 = model calibration.
P3 = tooling. P4 = hygiene and process. P5 = presentation.

---

## Part 0 — State of the team (status, not backlog)

Recorded so a later reader can judge this backlog against where the roster actually was.

| | week 3, post-waivers, one trade pending |
|---|---|
| record | 2–2 |
| champ / playoff / exp wins | **38.2% / 96.4% / 20.45** (post-pending-trade), 37.6% pre |
| lineup VORP | **57.1**, first; next best 42.1 |
| starters below replacement | **0** — the only roster in the league at zero |
| VORP outside the top two players | **35.0** — next best 27.2. The edge is depth, not stars |
| FAAB | 51 of 100; league average 61 |
| this week | ~79% to win, margin +43, both rosters' only Questionable is theirs |

**Structural facts a lesser model must not "fix" without reading the linked findings:**

- **Week 7 DL is the only bye hole on the remaining schedule.** Every other week
  self-covers, including week 14. Claim a DL in week 6; do not trade a starter for one.
- **The trade market is closed.** Eight offers in one evening, eight declines, every one
  positive-EV for this roster and several positive for the counterparty. The league rates
  this roster low because its value is distributed (35 VORP across eleven men) and its
  stars are not household names. Do not build tooling to "find a better offer" — there is
  no better offer, and the depth is the asset.
- **F49's IDP scoring change is LIVE** (boundary 1, 2026-09-23). Edge rushers lost ~20%;
  tackle players ~2%. Any IDP number from before that sync is on a different scale.
- **The IDP epistemic rate is measured, not carried** (B1, `docs/audit/B1_IDP_EPISTEMIC_RATE.md`).
  0.15 is at or above optimal. Do not raise it because the blend looks inert.
- **The RB/WR epistemic rates are NOT scheduled**, by owner decision. B1's held-out scan
  said RB wants ~1.00 against 0.63 and WR ~0.20 against 0.55; Phase 7 already built, gated
  and reverted a joint change to these. The owner declined to reopen it. Record, do not act.
- **Three QBs are rostered** (one on IR). When the IR'd one returns, the roster is at 20
  active against 19 and someone must be cut. The bench is thin: every bench piece except
  one LB is load-bearing for a bye week (see `T6`).

---

## Part 1 — P1: correctness defects that affect decisions now

### C1. `trade_leverage` named a benched player as a rival's below-replacement starter

**Class:** tools / correctness. **Priority:** P1. **Release:** PATCH (fix) + finding.

**What.** On 2026-09-23 `scripts.trade_leverage` reported one rival as *"LB T.J. Watt
7.53, 3.33 below rep"* — a real buyer for this roster's LB surplus. The paired simulation
then showed every LB-for-DB offer to that rival costing THEM 0.4–0.7 expected wins.
`engine.baselines` had that rival starting **Nakobe Dean at 14.49 (VORP +3.63)**; Watt is
on their bench at 7.5. Two trades were constructed and offered on the strength of the
wrong target before the simulation caught it.

**Evidence.** `fantasy_sim/leverage.py` uses `market.starters_by_position(engine, rival,
week)` — the optimal assignment — so this is not a raw-name lookup. The likeliest cause:
`starters_by_position` solves on **week** expectation while `replacement_levels` is a
**season** mean, so "their_mean" and "below rep" are on different scales, and a player
whose week-3 environment was poor read as a below-replacement starter. Reproduce first;
do not assume.

**Scope.**
1. Characterise: a fixture roster with a 14.5 LB and a 7.5 LB; assert `trade_leverage`
   names the 14.5 man as the starter and reports no LB hole. Confirm it fails.
2. Diagnose the actual cause (week-vs-season is the hypothesis, not the finding).
3. Fix so that `their_starter` and `their_mean` are on the same basis as the replacement
   level they are compared against, and the printed line says which basis.
4. File as F67 with the real root cause; close in both audit docs.

**Acceptance.** The fixture test passes. Re-running `trade_leverage` on live data lists no
rival whose named "starter" is out-projected by a bench teammate at the same position.

**Traps.** `starters_by_position` is used by `market_sweep` too (B12). Fixing it in one
place and not the other recreates F52's shape. Check both callers.

### C2. `matchup_lineup` scores an opponent with an empty slot as a zero

**Class:** tools / correctness. **Priority:** P1. **Release:** PATCH (fix) + finding.

**What.** On 2026-09-23 the matchup tool printed the opponent with **12 starters** —
their DL slot was empty — and reported 81.9% / +48.9. The season simulation injects
`STREAMER_DL_0` at `BASE_STREAMER_MEANS['DL']` (7.5) for exactly this case; the matchup
tool does not. Adding the best free-agent DL to their roster by hand gave **79.2% / +43.1**.
The tool overstated the edge by ~2.7 points of P(win) and ~6 points of margin because a
real opponent never takes a zero.

**Scope.** In `decisions.matchup_lineups`, when the opponent's optimal assignment leaves a
required slot unfilled, fill it with the streamer the engine would use, and print a line
saying so: *"opponent had no DL; modelled at the 7.5 streamer, not zero."*
`league_week_outlook` should be checked for the same gap — it drives the League table.

**Acceptance.** A fixture opponent with no DL produces a 13-player assumed lineup with the
streamer named, and P(beat) is lower than with the slot empty.

**Traps.** Do not use the best free agent — that is a roster decision the opponent has not
made. The streamer constant is the engine's own assumption; use it so the matchup tool and
the season simulation agree. (But see `C5` on whether that constant is right.)

### C3. Sync overwrites a good Vegas file with the fallback

**Class:** data / correctness. **Priority:** P1. **Release:** PATCH.

**What.** A sync run with a dead `ODDS_API_KEY` wrote `vegas_totals.json` with
`source: fallback_api_error` — every team at 21.5, `opponent: FA` — over a file that had
real week-3 lines from a sync hours earlier. Every week-level projection degraded until the
next good sync. `data/current/` is not tracked, so there was nothing to restore. The sync
warned loudly (F57) and still destroyed the good data.

**Scope.** In `fetch_vegas_implied_totals`, when the API call fails and a `vegas_totals.json`
already exists for the **same week** with `source: odds_api`, keep it and stamp
`_meta.stale_since` rather than overwriting. Write the fallback only when there is no
same-week real file. `check_freshness` reports the stale stamp.

**Acceptance.** Test: a same-week real file survives a failed fetch and carries
`stale_since`; a different-week real file is replaced by the stamped fallback (last week's
lines are not this week's).

**Traps.** F3-era reasoning: a stale-but-real line is better than a flat 21.5, but only for
the same week. Do not let week 2's real lines masquerade as week 3.

### C4. The luck ledger reports results that disagree with the standings

**Class:** measurement / correctness. **Priority:** P1. **Release:** PATCH + finding.

**What.** On 2026-09-23 with two completed weeks the ledger reported
`actual_wins=2 expected_wins=1.43` and `close games wins=2 losses=0`. The team was 2–2
across four legs and had **lost** week 2's H2H 149.02–150.41 — a close loss. A 2–0 close-game
record is not possible on that season. Schedule luck counts H2H wins only (documented), but
even so week 2 should be a loss.

**Why it matters more than most bugs.** These are **pre-registered** measurements
(`docs/LUCK_LEDGER.md`, F53): their whole value is that the definitions were fixed before the
data. A pre-registered measurement that misreads the data is worse than none, because it
carries the credibility of pre-registration.

**Scope.**
1. Characterise on a fixture season with a known close loss; assert `close games` counts it.
2. Diagnose. Candidates: reading `points` from the wrong side of the matchup pair; treating
   the median leg as an H2H result; off-by-one on completed weeks.
3. Fix. Add a cross-check: `actual_wins` must equal the H2H wins in `league_standings.json`
   for the same weeks, and the ledger refuses to print if they differ.
4. File as F68.

**Acceptance.** The live ledger's `actual_wins` and close-game record match Sleeper's
standings for the completed weeks.

**Traps.** Do not change a definition to make the number come out. The definitions are
fixed in writing; only the reading of the data may be wrong.

### C5. `BASE_STREAMER_MEANS['QB']` is below the actual free-agent pool

**Class:** model / calibration. **Priority:** P1. **Release:** MAJOR if changed (it is an
engine-init constant read by every hole evaluation; goldens will move).

**What.** The engine fills an empty QB slot with a 14.0-point streamer. On 2026-09-23 the
free-agent QB pool had Jordan Love 17.3, Kyler Murray 17.9, Bo Nix 17.4, Stafford 16.6 —
every one above 14.0. So every evaluation of a QB hole (`evaluate_move` on a QB add, the
season sim for a team with no QB) prices the alternative at 14.0 when a 17+ QB is a $0
claim away. That inflates the value of filling the hole. The Mahomes evaluation that
evening compared against that 14.0 floor.

DL is fine (7.5 vs a pool at 6.8–7.7). The other positions were not checked.

**Scope.** Study first: for each position, compare `BASE_STREAMER_MEANS[pos]` to the mean of
the top-3 free agents at that position on the last four syncs (`projection_log.jsonl` has
the history). Report the gap. If QB is materially above 14.0 across all four, either
(a) raise the constant with the derivation in the comment, or (b) — better — have the
engine derive the streamer level from the actual pool at init, capped at the replacement
level so it can never exceed a rostered starter. (b) is the real fix and is MAJOR.

**Acceptance.** A `docs/audit/` entry with the per-position gap and n. If adopted, goldens
regenerated deliberately with the week01/06/15 deltas explained.

**Traps.** The streamer is deliberately BELOW replacement (Phase 4: won streamers capped at
replacement level so they cannot out-project rostered players). Deriving from the pool must
keep that cap. Do not let a hole become worth more than a starter.

---

## Part 2 — P2: model calibration

### M1. The 50% interval is still narrow — B7's shape problem, deferred to after week 6

**Class:** model. **Priority:** P2. **Release:** MAJOR if adopted.

**What.** B7 scaled `std_aleatoric` by 1.41 and repaired the 80% band (0.67 → 0.80) but the
50% band reached only **0.45** against a nominal 0.50. One scale factor fixes one quantile;
the predictive distribution is not Gaussian. B7 named option (b) — find *which* variance is
understated: per-player aleatoric, the environment draw's 0.10 sd, or the same-game
correlation the score draw omits (F16) — and deferred it to after week 6 so F25's
quoted-vs-realised data exists.

**Scope.** Not before week 6. Then: re-run `run_points_backtest`, and for each candidate
(env sd, copula, per-player) fit the one change that brings `cover50c` to 0.50 without
moving `cover80c` off 0.80. If none does, the answer is a heavier-tailed draw, which is a
different item. Adoption is MAJOR and needs its own tag.

**Acceptance.** Backtest entry with both coverages inside ±0.02 of nominal, or a written
finding that no single variance term does it.

**Traps.** Do not touch `INTERVAL_INFLATION` to chase cover50. It was fitted for the 80%
band and that fit is correct. Adding a second scalar is not "finding which variance."

### M2. `run_behavior_check` runs only `week01` and cannot see blend-scoped changes

**Class:** tests / calibration. **Priority:** P2. **Release:** PATCH.

**What.** B8 moved the `week06` and `week15` goldens and the behaviour check reported "no
drift" — because its scenario is `week01`, which has zero completed weeks and therefore
no blend. Recorded in F54 and F62. Any future change scoped to the posterior is invisible
to the drift check.

**Scope.** Add `--scenario week06` to the standard invocation in `CLAUDE.md` and to the
release checklist; commit a `baseline_week06.json`. Both scenarios run before a MAJOR.

**Acceptance.** A change to `_apply_bayesian_updates` that moves the week06 golden also
moves the week06 behaviour baseline.

**Traps.** The drift tolerance is 2% and one SE on `faab_spent` is ±2.1% (F62). A second
scenario doubles the false-drift rate. The report already names the alternative; keep it.

---

## Part 3 — P3: tools

### T1. `evaluate_trade` cannot model FAAB on either side

**Class:** tools. **Priority:** P3. **Release:** MINOR.

**What.** This league trades FAAB. On 2026-09-23 two FAAB-for-player offers were made and
evaluated by proxy — a throwaway bench player standing in for "give nothing." The
`evaluate_trade` API is player-for-player.

**Scope.** `--faab-a N` / `--faab-b N` (and the corresponding kwargs). FAAB moves
`league_standings.remaining_faab` for both sides in the `with` engine; the paired
simulation already reads that. A one-sided player trade (one side gives only FAAB) must be
accepted and the roster-limit check applied to the receiving side only.

**Acceptance.** Test: "team A gives 5 FAAB, team B gives player X" evaluates, A's active
count rises by one and refuses without a drop at the limit, and A's `remaining_faab` in
the `with` arm is 5 lower.

**Traps.** FAAB's value is nonlinear and F61 says this league's prices do not track model
value. The sim prices FAAB only through the manager-profile bidding model, which is coarse.
Print that caveat on every FAAB evaluation; do not let a $5 look precisely priced.

### T2. The bid ledger cannot record the losing bids, which this league can see

**Class:** tools / calibration. **Priority:** P3. **Release:** MINOR.

**What.** After a waiver run the owner can see every bid on a claim (29 / 21 / 20 on one
QB). The ledger records only the winning bid as `winning_bid_if_visible`, which for a
claim I won is an **upper bound** on the price and is scored through a censoring rule. The
losing bids turn that into an **exact clearing price** — the second-highest bid — which is
far better calibration data for the F61 question. F65's entry named this as not built.

**Scope.** `bid_review --record-rivals "Player" 21,20` appends `rival_bids` to the live
ledger row (F64's supersession rules apply). `calibration` uses `max(rival_bids) + 1` as
the clearing price where present and falls back to the censored rule otherwise; the
verdict line says how many claims had an exact price.

**Acceptance.** A row with rival bids scores v1/v2 against 22, not 29, and the summary
reports `n_exact`.

**Traps.** Do not overwrite `winning_bid_if_visible`; it is what was paid. Sleeper's
first-price auction means the winner pays their own bid, not the second price, so both
numbers are needed: one for "what did it cost me", one for "what would have won."

### T3. `find_trades` proposes players already committed to a pending trade

**Class:** tools. **Priority:** P3. **Release:** MINOR.

**What.** With a McConkey-for-Coker trade pending, `find_trades --require-mutual` ranked
"give Nick Bolton, get Jalen Coker" first. Sleeper's `/league/{id}/transactions/{week}`
returns pending trades with `status: pending`; `ingest_transactions` deliberately keeps
only `complete` (B14's premise). The finder never sees them.

**Scope.** `sync` writes pending trades to `data/current/pending_trades.json` (players by
pid, both sides). `find_trades` and `trade_leverage` read it and exclude any involved
player, printing *"N players excluded: committed to a pending trade."*

**Acceptance.** A fixture pending trade removes its players from both tools' candidate
lists.

**Traps.** Pending ≠ certain. A vetoed trade returns the players. The exclusion is
advisory and must say so; do not apply pending trades to the engine as if complete.

### T4. Raw-position filtering is still easy to write, and was written twice that evening

**Class:** tools / tests. **Priority:** P3. **Release:** PATCH.

**What.** Two ad-hoc queries filtered `pos == 'DL'` and silently excluded every `DE` and
`DT` — missing Brian Burns and Danielle Hunter, both DL-eligible, one of them the best
free agent at the position. Phase 3 finding 3 fixed this inside the engine
(`normalize_position`); nothing stops a tool or a one-off query from doing it again.

**Scope.**
1. `grep -n "pos.*==\s*['\"]\(DL\|LB\|DB\|RB\|WR\|TE\|QB\|K\)['\"]" fantasy_sim scripts`
   and route every hit through `normalize_position`. List the hits in the commit.
2. A test that imports every module under `fantasy_sim/` and `scripts/`, ASTs it, and
   fails on a `Compare` node whose left side is a `pos` attribute/subscript and whose right
   side is a bare position literal. Whitelist `normalize_position` itself.

**Acceptance.** The sweep is empty and the test is green; introducing `pos == 'DL'` in
any tool turns it red.

**Traps.** `fantasy_positions` in the raw cache is a LIST. Comparing a list to a string is
always False and never raises — the quietest version of this bug.

### T5. A "what to watch" matchup brief — built by hand twice that evening

**Class:** tools / reporting. **Priority:** P3. **Release:** MINOR.

**What.** The owner asked what to watch this week. The answer was assembled by hand from
four sources: both lineups grouped by NFL game, the Vegas total and spread per game,
correlated stacks (three starters in KC/MIA; the opponent with four in BAL/DAL), the only
Questionable starter on either side, and the windiest game. Every one of those is
mechanical. It was built twice because the pending trade and the opponent's empty DL slot
changed the answer (see `C2`).

**Scope.** `scripts/matchup_watch` (pure logic in `fantasy_sim/matchup_watch.py`): for the
week's opponent, print (1) both lineups grouped by shared NFL game with each game's total,
spread, wind and precipitation (B18's fields, now clean); (2) any game holding ≥3 starters
from one side, flagged as a stack, with the sum; (3) every Questionable/Doubtful starter
on either side (B4's rule: check BOTH rosters); (4) games where the two rosters share a
side (correlated) or oppose (hedged); (5) a one-line "their realistic losing script" —
the single game whose sum is largest for them. Rendered into the weekly report's matchup
section (B23's note already anchors it).

**Acceptance.** Fixture with a three-man stack, one Questionable opponent starter, and one
shared game produces all five blocks. Real-name overlay only via `real_name_overlay()`.

**Traps.** The report is pseudonymous on the runner. Weather is fetched and not modelled
(F55 open) — print it as context and say it is not in any projection above.

### T6. Bye-week exposure map, and the roster-crunch forecast

**Class:** tools / reporting. **Priority:** P3. **Release:** MINOR.

**What.** "Which weeks am I short at a position" was answered by hand: one hole all
season (week 7 DL). So was "when the IR'd QB returns I am at 20 active and must cut
someone — who?" Both are pure roster arithmetic and both drove real decisions (declining
every RB-for-WR trade because two RBs leave three bye holes and the RB wire is barren).

**Scope.** `scripts/roster_calendar`: per week, who is on bye, whether each required slot
is still fillable, and which bench piece covers which starter's bye. Second section:
for each IR player, "when he returns you are at N active; your only bench piece not
covering a bye is X." Rendered as a section in the weekly report.

**Acceptance.** Fixture roster reproduces the week-7 DL hole and names the one droppable
bench piece.

**Traps.** `INITIAL_ABSENCE_STATUSES` decides who is "out"; Questionable is deliberately
not in it (F51). Do not treat a Questionable starter as a hole.

---

## Part 4 — P4: hygiene and process

### H1. The real-name scanner exists only in a session transcript

**Class:** process. **Priority:** P4 but the rule is absolute. **Release:** PATCH.

**What.** A literal-match scan on 2026-09-22 missed four real-identity strings; a
tokenising scan on 2026-09-23 found them (a username built from a team name, a variable
named after a team, one word of a team name merged into a fictional one, a manager `style`
string equal to the first word of that team's real name). The working scan was an ad-hoc
Python block and is not in the repo.

**Scope.** `scripts/scan_real_names.py`, **local-only and env-gated** (`SHOW_REAL_TEAM_NAMES`
must be set; it refuses on `GITHUB_ACTIONS`): fetch display names and team names live,
tokenise on non-alphanumerics, keep tokens ≥5 chars plus singular stems, suppress the
ordinary-English collisions, scan every tracked text file, and print hits with the token.
Exit 1 on any hit. Never writes. A note in `CLAUDE.md` says to run it before any push that
touched tests or docs.

**Superseded on the suppression mechanism (F72, 2026-09-24).** This entry originally
enumerated four example stop words. Those words were chosen *because* they appear inside
real team names, so a committed list of them is a partial leak of the thing the tool
removes — the enumeration is deleted here for that reason. The built tool uses a gitignored
local allowlist plus word-boundary matching instead; see F72.

**Acceptance.** Planting `walrus_fan_99`-style derivatives of a real name in a scratch file
under the repo is caught; the repo as committed is clean.

**Traps.** The scanner itself must contain no real name and must not log the fetched list.
NFL player names are domain data and are not identities — a projections fixture that
happens to contain a cornerback whose first name matches a token is a false positive,
not a leak, and the scanner should print the token so a human can tell the two apart. The generic list exists so ordinary English does not drown the
signal; keep it short and document each entry.

### H2. F66's two remaining boundary gaps

**Class:** tests. **Priority:** P4. **Release:** PATCH.

**What.** `generate_league_schedule` has no request-pin; `ingest_drafts` has neither a
request-pin nor an empty-return test. Recorded in F66 as not done.

**Scope.** One test each at the `requests.get` level, following
`tests/test_boundary_contracts.py`. Verify load-bearing by mutation and record the
mutation in the docstring, as F66 did.

**Acceptance.** F66's entry updated to "all five closed."

### H3. Stray run logs get swept into commits

**Class:** hygiene. **Priority:** P4. **Release:** PATCH.

**What.** `w.log` was committed by a `git add -A` and removed one commit later.

**Scope.** `*.log` at the repo root in `.gitignore`; `data/current/syndicate_warnings.log`
is the one legitimate log and is under `data/`, which has its own rules. Check it is not
caught.

### H4. The docs guard pins the version but not the citation date

**Class:** docs / tests. **Priority:** P4. **Release:** PATCH.

**What.** `CITATION.cff` said `date-released: 2026-09-05` at v7.0.0 — two tags stale. The
guard pins `pyproject.toml` to the latest tag and ignores the date.

**Scope.** Extend `tests/test_docs` so `CITATION.cff`'s `version` equals the latest tag and
`date-released` is not earlier than that tag's date.

### H5. Sync should verify the odds key before it writes anything

**Class:** data / process. **Priority:** P4 (P1 for the owner's local runs). **Release:** PATCH.

**What.** A dead key is a 401 that the sync reports as "VEGAS FALLBACK" — the same message
as the API being down. On 2026-09-23 the Bash tool held a pre-rotation key for the entire
session after two `setx` rotations.

**Scope.** A preflight in `run_sync`: one cheap request; on 401 print
*"ODDS_API_KEY rejected (401). On Windows the shell may hold a pre-rotation value: read
`[Environment]::GetEnvironmentVariable('ODDS_API_KEY','User')`."* and, unless
`--allow-fallback`, stop before writing. `C3` makes the fallback non-destructive; this makes
it deliberate.

**Traps.** The scheduled runner has the secret and must not be blocked by a transient 5xx.
Only a 401 stops; a 5xx warns and proceeds under `C3`'s keep-the-good-file rule.

---

## Part 5 — P5: presentation

### R1. Championship odds have no history in the report

**Class:** reporting. **Priority:** P5. **Release:** MINOR.

**What.** Champ% moved 24.9 → 35.4 → 38.2 in one day (a QB claim, a K claim, a trade).
`predictions_2026.jsonl` has every canonical row. Nothing shows the trajectory.

**Scope.** A "how the odds have moved" section: one row per canonical sync, champ% /
playoff% / expected wins, with the change since the previous row and a one-line annotation
from the decision log for that window (claims and trades that landed). Chart optional.

**Traps.** Canonical rows only (F56/B5's provenance split). An ad-hoc evaluation is not a
prediction and must not appear in the series.

### R2. The luck ledger prints p = 0.000 next to "too early"

**Class:** reporting. **Priority:** P5. **Release:** PATCH.

**What.** `scoring luck … z −4.03 p 0.000 too early`. A reader takes the p-value.

**Scope.** Below the pre-registered `n` threshold, print the point estimate and SE and
suppress z and p (or print them as `—`), with the "too early" reason. Nothing about the
definitions changes.

---

## What this document does not schedule, and why

- **The RB/WR epistemic rates.** Owner decision, 2026-09-23: Phase 7 covered it. B1's
  held-out evidence is recorded in `docs/audit/B1_IDP_EPISTEMIC_RATE.md` for the day it is
  reopened.
- **F55's weather study and B10's durability study.** Blocked on data by design (season
  end; week 4). Their instruments are built.
- **Anything that makes the league trade with this roster.** See Part 0.
- **Phase 8 decomposition.** R1 hardware; unchanged.

---

## Ordering, if one model does all of it

1. **C3, H5, H3** — stop the data from being destroyed again. An hour, PATCH.
2. **C1, C2, C4** — the three tools that gave wrong answers on real decisions. Each is
   characterise → diagnose → fix → finding (F67, F68, and one for C2). PATCH.
3. **T4, H1** — the two mistakes that were made twice; make them impossible.
4. **T5, T6** — the two briefs that were built by hand; make them one command each.
5. **T1, T2, T3** — trade and bid tooling, in that order. MINOR each.
6. **C5** — study first; adoption is MAJOR and its own tag.
7. **M2, H2, H4, R1, R2** — any order.
8. **M1** — not before week 6.

MINOR/PATCH items can share a release. Any MAJOR (C5 if adopted, M1 if adopted) gets its
own tag with the six-section notes.

---

## What this document does not claim

That the list is complete. It is what one evening of real use surfaced. The first backlog
had 27 items and closed with F56–F66 filed along the way — expect this one to grow the same
way, and record each new finding where it belongs rather than here.
