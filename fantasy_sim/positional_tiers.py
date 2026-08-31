"""
Statistically-derived positional tiers.

Groups the full player pool from BASELINES_FILE by position (fantasy_sim.config.normalize_position)
and splits each group into tiers using each player's std_epistemic -- the season-long parameter
uncertainty in their true talent level (drawn once per simulated season and held fixed; see
config.py's EPISTEMIC_ERROR_RATES and CLAUDE.md's statistical conventions) -- not std_aleatoric,
which is week-to-week noise redrawn every week and answers a different question ("how much will
this player's score bounce around a fixed mean"), not "how good is this player really".

Tier assignment: within a position, sorted descending by mean, a running ANCHOR is the best
player not yet superseded. A player starts a new tier when the anchor's mean exceeds theirs by
more than TIER_Z combined standard errors (sqrt(anchor_se^2 + player_se^2)); otherwise they join
the anchor's tier and the anchor is unchanged. This is the standard construction for a 1-D
"cannot be statistically distinguished from the best player in this group" clustering.

Simple ADJACENT-pair gap testing (comparing each player only to their immediate predecessor,
not to the tier's anchor) was tried first and rejected -- not on aesthetic grounds, but because
it is underpowered by construction: with EPISTEMIC_ERROR_RATES this large (0.15 for IDP
positions, up to 0.63 for RB) and 100+ densely-packed players per position, a single adjacent
step's gap is almost never bigger than the combined SE of two adjacent players. Verified
directly against the real player pool: adjacent-pair testing collapsed every one of the eight
positions into exactly one tier (or two, for DL). The anchor/cumulative form below is what
actually answers "which players are statistically indistinguishable from each other" instead of
just restating that projections form a smooth curve.

TIER_Z = 1.0 (one combined standard error, ~84% one-sided confidence the anchor is genuinely
better) is a CHOSEN display threshold, not something calibrated against real data -- treat it
the same way as VACATED_VOLUME_CAPTURE_RATE: carried as a documented, unverified constant. It
was picked before looking at how many tiers it produces per position, then confirmed to give
usable (4-10 tier) results across all eight positions -- not adjusted afterward to hit a target
count.

Only the eight positions this league actually starts are tiered (REQUIRED_STARTING_SLOTS minus
'FLEX', which is a lineup slot, not a position). Team defenses (raw pos "DEF") are excluded on
purpose: this is an IDP league and REQUIRED_STARTING_SLOTS has no DEF slot at all -- Sleeper's
player export includes all 32 NFL team defenses regardless, but they are not rosterable here.
"""
import math

import matplotlib.pyplot as plt
import seaborn as sns

from html import escape

from fantasy_sim.config import normalize_position, REQUIRED_STARTING_SLOTS, display_player_name
from fantasy_sim.storage import (
    BASELINES_FILE, ensure_dir_for, load_json, save_json, tier_chart_path,
    positional_tiers_report_path, positional_tiers_table_path,
)

TIER_Z = 1.0

TIERED_POSITIONS = set(REQUIRED_STARTING_SLOTS) - {'FLEX'}

# Charts render only a prefix of each position for legibility (JSON export is never capped).
# The prefix is chosen by TIER count, not a flat player count: a fixed top-N cap picked before
# knowing where tier boundaries fall turned out to hide them entirely for the high-CV positions
# it would matter most for -- with EPISTEMIC_ERROR_RATES this large, tier 2 doesn't start until
# rank 44 for RB and rank 58 for WR, and QB never reaches a second tier across all 32 rostered
# QBs (verified against the real player pool, not assumed). A flat top-30 cap would have shown
# a monochrome, uninformative chart for exactly the positions where the "is there real
# separation here" question is most interesting. Showing every player through
# CHART_MIN_TIERS_SHOWN tiers (or the whole group, if it never reaches that many) makes the
# chart's extent follow the same statistics it's plotting, capped at CHART_MAX_ROWS purely so a
# large tier-1-through-4 group doesn't produce an unreadable chart.
CHART_MIN_TIERS_SHOWN = 4
CHART_MAX_ROWS = 60

