"""webui.glance -- what the landing page and the System page read, all from the bound root.

Every function here reads through webui.paths.Root and hands the library's PURE functions
the values (their own readers are CWD-relative and this process never chdir()s -- docs/
WEB_UI.md 2.2). Nothing imports the engine; nothing writes.

home_report() is the at-a-glance page: this week's matchup as the model priced it (the
latest predictions row for the week -- P(win), both expected totals, P(beat median)), my
season standing (the latest forecast export), the cumulative-wins trajectory for a
sparkline, standings, the newest lineup and calendar records, and a health strip
(freshness, sync sources, the next run window, the last job, the logs' push state).
"""
import datetime as _dt
import hashlib
import os
import re
import subprocess


def _parse_iso(t):
    return _dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(_dt.timezone.utc)


def _stamp(t):
    try:
        return _dt.datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------- freshness
def freshness_report(root):
    """freshness.assess over values read from the bound root (its own readers are CWD-relative)."""
    from fantasy_sim.freshness import assess, parse_stamp
    from fantasy_sim.storage import SYNC_OUTPUT_FILES
    manifest = root.read_json("current/sync_manifest.json")
    sync_start = parse_stamp((manifest or {}).get("started_at", "")) if manifest else None
    mtimes = {os.path.basename(p): root.mtime("current/" + os.path.basename(p)) for p in SYNC_OUTPUT_FILES}
    meta = (root.read_json("current/vegas_totals.json", {}) or {}).get("_meta") or {}
    week = (manifest or {}).get("current_week") or (root.read_json("current/league_state.json", {}) or {}).get("current_week")
    export_mtime = None
    if week:
        export_mtime = root.mtime(f"weeks/week_{int(week):02d}/syndicate_comprehensive_matrix_week_{int(week)}.json")
    status, reasons = assess(manifest, sync_start, mtimes, meta.get("week"), export_mtime, None,
                             vegas_stale_since=meta.get("stale_since"))
    finished = _stamp((manifest or {}).get("finished_at") or "")
    age_h = round((_dt.datetime.now(_dt.timezone.utc) - finished).total_seconds() / 3600.0, 1) if finished else None
    sources = (manifest or {}).get("sources") or {}
    n_ok = sum(1 for s in sources.values() if isinstance(s, dict) and s.get("ok") and not s.get("fallback"))
    n_fell = sum(1 for s in sources.values() if isinstance(s, dict) and s.get("fallback"))
    n_bad = sum(1 for s in sources.values() if isinstance(s, dict) and not s.get("ok"))
    reasons = list(reasons)
    # B8: assess() returns one list that mixes the cause of a STALE verdict with every
    # tolerated fallback ("degraded: ..."). Pages show the cause; the rest stays folded.
    stale_reasons = [r for r in reasons if not str(r).startswith("degraded:")]
    degraded_reasons = [str(r)[len("degraded:"):].strip() for r in reasons if str(r).startswith("degraded:")]
    return {"status": status, "reasons": reasons, "stale_reasons": stale_reasons, "degraded_reasons": degraded_reasons,
            "manifest": manifest, "week": week,
            "vegas_week": meta.get("week"), "vegas_stale_since": meta.get("stale_since"),
            "age_hours": age_h, "sources": sources, "n_ok": n_ok, "n_fell": n_fell, "n_bad": n_bad,
            "n_warn": len((manifest or {}).get("degraded") or []),
            "phrase": sync_phrase(n_bad, n_fell, len((manifest or {}).get("degraded") or []))}


def odds_at(root, n):
    """UI-E4: every team's odds from ONE week's export -- playoff (and its standard error),
    expected final wins, the magic number and what is banked from the forecast file; the
    title odds from the matrix's season outcomes (the forecast file carries none). The
    matrix repeats the playoff figure at two decimals; the forecast file's one-decimal
    figure is the one every page shows. {} when that week has no export."""
    n = int(n)
    d = f"weeks/week_{n:02d}"
    f = root.read_json(f"{d}/live_season_forecast_week_{n}.json", {}) or {}
    if not f:
        return {}
    m = root.read_json(f"{d}/syndicate_comprehensive_matrix_week_{n}.json", {}) or {}
    outcomes = m.get("season_outcomes") or []
    if isinstance(outcomes, dict):
        outcomes = [dict(Team=k, **v) for k, v in outcomes.items()]
    champ = {o.get("Team"): o.get("Champ_Pct") for o in outcomes if isinstance(o, dict)}
    out = {}
    for team, v in f.items():
        if not isinstance(v, dict):
            continue
        fc, cs = v.get("forecast") or {}, v.get("current_state") or {}
        out[team] = {"playoff": fc.get("playoff_probability_pct"), "playoff_se": fc.get("playoff_standard_error"),
                     "champ": champ.get(team), "exp_wins": fc.get("expected_final_wins"),
                     "magic": fc.get("approximate_magic_number"), "banked": cs.get("actual_wins_banked")}
    return out


