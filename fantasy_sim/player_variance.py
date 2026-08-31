"""
Boom/bust and floor/ceiling for every currently-rostered player, built from the SAME simulated
weekly scores the engine's own lineup decisions and exports are based on -- not a separate,
resampled model. Unlike fantasy_sim.positional_tiers and fantasy_sim.strength_of_schedule (both
standalone, reading only sync-produced data/ files), this module needs data that only exists
DURING a real run_simulation() call: FantasySimulationEngine.player_weekly_scores, a
(total_sims, 14) NaN-filled array per rostered player, populated inside run_simulation's
existing per-player scoring loop from final_score_by_name -- every rostered player's score for
that (sim, week), computed BEFORE the lineup optimizer runs, so a bench week is captured the
same as a start (accumulating only from `starters` would introduce real selection bias: being
benched correlates with a lower expected_pre, which correlates with the same environment
factors that affect the realized score). A bye week or an injury-clocked week is NaN, a
structural absence, never a fabricated zero -- the exact bug class AUDIT_PHASE_1_FINDINGS.md
finding 4 already caught for the team-level equivalent (global_weekly_scores).

Because this module needs the accumulator, not just data/ files, it is invoked from
scripts/run_simulation.py right after engine.run_simulation() returns, reading the engine
instance directly -- never re-simulating, and never touching export_and_visualize (so it cannot
affect the golden master; verified empirically, see AUDIT_PLAN.md's F11 entry).

Only the 156 currently-rostered players get a report -- the engine never simulates anyone else,
so this has no waiver-wire equivalent of positional_tiers' full 928-player pool.

Two charts per fantasy team (8 teams), plus one JSON summary for the whole league:
  - boom/bust: a violin plot of every rostered player's full simulated weekly-score
    distribution, colored by position, sorted by median.
  - floor/ceiling: a range-bar chart, p10-p90 per player with p50 marked -- the same "floating
    range bar" visual language already used for positional_tiers' tier-summary chart.
  - JSON: per player, mean/std/p10/p25/p50/p75/p90/min/max, a histogram (bin edges + counts,
    NOT raw per-sim draws -- kept small deliberately), and avg_weeks_observed / low_n. Rendering
    itself still uses the full in-memory arrays (discarded after this function returns, never
    written to disk) -- the "no raw draws" constraint is about the exported artifact's size, not
    what's available to draw the chart in the same process.

Low-sample-size players (traded away, injured most of the season -- avg_weeks_observed is
driven almost entirely by the model's own injury simulation, not by being benched, since bench
weeks are still counted) get the SAME visual treatment positional_tiers gives injured players:
hatching on the floor/ceiling bar (identical bar primitive, direct reuse), and an explicit
"(n=X.X weeks)" annotation on the boom/bust violin's axis label, since hatching an individual
seaborn violin body is fiddlier than a bar and a real count is more informative than a binary
flag anyway. MIN_WEEKS_THRESHOLD is a CHOSEN display threshold, originally 6 ("less than half
the season"), moved to 9 after checking it against the real observed-weeks distribution -- see
the constant's own comment for what that check found and why.
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from fantasy_sim.config import normalize_position
from fantasy_sim.storage import (
    boom_bust_chart_path, floor_ceiling_chart_path, player_variance_report_path, save_json,
)

# Originally chosen as 6 ("less than half the season") before looking at real data, then
# checked against the actual week-1 distribution across all 156 rostered players and moved to
# 9: the real data has a clean gap, not a smooth curve. Four players -- Alec Pierce, Josh
# Jacobs, Zach Charbonnet, Micah Parsons, all carrying real IR/PUP/Questionable/Doubtful status
# already named in AUDIT_PLAN.md's F4 entry -- cluster at 6.4-7.1 avg weeks observed; every
# other rostered player sits at 10.8+ with nothing in between. A threshold of 6 flagged ZERO
# players (verified, not assumed -- the first real run exposed this before it shipped); 9 sits
# in the middle of the real gap and correctly flags exactly the four players the model's own
# injury simulation is actually treating as significantly less available. See AUDIT_PLAN.md F11.
MIN_WEEKS_THRESHOLD = 9

_HISTOGRAM_BINS = 15


def _flatten_observed(scores_2d):
    """1D array of every non-NaN score for one player, flattened across sims and weeks."""
    return scores_2d[~np.isnan(scores_2d)]


def _player_summary(scores_2d, total_sims, n_bins=_HISTOGRAM_BINS):
    """mean/std/percentiles/histogram from observed (non-NaN) weeks only -- see module
    docstring for why bye/injury weeks must never be treated as zero. None (not 0.0) for every
    numeric field when a player has zero observed weeks; a histogram needs at least one value
    to have a defined range, so it's None there too rather than an empty/degenerate one."""
    flat = _flatten_observed(scores_2d)
    n_observed = int(flat.size)
    avg_weeks_observed = n_observed / total_sims
    low_n = avg_weeks_observed < MIN_WEEKS_THRESHOLD

    if n_observed == 0:
        return {
            'mean': None, 'std': None, 'p10': None, 'p25': None, 'p50': None, 'p75': None,
            'p90': None, 'min': None, 'max': None, 'n_observed': 0,
            'avg_weeks_observed': 0.0, 'low_n': True, 'histogram': None,
        }

    counts, edges = np.histogram(flat, bins=n_bins)
    return {
        'mean': float(np.mean(flat)), 'std': float(np.std(flat)),
        'p10': float(np.percentile(flat, 10)), 'p25': float(np.percentile(flat, 25)),
        'p50': float(np.percentile(flat, 50)), 'p75': float(np.percentile(flat, 75)),
        'p90': float(np.percentile(flat, 90)),
        'min': float(np.min(flat)), 'max': float(np.max(flat)),
        'n_observed': n_observed, 'avg_weeks_observed': float(avg_weeks_observed),
        'low_n': low_n, 'histogram': {'bin_edges': edges.tolist(), 'counts': counts.tolist()},
    }


