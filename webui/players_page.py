"""
webui.players_page -- every player, and the waiver board (docs/WEB_UI_ROADMAP.md UI-P1, W1).

One table over the players the sync prices: this week's price beside the season mean (the
live panel's own precedence, webui.live.expectations), an 80% range, and each player's
standing in this league -- mine, another team's, ON WAIVERS, or a free agent.

The waiver rule is the league's, docs/WAIVER_MECHANICS.md section 1: a player dropped within
the last `waiver_clear_days` (2) days is on waivers and needs a FAAB bid, resolved at the
daily run (`daily_waivers_hour` 9, observed as 09:00 PT); anyone else unrostered is a free
agent, $0 and instant. The drop times come from the decision log (each transaction once,
decisions_report). The page repeats the doc's own advice: Sleeper's bid box versus Add
button is the tell to trust.

The waiver board is the same rows cut to the available, joined to the newest
waiver-targets record for the week (value over replacement, the roster need it fills, the
suggested bid) and ordered by that record's ranking. Reads only.
"""
import datetime as _dt

from webui.glance import _newest, decisions_report, freshness_report
from webui.live import expectations

WAIVER_CLEAR_DAYS = 2          # docs/WAIVER_MECHANICS.md: league setting waiver_clear_days
WAIVER_HOUR_PT = 9             # docs/WAIVER_MECHANICS.md: daily_waivers_hour 9, observed as 09:00 PT (the one
                               # item there "not directly verified" -- the timezone is observed, not read)
Z80 = 1.2815515655446004       # the 90th percentile of the standard Normal: an 80% range


def _pt():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Los_Angeles")
    except Exception:                          # no tz database: Pacific daylight time, correct until November
        return _dt.timezone(_dt.timedelta(hours=-7))


def _parse(t):
    try:
        return _dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _iso(d):
    return d.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clears_at(dropped):
    """When a player dropped at `dropped` (ISO UTC) comes off waivers: the first daily run at
    WAIVER_HOUR_PT, Pacific, at or after the drop plus WAIVER_CLEAR_DAYS. None if unreadable."""
    d = _parse(dropped)
    if d is None:
        return None
    pt = _pt()
    ready = (d + _dt.timedelta(days=WAIVER_CLEAR_DAYS)).astimezone(pt)
    run = ready.replace(hour=WAIVER_HOUR_PT, minute=0, second=0, microsecond=0)
    if run < ready:
        run = (run + _dt.timedelta(days=1)).replace(hour=WAIVER_HOUR_PT)
    return _iso(run)


def _targets(root, week):
    """{name: target row} and the stamp, from the newest waiver-targets record for `week`."""
    if not week or int(week) not in root.decision_weeks():
        return {}, None
    dec = root.decisions(int(week))
    entries = sorted(dec["canonical"] + dec["archive"], key=lambda e: (e["stamp"] or "", e["name"]), reverse=True)
    e = _newest(entries, "waivers")
    rec = root.read_json(e["rel"], {}) if e else {}
    return {t.get("name"): t for t in (rec or {}).get("targets") or [] if isinstance(t, dict)}, (rec or {}).get("timestamp_utc")


def players_table(root, my_team, now=None):
    """{week, rows, targets_stamp}: one row per priced player."""
    week = freshness_report(root)["week"]
    week = int(week) if week else None
    now_d = _parse(now) if now else _dt.datetime.now(_dt.timezone.utc)
    base = root.read_json("current/player_baselines.json", {}) or {}
    owners = {}
    for team, es in (root.read_json("current/live_rosters.json", {}) or {}).items():
        for e in es or []:
            if e.get("name"):
                owners[e["name"]] = team
    last_drop = {}
    for d in reversed(decisions_report(root, my_team)["decisions"]):          # oldest first: the newest drop wins
        for p in d["drops"]:
            if p.get("name") and d.get("created"):
                last_drop[p["name"]] = d["created"]
    by_pid = expectations(root, week) if week else {}
    price = {r["name"]: r for r in by_pid.values()}
    targets, stamp = _targets(root, week)
    rows = []
    for name, b in base.items():
        if not isinstance(b, dict) or b.get("player_id") is None:
            continue
        owner = owners.get(name)
        standing = ("mine" if owner == my_team else "rostered") if owner else "free"
        clears = None
        if not owner and last_drop.get(name):
            c = clears_at(last_drop[name])
            if c and _parse(c) > now_d:
                standing, clears = "waivers", c
        e = price.get(name) or {}
        mean = e.get("mean", b.get("mean"))
        sd = e.get("sd")
        t = targets.get(name) or {}
        rows.append({"name": name, "pid": str(b["player_id"]), "pos": b.get("pos"), "nfl": b.get("team"),
                     "owner": owner, "standing": standing, "clears": clears,
                     "status": "IR" if b.get("on_ir") else (b.get("injury_status") or ""), "bye": b.get("bye"),
                     "season_mean": b.get("mean"), "week_mean": round(float(mean), 2) if mean is not None else None,
                     "source": e.get("source") or "baseline",
                     "p10": round(max(0.0, float(mean) - Z80 * float(sd)), 1) if (mean is not None and sd) else None,
                     "p90": round(float(mean) + Z80 * float(sd), 1) if (mean is not None and sd) else None,
                     "vorp": t.get("vorp"), "fills": t.get("fills"), "rank": t.get("season_rank"),
                     "bid": (t.get("bid") or {}).get("suggested")})
    rows.sort(key=lambda r: -(r["week_mean"] or 0.0))
    return {"week": week, "rows": rows, "targets_stamp": stamp, "n_waivers": sum(1 for r in rows if r["standing"] == "waivers")}
