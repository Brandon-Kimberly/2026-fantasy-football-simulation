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
    reasons = list(reasons)
    # B8: assess() returns one list that mixes the cause of a STALE verdict with every
    # tolerated fallback ("degraded: ..."). Pages show the cause; the rest stays folded.
    stale_reasons = [r for r in reasons if not str(r).startswith("degraded:")]
    degraded_reasons = [str(r)[len("degraded:"):].strip() for r in reasons if str(r).startswith("degraded:")]
    return {"status": status, "reasons": reasons, "stale_reasons": stale_reasons, "degraded_reasons": degraded_reasons,
            "manifest": manifest, "week": week,
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

    # ---- standings, with each team's odds through the season and its move since the last forecast
    standings = root.read_json("current/league_standings.json", {}) or {}
    race = odds_race(root, my_team)
    sparks = {s["name"]: s["values"] for s in race["playoff"]}
    prev_week = max((w for w in root.weeks() if wk and w < wk), default=None)
    prev = (root.read_json(f"weeks/week_{prev_week:02d}/live_season_forecast_week_{prev_week}.json", {}) or {}) if prev_week else {}
    prev_odds = {t: ((v or {}).get("forecast") or {}).get("playoff_probability_pct") for t, v in prev.items() if isinstance(v, dict)}
    table = []
    for team, s in standings.items():
        fc = (forecast.get(team) or {}).get("forecast") or {}
        table.append({"team": team, "wins": s.get("h2h_wins"), "points": s.get("points_scored"),
                      "faab": s.get("remaining_faab"), "playoff": fc.get("playoff_probability_pct"),
                      "champ": (outcomes.get(team) or {}).get("Champ_Pct"), "hue": team_hue(team),
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
            "weeks": root.weeks(), "prev_week": prev_week}


def odds_race(root, my_team):
    """Every team's playoff and title odds across the season's forecast exports (U1): one
    series per team in its own hue, mine marked, ordered by the latest odds so a legend
    reads like a table; `labels` are the weeks that ran. A team missing from a week's
    export gets None there."""
    labels, per_week = [], []
    for n in root.weeks():
        f = root.read_json(f"weeks/week_{n:02d}/live_season_forecast_week_{n}.json", {}) or {}
        if not f:
            continue
        m = root.read_json(f"weeks/week_{n:02d}/syndicate_comprehensive_matrix_week_{n}.json", {}) or {}
        outcomes = {o.get("Team"): o for o in (m.get("season_outcomes") or []) if isinstance(o, dict)}
        labels.append(f"wk {n}")
        per_week.append({t: (((v or {}).get("forecast") or {}).get("playoff_probability_pct"), (outcomes.get(t) or {}).get("Champ_Pct"))
                         for t, v in f.items() if isinstance(v, dict)})
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
    series, run = [], 0.0                       # my cumulative playoff delta, oldest first
    for d in reversed(mine):
        fx = d["effect"].get(my_team)
        if fx and fx.get("playoff") is not None:
            run += fx["playoff"]
            series.append({"at": d["created"], "week": d["week"], "value": round(run, 2), "label": d["label"]})
    return {"decisions": out, "mine": mine, "ledger": table, "my_row": next((L for L in table if L["team"] == my_team), None),
            "series": series, "n": len(out),
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
