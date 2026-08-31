#!/usr/bin/env python3
"""
Builds statistically-derived positional tiers from the current player pool
(data/player_baselines.json): a JSON report, one PNG chart per position, and one sortable HTML
ranked table per position -- all stamped with the current week (same source as the simulation
engine's own current_week: data/league_state.json) so re-running in a later week doesn't
overwrite an earlier week's tiers. See fantasy_sim/positional_tiers.py's module docstring for
the tiering method.

Usage:
    python -m scripts.run_positional_tiers
"""
from fantasy_sim.positional_tiers import build_positional_tier_report
from fantasy_sim.storage import LEAGUE_STATE_FILE, load_json


def main():
    current_week = load_json(LEAGUE_STATE_FILE).get('current_week', 1)
    report = build_positional_tier_report(current_week)
    for pos, players in sorted(report.items()):
        n_tiers = max((p['tier'] for p in players), default=0)
        print(f"{pos}: {len(players)} players, {n_tiers} tiers")


if __name__ == "__main__":
    main()
