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


def next_waiver_run(now=None):
    """UI-W2: the next daily waiver run -- WAIVER_HOUR_PT, Pacific -- strictly after `now`
    (a datetime; default the clock), as ISO UTC."""
    pt = _pt()
    now = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(pt)
    run = now.replace(hour=WAIVER_HOUR_PT, minute=0, second=0, microsecond=0)
    if run <= now:
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


def waiver_run(root, my_team=None):
    """UI-W3: the most recent daily waiver run -- every claim that won it (the run is the
    Pacific date the claims processed on), with the team, the players in and out, the
    winning bid, and the claim's paired-simulation grade for the claiming team (playoff
    percentage points, its standard error and the render.verdict tier), or None when the
    claim is not graded yet, and (UI-W4) the losing bids each claim beat, highest first, from
    Decision 4's failed-claims log. None when no claim is on file."""
    from webui.render import verdict
    rows = [d for d in decisions_report(root, my_team)["decisions"] if d.get("type") == "waiver" and _parse(d.get("created"))]
    if not rows:
        return None
    day = lambda d: _parse(d["created"]).astimezone(_pt()).date().isoformat()          # noqa: E731
    latest = max(day(d) for d in rows)
    contested = _contested(root)
    claims = []
    for d in rows:
        if day(d) != latest:
            continue
        team = d.get("actor") or (d.get("teams") or [None])[0]
        fx = (d.get("effect") or {}).get(team) or {}
        grade = None
        if d.get("evaluated") and fx.get("playoff") is not None:
            grade = {"delta": fx["playoff"], "se": fx.get("playoff_se"), "verdict": verdict(fx["playoff"], fx.get("playoff_se"))}
        claims.append({"team": team, "adds": d.get("adds") or [], "drops": d.get("drops") or [], "bid": d.get("faab_bid"),
                       "grade": grade, "skipped": d.get("skipped"), "is_mine": bool(d.get("is_mine")), "created": d.get("created"),
                       "losing": (contested.get(d.get("id")) or {}).get("bids") or []})         # UI-W4: the bids it beat
    claims.sort(key=lambda c: (-(c["bid"] or 0), c["team"] or ""))
    return {"date": latest, "claims": claims, "bids_logged": root.exists("logs/failed_claims.jsonl")}


FAAB_BUDGET = 100          # docs/WAIVER_MECHANICS.md: the league's waiver_budget, per team per season


def faab_table(root, my_team):
    """UI-W6: every team's budget -- left (the league's standings), spent, spent per completed
    week -- and the teams that can outbid the owner: every team with more left."""
    st = root.read_json("current/league_standings.json", {}) or {}
    wk = freshness_report(root).get("week")
    done = max(0, int(wk) - 1) if wk else 0
    rows = []
    for team, s in st.items():
        if not isinstance(s, dict) or s.get("remaining_faab") is None:
            continue
        left = float(s["remaining_faab"])
        spent = FAAB_BUDGET - left
        rows.append({"team": team, "left": left, "spent": spent, "per_week": round(spent / done, 2) if done else None})
    rows.sort(key=lambda r: (-r["left"], r["team"]))
    mine = next((r["left"] for r in rows if r["team"] == my_team), None)
    for r in rows:
        r["outbids"] = mine is not None and r["team"] != my_team and r["left"] > mine
    return {"rows": rows, "outbid": [r["team"] for r in rows if r["outbids"]], "weeks_done": done, "mine": mine}


def _log_rows(root, rel):
    """A log's parsed rows, oldest first; [] when it is not there yet."""
    try:
        rows, _n = root.tail_jsonl(rel, n=1_000_000)
    except (FileNotFoundError, ValueError):
        return []
    return [r for r in reversed(rows) if isinstance(r, dict) and "_unparsed" not in r]


