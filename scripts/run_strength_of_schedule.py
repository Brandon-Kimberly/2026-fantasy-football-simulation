#!/usr/bin/env python3
"""
Builds the strength-of-schedule report: a JSON report, an NFL-team x week heatmap, a
team-ranking bar chart, and a fantasy-roster x week heatmap -- all derived from the simulation
engine's own _compute_week_environment (never reimplemented), all stamped with the engine's
current week. See fantasy_sim/strength_of_schedule.py's module docstring for the two layers.

Usage:
    python -m scripts.run_strength_of_schedule
"""
from fantasy_sim.strength_of_schedule import build_strength_of_schedule_report


def main():
    team_grid, roster_grid, week = build_strength_of_schedule_report()
    print(f"Week {week}: strength-of-schedule built for {len(team_grid)} NFL teams, "
          f"{len(roster_grid)} fantasy rosters.")


if __name__ == "__main__":
    main()
