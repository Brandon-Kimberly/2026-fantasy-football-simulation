# Audit Phase 3 — Data Ingestion Integrity

**Invariant under test:** every field that looks live is live; every fallback is loud.

**Deliverable:** `tests/test_ingestion.py` — 29 tests as of the finding-5 guard (20 at
characterisation). 1 is a live ESPN match-rate check behind `RUN_LIVE_INGESTION_TESTS=1`
(skipped by default so the suite never touches the network). Plus the fallback inventory the
plan asked for (§ Fallback inventory).

**Suite:** 124 → 153 tests. No pre-existing test changed behaviour.

**Status:** finding 1 fixed; finding 6 team corrected + runtime guard; finding 5 mitigated with
the pid rekey tracked as `AUDIT_PLAN.md` F1. Findings 2, 3, 4, 7, 8, the P5 silent drop and the
`n_0` piece are open, 7 tests red by design. Triage before further remediation.

The bounded joint decision on `_apply_bayesian_updates` (Phase 2 finding 4) and
`DEF_RATING_SHRINKAGE_N0` is in its own section at the end (§ The `n_0` decision) and is kept
out of the phase's own findings numbering.

---

## Method

Two kinds of evidence:

- **Offline, on the committed `data/` snapshot and mocked conditions.** Every fallback in
  `sync.py` was triggered under test (date past the preseason gate, missing API key, API
  exception, empty API payload, one failed schedule week, null team, duplicate names, zero
  projection, stale cache) and the artefact it left behind was inspected. This is what the
  test file pins.
- **Live, once, on 2026-08-28** against Sleeper, ESPN (`espn_api 0.46.0` is installed, so the
  blend is genuinely active in production) and ESPN's 2025 scoreboard: ESPN match rate, cache
  drift, projection coverage, name collisions, and the 2025 points-allowed variance components
  that the `n_0` decision needs. Numbers are recorded here; the live test re-measures them on
  demand.

State at measurement: Sleeper reports `season_type: pre, week 3`, so `current_week` is forced
to 1; `weekly_actuals` is empty; every defensive rating is at its prior with `games_sampled: 0`;
`vegas_totals.json` is byte-for-byte `WEEK_1_VERIFIED_VEGAS`. All of that is the intended
preseason state. Several findings below are therefore **latent** — they activate on the first
in-season sync.

---

## Fallback inventory

Every `except`, `.get(default)`, `continue` and `or` fallback in the ingestion path, classified.
"Loud" means a WARNING or better reaches the log; "artefact" is what a consumer sees afterwards.

