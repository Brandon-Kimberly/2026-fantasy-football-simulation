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
- **The renewal chain is broken at 2025.** `previous_league_id` is `None` there, so the
  chain alone reaches only 2026 and 2025. Set **`SLEEPER_LEAGUE_ID_2024`** and `--all`
  picks 2024 up through `config.KNOWN_LEAGUE_IDS` (B20). League ids are environment-only
  (F37) and are never written down here.
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

## Addendum: three more, registered 2026-09-29

**Registered 2026-09-29, after weeks 1-3 of 2026 had been played and were visible on the web
UI, and before any of the three below had been computed by anything in this project.** They
are registered late, not post hoc: the owner asked for them (docs/WEB_UI_ROADMAP.md, Decision
2) and nothing had computed them on this season's data before this commit. The git history of
this file is the timestamp, and the implementation lands in a later commit. Every rule above
holds for them: each is shown separately, never combined with anything, and z and p are
withheld below `MIN_WEEKS_FOR_INFERENCE` (6).

| metric | definition | null | lucky direction |
|---|---|---|---|
| `forecast_luck` | my actual H2H wins − the sum of my quoted pre-game H2H win probabilities | 0 | **+** |
| `median_luck` | (my actual median wins − the sum of my quoted pre-game median probabilities) − the league average of the same | 0 | **+** |

Definitions, fixed now:

- **The quote** is the model's canonical pre-kickoff forecast for that week, the row
  `webui.accuracy.quoted_week` returns, which the Accuracy page scores. A week with no such
  quote, or with no result, is left out, and the count of weeks left out is reported.
- **Wins** are the league's record as played (F83, `webui.results.week_results`). A tie is
  half a win, and a tie against the median is half a median win.
- **Standard error:** sqrt(sum of p × (1 − p)) over the counted weeks, treating each quoted
  game as an independent Bernoulli trial. The median measure's league average is itself
  estimated, which the SE ignores; that is stated beside the number, not corrected.
- **`forecast_luck` is not separately differenced against the league.** Across the league
  the sum of wins equals the sum of the quoted chances (each game's two chances sum to one),
  so the league average is zero by construction. The methodological commitment is met by
  that identity.
- **`median_luck` is differenced.** The quoted median chances need not sum to half the league
  each week, so the league average of the raw difference is subtracted.

**The schedule-swap matrix** is descriptive, not a test: no null, no z, no p. Row A, column
B is the head-to-head record team A would have had on team B's schedule. For each played
week, A's score is set against the score of B's opponent that week; in the week B played A,
A is set against B instead. Scores are Sleeper's box scores, the same basis and the same
re-scored-weeks caveat as all-play. The diagonal is A's own record on box scores, which can
differ from the league's record where a week was re-scored (F83), and the page says so.

## Usage

```
py -3.10 -m scripts.luck_ledger                                  # current season
py -3.10 -m scripts.luck_ledger --season 2025
py -3.10 -m scripts.luck_ledger --all                            # renewal chain
py -3.10 -m scripts.luck_ledger --all                            # 2024 needs SLEEPER_LEAGUE_ID_2024
py -3.10 -m scripts.luck_ledger --json
```

**Deliberately not wired into `scripts.weekly_report`** (owner's decision, 2026-09-21):
a luck number in front of you every Sunday is an invitation to read noise as persecution.
This is a thing you pull when you want it.
