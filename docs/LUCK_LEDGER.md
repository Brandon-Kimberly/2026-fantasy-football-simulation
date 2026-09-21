# Luck ledger — pre-registration

**Registered 2026-09-21, before the rest of the 2026 season was played.** The git history
of this file and of `fantasy_sim/luck_ledger.py` is the timestamp.

## Why this exists

The owner's standing question is *"am I genuinely unlucky, or does it just feel that
way?"* — and the reason it is hard to answer honestly is that **any** specific sequence of
events is improbable after the fact. A 1.39-point loss in which a reversed fumble call
extends overtime is a one-in-something event; so is every other week, viewed narrowly
enough. Post-hoc probability is not evidence.

The only defence is to fix the measurements **before** the data arrives, then read
whatever they say. That is what this document is. Changing a definition after seeing an
answer converts this from a test into a story, and the whole thing becomes worthless.

## The five measurements

Each is reported separately, with its own standard error and two-sided p-value. **No
combined "luck score" is produced** — deliberately, matching `season_retrospective`'s
refusal of a combined verdict. A single blended number is exactly the thing that invites
narrative-fitting.

| metric | definition | null | lucky direction |
|---|---|---|---|
| `schedule_luck` | actual H2H wins − all-play expected wins | 0 | **+** |
| `opponent_luck` | my points-against per game − league average | 0 | **−** |
| `close_games` | W−L in H2H decided by < 10.0 points | .500 | **+** |
| `dnp_luck` | my starter-DNPs per game − league average | 0 | **−** |
| `scoring_luck` | my mean weekly z − league mean weekly z | 0 | **+** |

`CLOSE_MARGIN = 10.0`. A margin of *exactly* 10.0 is **not** close (strict inequality).
`DNP_EPSILON = 1e-9`: a starter scoring exactly 0.00 is the DNP proxy — the same proxy
`season_retrospective` uses, and it cannot distinguish "inactive" from "played and scored
nothing".

## The one methodological commitment

**Every metric is differenced against the league, never against an absolute.**

The engine has measured bias — points-backtest `bias −2.12`, `cover80 0.654` against a
nominal 0.80, optimal sd inflation ~1.27. Scoring a team's z against zero would therefore
re-measure *the model's error* and report it as *that team's luck*. Differencing against
the league cancels everything shared and leaves only what is specific to one roster.

This is why `scoring_luck` compares `my_mean_z` to `league_mean_z` rather than to 0.

## What it cannot do

- **It will not be significant soon.** Schedule luck over a 14-game season carries an SE
  near ±1.8 wins, so even a 2-win deficit is barely 1 SE. Below six completed weeks the
  tool prints `too early` instead of a significance word, on purpose.
- **`scoring_luck` only exists from 2026.** It needs contemporaneous projections, and
  `data/logs/predictions_2026.jsonl` began this season. 2024 and 2025 report `None`
  rather than a fabricated zero.
- **2024's `dnp_luck` is contaminated.** That season had an abandoned roster whose owner
  stopped setting lineups, so its starters score 0.00 in bulk and the league-average DNP
  rate is inflated (2.12/game against 0.43 for the owner). Read 2024's DNP row as
  unusable, not as good luck.
- **The renewal chain is broken at 2025.** `previous_league_id` is `None` there, so
  `--all` reaches only 2026 and 2025. 2024 must be named explicitly:
  `--league-id 1134957276114178048`.
- **Refereeing, negated touchdowns and similar are not measured at all.** Sleeper does not
  log plays that were called back. That grievance is real and this tool is silent on it;
  it is not evidence either way.

## Standing result

Recorded at registration so later readings have a baseline:

| season | schedule | opponent | close | DNP |
|---|---|---|---|---|
| 2024 (14 wk) | **−1.14** unlucky | +1.28 unlucky | −0.50 unlucky | *contaminated* |
| 2025 (14 wk) | **−1.14** unlucky | +3.46 unlucky | −0.50 unlucky | −0.06 lucky |
| 2026 (2 wk) | −0.43 unlucky | +5.27 unlucky | 0.00 neutral | −0.69 lucky |

Schedule luck landed at **−1.14 in both completed seasons** — the same direction twice.
Pooled that is −2.28 wins against a pooled SE near ±2.6, **z ≈ −0.88, p ≈ 0.38**: a
consistent lean, and still indistinguishable from chance. Two seasons is not enough, which
is the point of writing it down and waiting.

## Usage

```
py -3.10 -m scripts.luck_ledger                                  # current season
py -3.10 -m scripts.luck_ledger --season 2025
py -3.10 -m scripts.luck_ledger --all                            # renewal chain
py -3.10 -m scripts.luck_ledger --league-id 1134957276114178048  # orphaned 2024
py -3.10 -m scripts.luck_ledger --json
```

**Deliberately not wired into `scripts.weekly_report`** (owner's decision, 2026-09-21):
a luck number in front of you every Sunday is an invitation to read noise as persecution.
This is a thing you pull when you want it.