| # | site | trigger | artefact left behind | loud? | class |
|---|---|---|---|---|---|
| V1 | `fetch_vegas_implied_totals` date gate `< 2026-09-09` | preseason | week-1 table written | no | intended |
| V2 | same, in-season, `ODDS_API_KEY` empty | no key | **`vegas_totals.json` NOT written**; power ratings overwritten flat 21.5 | no | **silent data loss** (finding 1) |
| V3 | same, API raises | outage | same as V2 | no | **silent data loss** (finding 1) |
| V4 | same, API returns `[]` | empty market | file written with every team 21.5 / opponent `FA` | no | **silent flat environment** (finding 1) |
| V5 | per-game `continue`s (no bookmaker, no market, unknown team) | partial payload | those teams get 21.5 / `FA` | no | silent, per-team |
| W1 | Open-Meteo fetch `except: pass` | weather API down | wind/precip 0.0 | no | harmless — never consumed (finding 9) |
| S1 | `generate_nfl_schedule` per-week `except: pass` | one week fails | that week `{}` → every team `FA` → flat 21.5 for that week | no | **silent flat week** (finding 2) |
| S2 | same, completed-score `except (TypeError, ValueError)` | malformed score | game dropped from defensive sample | no | silent undercount (finding 2) |
| S3 | week-1 fallback to `WEEK_1_VERIFIED_VEGAS` opponents | whole fetch fails | week 1 populated, weeks 2–18 `{}` | no | intended but silent |
| P1 | `generate_player_baselines` weekly-projection `except: pass` | endpoint down | falls to season endpoint | no | degradation |
| P2 | season-endpoint `except: pass` | both down | `projections = {}` → **empty baselines file written** → engine aborts on 156 missing players | no (engine aborts loudly later) | fail-loud by accident |
| P3 | `gp` default 16.0 on the season fallback | no `gp` | weekly = season / 16 (season is 17 games) | no | minor scale error, fallback path only |
| P4 | `total_pts <= 0 → pts_half_ppr` | league scoring yields 0 | half-PPR total on a league with 57 custom keys incl. IDP | no | **wrong scale if it ever fires** — 0 of 156 rostered fire today |
| P5 | `sleeper_weekly_mean <= 0 → continue` | zero projection | player absent from baselines; engine aborts unless whitelisted | no | **silent drop** (finding 6) |
| P6 | `existing_baselines` `except: pass` | unreadable file | no prior blend | no | degradation |
| P7 | ESPN fetch `except` → `{}` | any failure | Sleeper-only for everyone | no | intended degradation; **97% match when up** |
| P8 | `VOLATILITY_CONSTANTS.get(raw_pos, 1.5)` / `EPISTEMIC_ERROR_RATES.get(raw_pos, 0.18)` | raw Sleeper position not a key | anonymous default | no | **silent wrong constant** (finding 3) |
| P9 | `player.get("team", "FA")` | explicit `null` | `team: None` in baselines | no | latent (finding 4) |
| P10 | `baselines[name] = ...` | duplicate full name | last pid wins | no | **silent overwrite** (finding 5) |
| C1 | `update_player_cache` | file exists | never refetched | no | **staleness by design** (finding 7) |
| R1 | `if str(pid) in players_db` | pid absent from cache | rostered player silently dropped | no | latent — 0 of 156 today |
| R2 | `roster_map` default `"Unknown"` / `f"Roster_{id}"` | unmapped owner | team named "Unknown" | no | latent |
| L1 | `generate_league_schedule` per-week `continue` on non-200/empty | one week fails | **`full_schedule.append` skipped → every later week shifts one index earlier** | no | **silent misalignment** (finding 2b) |
| L2 | same, `requests.get` with no timeout / no try | hang or exception | sync crashes | yes (crash) | fail-loud |
| D1 | `PRESEASON_DEFENSIVE_PRIOR.get(team, LEAGUE_AVG_PPG)` | team missing | 21.5 against a table averaging 22.8 | no | latent scale mismatch (finding 8) |
| A1 | `_extract_weekly_h2h_results` `len(pair) != 2 → continue` | bye/malformed | no decision awarded | no | intended |
| A2 | `_extract_weekly_player_scores` unknown pid → `continue` | pid absent | score dropped | no | latent |

---

## Findings

### 1. In-season Vegas fallbacks leave a stale week-1 file on disk — and nothing can tell — **FIXED**

`fetch_vegas_implied_totals` has three in-season fallback paths. Two of them (no key; API
error) **return** `DEFAULT_FALLBACK_TOTALS` but **do not write `vegas_totals.json`**, while they
*do* overwrite `nfl_team_power_ratings.json` with a flat 21.5. The third (empty payload) writes
a file in which every team is 21.5 / opponent `FA`. None of the three logs anything.

Consequence, verified through the engine: the engine applies `vegas_totals.json` to the
*current* week. With the file left at the week-1 table, a week-5 run hands DET a week-1 line
against CHI when the schedule says GB. Future weeks use the flat 21.5. Nothing stamps the file
with a week, so no consumer can detect it — although the information to detect it exists:
`nfl_schedule.json` names the real opponent, and a one-line comparison would catch it.

`ODDS_API_KEY` is **not set** in this environment. On the first sync after 2026-09-09, path V2
fires. **Severity: high, latent.** Activates in 12 days.

**Fixed (write path + staleness signal). `ODDS_API_KEY` remains the actual fix for correct
opponents.** Three parts:

- *sync:* every path out of `fetch_vegas_implied_totals` now goes through `_write_vegas`, which
  writes `vegas_totals.json` and stamps it with `_meta = {week, source, fetched_at}`. The four
  fallback paths (no key, API error, empty payload, partial payload) each log a WARNING that
  names `ODDS_API_KEY`. `generate_nfl_power_ratings` skips the stamp.
- *engine:* `_check_vegas_staleness` runs at construction. A team's line is condemned if the
  stamp's week is not the current week **or** its opponent disagrees with `nfl_schedule.json`
  for the current week — the second signal catches unstamped legacy files. Condemned lines are
  refused (that team gets the ratings-model environment for its real opponent) and an ERROR is
  logged naming the teams and the key. The run proceeds; the stale data does not.