# Threshold for calling out "most of this position is one tier" even when a couple of lower
# tiers exist beneath it. Chosen after observing a clean, large gap in the real data between
# positions that are dominated by tier 1 (QB 100%, WR 95%, K 85%, RB 72%) and positions that
# aren't (TE 42%, DB 18%, LB 15%, DL 8%) -- any threshold between 42% and 72% draws the same
# line; 60% is a round number inside that gap, not tuned to include or exclude a specific
# position.
MAJORITY_TIER1_THRESHOLD = 0.60


def _position_group(raw_pos):
    """Position bucket for tiering, or None if this player isn't in a tiered slot: raw pos
    'DEF' (team defenses, not rosterable in this IDP league), or anything else
    normalize_position sends to the FLEX catch-all (e.g. an unrecognised long-snapper record)."""
    raw = str(raw_pos).upper().strip()
    if raw == 'DEF':
        return None
    pos = normalize_position(raw)
    return pos if pos in TIERED_POSITIONS else None


def compute_tiers(baselines, z=TIER_Z):
    """{position: [player dicts sorted by mean desc, each carrying 'tier' (1 = best) and
    'rank' within the position]}. See module docstring for the anchor/cumulative algorithm."""
    groups = {}
    for name, entry in baselines.items():
        if not isinstance(entry, dict):
            continue
        pos = _position_group(entry.get('pos'))
        if pos is None:
            continue
        groups.setdefault(pos, []).append({
            'name': name,
            'mean': float(entry.get('mean', 0.0)),
            'std_epistemic': float(entry.get('std_epistemic', 0.0)),
            'team': entry.get('team'),
            'bye': entry.get('bye'),
            'injury_status': entry.get('injury_status'),
            'on_ir': bool(entry.get('on_ir', False)),
        })

    report = {}
    for pos, players in groups.items():
        players.sort(key=lambda p: p['mean'], reverse=True)
        anchor = None
        tier = 0
        for rank, p in enumerate(players, start=1):
            if anchor is None:
                tier = 1
                anchor = p
            else:
                combined_se = math.sqrt(anchor['std_epistemic'] ** 2 + p['std_epistemic'] ** 2)
                if (anchor['mean'] - p['mean']) > z * combined_se:
                    tier += 1
                    anchor = p
            p['tier'] = tier
            p['rank'] = rank
        report[pos] = players
    return report


def _display_label(player):
    """Thin adapter from this module's player-dict shape to the shared, cross-module
    config.display_player_name(name, team) -- see that function for what it actually does."""
    return display_player_name(player['name'], player.get('team'))


def _crop_for_bar_chart(players):
    """The per-player bar chart's display prefix -- see CHART_MIN_TIERS_SHOWN/CHART_MAX_ROWS."""
    cutoff = next(
        (p['rank'] for p in players if p['tier'] > CHART_MIN_TIERS_SHOWN),
        len(players) + 1,
    ) - 1
    return players[:min(max(cutoff, 1), CHART_MAX_ROWS)]


def _trigger_summary_chart(players):
    """True when a per-player bar chart of this group would be dominated by a single tier --
    see MAJORITY_TIER1_THRESHOLD. Evaluated on whatever list is passed in (the bar chart's
    cropped 'shown' prefix, since that's what a viewer would actually see rendered)."""
    n_tiers = max(p['tier'] for p in players)
    if n_tiers == 1:
        return True
    n_tier1 = sum(1 for p in players if p['tier'] == 1)
    return (n_tier1 / len(players)) >= MAJORITY_TIER1_THRESHOLD


