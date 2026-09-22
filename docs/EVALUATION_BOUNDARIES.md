# Evaluation boundaries — 2026 season

**What this is.** `SEASON_2026_EVALUATION.md` pre-commits the criteria the season is
judged on. It is hashed and CI-guarded and is **not** edited after kickoff — deliberately.
This file is the companion it cannot contain: the dated points at which the *model itself*
changed underneath those criteria, so the January analysis partitions the season correctly
instead of pooling across two different instruments.

Created 2026-09-22 (F56). Append only; never rewrite a recorded boundary.

---

## Boundary 1 — IDP scoring change (F49)

**What changed.** The league's IDP categories stack: one solo sack was worth
`idp_sack 4.0 + idp_tkl_loss 2.0 + idp_tkl_solo 1.5 + idp_qb_hit 1.0 = 8.5` — more than a
receiving touchdown. The league voted to cut `idp_sack` to **2.0** and `idp_qb_hit` to
**0.5**, taking a solo sack to **6.0**, a 29% cut.

**Recorded at.** `bb80127` — *F49: mid-season IDP scoring change — the evaluation boundary*

**Effective date.** **Not yet live as of 2026-09-22.** Verified against the league object
that same day: still 4.0 / 1.0. **Whoever observes the change take effect must append the
first `synced_at` that carries the new values to this file.** `sync.py:916` reads scoring
live from the league object, so no code change marks it — the only trace will be in the
baselines and in the sidecar's timestamp.

**What the January analysis must do.**
- **Criterion 1 (calibration): PARTITION.** Quotes made before the change were correct
  under the rules that applied when they were made. Pooling mixes two games.
- **Criterion 2 (points-for, top third): UNAFFECTED.** It is relative; every team's IDP
  scoring falls together.
- **Criterion 3 (interval coverage): THE TRAP.** Cutting the fattest tail in the scoring
  system NARROWS real team-week dispersion. Coverage is already too tight
  (`cover80 = 0.654` against a nominal 0.80). Post-change coverage can therefore *improve*
  for a reason that has nothing to do with the model improving. **Compare pre-change
  coverage to pre-change baseline only.**

---

## Boundary 2 — ESPN blend restoration and blend-coverage fix (F52 + F54)

**What changed.** Two faults, fixed together and taking effect at the same re-sync.

- **F52** — `fetch_espn_projection_data` never passed `week` to `free_agents()`, so
  `espn_api` returned the inactive dummy league's `current_week` (0) plus week 1. The ESPN
  half of the 50/50 mean, the `source_disagreement` epistemic signal, and F29's K/IDP
  subscore channel were all dead from week 2.
- **F54** — the Bayesian posterior reached only **157 of ~1,140** players, because it was
  fed from matchup payloads that by design contain only rostered players. Replacement
  level was also computed three lines *before* the blend that should inform it.

**Recorded at.** `af29f58` + `a8d7a8c` (F52), `31f6649` + `143b041` (F54), released as
**v6.0.0** (MAJOR, goldens regenerated on week06).

**Effective sync — the boundary itself:**

```
2026-09-20T14:48:18Z   espn    0/149   blend OFF   <- last pre-fix sync
2026-09-20T16:59:41Z   espn  110/150   blend ON    <- BOUNDARY
```

Read directly from `data/logs/sync_provenance.jsonl`; no hand-matching against `git log`.

**What the January analysis must do.**
- **PARTITION criterion 1 at `2026-09-20T16:59:41Z`.** Quotes before it were made by a
  model running on one un-blended projection source with default epistemic rates for
  ~750 of ~1,140 players. That is a different instrument, not a worse day.
- Week 1 was blended (F52's bug only bit from week 2). **Week 2 is the mixed week** — it
  contains 20 pre-fix syncs and 4 post-fix. Use the *canonical* row for the week, and
  record which side of the boundary it fell on.
- Replacement levels moved at the same instant (RB 11.10 → 10.47, WR 10.14 → 9.72,
  LB 11.06 → 11.80, DB 9.24 → 9.72), so **any VORP comparison across this boundary is
  invalid** without re-deriving both sides.

---

## The two boundaries do not coincide

This is the whole reason the file exists. Boundary 2 landed **2026-09-20**; boundary 1
had not landed at all as of 2026-09-22. A single "before/after" split of the season is
wrong for at least one of them. Criterion 1 needs **three** segments once boundary 1
arrives:

| segment | blend | IDP scoring |
|---|---|---|
| week 1 → 2026-09-20T14:48Z | ESPN dead from wk 2; posterior on 157 players | 8.5/sack |
| 2026-09-20T16:59Z → boundary 1 | restored | 8.5/sack |
| boundary 1 → season end | restored | 6.0/sack |

---

## How to reconstruct any boundary

`data/logs/sync_provenance.jsonl` carries one row per sync, joined to
`projection_log.jsonl` on `synced_at`:

```
synced_at, git_commit, schema_version, season, week, espn_rows, total_rows
```

- `schema_version: 0` with `git_commit: null` and `backfilled: true` marks a row
  **reconstructed** by `scripts/backfill_sync_provenance` from the projection log. The
  commit is genuinely unrecoverable for those — nothing recorded it — and inventing one
  would be worse than the gap.
- `schema_version: 1` and a real `git_commit` marks a row **observed** at sync time.
- All 77 syncs up to and including 2026-09-22 are backfilled; everything after is
  observed.

`espn_rows` is the field that cannot be recovered any other way, and it is what makes
boundary 2 mechanical rather than archaeological.