def odds_now(root):
    """UI-E4: THE answer to "what are the odds now", for every page: the newest export at or
    before the sync week. `behind` is how many weeks the sync has moved on since (between
    Tuesday's sync and that week's simulation it is 1), so a page can say which forecast it
    shows instead of showing nothing."""
    sync = freshness_report(root)["week"]
    sync = int(sync) if sync else None
    ws = [w for w in root.weeks() if sync is None or w <= sync]
    for w in reversed(ws):
        teams = odds_at(root, w)
        if teams:
            return {"week": w, "teams": teams, "behind": (sync - w) if sync else 0}
    return {"week": None, "teams": {}, "behind": None}


def _wl(v):
    if v is None:
        return None
    v = float(v)
    return "W" if v >= 1 else ("L" if v <= 0 else "T")


def odds_moves(root, n=None):
    """UI-O3: what each result cost or bought. Every team's playoff and title odds in the
    forecast for week `n` (default: THE current one, odds_now) against the forecast before
    it, with the results of the weeks in between -- the head-to-head game and the median
    game. The change also carries every roster move and projection update since, so a
    page says "what week N did", never "what the win did". No earlier forecast: no rows,
    never a zero."""
    n = n if n is not None else odds_now(root)["week"]
    prev = max((w for w in root.weeks() if n and w < n and odds_at(root, w)), default=None)
    out = {"week": n, "prev": prev, "teams": {}, "rows": []}
    if not n or prev is None:
        return out
    from webui.accuracy import chances_in, quoted_week
    now, then = odds_at(root, n), odds_at(root, prev)
    actuals = root.read_json("current/weekly_actuals.json", {}) or {}
    quotes = {w: quoted_week(root, w) for w in range(prev, n)}        # UI-Q2: what it said at the time
    for team, v in now.items():
        was = then.get(team)
        if not was:
            continue
        d = lambda a, b: round(float(a) - float(b), 1) if a is not None and b is not None else None
        results = []
        for w in range(prev, n):
            r = ((actuals.get(f"week_{w}") or {}).get("team_results") or {}).get(team)
            if r:
                q = chances_in(quotes.get(w), team) or {}
                results.append({"week": w, "h2h": _wl(r.get("h2h_win")), "median": _wl(r.get("median_win")),
                                "p_h2h": q.get("h2h"), "p_median": q.get("median")})
        out["teams"][team] = {"team": team, "playoff": v["playoff"], "playoff_was": was["playoff"],
                              "d_playoff": d(v["playoff"], was["playoff"]), "champ": v["champ"],
                              "champ_was": was["champ"], "d_champ": d(v["champ"], was["champ"]), "results": results}
    out["rows"] = sorted(out["teams"].values(), key=lambda r: -(r["d_playoff"] if r["d_playoff"] is not None else -999))
    return out


def _wlt(w, l, t):
    return {"w": w, "l": l, "t": t, "text": f"{w}–{l}" + (f"–{t}" if t else "")}


def records(root):
    """UI-F4: each team's head-to-head, median and combined records from the weekly
    actuals. In a median league those are two contests a week, and the standings' one
    number (`h2h_wins`, which despite its name counts both) hides which one a team is
    winning. The standings stay the authority on the total (F84); `agrees` says whether
    the actuals account for exactly the wins the standings report, and a page shows the
    split only when they do -- the actuals can lag a week behind."""
    actuals = root.read_json("current/weekly_actuals.json", {}) or {}
    standings = root.read_json("current/league_standings.json", {}) or {}
    tally = {}
    for key, wk in actuals.items():
        if not str(key).startswith("week_") or not isinstance(wk, dict):
            continue
        for team, r in (wk.get("team_results") or {}).items():
            t = tally.setdefault(team, {"h2h": [0, 0, 0], "median": [0, 0, 0], "weeks": 0})
            t["weeks"] += 1
            for kind, field in (("h2h", "h2h_win"), ("median", "median_win")):
                v = (r or {}).get(field)
                if v is None:
                    continue
                v = float(v)
                t[kind][0 if v >= 1 else (1 if v <= 0 else 2)] += 1
    out = {}
    for team, t in tally.items():
        comb = [a + b for a, b in zip(t["h2h"], t["median"])]
        row = standings.get(team) or {}
        stated = row.get("h2h_wins")
        agrees = stated is None or int(float(stated)) == comb[0]
        out[team] = {"h2h": _wlt(*t["h2h"]), "median": _wlt(*t["median"]), "combined": _wlt(*comb),
                     "weeks": t["weeks"], "agrees": agrees}
    return out


