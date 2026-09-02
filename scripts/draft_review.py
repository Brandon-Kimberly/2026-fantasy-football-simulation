#!/usr/bin/env python3
"""
Draft review report (F15): per-manager and per-round reach/value/steal tables from the
at-draft analysis (fantasy_sim.draft_review.review_draft), plus one chart. THE VERDICTS ARE
A PROXY -- the analysis prices every pick against TODAY's baseline pool, not the board that
existed on draft day; the banner this script prints first says so, and stays.

  py -3.10 -m scripts.draft_review                  # the 2026 draft
  py -3.10 -m scripts.draft_review --season 2025    # the 2025 draft (a year of drift: proxy caveat applies doubly)
  py -3.10 -m scripts.draft_review --picks          # adds the full pick-by-pick board walk

Reads data/logs/draft_{season}.json and data/current/ only; writes one JSON record and one
chart under data/decisions/.
"""
import argparse
import datetime as _dt
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from fantasy_sim.draft_review import render_draft_html, review_draft
from fantasy_sim.positional_tiers import TIER_Z
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import decisions_season_path, draft_log_file, save_chart, save_json


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", default="2026")
    ap.add_argument("--picks", action="store_true", help="print the full pick-by-pick board walk")
    args = ap.parse_args(argv)

    with open(draft_log_file(args.season), encoding="utf-8") as f:
        draft = json.load(f)
    engine = FantasySimulationEngine()
    r = review_draft(draft, engine.baselines, engine.replacement_levels)

    bar = "=" * 78
    print(f"\n{bar}\n  {r['proxy_note']}\n  ({r['caps_note']})\n{bar}")
    s = r["sanity"]
    print(f"\nDraft review -- season {r['season']}, draft {r['draft_id']}, {len(r['picks'])} picks")
    print(f"  sanity: pick 1 ({s['pick1']}) is #{s['pick1_board_rank_by_vorp']} of "
          f"{s['board_size']} on today's board by VORP -- {s['note']}")
    if r["unresolved"]:
        print(f"  unresolved (no baseline today, listed per F15's acceptance criterion): "
              f"{', '.join(r['unresolved'])}")

    print(f"\nPer manager (steal/reach = VORP gap beyond {TIER_Z:.1f} combined SE, the tier convention):")
    print(f"  {'team':18s} {'picks':>5s} {'steal':>5s} {'value':>5s} {'reach':>5s} {'unres':>5s} {'meanGap':>7s} {'totVORP':>7s}")
    for m in r["managers"]:
        mg = f"{m['mean_gap']:+7.2f}" if m["mean_gap"] is not None else "      -"
        print(f"  {m['team']:18s} {m['picks']:5d} {m['steals']:5d} {m['values']:5d} "
              f"{m['reaches']:5d} {m['unresolved']:5d} {mg} {m['total_vorp']:7.1f}")

    print("\nPer round:")
    print(f"  {'rd':>3s} {'picks':>5s} {'steal':>5s} {'value':>5s} {'reach':>5s} {'meanGap':>7s}")
    for rd in r["rounds"]:
        mg = f"{rd['mean_gap']:+7.2f}" if rd["mean_gap"] is not None else "      -"
        print(f"  {rd['round']:3d} {rd['picks']:5d} {rd['steals']:5d} {rd['values']:5d} {rd['reaches']:5d} {mg}")

    scored = [p for p in r["picks"] if p["vorp_gap"] is not None]
    for title, rows in (("Biggest steals", sorted(scored, key=lambda p: -p["vorp_gap"])[:5]),
                        ("Biggest reaches", sorted(scored, key=lambda p: p["vorp_gap"])[:5])):
        print(f"\n{title} (proxy caveat above applies):")
        for p in rows:
            alt = p["best_alt"]["name"] if p["best_alt"] else "-"
            print(f"  pick {p['pick_no']:3d} rd {p['round']:2d}  {p['name']:24s} ({p['pos']}) "
                  f"{p['team']:18s} gap {p['vorp_gap']:+6.2f}  best-alt {alt}")

    if args.picks:
        print("\nPick-by-pick:")
        for p in r["picks"]:
            v = f"{p['vorp']:+6.2f}" if p["vorp"] is not None else "     -"
            g = f"{p['vorp_gap']:+6.2f}" if p["vorp_gap"] is not None else "     -"
            alt = p["best_alt"]["name"] if p["best_alt"] else "-"
            print(f"  {p['pick_no']:3d} rd {p['round']:2d} {p['team']:18s} {p['name']:24s} "
                  f"{p['pos']:3s} vorp {v} tier {p['tier'] or '-'} {p['label']:10s} gap {g} best-alt {alt}")

    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Median-relative bars: an absolute VORP gap is bounded above by 0 by construction (you
    # cannot beat the best available player), so absolute bars all read negative and look
    # like uniform failure. 0 here = the league median pick; positive = better than typical.
    med = r["league_median_gap"] or 0.0
    fig, ax = plt.subplots(figsize=(9, 0.5 * len(r["managers"]) + 1.8))
    teams = [m["team"] for m in r["managers"]][::-1]
    rel = [(m["rel_gap"] if m["rel_gap"] is not None else 0.0) for m in r["managers"]][::-1]
    ax.barh(teams, rel, color=["#2e7d32" if g >= 0 else "#c62828" for g in rel])
    ax.axvline(0, color="black", linewidth=1.0)
    ax.axvline(-med, color="#555", linewidth=0.8, linestyle="--")
    ax.text(-med, len(teams) - 0.3, "0.0 absolute = unachievable ceiling \n(best available every pick) ",
            fontsize=7, color="#555", va="top", ha="right")
    ax.set_xlabel(f"mean VORP gap per pick, relative to the league median pick ({med:+.2f} absolute)")
    ax.set_title(f"Draft {r['season']} -- pick quality by manager vs the league median "
                 f"(PROXY: today's board, not draft-day's; {TIER_Z:.1f} combined-SE verdicts)", fontsize=9)
    fig.tight_layout()
    chart_path = decisions_season_path(f"draft_review_{args.season}_{stamp}.png")
    save_chart(chart_path, dpi=300, bbox_inches="tight")
    plt.close(fig)

    import base64
    with open(chart_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    chart_html = (f'<figure><img src="data:image/png;base64,{b64}" alt="pick quality by manager">'
                  f'<figcaption>0 = league median pick quality ({med:+.2f} absolute gap); the dashed '
                  f'line is the unachievable 0.0-absolute ceiling. Positive bars drafted better than '
                  f'the typical pick.</figcaption></figure>')
    html_path = decisions_season_path(f"draft_review_{args.season}_{stamp}.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(render_draft_html(r, chart_html=chart_html))

    out = decisions_season_path(f"draft_review_{args.season}_{stamp}.json")
    save_json(out, {"timestamp_utc": stamp, "tool": "draft_review", "review": r})
    print(f"\n  chart -> {chart_path}\n  report -> {html_path}\n  logged -> {out}")
    return r


if __name__ == "__main__":
    main()