def _dominance_caption(players):
    """Factual tier-1 caption for whatever group is passed in. Not itself gated by
    MAJORITY_TIER1_THRESHOLD -- that threshold only decided whether to draw a summary chart at
    all (via _trigger_summary_chart, evaluated on the bar chart's cropped prefix); once a
    summary chart is being drawn for the FULL group, its caption states the real numbers for
    that full group, whatever they are."""
    n_tiers = max(p['tier'] for p in players)
    n_tier1 = sum(1 for p in players if p['tier'] == 1)
    total = len(players)
    if n_tiers == 1:
        return f"all {total} are ONE statistically indistinguishable tier"
    tier2_start = min(p['rank'] for p in players if p['tier'] == 2)
    return (f"Tier 1 alone is {n_tier1} of {total} ({100 * n_tier1 / total:.0f}%) -- "
            f"real separation doesn't start until rank {tier2_start}")


def _tier_summary_rows(players, max_names=5):
    """One row per tier from a sorted-by-mean-desc, tier-contiguous player list (exactly what
    compute_tiers produces): player count, mean range, and up to max_names representative
    (best) names for the chart annotation."""
    rows = []
    current_tier = None
    for p in players:
        if p['tier'] != current_tier:
            rows.append({'tier': p['tier'], 'members': []})
            current_tier = p['tier']
        rows[-1]['members'].append(p)
    for r in rows:
        means = [m['mean'] for m in r['members']]
        r['count'] = len(r['members'])
        r['mean_min'] = min(means)
        r['mean_max'] = max(means)
        r['top_names'] = [_display_label(m) for m in r['members'][:max_names]]
    return rows


def _tier_palette(players):
    """The viridis palette for a position's tiers, indexable as palette[tier - 1]. ALWAYS
    built from every tier in the FULL group, never a display-cropped subset -- so the bar
    chart, the tier-summary chart, and the HTML table all agree on what color tier 4 is, even
    for a position (e.g. DL, 10 tiers) whose bar chart only ever renders tiers 1-4. Building
    the palette from a cropped subset would size tier 4 as if it were the position's WORST
    tier (bright yellow, the end of the viridis range) when six cooler tiers actually follow
    it -- a real inconsistency this shared helper exists to rule out by construction."""
    n_tiers = max((p['tier'] for p in players), default=1)
    return sns.color_palette("viridis", n_tiers)


def _hex_color(rgb_float):
    return '#%02x%02x%02x' % tuple(round(c * 255) for c in rgb_float)


def render_tier_player_bars(pos, players, shown, week):
    """One horizontal bar per player, colored by tier, error bars = std_epistemic. IR/injured
    players get a hatched bar. `players` is the FULL group (for the tier palette, see
    _tier_palette); `shown` is the display-cropped list actually rendered (best on top)."""
    shown = shown[::-1]  # best player on top of a horizontal barh
    palette = _tier_palette(players)

    fig_height = max(4.0, 0.32 * len(shown))
    fig, ax = plt.subplots(figsize=(10, fig_height))
    colors = [palette[p['tier'] - 1] for p in shown]
    bars = ax.barh(
        [_display_label(p) for p in shown], [p['mean'] for p in shown],
        xerr=[p['std_epistemic'] for p in shown],
        color=colors, edgecolor='black', linewidth=0.6,
        error_kw={'ecolor': 'black', 'elinewidth': 1.0, 'capsize': 3, 'alpha': 0.6},
    )
    for bar, p in zip(bars, shown):
        if p['on_ir'] or (p['injury_status'] and str(p['injury_status']).upper() not in ('', 'NONE')):
            bar.set_hatch('///')
            bar.set_alpha(0.55)

    ax.set_title(f"{pos} Tiers (statistically distinguishable groups, {TIER_Z:.1f} combined SE)",
                 fontsize=13, fontweight='bold', pad=15)
    ax.set_xlabel("Projected Mean (error bar = season-long epistemic uncertainty)", fontweight='bold')
    ax.set_xlim(0, max(p['mean'] + p['std_epistemic'] for p in shown) * 1.15)
    sns.despine(top=True, right=True)
    plt.tight_layout()
    plt.savefig(tier_chart_path(pos, week), dpi=300)
    plt.close()


