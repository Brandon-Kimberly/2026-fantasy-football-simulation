#!/usr/bin/env python3
"""
Builds the expected-wins-over-simulated-week overlay chart, re-visualizing
expected_cumulative_wins_by_week (already exported inside
data/weeks/week_NN/syndicate_comprehensive_matrix_week_N.json by a real run_simulation() call)
across all 8 fantasy teams on one shared axis. Reads that file only -- no re-simulation, no
engine instantiation. Run this any time after scripts.run_simulation has produced that week's
export.

Usage:
    python -m scripts.run_win_trajectory
"""
from fantasy_sim.storage import LEAGUE_STATE_FILE, load_json
from fantasy_sim.win_trajectory import build_win_trajectory_chart


def main():
    current_week = load_json(LEAGUE_STATE_FILE).get('current_week', 1)
    trajectories = build_win_trajectory_chart(current_week)
    print(f"Week {current_week}: win trajectory built for {len(trajectories)} teams.")


if __name__ == "__main__":
    main()