def render_floor_ceiling_chart(fantasy_team, entries, week):
    """p10-p90 range bar per player, p50 marked, sorted by median descending. Players with zero
    observed weeks (p50 is None) are excluded from the bars -- there is nothing to draw a range
    for -- and named in a footnote instead of silently vanishing."""
    plotted = [e for e in entries if e['p50'] is not None]
    excluded = [e['name'] for e in entries if e['p50'] is None]
    plotted.sort(key=lambda e: e['p50'], reverse=True)

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(10, max(4.0, 0.4 * len(plotted) + 1.5)))
    if plotted:
        ordered = plotted[::-1]  # best player on top of a horizontal barh
        labels = [e['name'] for e in ordered]
        lefts = [e['p10'] for e in ordered]
        widths = [e['p90'] - e['p10'] for e in ordered]
        palette = sns.color_palette("mako", len(ordered))
        bars = ax.barh(labels, widths, left=lefts, color=palette, edgecolor='black',
                        linewidth=0.6, height=0.6)
        for bar, e in zip(bars, ordered):
            ax.plot([e['p50'], e['p50']], [bar.get_y(), bar.get_y() + bar.get_height()],
                    color='black', linewidth=2.2)
            if e['low_n']:
                bar.set_hatch('///')
                bar.set_alpha(0.6)

    ax.set_title(f"Week {week} Floor/Ceiling -- {fantasy_team} "
                 f"(p10-p90, black line = median; hatched = fewer than {MIN_WEEKS_THRESHOLD} "
                 f"weeks observed)", fontsize=12, fontweight='bold', pad=15)
    ax.set_xlabel("Simulated Weekly Score", fontweight='bold')
    if excluded:
        fig.text(0.5, 0.01, f"No games observed this window (excluded above): {', '.join(excluded)}",
                  ha='center', fontsize=8, style='italic', color='#444444')
    sns.despine(top=True, right=True)
    plt.tight_layout()
    plt.savefig(floor_ceiling_chart_path(fantasy_team, week), dpi=300)
    plt.close()
    return fig