def render_tier_summary_chart(pos, players, caption, week):
    """Compact replacement for render_tier_player_bars when one tier dominates a position
    (see _trigger_summary_chart): one bar per TIER instead of one bar per player, spanning
    that tier's mean range, annotated with its best few names. Built from the FULL group, not
    a display-cropped prefix -- a per-tier chart scales fine to any tier count."""
    rows = _tier_summary_rows(players)[::-1]  # tier 1 on top
    n_tiers = len(rows)
    palette = _tier_palette(players)

    fig, ax = plt.subplots(figsize=(12, max(2.5, 0.9 * n_tiers)))
    labels = [f"Tier {r['tier']} ({r['count']} players)" for r in rows]
    lefts = [r['mean_min'] for r in rows]
    widths = [r['mean_max'] - r['mean_min'] for r in rows]
    colors = [palette[r['tier'] - 1] for r in rows]
    bars = ax.barh(labels, widths, left=lefts, color=colors, edgecolor='black',
                    linewidth=0.8, height=0.6)
    for bar, r in zip(bars, rows):
        names = ", ".join(r['top_names'])
        remaining = r['count'] - len(r['top_names'])
        if remaining > 0:
            names += f", +{remaining} more"
        ax.text(bar.get_x() + bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                names, va='center', ha='left', fontsize=9)

    ax.set_title(f"{pos} Tiers -- Summary ({TIER_Z:.1f} combined SE)",
                 fontsize=13, fontweight='bold', pad=28)
    ax.text(0.5, 1.02, caption, transform=ax.transAxes, ha='center', va='bottom',
            fontsize=10, style='italic', color='#444444')
    ax.set_xlabel("Projected Mean Range Within Tier", fontweight='bold')
    sns.despine(top=True, right=True)
    plt.tight_layout()
    plt.savefig(tier_chart_path(pos, week), dpi=300, bbox_inches='tight')
    plt.close()


_TABLE_CSS = """
body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 1.5rem; color: #1a1a1a; }
h1 { font-size: 1.3rem; margin-bottom: 0.25rem; }
p.caption { color: #555; margin-top: 0; margin-bottom: 1rem; }
table { border-collapse: collapse; width: 100%; max-width: 900px; }
th, td { padding: 0.4rem 0.7rem; text-align: left; border-bottom: 1px solid #ddd; }
th { cursor: pointer; user-select: none; background: #f4f4f4; position: sticky; top: 0; }
th.sort-asc::after { content: " \\25B2"; }
th.sort-desc::after { content: " \\25BC"; }
tbody tr:hover { filter: brightness(0.94); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
"""

_TABLE_JS = """
document.querySelectorAll('th[data-key]').forEach(function (th) {
  th.addEventListener('click', function () {
    var table = th.closest('table');
    var tbody = table.querySelector('tbody');
    var headers = Array.from(table.querySelectorAll('th'));
    var idx = headers.indexOf(th);
    var asc = th.getAttribute('data-asc') !== 'true';
    headers.forEach(function (h) { h.removeAttribute('data-asc'); h.classList.remove('sort-asc', 'sort-desc'); });
    th.setAttribute('data-asc', asc);
    th.classList.add(asc ? 'sort-asc' : 'sort-desc');
    var isNumber = th.getAttribute('data-type') === 'number';
    var rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort(function (a, b) {
      var av = a.children[idx].getAttribute('data-sort');
      var bv = b.children[idx].getAttribute('data-sort');
      if (isNumber) { return asc ? (parseFloat(av) - parseFloat(bv)) : (parseFloat(bv) - parseFloat(av)); }
      return asc ? av.localeCompare(bv) : bv.localeCompare(av);
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  });
});
"""

# (header label, data-type for sort comparison) -- column order the user asked for.
_TABLE_COLUMNS = [
    ("Rank", "number"), ("Name", "text"), ("Team", "text"),
    ("Tier", "number"), ("Projected Mean", "number"), ("Uncertainty Range (±1 SE)", "number"),
]


