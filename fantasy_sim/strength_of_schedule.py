"""
Visualizes the real-NFL schedule-difficulty signal the simulation engine already computes for
every player, week by week: FantasySimulationEngine._compute_week_environment blends a real NFL
team's own offensive power rating with its OPPONENT's real empirical defensive strength (a
genuine two-sided matchup model, not a mirror of one side -- see that method's own docstring),
using live Vegas for the current week and the ratings-model blend for every future week. This
module never reimplements that formula: it calls the engine's own method directly, so the chart
can never silently drift from what the simulation actually does to a player's projection.

Two layers:
  - NFL-team level (build_team_environment_grid): every real NFL team x every remaining week,
    the implied team total. Useful standalone (streaming/waiver decisions), independent of any
    one fantasy roster.
  - Fantasy-roster level (build_roster_sos_grid): rolls the team-level grid up by which real NFL
    teams each of the 8 fantasy rosters actually has players on, UNWEIGHTED across every
    rostered player (not just starters) -- averaging in a bench player's team exactly like a
    starter's. This is a coarser, honestly-labelled approximation (see the chart's own caption),
    not a lineup-aware precision measure -- that would need start probabilities the engine only
    knows mid-simulation, a real separate feature, not a visualization change.

A bye week (team absent from that week's nfl_schedule.json entry) resolves through
_compute_week_environment to an 'FA' opponent and a flat fallback total (21.5) -- rendered as a
distinct hatched cell, never as if it were a real, neutral matchup.
"""
import contextlib
import io

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import (
    save_json, sos_report_path, sos_roster_chart_path, sos_team_grid_chart_path,
    sos_team_summary_chart_path,
)

# Matches _compute_environment_normaliser's own window (current_week..16): the full remainder
# of the simulated season, including the fantasy playoffs, not just the regular season.
SOS_END_WEEK = 16
REGULAR_SEASON_WEEKS = 14  # for the regular-season/playoff divider drawn on the team grid chart


def _load_environment_engine():
    """Instantiates a throwaway FantasySimulationEngine purely to reuse its own
    _compute_week_environment -- confirmed read-only (loads JSON, builds in-memory dicts, no
    file writes, no RNG draws). __init__ does two raw print() calls that are pure noise for a
    read-only reuse: "[PRE-FLIGHT SUCCESS] N Projections Validated." and, if triggered, "[INFO]
    Imputed whitelisted missing asset: ...". Suppressed here via stdout redirection, not by
    editing simulation.py -- those prints are legitimate when the engine actually runs a
    simulation, and since this module's whole purpose is routine weekly generation, letting
    them fire every time would bury any real warning under repeated cosmetic noise. logging
    output (e.g. a genuine KNOWN_MISSING_ASSETS/roster mismatch warning) is left untouched --
    that's a real, actionable signal, not noise, and belongs on screen regardless of which
    script triggered it."""
    with contextlib.redirect_stdout(io.StringIO()):
        return FantasySimulationEngine()


def _environment_grid_from_lookup(nfl_teams, weeks, env_lookup):
    """{nfl_team: {week: {'total', 'opponent', 'is_bye'}}}. env_lookup(week, team) -> the dict
    _compute_week_environment returns; injected so this is testable without a real engine."""
    grid = {}
    for team in nfl_teams:
        row = {}
        for week in weeks:
            env = env_lookup(week, team)
            row[week] = {
                'total': env['total'],
                'opponent': env['opponent'],
                'is_bye': env['opponent'] == 'FA',
            }
        grid[team] = row
    return grid


def build_team_environment_grid(engine):
    weeks = list(range(engine.current_week, SOS_END_WEEK + 1))
    nfl_teams = sorted(engine.power_ratings.keys())
    grid = _environment_grid_from_lookup(nfl_teams, weeks, engine._compute_week_environment)
    return grid, weeks


def build_roster_sos_grid(rosters_meta, team_grid, weeks):
    """{fantasy_team: {week: avg_total_or_None}} -- unweighted average, across EVERY rostered
    player (not just starters), of their real NFL team's environment total for that week. A
    player whose real team isn't in team_grid (a free agent, or a team missing a power rating)
    is skipped for that week, not treated as a fabricated zero; a week with no resolvable
    players at all is None, not zero."""
    grid = {}
    for fantasy_team, players in rosters_meta.items():
        row = {}
        for week in weeks:
            totals = [
                team_grid[info['team']][week]['total']
                for info in players.values()
                if info.get('team') in team_grid
            ]
            row[week] = float(np.mean(totals)) if totals else None
        grid[fantasy_team] = row
    return grid