def render_boom_bust_chart(fantasy_team, entries, raw_scores, week):
    """Violin plot of every rostered player's full simulated weekly-score distribution, colored
    by position, sorted by median. Players with zero observed weeks are excluded (a violin
    needs at least one value); a low-n player's x-axis label is annotated with the real average
    number of weeks observed rather than hatched (see module docstring)."""
    plotted = [e for e in entries if raw_scores.get(e['name'], np.array([])).size > 0]
    plotted.sort(key=lambda e: (e['p50'] if e['p50'] is not None else -np.inf), reverse=True)

    def _label(e):
        if e['low_n']:
            return f"{e['name']} (n={e['avg_weeks_observed']:.1f} weeks)"
        return e['name']

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(max(10, 0.55 * len(plotted)), 6))
    if plotted:
        order = [_label(e) for e in plotted]
        rows = [
            {'PlayerLabel': _label(e), 'Score': v, 'Position': e['pos']}
            for e in plotted for v in raw_scores[e['name']]
        ]
        df = pd.DataFrame(rows)
        sns.violinplot(x='PlayerLabel', y='Score', hue='Position', data=df, order=order,
                        dodge=False, density_norm='width', inner='quartile', ax=ax)
        if ax.get_legend() is not None:
            ax.legend(title="Position", loc='upper right', fontsize=8)
        plt.setp(ax.get_xticklabels(), rotation=45, ha='right', fontsize=8)

        # The engine hard-caps any single draw at SIM_CONFIG['MAX_REALISTIC_WEEKLY_SCORE']
        # (80.0), so a rare week genuinely reaches it -- matplotlib's default auto-scaling then
        # stretches the whole y-axis to fit that one point, compressing the real distribution
        # (which sits far lower for nearly every player) into a sliver at the bottom. Capped
        # here at the ~99th percentile of every score actually plotted on THIS chart (pooled
        # across all shown players, computed from the real values, not a fixed constant like
        # MAX_REALISTIC_WEEKLY_SCORE) -- verified against the real week-1 data before landing on
        # 99%: it sat at ~50.7 against a true max near 80, i.e. the auto-scaled axis was wasting
        # its top third on well under 1% of density.
        pooled = np.concatenate([raw_scores[e['name']] for e in plotted])
        y_cap = float(np.percentile(pooled, 99))
        ax.set_ylim(0, y_cap * 1.05)
        fig.text(0.5, 0.01,
                 "Y-axis capped at the 99th percentile of this chart's pooled scores -- a small "
                 "fraction of rare high-end weeks extend above this range.",
                 ha='center', fontsize=8, style='italic', color='#444444')

    ax.set_title(f"Week {week} Boom/Bust -- {fantasy_team} "
                 f"(full simulated weekly-score distribution)",
                 fontsize=12, fontweight='bold', pad=15)
    ax.set_xlabel("")
    ax.set_ylabel("Simulated Weekly Score", fontweight='bold')
    sns.despine(top=True, right=True)
    plt.tight_layout()
    plt.savefig(boom_bust_chart_path(fantasy_team, week), dpi=300)
    plt.close()
    return fig


def build_player_variance_report(engine):
    """Entry point: called from scripts/run_simulation.py right after engine.run_simulation()
    returns (never re-simulating). Reads engine.player_weekly_scores/rosters/meta/current_week
    directly; writes the JSON report and both charts per fantasy team."""
    week = engine.current_week
    total_sims = next(iter(engine.player_weekly_scores.values())).shape[0]

    report = {}
    for team, players in engine.rosters.items():
        entries = []
        for name in players:
            scores_2d = engine.player_weekly_scores.get(name)
            if scores_2d is None:
                continue
            summary = _player_summary(scores_2d, total_sims)
            pos = normalize_position(engine.meta.get(team, {}).get(name, {}).get('pos', 'FLEX'))
            entries.append({'name': name, 'pos': pos, **summary})
        report[team] = entries

    save_json(player_variance_report_path(week), report)

    for team, entries in report.items():
        raw_scores = {
            e['name']: _flatten_observed(engine.player_weekly_scores[e['name']]) for e in entries
        }
        render_boom_bust_chart(team, entries, raw_scores, week)
        render_floor_ceiling_chart(team, entries, week)

    return report