def _build_tier_table_html(pos, players):
    """Full ranked table for one position as a self-contained, sortable HTML page. Built from
    the SAME per-player data as the charts (compute_tiers's output): _display_label for the
    collision-guard fix, _tier_palette for tier coloring, so this artifact and the PNGs always
    agree. players is the full, uncapped list -- unlike the charts, this format scales fine to
    any row count, which is exactly why it replaced the PNG "full ranked list" table."""
    palette_hex = [_hex_color(c) for c in _tier_palette(players)]

    header_cells = "".join(
        f'<th data-key="{escape(label)}" data-type="{dtype}">{escape(label)}</th>'
        for label, dtype in _TABLE_COLUMNS
    )

    body_rows = []
    for p in players:
        name = _display_label(p)
        team = p.get('team') or 'FA'
        mean = p['mean']
        se = p['std_epistemic']
        low, high = mean - se, mean + se
        color = palette_hex[p['tier'] - 1]
        body_rows.append(
            '<tr>'
            f'<td class="num" data-sort="{p["rank"]}">{p["rank"]}</td>'
            f'<td data-sort="{escape(name.lower())}">{escape(name)}</td>'
            f'<td data-sort="{escape(str(team).lower())}">{escape(str(team))}</td>'
            f'<td class="num" data-sort="{p["tier"]}" style="background-color:{color};">'
            f'T{p["tier"]}</td>'
            f'<td class="num" data-sort="{mean}">{mean:.1f}</td>'
            # Sorted numerically by the range's LOWER bound (floor projection), not the
            # formatted string -- "10.5-12.0" sorts before "9.5-11.0" alphabetically, which
            # is wrong for a numeric range.
            f'<td class="num" data-sort="{low}">{low:.1f}–{high:.1f}</td>'
            '</tr>'
        )

    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{escape(pos)} Tiers</title><style>{_TABLE_CSS}</style></head><body>"
        f"<h1>{escape(pos)} Full Ranked Table ({len(players)} players)</h1>"
        "<p class=\"caption\">Tier background color matches the position's tier chart "
        "(viridis, tier 1 = darkest). Click a column header to sort; click again to reverse."
        "</p>"
        f"<table><thead><tr>{header_cells}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
        f"<script>{_TABLE_JS}</script></body></html>"
    )


def _render_tier_table(pos, players, out_path):
    ensure_dir_for(out_path)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(_build_tier_table_html(pos, players))


def render_tier_tables(report, week):
    """One sortable HTML ranked table per position, covering every player (never cropped)."""
    for pos, players in report.items():
        if not players:
            continue
        _render_tier_table(pos, players, positional_tiers_table_path(pos, week))


def render_tier_charts(report, week):
    """One chart per position. Positions where a per-player bar chart would be dominated by a
    single tier (see _trigger_summary_chart) get the compact tier-summary chart instead of the
    detailed per-player bars; the full per-player detail lives in the HTML table either way
    (see render_tier_tables), not in a second PNG."""
    sns.set_theme(style="whitegrid")
    plt.rcParams.update({'font.sans-serif': 'DejaVu Sans', 'font.size': 10})

    for pos, players in report.items():
        if not players:
            continue
        shown = _crop_for_bar_chart(players)
        if _trigger_summary_chart(shown):
            render_tier_summary_chart(pos, players, _dominance_caption(players), week)
        else:
            render_tier_player_bars(pos, players, shown, week)


def build_positional_tier_report(week):
    """Entry point: load baselines, compute tiers, write the JSON report, one chart per
    position, and one sortable HTML ranked table per position -- all stamped with `week` (see
    positional_tiers_report_path/tier_chart_path/positional_tiers_table_path) so a later week's
    run doesn't overwrite an earlier week's. Reads only BASELINES_FILE and writes only
    positional_tiers_report_path(week) + per-position PNGs/HTML -- touches nothing
    export_and_visualize owns, so it cannot affect the golden master."""
    baselines = load_json(BASELINES_FILE)
    report = compute_tiers(baselines)
    save_json(positional_tiers_report_path(week), report)
    render_tier_charts(report, week)
    render_tier_tables(report, week)
    return report