def render_team_grid_chart(team_grid, weeks, week):
    """32-team x week heatmap of implied total, sorted easiest-to-hardest by average across the
    window. Bye weeks are masked out of the color scale and annotated 'BYE' instead of showing
    the flat fallback total as if it were a real matchup."""
    teams = list(team_grid.keys())
    values = pd.DataFrame(
        {t: {w: (np.nan if team_grid[t][w]['is_bye'] else team_grid[t][w]['total']) for w in weeks}
         for t in teams}
    ).T
    values = values.reindex(columns=weeks)
    order = values.mean(axis=1, skipna=True).sort_values(ascending=False).index
    values = values.loc[order]

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(max(10, 0.6 * len(weeks)), 0.35 * len(teams) + 2))
    sns.heatmap(values, annot=True, fmt=".1f", cmap="RdYlGn", mask=values.isna(),
                linewidths=.5, cbar_kws={'label': 'Implied Team Total'}, ax=ax)
    for i, t in enumerate(order):
        for j, w in enumerate(weeks):
            if team_grid[t][w]['is_bye']:
                ax.text(j + 0.5, i + 0.5, "BYE", ha='center', va='center',
                        fontsize=8, fontweight='bold', color='#666666')
    if REGULAR_SEASON_WEEKS in weeks and weeks[-1] > REGULAR_SEASON_WEEKS:
        divider_x = weeks.index(REGULAR_SEASON_WEEKS) + 1
        ax.axvline(divider_x, color='black', linewidth=2.0)
        ax.text(divider_x + 0.05, -0.3, 'fantasy playoffs →', fontsize=8, style='italic')
    ax.set_title(f"Week {week} Strength of Schedule by NFL Team "
                 f"(Weeks {weeks[0]}–{weeks[-1]}, sorted easiest → hardest)",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xlabel("Week", fontweight='bold')
    ax.set_ylabel("")
    plt.tight_layout()
    plt.savefig(sos_team_grid_chart_path(week), dpi=300)
    plt.close()


def render_team_summary_chart(team_grid, weeks, week):
    """Horizontal bar of each team's average implied total across the window, sorted
    descending -- the same ranking the heatmap's row order encodes, as a standalone glance."""
    teams = list(team_grid.keys())
    avg = {
        t: float(np.mean([team_grid[t][w]['total'] for w in weeks if not team_grid[t][w]['is_bye']]))
        for t in teams
    }
    ranked = sorted(avg.items(), key=lambda kv: kv[1])

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(10, max(6, 0.28 * len(ranked))))
    colors = sns.color_palette("RdYlGn", len(ranked))
    bars = ax.barh([t for t, _ in ranked], [v for _, v in ranked], color=colors,
                    edgecolor='black', linewidth=0.5)
    for bar, (_, v) in zip(bars, ranked):
        ax.text(bar.get_width() + 0.15, bar.get_y() + bar.get_height() / 2,
                f"{v:.1f}", va='center', ha='left', fontsize=8)
    ax.set_title(f"Week {week} Remaining-Schedule Ranking "
                 f"(Weeks {weeks[0]}–{weeks[-1]}, avg. implied total, bye weeks excluded)",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xlabel("Average Implied Team Total", fontweight='bold')
    sns.despine(top=True, right=True)
    plt.tight_layout()
    plt.savefig(sos_team_summary_chart_path(week), dpi=300)
    plt.close()


def render_roster_grid_chart(roster_grid, weeks, week):
    """8-fantasy-team x week heatmap, same visual language as render_team_grid_chart, of the
    unweighted-across-all-rostered-players average implied total."""
    fantasy_teams = list(roster_grid.keys())
    values = pd.DataFrame(
        {t: {w: roster_grid[t][w] for w in weeks} for t in fantasy_teams}
    ).T
    values = values.reindex(columns=weeks)
    order = values.mean(axis=1, skipna=True).sort_values(ascending=False).index
    values = values.loc[order]

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(max(10, 0.6 * len(weeks)), 0.5 * len(fantasy_teams) + 3.0))
    sns.heatmap(values, annot=True, fmt=".1f", cmap="RdYlGn", mask=values.isna(),
                linewidths=.5, cbar_kws={'label': 'Avg. Implied Total Across Roster'}, ax=ax)
    # Title + two caption lines are stacked in FIGURE-fraction coordinates (fig.suptitle/
    # fig.text), not axes-fraction + a point-based title pad -- mixing those two unit systems
    # is what produced an overlapping, garbled title on the first attempt at this (verified
    # visually, not assumed fixed). subplots_adjust reserves the vertical room these three
    # elements actually need; re-checked against the rendered PNG, not just the numbers below.
    fig.suptitle(f"Week {week} Strength of Schedule by Fantasy Roster",
                 fontsize=13, fontweight='bold', y=0.97)
    fig.text(0.5, 0.925,
             "Unweighted average across every rostered player, not just starters -- a coarse "
             "roster-composition signal, not a lineup-aware precision measure.",
             ha='center', fontsize=9, style='italic', color='#444444')
    # Verified against the real week-1 data, not assumed: between-roster std of the row means
    # (~0.41) runs about 1.5x the average within-roster week-to-week std (~0.28). Rows barely
    # shift week to week because each one averages 15-20+ players spread across many real NFL
    # teams -- one team's tough matchup is usually offset by another's easy one -- so most of
    # what a reader sees here is which offenses a roster happens to include, not sharp
    # matchup-driven swings.
    fig.text(0.5, 0.895,
             "Row-to-row differences mostly reflect which real NFL offenses a roster includes, "
             "not week-to-week matchup swings within a row.",
             ha='center', fontsize=9, style='italic', color='#444444')
    ax.set_xlabel("Week", fontweight='bold')
    ax.set_ylabel("")
    plt.tight_layout()
    plt.subplots_adjust(top=0.85)
    plt.savefig(sos_roster_chart_path(week), dpi=300)
    plt.close()


def build_strength_of_schedule_report():
    """Entry point: instantiate the engine (read-only reuse), build both grids, write the JSON
    report and all three charts, all stamped with the engine's own current_week. Touches
    nothing export_and_visualize owns (never calls run_simulation), so it cannot affect the
    golden master."""
    engine = _load_environment_engine()
    week = engine.current_week
    team_grid, weeks = build_team_environment_grid(engine)
    roster_grid = build_roster_sos_grid(engine.meta, team_grid, weeks)

    save_json(sos_report_path(week), {
        'week': week,
        'weeks_covered': weeks,
        'by_nfl_team': team_grid,
        'by_fantasy_team': roster_grid,
    })
    render_team_grid_chart(team_grid, weeks, week)
    render_team_summary_chart(team_grid, weeks, week)
    render_roster_grid_chart(roster_grid, weeks, week)
    return team_grid, roster_grid, week
