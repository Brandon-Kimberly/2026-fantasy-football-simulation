# Streamer levels vs the free-agent pool — C5 study

**Measured 2026-09-24, week 3. `n = 1` sync. NOTHING WAS CHANGED.**

`BASE_STREAMER_MEANS` is read at engine init by every hole evaluation, so moving it is a
**MAJOR** release under this repo's policy (the model's predictions change materially while
the goldens stay byte-identical — the F28 class). This document is the measurement C5 asked
for; the decision is the owner's.

Reproduce with `py -3.10 -m scripts.streamer_study`.

## What is being compared

An unfillable slot is scored `max(0, N(m_str, 2.2))` with, in `run_simulation`:

```
m_str = max(replacement_level[pos] * 0.8,
            BASE_STREAMER_MEANS[pos] * STREAMER_DECAY_RATE ** streamers_used[pos])
```

Against that, the **claimable level**: the mean of the top 3 free agents at the position,
by baseline mean. `derived_capped` is what a pool-derived streamer would be under Phase 4's
cap — never above the replacement level, because a streamer that out-projects a rostered
starter makes a *hole* worth more than a *player*.

## The table

| pos | BASE | replacement | `m_str` | pool top-3 | raw gap | capped | vs `m_str` | pool n |
|---|---|---|---|---|---|---|---|---|
| DB   | 8.00  | 8.71  | 8.00  | 9.54  | +1.54 | 8.71  | **+0.71** | 281 |
| DL   | 7.50  | 6.83  | 7.50  | 7.40  | −0.10 | 6.83  | −0.67 | 287 |
| FLEX | 8.50  | 10.09 | 8.50  | 9.37  | +0.87 | 9.37  | **+0.87** | 276 |
| K    | 8.00  | 10.95 | 8.76  | 11.46 | +2.70 | 10.95 | **+2.19** | 25 |
| LB   | 8.00  | 10.86 | 8.69  | 11.39 | +2.70 | 10.86 | **+2.17** | 171 |
| QB   | 14.00 | 18.32 | 14.66 | 16.58 | +1.92 | 16.58 | **+1.92** | 19 |
| RB   | 9.00  | 10.43 | 9.00  | 7.34  | −1.66 | 7.34  | −1.66 | 68 |
| TE   | 7.50  | 7.41  | 7.50  | 8.05  | +0.55 | 7.41  | −0.09 | 89 |
| WR   | 9.00  | 10.09 | 9.00  | 9.05  | +0.05 | 9.05  | +0.05 | 119 |

Positions where Phase 4's cap binds (pool above replacement): DB, DL, K, LB, TE.

## Four things the backlog item had wrong

**1. QB is not the worst case — K and LB are.** C5 says *"DL is fine. The other positions
were not checked."* Checking them puts **K at +2.19 and LB at +2.17**, both above QB's
+1.92. The item's framing would have led to a QB-only fix that left the two larger gaps in
place.

**2. The streamer is not 14.0.** `m_str` for QB is `max(18.32 × 0.8, 14.0) = 14.66` — the
replacement floor binds, and it is the floor rather than the constant that sets the value.
Comparing the pool against the bare constant overstates the QB gap by 0.66.

**3. The QB pool moved within a day.** C5 cites Love 17.3 / Murray 17.9 / Nix 17.4 /
Stafford 16.6. A day later the top three free-agent QBs are 16.78 / 16.54 / 16.47 — Love
was claimed. A single-sync measurement of a 19-man pool is **volatile**, which is exactly
why `n = 1` is not enough to move a constant.

**4. Not every gap points the same way.** RB is **−1.66**: the streamer is *above* what is
claimable, so an RB hole is currently priced too kindly. A fix that only raised constants
would make RB worse.

## `n = 1`, and why the backlog's data source cannot fix that

C5 says *"`projection_log.jsonl` has the history."* **It does not.** That log is one line
per **rostered** player per sync, so a player who has been a free agent all season never
appears in it and the pool cannot be reconstructed for any past date. The 84 sync stamps in
it are no help for this question.

`scripts/streamer_study --record` now appends one row per run to
`data/logs/streamer_levels.jsonl`, and the study runs as a weekly-report step, so the
four-sync comparison the item wanted becomes possible from here forward. **The right time
to revisit is week 7**, with four independent observations.

## Recommendation

**Do not change `BASE_STREAMER_MEANS` yet.** One volatile observation is not a basis for a
MAJOR release, and three of the nine positions point the other way or are already right.

When there are four syncs, option **(b)** from C5 is the better fix and is still the
recommendation: derive `m_str` from the actual pool at engine init, capped at the
replacement level. It is self-maintaining, it cannot drift as the wire changes, and it
subsumes all nine numbers. Option (a) — hand-raising QB — would fix the third-largest gap
and leave K and LB.

Acceptance for that change, when it happens: goldens regenerated deliberately with the
week01/06/15 deltas explained, a points-backtest line before and after, and
`scripts.run_behavior_check` run (this is a sync/init constant, so the goldens alone cannot
see it — F28).

## Separately found: the week tools and the engine disagree about the SECOND streamer

Not part of C5 and not changed here. The engine decays repeated streamers at the same
position (`STREAMER_DECAY_RATE = 0.85`, so a team's second DL streamer in a week is worth
0.85× the first), while `decisions.streamer_mean` — added by C2/F69 so the week tools and
the season simulation would agree — applies **no decay**. The two agree exactly for the
first hole, which is every case observed live so far, and diverge for a roster with two
holes at one position. This is the same class F69 was about (the week tools and the season
simulation disagreeing about one roster) and should be its own item.