- *documentation:* `config.py` and the README now say plainly that the key is required for any
  correct in-season forecast, and that the loudness makes the keyless state *visible*, not
  *correct*. The README's old text ("falls back to a verified Week 1 dataset otherwise") was
  itself wrong — after 9/9 the fallback is flat 21.5.

Verified through the engine: the committed **week06 fixture had been reproducing this bug** —
28 of 28 scheduled teams carried week-1 opponents. With the fix, all 28 are condemned and week 6
runs on the ratings model; week01's file is genuinely for week 1 and is untouched. Golden
movement: week01 unchanged in all three stages; week06 moved (one cause). Known residual: when a
fallback-stamped file (all opponents `FA`) meets a populated schedule, every line is condemned by
the opponent check and the ERROR path fires rather than the softer "fallback" WARNING; the
message still names the key, and the behaviour (ratings model for real opponents) is the better
of the two.

### 2. One failed schedule week silently flattens that week and undercounts the defensive sample — **FIXED (2 and 2b)**

`generate_nfl_schedule` wraps each week in `except: pass`. A single failed week leaves
`nfl_schedule[wk] == {}`, so every team resolves to `FA` → 21.5 / no opponent / no defensive
tier for that week, and — because completed scores are harvested in the same pass — every team
loses a game from the sample `generate_defensive_ratings` shrinks toward. 14 rows instead of 16
in the test; no marker, no warning.

**2b, same class, worse:** `generate_league_schedule` skips a failed week with `continue` and
then `full_schedule.append`s only the weeks that succeeded. The engine indexes that list by
`week_idx`. One failed fetch shifts every subsequent week's *fantasy* matchups one week earlier.
Not reproduced under test because the function also has no timeout and no `try` around
`requests.get` (a hang or exception crashes sync instead — see L2), but a 404/empty body takes
the `continue` path. **Severity: medium (2), high-latent (2b).**

**Fixed.** `generate_nfl_schedule`: a failed or non-2xx week is recorded under
`nfl_schedule["_meta"]["failed_weeks"]` and logged at WARNING (the message says whether the
defensive sample lost games); malformed events and non-numeric scores warn per game instead of
vanishing; the week-1 table fallback announces itself. The engine reads weeks with `.get`, so
`_meta` is invisible to it. `generate_league_schedule`: `requests.get` now has a timeout and a
`try`; a failed week contributes an **empty** week so every later week keeps its index, warns,
and the function returns the failed weeks; an `assert` pins one entry per week. The undercount
test was re-scoped to assert the *record* of the loss (the games cannot be recovered without a
re-fetch); a new test pins the index alignment. No golden movement (sync only, verified).

### 3. Position constants are looked up by Sleeper's raw position, not the engine's — **FIXED**

`VOLATILITY_CONSTANTS` and `EPISTEMIC_ERROR_RATES` are keyed `DL/DB/RB/...`. Sleeper reports
`DE, DT, NT, CB, S, FS, SS, FB`. `sync` looks up by the raw string, so every such player gets
the anonymous defaults `k=1.5, rate=0.18`. Committed baselines: 41 DE, 59 DT, 43 CB, 4 FB, 2 NT,
1 SS, plus 32 team `DEF` entries. **Five rostered DEs** get an epistemic rate of 0.18 instead of
DL's 0.15 (+20%); a rostered FB would get 0.18 instead of RB's 0.63 and k=1.5 instead of 1.98.
The engine normalises positions on its side (`normalize_position`), so the *slot* is right and
the *constant* is wrong — precisely the silent kind. **Severity: medium.**

**Fixed.** `normalize_position` moved to `config.py` (re-exported from `simulation.py` so every
existing import works) and `sync` applies it before the constant lookups. The stored `pos` stays
raw; the engine normalises on read, as before. Positions with no calibrated constants (team
`DEF`, `FLEX`) are summarised in one WARNING per sync. No golden movement (the fixtures' baselines
are committed; verified).

### 4. `team: null` survives into baselines — **FIXED**