def sync_phrase(n_bad, n_fell, n_warn):
    """UI-F2: what the last sync did, with SOURCES and WARNINGS counted apart. The manifest's
    `degraded` list is warnings (name collisions, carried baselines, depth-chart
    disagreements...), not sources; the pages used to call thirty of them "30 sources fell
    back" beside a table showing one source failed."""
    bits = []
    if n_bad:
        bits.append(f"{n_bad} source{'s' if n_bad != 1 else ''} failed")
    if n_fell:
        noun = "" if n_bad else f" source{'s' if n_fell != 1 else ''}"
        bits.append(f"{n_fell}{noun} fell back to an older copy")
    out = " and ".join(bits) if bits else "every source came through"
    if n_warn:
        out += f", and the sync raised {n_warn} warning{'s' if n_warn != 1 else ''}"
    return out


# --------------------------------------------------------------------------- windows
def canonical_stamps(root, week):
    """(marker, aware UTC datetime) for `week`: canonical digests on disk plus the committed
    predictions rows -- the two things run_windows counts as coverage."""
    from fantasy_sim.run_windows import parse_canonical_digest, stamps_from_predictions_rows
    out = []
    for e in root.decisions(week)["canonical"]:
        if e["ext"] == "md":
            dt = parse_canonical_digest(e["name"], week)
            if dt is not None:
                out.append((e["name"], dt))
    for e in root.logs():
        if e["name"].startswith("predictions_") and e["ext"] == "jsonl":
            rows, _n = root.tail_jsonl(e["rel"], n=100000)
            out.extend(stamps_from_predictions_rows(rows, week))
    return out


def windows_report(root, state_week):
    """run_windows.compute_windows on the SYNCED kickoffs only. The CLI live-fetches ESPN when
    the schedule carries none; the server never does, and says so instead."""
    from fantasy_sim.run_windows import compute_windows, watch_verdict
    sched = root.read_json("current/nfl_schedule.json", {}) or {}
    raw = (sched.get("_meta") or {}).get("kickoffs") or {}
    if not raw:
        return {"source": None, "result": None, "verdict": None, "next": None,
                "note": "no kickoffs in the synced schedule: run a sync to persist them (the CLI would "
                        "live-fetch ESPN here; this server never reaches the network for it)"}
    kicks = {int(w): [_parse_iso(t) for t in ts] for w, ts in raw.items() if ts}
    now = _dt.datetime.now(_dt.timezone.utc)
    probe = compute_windows(now, kicks, [], state_week=state_week)
    target = probe.get("target_week")
    if target is None:
        return {"source": "synced schedule", "result": probe, "verdict": None, "next": None, "note": None}
    result = compute_windows(now, kicks, canonical_stamps(root, target), state_week=state_week,
                             next_week_stamps=canonical_stamps(root, target + 1))
    nxt = None
    for w in result.get("windows") or []:
        if w.get("status") in ("OPEN", "UPCOMING") and not w.get("covered_by"):
            nxt = w
            break
    return {"source": "synced schedule", "result": result, "verdict": watch_verdict(result, now),
            "next": nxt, "note": None}


def logs_git_report(root):
    """freshness.logs_git_state over git run in the root; None when the root is not a checkout."""
    from fantasy_sim.freshness import logs_git_state
    try:
        por = subprocess.run(["git", "status", "--porcelain", "--", "data/logs"], cwd=root.root,
                             capture_output=True, text=True, timeout=15)
        if por.returncode != 0:
            return None
        ahead = subprocess.run(["git", "rev-list", "--count", "@{u}..HEAD", "--", "data/logs"],
                               cwd=root.root, capture_output=True, text=True, timeout=15)
        uncommitted, n_ahead = logs_git_state(por.stdout, ahead.stdout if ahead.returncode == 0 else None)
        return {"uncommitted": uncommitted, "unpushed": n_ahead}
    except (OSError, subprocess.SubprocessError):
        return None


def latest_digests(root):
    """{week: newest canonical weekly_report .html entry}."""
    out = {}
    for wk in root.decision_weeks():
        for e in root.decisions(wk)["canonical"]:
            if e["tool"] == "weekly_report" and e["ext"] == "html":
                out[wk] = e
                break
    return out


