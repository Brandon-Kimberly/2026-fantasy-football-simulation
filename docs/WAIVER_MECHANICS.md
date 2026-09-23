# Waiver and roster mechanics

Whether a claim can **land at all** is decided by league settings, not by how good the
player is. Three of these cost real time in week 3 of 2026 and were written down nowhere.
This file is the reference. It describes *mechanics*, not strategy — what the tools value
is a separate question.

**Every setting below was read live from the league object on 2026-09-23**, not transcribed
from memory. Re-verify with one read-only call if the commissioner changes anything:

```bash
py -3.10 -c "import os,requests,json; \
  print(json.dumps(requests.get('https://api.sleeper.app/v1/league/'+os.environ['SLEEPER_LEAGUE_ID']).json()['settings'], indent=2))"
```

## The settings that matter

| setting | value | what it means here |
|---|---|---|
| `waiver_type` | 2 | FAAB bidding, not rolling priority |
| `waiver_budget` | 100 | season budget per team |
| `daily_waivers` | 1 | waivers process **every day**, not once a week |
| `daily_waivers_hour` | 9 | the daily run; **09:00 PT in practice** — see the note below |
| `waiver_clear_days` | 2 | a dropped player sits on waivers this long |
| `reserve_slots` | 2 | IR slots, **separate from** the active roster |
| `reserve_allow_out` | 1 | `Out` is IR-eligible |
| `reserve_allow_doubtful` | 1 | `Doubtful` is IR-eligible |
| `reserve_allow_cov` | 1 | `COV` is IR-eligible |
| `reserve_allow_na` | 0 | **`NA` is NOT IR-eligible** |
| `reserve_allow_sus` | 0 | `Sus` is NOT IR-eligible |
| `trade_deadline` | 11 | |
| `trade_review_days` | 1 | a trade sits open for veto for one day |
| `playoff_week_start` | 15 | |

> **On the hour.** The raw setting is the integer `9`. The league's observed behaviour is
> that a claim submitted after roughly 09:05 PT has missed that day's run, and an
> already-cleared player is addable instantly. The `9` is recorded here as the raw value;
> the timezone is **observed, not read from the API**, and is the one item on this page
> that is not directly verified.

## 1. On waivers, or a free agent?

This decides whether you **bid** or just **click Add**, and getting it wrong wastes a day.

- A player dropped **within the last `waiver_clear_days` (2) days** is *on waivers*. Taking
  him needs a **FAAB bid**, resolved at the next 09:00 daily run. Competing bids are
  sealed; highest wins.
- Anyone else — never rostered this season, or dropped more than 2 days ago — is a **free
  agent**. He costs **$0** and the add is **instant**.

**The UI tell, which is faster than reasoning about dates:** a **bid box** means waivers; an
**"Add" button** means free agent. Trust the button.

The practical consequence: a player you lost a bid on can often simply be *added for free*
two days later, if nobody else took him. Losing a bid is not always losing the player.

## 2. Only one of several claims should land

You often want *one of* several alternatives — any decent QB, say — and you must not end up
with all of them.

**With a full active roster:** give **every** alternative claim the **same drop**. The first
claim that succeeds consumes that drop; the rest then fail on roster space. You get exactly
one, and you choose the priority order by bid size.

**With an open active slot** (including one freed by moving a player to IR): no drop is
needed, and the open slot does the same job — the first claim fills it and the rest fail.

This is a mechanic, not a trick: it works because claims resolve in order within the same
daily run and each one re-checks roster legality.

## 3. IR slots, and the roster math that follows

`roster_positions` is **19 active spots** — 13 starters (`QB, RB, RB, WR, WR, TE, FLEX,
FLEX, FLEX, K, DL, LB, DB`) plus 6 bench — and `reserve_slots` adds **2 IR spots on top**.
So a legal roster can hold **21 players when 2 are on IR**.

`decisions.ACTIVE_ROSTER_LIMIT = 19` is the *active* count, and the engine's
`_active_count()` excludes anyone flagged `on_ir`.

**Why this matters for claims:** moving an `Out`/`Doubtful`/`COV` player to IR frees an
active slot, so **a claim that would have needed a drop no longer does**. Check the IR slots
before dropping anyone. (This is exactly what made a free-agent QB claim cost nothing in
week 3 of 2026, where the naive read was that a trade was needed to fill the hole.)

**The `NA` trap:** `reserve_allow_na = 0`, so a player listed `NA` **cannot** be stashed on
IR even though the engine treats `NA` as an absence
(`SIM_CONFIG['INITIAL_ABSENCE_STATUSES']` is `('IR','PUP','Out','Sus','DNR','NA')`). Such a
player occupies an **active** slot while contributing nothing. `Sus` is the same. See the
note at `fantasy_sim/config.py` on `reserve_allow_na`.

## Where the model's view lives, and where it does not

- These settings are **not** cached in `data/current/`. Nothing in the pipeline reads them;
  they are a human reference, re-verified with the snippet at the top.
- `remaining_faab` per team **is** synced (`league_standings.json`), derived as
  `100 - waiver_budget_used`.
- What a claim is *worth* is a different question, answered by
  `py -3.10 -m scripts.evaluate_move` (paired evaluation) and
  `py -3.10 -m scripts.waiver_targets`. FAAB amounts are deliberately **unpriced** by the
  simulation — see F31 in `docs/AUDIT_PLAN.md` for why a budget delta run through the
  paired evaluation would systematically report ~zero.
