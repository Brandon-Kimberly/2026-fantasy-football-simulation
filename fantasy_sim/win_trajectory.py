"""
Re-visualizes expected_cumulative_wins_by_week -- already computed and exported for every team,
every week, inside syndicate_comprehensive_matrix_week_N.json's weekly_trajectories field -- as
one overlay line chart across all 8 fantasy teams on shared axes, instead of the engine's own
Week_N_All_Teams_Trajectories.png (a 2x4 grid of per-team subplots with percentile bands).

No new computation, no engine instantiation: this is purely a re-visualization of a number the
engine already exports, read from a single already-written JSON file. It is deliberately NOT a
duplicate of the existing per-team chart -- that one emphasizes each team's OWN uncertainty
(p01-p99 bands around its mean); this one emphasizes direct TEAM-TO-TEAM comparison (who is
pulling ahead, and in which week), which eight separate small subplots make harder to read at a
glance than one shared axis does.

This closes out the "wins-over-time" half of the originally-scoped trajectory item. The other
half -- playoff-odds-over-REAL-calendar-time, comparing successive weeks' live_season_forecast
files -- is a genuinely different thing (this chart's x-axis is simulated week WITHIN one run;
that one's x-axis would be real week ACROSS runs) and stays deliberately unbuilt: as of this
module's creation only one week (week 1) of real forecast history exists, not enough to plot
even a two-point line. See AUDIT_PLAN.md for when to revisit.
"""
import matplotlib.pyplot as plt
import seaborn as sns

from fantasy_sim.config import REGULAR_SEASON_WEEKS, SIM_CONFIG
from fantasy_sim.storage import load_json, save_chart, syndicate_comprehensive_matrix_path, win_trajectory_chart_path


def extract_trajectories(ai_matrix):
    """{team: [expected cumulative wins for week 1..14]} straight from the already-exported
    weekly_trajectories field -- no computation, just a reshape for plotting."""
    return {
        team: data["expected_cumulative_wins_by_week"]
        for team, data in ai_matrix["weekly_trajectories"].items()
    }


def render_win_trajectory_chart(trajectories, week):
    """One line per team, sorted by final-week value so the legend order matches the visual
    ranking. Break-even reference line matches export_and_visualize's own convention (half of
    REGULAR_SEASON_WEEKS * decisions_per_week, where decisions_per_week is 2 under median
    scoring and 1 without it -- read from SIM_CONFIG rather than hardcoded, so this stays
    correct if the league's format or a backtest's MEDIAN_SCORING_ENABLED override changes)."""
    decisions_per_week = 2 if SIM_CONFIG.get('MEDIAN_SCORING_ENABLED', True) else 1
    break_even = REGULAR_SEASON_WEEKS * decisions_per_week / 2.0

    teams = sorted(trajectories.keys(), key=lambda t: trajectories[t][-1], reverse=True)

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(11, 7))
    palette = sns.color_palette("tab10", len(teams))
    for color, team in zip(palette, teams):
        values = trajectories[team]
        weeks = list(range(1, len(values) + 1))
        ax.plot(weeks, values, marker='o', markersize=4, linewidth=2, color=color, label=team)

    ax.axhline(break_even, color='black', linestyle='--', linewidth=1.5, alpha=0.7,
               label=f'.500 Break-Even ({break_even:g} Wins)')
    ax.set_title(f"Week {week} Expected Wins Trajectory (Simulated Season)",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xlabel("Simulated Week", fontweight='bold')
    ax.set_ylabel("Expected Cumulative Wins", fontweight='bold')
    ax.legend(loc='upper left', bbox_to_anchor=(1.02, 1.0), fontsize=9, frameon=True)
    sns.despine(top=True, right=True)
    plt.tight_layout()
    save_chart(win_trajectory_chart_path(week), dpi=300, bbox_inches='tight')
    plt.close()
    return fig


def build_win_trajectory_chart(week):
    """Entry point: reads this week's already-written syndicate_comprehensive_matrix file and
    renders the overlay chart. Never touches export_and_visualize or run_simulation, so it
    cannot affect the golden master."""
    ai_matrix = load_json(syndicate_comprehensive_matrix_path(week))
    trajectories = extract_trajectories(ai_matrix)
    render_win_trajectory_chart(trajectories, week)
    return trajectories
