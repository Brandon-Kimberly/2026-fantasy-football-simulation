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
    return {"status": status, "reasons": list(reasons), "manifest": manifest, "week": week,
            "vegas_week": meta.get("week"), "vegas_stale_since": meta.get("stale_since"),
            "age_hours": age_h, "sources": sources, "n_ok": n_ok, "n_fell": n_fell, "n_bad": n_bad}


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
def team_hue(name):
    """A stable hue per team, from the pseudonym: color follows the entity, never its rank."""
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
    d = f"weeks/week_{wk:02d}" if wk else None

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

    # ---- season standing
    forecast = root.read_json(f"{d}/live_season_forecast_week_{wk}.json", {}) if d else {}
    forecast = forecast or {}
    mine_fc = (forecast.get(my_team) or {}).get("forecast") or {}
    mine_cs = (forecast.get(my_team) or {}).get("current_state") or {}
    matrix = root.read_json(f"{d}/syndicate_comprehensive_matrix_week_{wk}.json", {}) if d else {}
    matrix = matrix or {}
    traj = ((matrix.get("weekly_trajectories") or {}).get(my_team) or {}).get("expected_cumulative_wins_by_week") or []
    outcomes = {o.get("Team"): o for o in (matrix.get("season_outcomes") or []) if isinstance(o, dict)}
    champ = (outcomes.get(my_team) or {}).get("Champ_Pct")

    # ---- standings
    standings = root.read_json("current/league_standings.json", {}) or {}
    table = []
    for team, s in standings.items():
        fc = (forecast.get(team) or {}).get("forecast") or {}
        table.append({"team": team, "wins": s.get("h2h_wins"), "points": s.get("points_scored"),
                      "faab": s.get("remaining_faab"), "playoff": fc.get("playoff_probability_pct"),
                      "champ": (outcomes.get(team) or {}).get("Champ_Pct"), "hue": team_hue(team)})
    table.sort(key=lambda r: (-(float(r["wins"] or 0)), -(float(r["points"] or 0))))
    for i, r in enumerate(table):
        r["rank"] = i + 1
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

    # ---- health
    win = windows_report(root, week)
    last_job = (runner.list() or [None])[0] if runner is not None else None
    git = logs_git_report(root)
    return {"week": wk, "opponent": opponent, "matchup": matchup, "all_matchups": all_matchups,
            "forecast": mine_fc, "current": mine_cs, "champ": champ, "trajectory": traj,
            "standings": table, "my_row": my_row, "losses": losses, "opp_row": opp_row, "opp_losses": opp_losses,
            "lineup": lineup, "lineup_link": lineup_e["link"] if lineup_e else None,
            "holes": holes, "calendar_link": cal_e["link"] if cal_e else None,
            "designations": designations, "watch_link": watch_e["link"] if watch_e else None,
            "fresh": fr, "windows": win, "last_job": last_job, "git": git,
            "hue": team_hue(my_team), "opp_hue": team_hue(opponent) if opponent else None,
            "weeks": root.weeks()}
