# B10 — Player-specific durability: the study, and why it has no answer yet

**2026-09-23.** Backlog item B10 asks whether injury-designation history predicts a later
DNP above the positional base rate, and is explicit that **adoption is a separate, later
decision** (MAJOR if it happens; the study itself is PATCH).

B10's acceptance is *"a `docs/audit/` entry with the fitted lift and n"*. **There is no
fitted lift, and n is zero.** This entry records why, what was measured to establish it,
and the exact week the study becomes runnable — because "we could not do it" is only
useful if it says what was missing.

---

## The data inventory, measured

| source | what it holds | weeks |
|---|---|---|
| `data/logs/season_2025.json` | `roster_map`, `final_standings`, `matchups` (`roster_id`, `matchup_id`, `points`, `players`, `starters`, `players_points`) | 2025, all |
| `data/logs/designations.jsonl` (B21) | 28 rows: 11 Out, 11 Questionable, 3 IR, 2 PUP, 1 NA | **week 3 only** |
| `data/logs/first_recorded_scores.jsonl` (B19) | 1,748 rows, ~800 distinct players/week, zeros included | **weeks 1–2 only** |
| `data/current/sleeper_players_cache.json` | today's `injury_status`, overwritten every sync | now |

**2025 carries zero designations.** B10 guessed the history was *"only partially
recoverable for 2025"*. It is not partially recoverable; the committed 2025 export has no
injury field of any kind. Searching the whole file for `injury`, `Questionable`,
`Doubtful` and `practice` returns nothing. `projection_log.jsonl`,
`predictions_2026.jsonl`, `decision_log.jsonl` and `first_recorded_scores.jsonl` likewise
carry no status field, and the week-1 lineup records predate B4, so they have no
`designation` either. Sleeper exposes no historical-injury endpoint. The series simply
does not exist before B21 started writing it today.

**So the overlap is empty.** A lift needs a designation in week W and an outcome in week
W+1. Designations exist for week 3. Completed-week scores exist for weeks 1–2 (B19 writes
a week once it *completes*). There is no W with both.

    usable pairs: NONE

**The first usable pair is week 3 → week 4**, so the study runs once week 4 completes.

---

## F63 — the denominator was never being recorded

Found while doing the above, and it would have bitten silently in three weeks.

B10's test is "above the **base rate**", which is a rate among the **undesignated**.
`append_designations` wrote only players carrying a designation, and said so deliberately:

> Only players carrying a designation are written. A row per healthy man per week is 150
> rows of "nothing happened", and the question is about designations, not roll call.

For B10 that is backwards. The healthy men *are* the question — they are the comparison
group. And the roll call is not recoverable afterwards: `live_rosters.json` is overwritten
every sync, and the players cache holds only today.

Without it the study must borrow a denominator from `first_recorded_scores`, which is the
**league-wide** stats feed — ~800 players a week against the ~152 rostered. A player
nobody was tracking then lands in the "undesignated" arm while quite possibly carrying a
designation that was never written down. That inflates the undesignated DNP rate and
biases the measured lift **downward**: the study could only ever understate its own
effect. `study()` reports `population_source` so a reader can tell which of the two they
are looking at, and treats `scored_feed` results as a lower bound.

**Fixed**: the roll call is now written, healthy players with `injury_status: null`. The
existing dedupe on `(week, pid, status)` carries it without inflating the file — one row
per healthy player per week, and a Friday Questionable is still a distinct key, so the
transition survives. Cost: ~152 rows/week, ~2,700 a season.

Two committed tests pinned the old behaviour (`test_a_healthy_player_writes_nothing`, and
an `n == 2` assertion reading *"only the two with a designation"*). Both were amended
rather than deleted, with the reason recorded in place: they had pinned the defect as if
it were the requirement.

---

## What was built

`fantasy_sim/durability.py` — pure, tested against hand-computed fixtures:

- `designation_counts(rows, through_week, lookback)` — distinct designated **weeks** per
  player in the trailing window. Weeks, not rows: B21 dedupes on `(week, pid, status)`, so
  a Friday Questionable that became a Sunday Out is two rows in one week and one
  designated week.
- `dnp_flags(score_rows, week)` — exact `0.0` only. A player absent from the feed is
  *unknown* and is dropped, not counted as a DNP.
- `study(...)` — the 2×2, pooled and stratified by position, with Wilson intervals,
  `risk_difference` alongside `lift`, and `adoptable` + `reason`.
- `scripts/durability_study.py` — runs it on the real logs and prints the inventory.

### Three decisions worth stating

**Do not proxy durability from scores.** B10's trap, and it is pinned by a test. A low
score is not an injury. Only a designation designates; only an exact 0.0 is a DNP.

**`lift` is `None` when the undesignated rate is zero.** A ratio with a zero denominator
is undefined, and "infinite lift" off a handful of healthy men is precisely the overclaim
this module exists to prevent. `risk_difference` stays defined and is reported beside it.

**Wilson, not normal, intervals.** At the sample sizes available for months a normal
approximation puts the lower bound below zero and reads as precision that is not there.

### `adoptable` can only return False today

By construction, and this is deliberate. B10 requires a lift that is significant **and
survives holdout**. 2026 is the only season with designations at all, so there is nothing
to hold out. The most `_adoptability` can return, even on a clean separated result, is
`False` with the reason *"separated in-sample, but B10 requires a HOLDOUT"*. When a second
season of designations exists, that branch is the one to revisit.

---

## Status

**Study: blocked on data, not on work.** The estimator is built and tested; it runs
itself once week 4 completes. **Adoption: not attempted, and not attemptable this
season** — no holdout exists.

`INJURY_RATES` is unchanged. Nothing in the engine imports `fantasy_sim.durability`, so
no prediction moved: this is PATCH, and the MAJOR B10 anticipated does not arrive with it.