# ------------------------------------------------------------------------------ home
# The owner's palette (2026-09-27), keyed by pseudonym: colour follows the entity, never its
# rank. A team not listed here gets a stable hash hue so nothing is ever uncoloured.
TEAM_HUES = {"Quantum Ferrets": 272, "Cosmic Badgers": 140, "Crimson Marmots": 4, "Neon Walruses": 330,
             "Turbo Llamas": 48, "Rocket Pandas": 200, "Polar Yetis": 28, "Iron Wombats": 222}


def team_hue(name):
    """A stable hue per team: the owner's table first, a hash of the pseudonym otherwise."""
    if str(name) in TEAM_HUES:
        return TEAM_HUES[str(name)]
    return int(hashlib.md5(str(name).encode("utf-8")).hexdigest()[:4], 16) % 360


def _newest(entries, tool, ext="json"):
    for e in entries:
        if e["tool"] == tool and e["ext"] == ext:
            return e
    return None


def _week_prediction(root, week):
    """The latest predictions row logged for `week`, canonical preferred."""
    best = None
    for e in root.logs():
        if not (e["name"].startswith("predictions_") and e["ext"] == "jsonl"):
            continue
        rows, _n = root.tail_jsonl(e["rel"], n=100000)
        for r in rows:                       # newest first
            if r.get("record_type") == "week_predictions" and str(r.get("week")) == str(week):
                if best is None or (r.get("canonical") and not best.get("canonical")):
                    best = r
                if best.get("canonical"):
                    break
    return best