`_build_roster_player_entry` documents this exact bug ("`.get('team', 'FA')` does NOT catch an
explicit None") and fixes it for rosters. `generate_player_baselines` has the same line, unfixed.
Two committed baselines carry `team: null` (Tyler Davis, Cedric Tillman). Every engine consumer
happens to tolerate `None` today (`or 'FA'`, `not in ['FA', None]`, `isinstance(..., str)`), so
this is latent — but each of those guards is a separate place the fix has to be remembered.
**Severity: low.**

### 5. Baselines and rosters are keyed by full name; Sleeper has duplicate names — **MITIGATED (interim guard); rekey tracked as F1**

Two of 962 baseline-producing names collide today: **Justin Jefferson** (WR/MIN 13.82 vs LB/CLE
3.04) and **Byron Murphy** (CB/MIN 6.95 vs DL/SEA 6.64). Last pid wins. Jefferson's committed
baseline is the WR's — by iteration order, not by design. Byron Murphy's committed baseline
**is the SEA DL's**; a manager rostering the MIN CB would be simulated with the DL's projection,
position and team. Neither is rostered today. `live_rosters.json` is keyed the same way, so the
engine has no way to distinguish them even if baselines did. **Severity: medium, latent.**

**Mitigated (interim guard, sync-only). The pid rekey is tracked as `AUDIT_PLAN.md` F1.**
`sync.resolve_player_keys` now assigns every name key: for a colliding name, the sole rostered
claimant keeps the plain name (rosters are minted from it), every other claimant is stored as
`"Name (pid)"`, each collision logs a WARNING with both records, and two rostered claimants
**raise** — that case is unrepresentable under name keying. Applied identically to baselines and
`weekly_actuals.player_scores`. Today: Justin Jefferson's LB becomes `"Justin Jefferson (13524)"`;
both Byron Murphys are suffixed until one is rostered.

Found while scoping the guard, and fixed with it: the sync-to-sync prior blend
(`0.6·fresh + 0.4·last mean`) looked the prior up **by the current name key**, so a key flip
either dropped the player's own smoothing or — worse — blended a newly rostered CB with the DL's
stored mean (reproduced: `0.6·7.00 + 0.4·6.64 = 6.86`). Each baseline now stores `player_id` and
the prior is looked up by pid first; the name fallback applies only to a legacy pid-less file and
never to a colliding name (fresh-only for that one sync, with a WARNING). Tests cover the flip in
both directions and the legacy case.

Verified against the run, not predicted: **the golden master did not move** (sync-layer only;
the engine runs on committed inputs), and the still-open findings 2, 3, 7, 8 fail with identical
messages before and after — no interaction. `backtest_season` passes no rostered pids, so in the
backtest all colliders are suffixed and a rostered collider would abort loudly at pre-flight
rather than be mis-simulated.

### 6. A rostered player with a zero projection is silently dropped, then hand-imputed with the wrong team — **PARTLY FIXED (whitelist team + runtime guard); silent drop still open**

Jordyn Tyson (WR) is in the week-1 projection payload with 0 points → `continue` → absent from
baselines → the engine aborts unless the name is in `KNOWN_MISSING_ASSETS`. His whitelist entry
says `team: "FA"`. Sleeper's database — committed in `data/` — says **NO**. He therefore gets the
`FA` environment fallback and no teammate correlation. The drop itself emits nothing; the only
signal is a crash one stage later, resolved by hand-typing a number. **Severity: medium.**

**Fixed (data + runtime guard).** The whitelist entry now says `team: "NO"`, matching Sleeper pid
13281 (WR, NO, rookie, `depth_chart_order` 3, `injury_status` Doubtful) and the committed roster
file; the config comment records the source and marks `mean 6.5` as unverified. The engine now
compares every imputed whitelist entry's team and (normalised) position against the roster at
construction and logs a WARNING naming the entry on any mismatch -- the entry is still used as
written, so the fix lives in `config.py`, not in a silent override. Two tests: the data test
cross-checks every whitelist entry against both the cache and the roster; the runtime test
injects a mismatching entry and asserts the warning. Golden movement: both scenarios, one
player wide (weekly team mean +0.004 / -0.005).

Still open from this finding: the silent `continue` on a zero projection (P5) -- the drop that
makes the whitelist necessary in the first place.

**P5 closed:** the drop now logs a WARNING naming the player, the whitelist, and the team the
entry must carry. The characterisation test passes as the guard.

### 7. The player cache is never refreshed — **FIXED**

`update_player_cache` fetches once and reads the file forever. There is no age check, no force
path, no CLI flag. The live comparison found the one-day-old cache already differing from Sleeper
on a rostered player (`injury_status`). Team and position drift is what late-August cuts and
trades produce, and the cache is what every name, team and position in the pipeline comes from.
**Severity: medium.**

**Fixed.** `update_player_cache(force=False, max_age_seconds=86400)` refreshes when the file is
older than a day (Sleeper's own once-a-day guidance for the 20 MB endpoint) or on `force=True`,
logs the refresh and its reason at INFO, and on a failed refresh serves the stale file with a
WARNING stating its age rather than crashing. Client only; no golden movement.

### 8. The defensive prior fallback is on a different scale from the prior table — **FIXED (fallback = table mean, derived)**

Teams missing from `PRESEASON_DEFENSIVE_PRIOR` fall back to `LEAGUE_AVG_PPG = 21.5`. The table
itself averages **22.81**, and real 2025 points allowed averaged **23.01**. A missing team would
be ranked an above-average defence by construction. All 32 are present, so latent — but this is
the third place (after Phase 2's `22.0` normaliser and the ratings' 22.6) where 21.5 does not
match the data it stands beside. `LEAGUE_AVG_PPG` has no sourcing comment. **Severity: low.**

### 9. Fields ingested and never read — **CLOSED as reported; no code change**

- **Weather.** Up to ~16 Open-Meteo calls per in-season sync populate `wind_mph` and
  `precip_prob`. The engine reads neither — they appear only in its default dicts.
- **`injury_status`, `status`, `active`.** Present on every cached player; never consulted. A
  player Sleeper lists as Out is simulated as healthy. (The engine's injury model is purely
  stochastic.)
- **Standings `h2h_wins` and `points_scored`.** Written every sync; the engine reads only
  `remaining_faab` from that file (banked results come from `weekly_actuals`).
- **`depth_chart_order`** — present on 12,193 cache entries, wanted by Phase 7, discarded
  (already noted in Phase 1).

Reported, not tested: the plan's "looks live, is live" question, answered in the negative for
four fields. **Severity: low individually.**

### Verified — holds

- **ESPN match rate: 97%** of rostered blend-eligible players (116/119); **99%** of all
  eligible baselines with mean ≥ 5 (181/182). The three rostered misses (Tyson, Kittle,
  Charbonnet) are players ESPN had no week-1 projection for, not normalisation failures.
- **Defensive shrinkage arithmetic** behaves exactly as claimed: prior at n=0, weight
  n/(n+n₀) on the data, monotone, bounded. (Whether n₀=4 is right: § below.)
- **Preseason Vegas gate** serves the verified table without touching the API.
- **Week-1 schedule fallback** populates week 1 from the verified table when ESPN is down.
- **Roster coverage:** all 156 rostered pids are in the cache; 152 have a league-scoring
  projection > 0, none fall to the half-PPR fallback.

---

## The `n_0` decision (bounded piece — Phase 2 finding 4 + `DEF_RATING_SHRINKAGE_N0`)

`config.py` says `DEF_RATING_SHRINKAGE_N0 = 4.0` is "the same 'trust N games of prior' shrinkage
strength used for player baselines in the simulation engine, for statistical consistency." The
two uses share a number and a phrase, and are **not the same construct**:

| | defensive rating (`sync`) | player update (`_apply_bayesian_updates`) |
|---|---|---|
| prior | a point estimate from a table; **no variance stated** | a mean **with a stated variance** `std_epistemic²` |
| form | `(n₀·prior + n·x̄) / (n₀ + n)` | `1/(n₀/v₀ + n/actual_var)` |
| what `n₀` does | *is* the prior's variance, expressed as pseudo-games (v₀ = σ²/n₀) — the standard, correct conjugate form when no variance is given | multiplies the precision of a variance that is *already stated* — a double count |
| likelihood variance | implicit σ² of a game | sample variance of 2–5 scores, floored at v₀/2, instead of the calibrated `std_aleatoric²` |

So the two are already "consistent" in the only sense that matters — both are conjugate normal —
*provided* the player side stops applying a pseudo-count on top of a stated variance. Measured
on the committed baselines, the stated `std_epistemic` priors already imply pseudo-counts of
**≈1 game** for offence (RB 0.87, WR 1.01, QB 1.65, TE 1.81) and **≈10** for IDP (LB 9.9,
DB 10.8, DL 8.0); the ×4 makes those ≈4 and ≈40.

**What the defensive `n₀` should be, from data.** Real 2025 season, 272 completed games:
within-team game-to-game variance of points allowed **91.4** (sd 9.6); variance of the 32 team
means 13.1, of which 91.4/17 = 5.4 is sampling noise, leaving true between-team variance
**7.7** (sd 2.8). Empirical-Bayes pseudo-count = 91.4 / 7.7 = **11.9 games**.

| games | weight on data, code (n₀=4) | warranted (n₀≈12) |
|---|---|---|
| 1 | 0.20 | 0.08 |
| 4 | 0.50 | 0.25 |
| 8 | 0.67 | 0.40 |
| 17 | 0.81 | 0.59 |

The code trusts a handful of games about **3× too much**. The 2026 prior table correlates 0.85
with realised 2025 points allowed (cross-season, indicative only), with sd 2.5 vs realised 3.6 —
a reasonable but slightly under-dispersed prior.

**Recommendation (not applied):**

1. **Player update:** implement the conjugate normal as written in Phase 2 finding 4 —
   precision `1/std_epistemic² + n/std_aleatoric²`, no `n₀`, no sample-variance floor. This is a
   correctness change with no new constant.
2. **Defensive rating:** keep the pseudo-count form (it is right for a variance-less prior) and
   **recalibrate `DEF_RATING_SHRINKAGE_N0` from 4.0 to ≈12**, sourced to the 2025 variance
   decomposition above. This is a calibration change and carries the Phase 7 caveat that it is
   tuned on one season; it is, however, the first value of this constant with a derivation.
3. **Retire the "statistical consistency" comment** in `config.py` and replace it with the
   actual relationship: both are conjugate updates; the defensive prior's variance is expressed
   as a pseudo-count because the table gives none, and `n₀ ≈ 12` is what the data say that
   variance is.

Both halves move hashes (week06 `stage_a` for the player side; defensive ratings only matter
once `games_sampled > 0`, so the golden fixtures — all at 0 — would not move for the defensive
half). They should be two commits.

**Applied — player half.** `_apply_bayesian_updates` and its `backtest_player` mirror now use
the conjugate form above (no `n_0`, `std_aleatoric²` as the likelihood variance). The Phase 2
finding-4 tests pass as regression guards; the mirror's parity test against the real method
passes. Golden movement week06 only: weekly team mean +1.8%, season-points std 153 → 172.

**Applied — defensive half.** See the next commit: `DEF_RATING_SHRINKAGE_N0` 4.0 → 12.0 with the
2025 derivation as its source, and the "statistical consistency" comment replaced.

---

## Triage table

| # | Finding | Severity | Blast radius | Latent? |
|---|---|---|---|---|
| 1 | In-season Vegas fallbacks leave a stale week-1 file; no stamp, no warning — **fixed** (write + stamp + engine refusal; `ODDS_API_KEY` still the real fix) | High | current-week environment for every player, all season | was 12 days out |
| 2 | Failed schedule week → flat week + defensive undercount, silently — **fixed** (recorded in `_meta`, warned) | Medium | that week + defensive ratings | guarded |
| 2b | Failed league-schedule week shifts every later week's matchups — **fixed** (empty week keeps the index) | High | standings, H2H, playoffs | guarded |
| 3 | Constants looked up by raw position → anonymous defaults — **fixed** | Medium | 5 rostered DEs; any CB/S/FB/DT | fixed |
| 4 | `team: null` in baselines — **fixed** (`or "FA"`) | Low | 2 baselines | fixed |
| 5 | Name-keyed baselines/rosters; duplicates overwrite — **mitigated: loud, deterministic collision keys + pid-tracked prior; rekey = F1** | Medium | 2 names today | guarded |
| 6 | Zero-projection player silently dropped; whitelist team wrong — **team fixed + runtime guard; silent drop open** | Medium | Jordyn Tyson's environment/correlation | live now |
| 7 | Player cache never refreshed — **fixed** (24h max age, force, loud failure) | Medium | every name/team/position | fixed |
| 8 | Defensive prior fallback 21.5 vs table 22.8 — **fixed** (fallback derived from the table mean, announced) | Low | any team missing from the table | fixed |
| 9 | Weather / injury_status / standings fields never read — **closed as reported**; `injury_status` is a modelling decision for Phase 4/7, not ingestion | Low | wasted calls; unmodelled injuries | — |
| n₀ | Two different constructs sharing a number; defensive n₀ 3× too trusting | High (bounded piece) | posterior widths; in-season defensive tiers | in-season |
