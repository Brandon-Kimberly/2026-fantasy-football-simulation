# Changelog

Newest first. Each entry is the release's headline; the full six-section notes (what's
in it, audit counts, hardware/season blockers, backlog, and what the tag does *not*
claim) live on the linked release. MAJOR means the model's predictions changed
materially (see the release policy in `CLAUDE.md`).

## [v4.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v4.0.0) — 2026-09-03 (MAJOR)

FAAB behavior calibrated to the real league: bid sizes fitted to the 99 attributed 2025
claims, an upgrade-bidding channel, and a two-parameter per-manager model — simulated
spending moved from ~31% of real to inside the pre-declared [650, 800] band. Trade
evaluator records FAAB transfers as explicitly unpriced. Also: the weekly report's
visual redesign, and F30's capture-rate measurement (measured and held).

## [v3.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v3.0.0) — 2026-09-02 (MAJOR)

K and IDP players gain a real epistemic signal: both projection sources' stat lines
scored under this league's own settings, with the disagreement driving uncertainty.
The sync stage gets its own byte-exact golden, closing the blind spot v2.0.0 exposed.

## [v2.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v2.0.0) — 2026-09-02 (MAJOR)

First measured IDP variance constants (DL/LB/DB derived from full-NFL 2025 stats,
replacing placeholder fallbacks) and K re-fit under the league's current kicker rules.
The engine goldens were byte-identical through the change — the finding that sync-time
constants sit upstream of what they pin, now written into the release policy itself.

## [v1.0.0](https://github.com/Brandon-Kimberly/2026-fantasy-football-simulation/releases/tag/v1.0.0) — 2026-09-02

The audited baseline: Phases 0–7 complete, the golden master, the real-data backtest
gate, and the audit trail that defines this project. Tagged at the F27 commit.