def home_report(root, my_team, runner=None):
    fr = freshness_report(root)
    week = fr["week"]
    wk = int(week) if week else None

    # ---- matchup, as the model priced it
    opponent = None
    sched = root.read_json("current/league_schedule.json", []) or []
    if wk and isinstance(sched, list) and 0 < wk <= len(sched):
        for pair in sched[wk - 1] or []:
            if my_team in pair:
                opponent = pair[0] if pair[1] == my_team else pair[1]
    pred = _week_prediction(root, wk) if wk else None
    matchup = {"opponent": opponent, "p_win": None, "se": None, "mine": None, "theirs": None,
               "p_median": None, "logged_at": None, "canonical": None}
    if pred:
        for m in pred.get("matchups") or []:
            if my_team in (m.get("a"), m.get("b")):
                other = m["b"] if m.get("a") == my_team else m["a"]
                matchup.update(opponent=matchup["opponent"] or other, se=m.get("se"),
                               p_win=m.get("p_a") if m.get("a") == my_team else m.get("p_b"))
        med = pred.get("median") or {}
        mine = med.get(my_team) or {}
        matchup.update(mine=mine.get("expected_total"), p_median=mine.get("p_beat_median"),
                       theirs=(med.get(matchup["opponent"]) or {}).get("expected_total") if matchup["opponent"] else None,
                       logged_at=pred.get("logged_at"), canonical=pred.get("canonical"))
    all_matchups = []
    if pred:
        for m in pred.get("matchups") or []:
            all_matchups.append({"a": m.get("a"), "b": m.get("b"), "p_a": m.get("p_a"), "p_b": m.get("p_b")})

    # ---- season standing, from THE current forecast (UI-E4): the newest export at or before
    # the sync week, which after Tuesday's sync is last week's until this week's run lands
    now = odds_now(root)
    ow = now["week"]
    od = f"weeks/week_{ow:02d}" if ow else None
    forecast = root.read_json(f"{od}/live_season_forecast_week_{ow}.json", {}) if od else {}
    forecast = forecast or {}
    mine_fc = (forecast.get(my_team) or {}).get("forecast") or {}
    mine_cs = (forecast.get(my_team) or {}).get("current_state") or {}
    matrix = root.read_json(f"{od}/syndicate_comprehensive_matrix_week_{ow}.json", {}) if od else {}
    matrix = matrix or {}
    traj = ((matrix.get("weekly_trajectories") or {}).get(my_team) or {}).get("expected_cumulative_wins_by_week") or []
    seed = seed_report((matrix.get("finishing_seed_probabilities") or {}).get(my_team) or {},
                       (((forecast.get(my_team) or {}).get("forecast")) or {}).get("playoff_probability_pct"))
    champ = (now["teams"].get(my_team) or {}).get("champ")

    # ---- standings, with each team's odds through the season and its move since the last forecast
    standings = root.read_json("current/league_standings.json", {}) or {}
    race = odds_race(root, my_team)
    sparks = {s["name"]: s["values"] for s in race["playoff"]}
    prev_week = max((w for w in root.weeks() if ow and w < ow), default=None)
    prev_odds = {t: v["playoff"] for t, v in odds_at(root, prev_week).items()} if prev_week else {}
    table = []
    for team, s in standings.items():
        fc = now["teams"].get(team) or {}
        table.append({"team": team, "wins": s.get("h2h_wins"), "points": s.get("points_scored"),
                      "faab": s.get("remaining_faab"), "playoff": fc.get("playoff"),
                      "champ": fc.get("champ"), "hue": team_hue(team),
                      "spark": sparks.get(team) or [], "rank_delta": None})
    table.sort(key=lambda r: (-(float(r["wins"] or 0)), -(float(r["points"] or 0))))
    for i, r in enumerate(table):
        r["rank"] = i + 1
    if prev_odds:
        now_rank = {r["team"]: i for i, r in enumerate(sorted(table, key=lambda r: -(float(r["playoff"] or 0))))}
        then_rank = {t: i for i, (t, _v) in enumerate(sorted(prev_odds.items(), key=lambda kv: -(float(kv[1] or 0))))}
        for r in table:
            if r["team"] in then_rank and r["playoff"] is not None:
                r["rank_delta"] = then_rank[r["team"]] - now_rank[r["team"]]       # positive = moved up
    my_row = next((r for r in table if r["team"] == my_team), None)
    losses = (2 * (wk - 1) - int(my_row["wins"] or 0)) if (my_row and wk) else None
    opp_row = next((r for r in table if r["team"] == opponent), None) if opponent else None
    opp_losses = (2 * (wk - 1) - int(opp_row["wins"] or 0)) if (opp_row and wk) else None

    # ---- newest records
    dec = root.decisions(wk) if wk and wk in root.decision_weeks() else {"canonical": [], "archive": []}
    newest = dec["canonical"] + dec["archive"]
    newest.sort(key=lambda e: (e["stamp"] or "", e["name"]), reverse=True)
    lineup_e = _newest(newest, "lineup")
    lineup = root.read_json(lineup_e["rel"], {}) if lineup_e else {}
    cal_e = _newest(newest, "roster_calendar")
    cal = root.read_json(cal_e["rel"], {}) if cal_e else {}
    holes = ((cal.get("calendar") or {}).get("holes") or {}) if cal else {}
    watch_e = _newest(newest, "matchup_watch")
    watch = root.read_json(watch_e["rel"], {}) if watch_e else {}
    designations = [x for x in (watch.get("designations") or []) if x.get("side") == "mine"] if watch else []

    # ---- game day: the next kickoff and the history with this opponent
    kick = kickoff_report(root, wk) if wk else None
    h2h = h2h_report(root, my_team, opponent)

    # ---- health
    win = windows_report(root, week)
    last_job = (runner.list() or [None])[0] if runner is not None else None
    git = logs_git_report(root)
    return {"week": wk, "opponent": opponent, "matchup": matchup, "all_matchups": all_matchups,
            "forecast": mine_fc, "current": mine_cs, "champ": champ, "trajectory": traj, "seed": seed,
            "standings": table, "my_row": my_row, "losses": losses, "opp_row": opp_row, "opp_losses": opp_losses,
            "lineup": lineup, "lineup_link": lineup_e["link"] if lineup_e else None,
            "holes": holes, "calendar_link": cal_e["link"] if cal_e else None,
            "designations": designations, "watch_link": watch_e["link"] if watch_e else None,
            "fresh": fr, "windows": win, "last_job": last_job, "git": git, "kick": kick, "h2h": h2h,
            "hue": team_hue(my_team), "opp_hue": team_hue(opponent) if opponent else None,
            "weeks": root.weeks(), "prev_week": prev_week, "odds_week": ow, "odds_behind": now["behind"],
            "my_move": (odds_moves(root, ow)["teams"].get(my_team) if ow else None)}


def _seed_no(key):
    m = re.search(r"(\d+)", str(key))
    return int(m.group(1)) if m else None


def seed_report(seeds, playoff_pct):
    """My finishing-seed distribution, ready to draw: one row per seed with its share and
    a shade that fades away from the top seed, and the playoff cut marked where the
    running total meets the forecast's playoff probability.

    The cut is READ OFF the two numbers, never assumed: the export decides how many teams
    make it, and if the running total never lands within a point of the playoff figure
    (a different league size, a changed format, a partial export) no cut is claimed and
    the whole distribution is drawn in one neutral colour."""
    rows = []
    for key, val in sorted((seeds or {}).items(), key=lambda kv: (_seed_no(kv[0]) or 99)):
        n = _seed_no(key)
        if n is None or val is None:
            continue
        rows.append({"seed": n, "pct": float(val)})
    if not rows:
        return {"rows": [], "spots": 0, "make": 0.0, "miss": 0.0}
    spots, best, run = 0, None, 0.0
    if playoff_pct is not None:
        for i, r in enumerate(rows, 1):
            run += r["pct"]
            gap = abs(run - float(playoff_pct))
            if best is None or gap < best:
                best, spots = gap, i
        if best is None or best > 1.0:
            spots = 0
    for i, r in enumerate(rows, 1):
        r["cls"] = ("in" if i <= spots else "out") if spots else "na"
        r["cut"] = bool(spots) and i == spots
        r["shade"] = round(max(0.30, 1.0 - 0.15 * (i - 1)), 2)
    return {"rows": rows, "spots": spots,
            "make": round(sum(r["pct"] for r in rows if r["cls"] == "in"), 2),
            "miss": round(sum(r["pct"] for r in rows if r["cls"] == "out"), 2)}


