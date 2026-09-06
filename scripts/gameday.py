#!/usr/bin/env python3
"""The one-command pre-lock check (owner usability request, 2026-09-06).

  py -3.10 -m scripts.gameday            # sync if stale, flag news, optimize, open
  py -3.10 -m scripts.gameday --no-sync  # use the data on disk as-is

Built for 9:40 AM on a Sunday: no parameters, no tool names to remember. It syncs when
the data is older than an hour (fresh news is the whole point), diffs every rostered
player's injury status against the pre-sync snapshot so "did the news break my lineup?"
is answered explicitly, runs the lineup optimizer and the matchup constructions, and
opens one compact HTML page in the browser -- with the owner's real-name legend when
SHOW_REAL_TEAM_NAMES is set. Read-only with respect to the quoted record: this is the
standing-policy answer to late news (the canonical quote stands; decisions refresh).
"""
import argparse
import contextlib
import datetime as _dt
import io
import os
import sys
from html import escape

SYNC_MAX_AGE_MIN = 60


def status_changes(old_baselines, new_baselines, roster):
    """Pure: injury-status transitions for MY rostered players between two baseline
    snapshots. A player who vanished from baselines entirely is the loudest case."""
    out = []
    for name in roster:
        was = (old_baselines.get(name) or {}).get("injury_status")
        if name not in new_baselines:
            out.append({"player": name, "was": was, "now": "MISSING FROM BASELINES"})
            continue
        now = (new_baselines.get(name) or {}).get("injury_status")
        if now != was:
            out.append({"player": name, "was": was, "now": now})
    return out


def render_page(week, synced, changes, legend, blocks):
    T = escape
    css = ("body{font-family:Segoe UI,system-ui,sans-serif;max-width:60rem;margin:1.5rem auto;"
           "padding:0 1rem;background:#f6f3ec;color:#26313a}"
           "h1{margin:.2rem 0}.meta{color:#5c6670}"
           ".card{border-left:5px solid #3d5a73;background:#fbf9f3;padding: .8rem 1.1rem;margin:1rem 0}"
           ".alert{border-left-color:#7a2e2e;background:#f2e3de}"
           "pre{overflow-x:auto;font-size:.92em;line-height:1.35}"
           "table{border-collapse:collapse}td,th{padding:.25rem .6rem;text-align:left}")
    out = [f"<title>Gameday -- week {week}</title><style>{css}</style>",
           f"<h1>Gameday</h1><p class='meta'>week {T(str(week))} &middot; data synced {T(str(synced))}</p>",
           legend or ""]
    if changes:
        rows = "".join(f"<tr><td><b>{T(c['player'])}</b></td><td>{T(str(c['was']))}</td>"
                       f"<td><b>{T(str(c['now']))}</b></td></tr>" for c in changes)
        out.append('<div class="card alert"><b>Status changes on your roster since the '
                   f'previous sync:</b><table><tr><th>player</th><th>was</th><th>now</th></tr>{rows}</table></div>')
    else:
        out.append('<div class="card">No status changes on your roster since the previous sync.</div>')
    for title, text in blocks:
        out.append(f'<div class="card"><b>{T(title)}</b><pre>{T(text)}</pre></div>')
    return "\n".join(out)


def _capture(fn, argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(argv)
    return buf.getvalue().strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-sync", action="store_true", help="use the data on disk as-is")
    args = ap.parse_args(argv)

    from fantasy_sim.config import MY_TEAM
    from fantasy_sim.freshness import read_manifest
    from fantasy_sim.storage import BASELINES_FILE, load_json
    from fantasy_sim.weekly_report import legend_html, real_name_overlay

    old_baselines = load_json(BASELINES_FILE) if os.path.exists(BASELINES_FILE) else {}

    manifest, sync_start = read_manifest()
    age_min = ((_dt.datetime.now(_dt.timezone.utc).timestamp() - sync_start) / 60
               if sync_start else 1e9)
    if not args.no_sync and age_min > SYNC_MAX_AGE_MIN:
        print(f"[gameday] sync is {age_min:.0f} min old -- refreshing...")
        from fantasy_sim.sync import sync_all
        sync_all()
        manifest, _ = read_manifest()
    else:
        print(f"[gameday] using data on disk (synced {manifest.get('finished_at') if manifest else '?'})")

    new_baselines = load_json(BASELINES_FILE)
    rosters = load_json(os.path.join("data", "current", "live_rosters.json"))
    roster = [p["name"] for p in rosters.get(MY_TEAM, [])]
    changes = status_changes(old_baselines, new_baselines, roster)
    for c in changes:
        print(f"[gameday] STATUS CHANGE: {c['player']}  {c['was']} -> {c['now']}")

    from scripts.optimize_lineup import main as opt_main
    from scripts.matchup_lineup import main as match_main
    print("[gameday] optimizing lineup...")
    lineup_txt = _capture(opt_main, [])
    print("[gameday] running matchup constructions...")
    matchup_txt = _capture(match_main, [])

    week = (manifest or {}).get("current_week", "?")
    html = render_page(week, (manifest or {}).get("finished_at", "?"), changes,
                       legend_html(real_name_overlay()),
                       [("Optimal lineup (p10 / p50 / p90, margin over bench)", lineup_txt),
                        ("Matchup constructions (P(beat opponent), P(beat median))", matchup_txt)])
    out_dir = os.path.join("data", "decisions", f"week_{int(week):02d}" if str(week).isdigit() else "week_00",
                           "archive")
    os.makedirs(out_dir, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(out_dir, f"gameday_{stamp}.html")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    print(f"[gameday] -> {path}")
    try:
        os.startfile(os.path.abspath(path))  # noqa: S606 -- owner's own browser, Windows-only convenience
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