def _contested(root):
    """{winning transaction_id: [{team, bid}, ...]} -- each outbid lost claim under the claim
    that beat it (the pairing the sync made by the run's processing time), highest bid first."""
    out, seen = {}, set()
    for r in _log_rows(root, "logs/failed_claims.jsonl"):
        if r.get("transaction_id") in seen:           # first row wins: the log is union-merged (.gitattributes)
            continue
        seen.add(r.get("transaction_id"))
        w = r.get("won_by") or {}
        if r.get("reason") != "outbid" or not w.get("transaction_id") or r.get("faab_bid") is None:
            continue
        out.setdefault(w["transaction_id"], {"won_by": w, "row": r, "bids": []})["bids"].append({"team": r.get("team"), "bid": r["faab_bid"]})
    for g in out.values():
        g["bids"].sort(key=lambda b: (-b["bid"], b["team"] or ""))
    return out


LEDGER_WINDOW_DAYS = 8       # a bid-ledger row counts for a run when it was placed in the 8 days before it: a
#                              claim sits at most a week before the daily run takes it (the ledger's own F65 note)


def clearing_prices(root, my_team):
    """UI-W4, on Decision 4's failed-claims log: every contested claim this season -- the
    winning bid against the next best, and what the winner paid above it (Sleeper's auction
    is first-price, so a winner pays their own bid, and the next best bid + 1 is what winning
    actually took). Per team, the contested wins and the total paid above the next bid; the
    league's median; and the owner's suggested bid ranges from the bid ledger laid against the
    price each claim took. None when no contested claim is on file."""
    groups = _contested(root)
    claims = []
    for tid, g in groups.items():
        w, r, bids = g["won_by"], g["row"], g["bids"]
        if w.get("faab_bid") is None:
            continue
        top = bids[0]
        run = _parse(r.get("processed"))
        claims.append({"transaction_id": tid, "player": r.get("name"), "player_id": r.get("player_id"), "week": r.get("week"),
                       # the run's Pacific date labels a claim, not the submission leg (F65: a claim can sit across one)
                       "run": f"{run.astimezone(_pt()):%b} {run.astimezone(_pt()).day}" if run else None,
                       "processed": r.get("processed"), "winner": w.get("team"), "paid": w["faab_bid"],
                       "next_bid": top["bid"], "next_team": top["team"], "above": w["faab_bid"] - top["bid"],
                       "losing": len(bids), "bids": bids})
    if not claims:
        return None
    claims.sort(key=lambda c: (c["processed"] or "", c["player"] or ""), reverse=True)
    by_team = {}
    for c in claims:
        t = by_team.setdefault(c["winner"], {"contested": 0, "above": 0})
        t["contested"] += 1
        t["above"] += c["above"]
    aboves = sorted(c["above"] for c in claims)
    mid = len(aboves) // 2
    median = aboves[mid] if len(aboves) % 2 else (aboves[mid - 1] + aboves[mid]) / 2
    ledger = _log_rows(root, "logs/bid_ledger.jsonl")
    bands = []
    for c in claims:
        mine_lost = next((b for b in c["bids"] if b["team"] == my_team), None)
        if c["winner"] != my_team and not mine_lost:
            continue
        others = [b["bid"] for b in c["bids"] if b["team"] != my_team] + ([c["paid"]] if c["winner"] != my_team else [])
        run = _parse(c["processed"])
        rows = [x for x in ledger if str(x.get("player_id")) == str(c["player_id"]) and _parse(x.get("placed_at")) and run
                and _dt.timedelta(0) <= run - _parse(x["placed_at"]) <= _dt.timedelta(days=LEDGER_WINDOW_DAYS)]
        if not rows or not others:
            continue
        x = max(rows, key=lambda x: _parse(x["placed_at"]))          # the live bid: the latest placed (the ledger's F64 rule)
        lo, hi = x.get("suggested_v2_low"), x.get("suggested_v2_high")
        if lo is None or hi is None:
            continue
        price = max(others) + 1
        bands.append({"player": c["player"], "week": c["week"], "run": c["run"], "low": lo, "high": hi, "price": price, "placed": x.get("bid_placed"),
                      "won": c["winner"] == my_team, "verdict": "below" if hi < price else ("above" if lo > price else "within")})
    team_rows = sorted(({"team": t, **v} for t, v in by_team.items()), key=lambda r: (-r["above"], r["team"] or ""))
    return {"claims": claims, "by_team": by_team, "team_rows": team_rows, "median_above": median, "bands": bands}