def kickoff_report(root, week, now=None):
    """U6: the synced kickoffs for `week` against the clock -- the next one (ISO, and
    seconds away), how many games start then, how many are still ahead, whether the
    first has yet to kick off or every game is under way, and whether ANY has (UI-F1: Home
    reads live scores on load from then on). Kickoffs come from the sync
    (nfl_schedule._meta.kickoffs); this never reaches the network."""
    sched = root.read_json("current/nfl_schedule.json", {}) or {}
    raw = ((sched.get("_meta") or {}).get("kickoffs") or {}).get(str(int(week))) if week else None
    out = {"week": week, "next": None, "in_seconds": None, "games": 0, "remaining": 0, "at_next": 0,
           "done": False, "first": False, "started": False, "last": None}
    if not raw:
        return out
    now = now or _dt.datetime.now(_dt.timezone.utc)
    kicks = sorted((_parse_iso(t), t) for t in raw)
    ahead = [(d, t) for d, t in kicks if d > now]
    out.update(games=len(kicks), remaining=len(ahead), done=not ahead, first=len(ahead) == len(kicks),
               started=len(ahead) < len(kicks), last=kicks[-1][1])
    if ahead:
        d, t = ahead[0]
        out.update(next=t, in_seconds=int((d - now).total_seconds()), at_next=sum(1 for dd, _t in ahead if dd == d))
    return out


def _h2h_row(season, week, mine, theirs):
    return {"season": season, "week": week, "mine": mine, "theirs": theirs,
            "won": mine > theirs, "tied": mine == theirs}


def h2h_report(root, my_team, opponent):
    """U8: every meeting with `opponent` -- this season from the schedule and the weekly
    actuals, earlier seasons from the season archives under data/logs -- newest first,
    with the record and the last result. Nothing is computed that the logs do not hold."""
    empty = {"rows": [], "wins": 0, "losses": 0, "ties": 0, "last": None, "since": None}
    if not opponent:
        return empty
    rows = []
    state = root.read_json("current/league_state.json", {}) or {}
    season = str(state.get("season") or "this season")
    sched = root.read_json("current/league_schedule.json", []) or []
    actuals = root.read_json("current/weekly_actuals.json", {}) or {}
    for i, pairs in enumerate(sched if isinstance(sched, list) else []):
        wk = i + 1
        if not any(my_team in p and opponent in p for p in (pairs or []) if isinstance(p, (list, tuple))):
            continue
        tr = (actuals.get(f"week_{wk}") or {}).get("team_results") or {}
        a, b = (tr.get(my_team) or {}).get("points_scored"), (tr.get(opponent) or {}).get("points_scored")
        if a is None or b is None:
            continue
        rows.append(_h2h_row(season, wk, float(a), float(b)))
    for e in root.logs():
        name = e.get("name") or ""
        if not (name.startswith("season_") and name.endswith(".json")):
            continue
        arc = root.read_json(e["rel"], {}) or {}
        rmap = {str(k): v for k, v in (arc.get("roster_map") or {}).items()}
        by_name = {v: k for k, v in rmap.items()}
        mine_rid, opp_rid = by_name.get(my_team), by_name.get(opponent)
        if mine_rid is None or opp_rid is None:
            continue
        arc_season = str(arc.get("season") or name[len("season_"):-len(".json")])
        for wk, entries in (arc.get("matchups") or {}).items():
            by_rid = {str(m.get("roster_id")): m for m in (entries or []) if isinstance(m, dict)}
            a, b = by_rid.get(mine_rid), by_rid.get(opp_rid)
            if a and b and a.get("matchup_id") is not None and a.get("matchup_id") == b.get("matchup_id"):
                try:
                    rows.append(_h2h_row(arc_season, int(wk), float(a.get("points") or 0.0), float(b.get("points") or 0.0)))
                except (TypeError, ValueError):
                    continue
    rows.sort(key=lambda r: (r["season"], r["week"]), reverse=True)
    if not rows:
        return empty
    return {"rows": rows, "wins": sum(1 for r in rows if r["won"]), "losses": sum(1 for r in rows if not r["won"] and not r["tied"]),
            "ties": sum(1 for r in rows if r["tied"]), "last": rows[0], "since": rows[-1]["season"]}


def odds_race(root, my_team):
    """Every team's playoff and title odds across the season's forecast exports (U1): one
    series per team in its own hue, mine marked, ordered by the latest odds so a legend
    reads like a table; `labels` are the weeks that ran. A team missing from a week's
    export gets None there."""
    labels, per_week = [], []
    for n in root.weeks():
        o = odds_at(root, n)                                                 # UI-E4
        if not o:
            continue
        labels.append(f"wk {n}")
        per_week.append({t: (v["playoff"], v["champ"]) for t, v in o.items()})
    teams = []
    for wkd in per_week:
        for t in wkd:
            if t not in teams:
                teams.append(t)

    def build(idx):
        out = [{"name": t, "values": [(wkd.get(t) or (None, None))[idx] for wkd in per_week],
                "cls": "me" if t == my_team else "", "hue": team_hue(t)} for t in teams]
        out.sort(key=lambda s: -(next((v for v in reversed(s["values"]) if v is not None), -1.0)))
        return out
    return {"labels": labels, "playoff": build(0), "champ": build(1)}


# ------------------------------------------------------------------- decisions tab
TX_LABELS = {"free_agent": "free-agent add", "waiver": "waiver claim", "trade": "trade"}


def _tx_teams(tx):
    teams = list(tx.get("teams") or [])
    for side in ("adds", "drops"):
        for p in tx.get(side) or []:
            if p.get("to_team") and p["to_team"] not in teams:
                teams.append(p["to_team"])
    return teams


def _pl(p):
    pr = p.get("projection") or {}
    return {"name": p.get("name"), "pos": pr.get("pos") or "", "mean": pr.get("mean"), "to": p.get("to_team")}


def decisions_report(root, my_team):
    """Every logged transaction joined to its paired evaluation (the same decision log
    carries both: a `type` row per move, a `record_type: evaluation` row per finished
    evaluate_move --evaluate-unevaluated run, keyed by transaction_id). Nothing is
    computed here that the tools did not already write; the page only joins and sums."""
    rows = []
    for e in root.logs():
        if e["name"] == "decision_log.jsonl":
            rows, _n = root.tail_jsonl(e["rel"], n=1000000)
            break
    evals = {}
    for r in rows:                              # newest first: the first seen per id wins
        if r.get("record_type") == "evaluation" and r.get("transaction_id") not in evals:
            evals[r["transaction_id"]] = r
    out = []
    for r in rows:
        if not r.get("type"):
            continue
        ev = evals.get(r.get("transaction_id"))
        teams = _tx_teams(r)
        effect = {}
        if ev and not ev.get("skipped"):
            for t, v in (ev.get("teams") or {}).items():
                if not isinstance(v, dict):
                    continue
                pp, cc = v.get("playoff_pct") or {}, v.get("champ_pct") or {}
                effect[t] = {"playoff": pp.get("delta"), "playoff_se": pp.get("se"), "champ": cc.get("delta"), "champ_se": cc.get("se"),
                             "playoff_with": pp.get("with"), "playoff_without": pp.get("without")}
        actor = None
        for p in r.get("adds") or []:
            actor = p.get("to_team") or actor
        actor = actor or (teams[0] if teams else None)
        out.append({"id": r.get("transaction_id"), "created": r.get("created"), "week": r.get("week"),
                    "type": r.get("type"), "label": TX_LABELS.get(r.get("type"), r.get("type")), "is_mine": bool(r.get("is_mine")),
                    "teams": teams, "actor": actor,
                    "adds": [_pl(p) for p in r.get("adds") or []], "drops": [_pl(p) for p in r.get("drops") or []],
                    "faab_bid": r.get("faab_bid"), "faab": r.get("faab"),
                    "evaluated": bool(ev), "skipped": (ev or {}).get("skipped"), "evaluated_at": (ev or {}).get("evaluated_at"),
                    "n_sims": (ev or {}).get("n_sims"), "batches": (ev or {}).get("batches"), "reversed": (ev or {}).get("post_execution_reversed"),
                    "effect": effect, "mine_effect": effect.get(my_team), "actor_effect": effect.get(actor) if actor else None})
    # per-team ledger: the sum of each team's OWN moves' effects on its own chances
    ledger = {}
    for d in out:
        for t in d["teams"]:
            if t != d["actor"] and d["type"] != "trade":
                continue
            L = ledger.setdefault(t, {"team": t, "moves": 0, "evaluated": 0, "playoff": 0.0, "champ": 0.0, "var_p": 0.0, "var_c": 0.0})
            L["moves"] += 1
            fx = d["effect"].get(t)
            if fx and fx.get("playoff") is not None:
                L["evaluated"] += 1
                L["playoff"] += fx["playoff"]
                L["champ"] += fx.get("champ") or 0.0
                L["var_p"] += (fx.get("playoff_se") or 0.0) ** 2
                L["var_c"] += (fx.get("champ_se") or 0.0) ** 2
    table = sorted(ledger.values(), key=lambda L: -L["playoff"])
    for L in table:
        L["playoff_se"] = L.pop("var_p") ** 0.5
        L["champ_se"] = L.pop("var_c") ** 0.5
    mine = [d for d in out if d["is_mine"] or my_team in d["teams"]]
    # U10: the awards -- best and worst (move, team) of the season and of the latest week
    # with an evaluated move -- and each team's own cumulative line, oldest first
    pairs = []
    for d in out:
        for t in d["teams"]:
            if t != d["actor"] and d["type"] != "trade":
                continue
            fx = d["effect"].get(t)
            if fx and fx.get("playoff") is not None:
                pairs.append({"id": d["id"], "team": t, "playoff": fx["playoff"], "playoff_se": fx.get("playoff_se"),
                              "champ": fx.get("champ"), "week": d["week"], "label": d["label"], "type": d["type"],
                              # a trade: what came to this team, and what left it (the other side's arrivals)
                              "adds": [p for p in d["adds"] if p.get("to") == t] if d["type"] == "trade" else d["adds"],
                              "drops": [p for p in d["adds"] if p.get("to") not in (t, None)] if d["type"] == "trade" else d["drops"],
                              "created": d["created"], "is_mine": t == my_team})
    latest_week = max((p["week"] for p in pairs if p.get("week") is not None), default=None)
    week_pairs = [p for p in pairs if p.get("week") == latest_week] if latest_week is not None else []
    awards = {"best": max(pairs, key=lambda p: p["playoff"]) if pairs else None,
              "worst": min(pairs, key=lambda p: p["playoff"]) if pairs else None,
              "week": latest_week,
              "best_week": max(week_pairs, key=lambda p: p["playoff"]) if week_pairs else None,
              "worst_week": min(week_pairs, key=lambda p: p["playoff"]) if week_pairs else None}
    timelines = {}
    for p in reversed(pairs):                   # the log is newest first; a line runs oldest first
        prev = timelines.get(p["team"], [0.0])[-1] if p["team"] in timelines else 0.0
        timelines.setdefault(p["team"], []).append(round(prev + p["playoff"], 2))

    series, run = [], 0.0                       # my cumulative playoff delta, oldest first
    for d in reversed(mine):
        fx = d["effect"].get(my_team)
        if fx and fx.get("playoff") is not None:
            run += fx["playoff"]
            series.append({"at": d["created"], "week": d["week"], "value": round(run, 2), "label": d["label"]})
    return {"decisions": out, "mine": mine, "ledger": table, "my_row": next((L for L in table if L["team"] == my_team), None),
            "series": series, "n": len(out), "awards": awards, "timelines": timelines,
            "n_evaluated": sum(1 for d in out if d["evaluated"] and not d["skipped"]),
            "n_skipped": sum(1 for d in out if d["skipped"]), "n_pending": sum(1 for d in out if not d["evaluated"])}


# ---------------------------------------------------------------- roster VORP (League)
def roster_vorp(root):
    """The newest roster_grades record that carries per-player detail (`rosters`, written
    by scripts.roster_grades since 2026-09-27): team VORP and rank, and each player's
    VORP over the replacement level of his position. None until one has been run."""
    best = None
    for wk in sorted(root.decision_weeks(), reverse=True):
        dec = root.decisions(wk)
        for e in sorted(dec["canonical"] + dec["archive"], key=lambda e: (e["stamp"] or "", e["name"]), reverse=True):
            if e["tool"] != "roster_grades":
                continue
            d = root.read_json(e["rel"], {}) or {}
            if d.get("rosters"):
                best = (e, d)
                break
        if best:
            break
    if not best:
        return None
    e, d = best
    teams = {t["team"]: t for t in ((d.get("league") or {}).get("teams") or []) if isinstance(t, dict)}
    players = {team: {p.get("name"): p for p in (det.get("players") or []) if isinstance(p, dict)}
               for team, det in (d.get("rosters") or {}).items()}
    return {"entry": e, "week": (d.get("league") or {}).get("week"), "stamp": d.get("timestamp_utc"),
            "teams": teams, "players": players,
            "replacement": {team: (det.get("replacement_levels") or {}) for team, det in (d.get("rosters") or {}).items()}}
