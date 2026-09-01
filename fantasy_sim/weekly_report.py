"""
fantasy_sim.weekly_report

The weekly orchestrator's logic (scripts/weekly_report.py is the thin entry point): run the
chain sync -> simulation -> roster grades -> lineup -> matchup -> waivers (-> trades with
--full), each step in-process so the digest is built from the tools' returned objects, and
FAIL LOUD: the first exception -- or a failed gate -- stops the chain, the digest carries a
FAILED banner naming the step and the error, and nothing downstream runs on stale or partial
data (F11's lesson applied to the pipeline).

Gates:
  * after sync: a manifest from THIS run must exist (sync_all writes it last, so its presence
    means completion) -- gate_sync_fresh;
  * after the simulation: the week's export must be newer than the step's start --
    gate_export_fresh.
A manifest with `degraded` entries is not a failure (those are tolerated by design) but is
rendered at the top of the digest so it is never invisible.
"""
import datetime as _dt
import os
import time
import traceback

from fantasy_sim.freshness import read_manifest, read_export_mtime   # module attrs: patchable in tests
from fantasy_sim.storage import ensure_dir_for, decisions_path


class StepFailed(Exception):
    """A gate refused: the previous step did not leave the data it was supposed to."""


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------- runner
def run_steps(steps):
    """steps: [(name, callable)]. Runs in order; stops at the first exception."""
    report = {"status": "OK", "failed_step": None, "error": None, "traceback": None, "results": {},
              "planned": [n for n, _ in steps], "started_at": _now_iso(), "finished_at": None}
    for name, fn in steps:
        try:
            report["results"][name] = fn()
        except Exception as e:          # noqa: BLE001 -- the whole point is to catch, record, stop
            report["status"] = "FAILED"
            report["failed_step"] = name
            report["error"] = f"{type(e).__name__}: {e}"
            report["traceback"] = "".join(traceback.format_exception(type(e), e, e.__traceback__))[-3000:]
            break
    report["finished_at"] = _now_iso()
    return report


# ----------------------------------------------------------------------------- gates
def gate_sync_fresh(run_started):
    manifest, started = read_manifest()
    if not manifest or not manifest.get("ok") or started is None:
        raise StepFailed("sync did not complete: no sync_manifest.json from this run "
                         "(sync_all writes it last; its absence means the sync raised or was interrupted)")
    if started < run_started - 1.0:
        raise StepFailed(f"sync did not complete: the manifest on disk is from an earlier run "
                         f"({manifest.get('started_at')}), not this one")
    return manifest


def gate_export_fresh(week, step_started):
    mtime = read_export_mtime(week)
    if mtime is None:
        raise StepFailed(f"simulation did not produce the week-{week} export")
    if mtime < step_started - 1.0:
        raise StepFailed(f"the week-{week} export on disk predates this run's simulation step -- "
                         "the simulation did not write it")


# ---------------------------------------------------------------------------- digest
def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def render_digest(report, team, week):
    res = report.get("results", {})
    md = [f"# Weekly report -- {team}, week {week}",
          f"_{report.get('started_at', '')} -> {report.get('finished_at', '')} UTC_", ""]
    if report.get("status") == "FAILED":
        md += [f"## FAILED AT STEP `{report.get('failed_step')}`", "",
               f"**{report.get('error')}**", "",
               "Downstream steps did not run. Nothing below reflects this week's data beyond the steps "
               "listed as completed; fix the failure and re-run.", ""]
        planned = report.get("planned") or []
        if planned:
            done = list(res)
            md += ["Completed: " + (", ".join(done) if done else "none") + "  ",
                   "Did not run: " + ", ".join(n for n in planned if n not in done and n != report.get("failed_step")), ""]
        if report.get("traceback"):
            md += ["```", report["traceback"].strip()[-1500:], "```", ""]

    manifest = (res.get("sync") or {}).get("manifest")
    if manifest:
        md += [f"Sync for week {manifest.get('current_week')} completed {manifest.get('finished_at')} UTC "
               f"({manifest.get('notices_count', 0)} routine notices).", ""]
        if manifest.get("degraded"):
            md += ["## DEGRADED -- the sync tolerated these failures", ""]
            md += [f"- {d}" for d in manifest["degraded"]] + [""]
    if res.get("freshness"):
        st, reasons = res["freshness"]["status"], res["freshness"]["reasons"]
        md += [f"## DATA FRESHNESS (sync skipped): **{st}**", ""] + [f"- {r}" for r in reasons] + [""]
    if report.get("status") == "FAILED":
        return "\n".join(md)

    sim = res.get("simulation")
    if sim and sim.get("season_outcomes"):
        rows = sorted(sim["season_outcomes"], key=lambda r: -r["Playoff_Pct"])
        md += ["## Season outlook", ""]
        md += [_table(["team", "Playoff%", "Champ%", "Exp W", "Exp Pts"],
                      [[("**%s**" % r["Team"]) if r["Team"] == team else r["Team"], f"{r['Playoff_Pct']:.1f}",
                        f"{r['Champ_Pct']:.1f}", f"{r['Expected_Wins']:.1f}", f"{r['Expected_Points']:.0f}"] for r in rows]), ""]

    lg = res.get("league")
    if lg:
        md += [f"## League this week -- all matchups (n={lg.get('n')}, {'cross-roster copula' if lg.get('cross') else 'per-roster copula'})", ""]
        md += [_table(["matchup", "P(A wins)", "P(B wins)", "+-", "A exp", "B exp", "margin sd"],
                      [[f"{m['a']} v {m['b']}", f"{100 * m['p_a']:.1f}%", f"{100 * m['p_b']:.1f}%", f"{100 * m['se']:.1f}",
                        f"{m['a_expected']:.1f}", f"{m['b_expected']:.1f}", f"{m['margin_sd']:.1f}"] for m in lg["matchups"]]), ""]
        md += [_table(["team", "opponent", "P(>= median)", "expected", "sd"],
                      [[("**%s**" % t) if t == team else t, d.get("opponent") or "-", f"{100 * d['p_beat_median']:.1f}%",
                        f"{d['expected_total']:.1f}", f"{d['sd_total']:.1f}"]
                       for t, d in sorted(lg["teams"].items(), key=lambda kv: -kv[1]["p_beat_median"])]), ""]
        for t, d in lg["teams"].items():
            md += [f"- {t} lineup: " + ", ".join(f"{x['slot']} {x['name']} ({x['expected']:.1f})" for x in d["lineup"])]
        md += [""]

    rg = res.get("roster_grades")
    if rg:
        md += ["## Roster grade", ""]
        lt = rg.get("league", {}).get("teams", [])
        md += [_table(["#", "team", "lineup VORP", "depth VORP", "opt score", "holes", "T1 starters", "starters < rep"],
                      [[t["rank"], ("**%s**" % t["team"]) if t["team"] == team else t["team"], f"{t['lineup_vorp']:.1f}",
                        f"{t['depth_vorp']:.1f}", f"{t['optimal_score']:.1f}", t["holes"], t["tier1_starters"],
                        t["starters_below_replacement"]] for t in lt]), ""]
        bp = (rg.get("team_detail") or {}).get("by_position", {})
        if bp:
            md += [_table(["pos", "starters", "bench", "start VORP", "depth VORP", "best free agent"],
                          [[p, b["n_starters"], b["n_bench"], f"{b['starters_vorp']:.1f}", f"{b['depth_vorp']:.1f}",
                            (f"{b['best_free_agent']['name']} ({b['best_free_agent']['vorp']:+.1f})" if b.get("best_free_agent") else "-")]
                           for p, b in sorted(bp.items())]), ""]

    lu = res.get("lineup")
    if lu:
        md += [f"## Lineup -- expected total {lu['expected_total']:.1f}" + (f", UNFILLED: {lu['unfilled']}" if lu.get("unfilled") else ""), ""]
        md += [_table(["slot", "player", "pos", "exp", "p10", "p50", "p90", "zero", "margin", "alternative"],
                      [[r["slot"], r["name"], r["pos"], f"{r['expected']:.1f}", f"{r['p10']:.1f}", f"{r['p50']:.1f}", f"{r['p90']:.1f}",
                        f"{100 * r['p_zero']:.0f}%", (f"{r['margin']:+.1f}" if r.get("alternative") else "-"), r.get("alternative") or "-"]
                       for r in lu["lineup"]]), ""]
        if lu.get("bench"):
            md += ["Bench: " + ", ".join(f"{b['name']} ({b['expected']:.1f}{', ' + b['reason'] if b.get('reason') else ''})" for b in lu["bench"]), ""]

    mu = res.get("matchup")
    if mu:
        c = mu["constructions"]
        md += [f"## Matchup -- vs {mu['opponent']} ({'favoured' if mu.get('favoured_by_max_mean') else 'underdog'} on the engine's lineup; "
               f"n={mu.get('n')}, {'cross-roster copula' if mu.get('cross') else 'per-roster copula'})", ""]
        md += [_table(["construction", "mean", "sd", "P(beat opp)", "+-", "P(>= median)", "margin", "margin sd"],
                      [[k, f"{c[k]['mean']:.1f}", f"{c[k]['sd']:.1f}", f"{100 * c[k]['p_beat_opponent']:.1f}%", f"{100 * c[k]['se']:.1f}",
                        f"{100 * c[k]['p_beat_median']:.1f}%", f"{c[k]['margin_mean']:+.1f}", f"{c[k]['margin_sd']:.1f}"]
                       for k in mu["ranking_by_p_beat_opponent"]]), ""]
        lineups = {tuple(sorted(x["name"] for x in v["lineup"])) for v in c.values()}
        if len(lineups) == 1:
            md += ["All four constructions pick the same lineup: **no variance lever on this roster this week** "
                   "(every bench alternative is dominated at its slot).", ""]
        else:
            best = mu["ranking_by_p_beat_opponent"][0]
            base = {x["slot"]: x["name"] for x in c["max_mean"]["lineup"]}
            diffs = [f"{x['slot']}: {base.get(x['slot'])} -> {x['name']}" for x in c[best]["lineup"] if base.get(x["slot"]) != x["name"]]
            md += [f"Best by P(beat opponent): **{best}**" + (" -- changes vs max_mean: " + "; ".join(diffs) if diffs else ""), ""]
        md += [f"Opponent lineup ({'assumed' if mu.get('opponent_lineup_assumed') else 'supplied'}): "
               + ", ".join(f"{x['name']} ({x['expected']:.1f})" for x in mu.get("opponent_lineup", [])), ""]

    wv = res.get("waivers")
    if wv:
        md += [f"## Waiver targets -- FAAB {wv['remaining_faab']:.0f} (league avg {wv['league_avg_faab']:.0f}); "
               f"holes: {wv['holes'] or 'none'}; next week: {wv['holes_next_week'] or 'none'}", ""]
        md += [_table(["player", "pos", "tier", "season", "VORP", "wk mean", "p10", "p50", "p90", "fills", "bid*", "incumbent / P(beats)"],
                      [[t["name"], t["pos"], t.get("tier") or "-", f"{t['mean']:.1f}", f"{t['vorp']:+.1f}", f"{t['week']['mean']:.1f}",
                        f"{t['week']['p10']:.1f}", f"{t['week']['p50']:.1f}", f"{t['week']['p90']:.1f}", t["fills"], t["bid"]["suggested"],
                        (f"{t['incumbent']} / {100 * t['p_beats_incumbent']['p']:.0f}%" if t.get("p_beats_incumbent") else "-")]
                       for t in wv["targets"]]), "",
               "\\* bid = UNVERIFIED value heuristic. P(beats incumbent): " + wv.get("caveat", ""), ""]

    tr = res.get("trades")
    if tr:
        md += [f"## Trade targets ({tr.get('contention_note', '')})", ""]
        md += [_table(["from", "target", "behind", "slot", "I give", "I get", "my +", "their +", "ok", "PO%", "seller", "will"],
                      [[b["with"], b["target"], b.get("buried_behind") or "-", b.get("fills_my_slot") or "-", ", ".join(b["i_give"]),
                        ", ".join(b["i_get"]), f"{b['my_gain']:+.1f}", f"{b['their_gain']:+.1f}", "yes" if b["acceptable"] else "no",
                        (f"{b['their_playoff_pct']:.0f}" if b.get("their_playoff_pct") is not None else "-"),
                        ("yes" if b.get("seller") else "no") if b.get("seller") is not None else "-", b.get("willingness", "-")]
                       for b in tr.get("buy", [])]), ""]
        if tr.get("sell"):
            md += ["Sell side: " + "; ".join(f"{s['buyer']} wants {', '.join(s['they_want'])} for {', '.join(s['they_give'])} "
                                              f"({s['my_gain']:+.1f} / {s['their_gain']:+.1f})" for s in tr["sell"][:5]), ""]
    return "\n".join(md)


def write_digest(md, path):
    ensure_dir_for(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


# ------------------------------------------------------------------- the real chain
def build_steps(team, full=False, skip_sync=False, sims=5000, evaluate=0):
    """Wires the real tools. Each step returns the object the digest renders from."""
    from fantasy_sim.storage import load_json, syndicate_comprehensive_matrix_path, LEAGUE_STATE_FILE
    state = {"run_started": time.time(), "week": None}

    def step_sync():
        from fantasy_sim.sync import sync_all
        sync_all()
        manifest = gate_sync_fresh(state["run_started"])
        state["week"] = int(manifest["current_week"])
        return {"manifest": manifest}

    def step_freshness():
        from fantasy_sim.freshness import check
        status, reasons, details = check(offline=False)
        state["week"] = details.get("week") or load_json(LEAGUE_STATE_FILE).get("current_week", 1)
        if status == "STALE":
            raise StepFailed("sync skipped and the data on disk is STALE: " + "; ".join(reasons))
        return {"status": status, "reasons": reasons, "manifest": details.get("manifest")}

    def step_simulation():
        from scripts.run_simulation import main as run_simulation
        started = time.time()
        run_simulation()
        gate_export_fresh(state["week"], started)
        rows = load_json(syndicate_comprehensive_matrix_path(state["week"])).get("season_outcomes", [])
        return {"season_outcomes": rows}

    def step_league():
        from fantasy_sim.decisions import league_week_outlook
        from fantasy_sim.simulation import FantasySimulationEngine
        return league_week_outlook(FantasySimulationEngine(), state["week"], sims=sims)

    def step_roster_grades():
        from scripts.roster_grades import main as m
        return m(["--team", team, "--week", str(state["week"])])

    def step_lineup():
        from scripts.optimize_lineup import main as m
        return m(["--team", team, "--week", str(state["week"])])

    def step_matchup():
        from scripts.matchup_lineup import main as m
        return m(["--team", team, "--week", str(state["week"]), "--sims", str(sims)])

    def step_waivers():
        from scripts.waiver_targets import main as m
        return m(["--team", team, "--week", str(state["week"])])

    def step_trades():
        from scripts.find_trades import main as m
        return m(["--team", team, "--week", str(state["week"]), "--evaluate", str(evaluate)])

    steps = [("freshness", step_freshness)] if skip_sync else [("sync", step_sync)]
    steps += [("simulation", step_simulation), ("league", step_league), ("roster_grades", step_roster_grades), ("lineup", step_lineup),
              ("matchup", step_matchup), ("waivers", step_waivers)]
    if full:
        steps.append(("trades", step_trades))
    return steps, state


def run_weekly_report(team, full=False, skip_sync=False, sims=5000, evaluate=0):
    steps, state = build_steps(team, full=full, skip_sync=skip_sync, sims=sims, evaluate=evaluate)
    report = run_steps(steps)
    week = state["week"] or "?"
    md = render_digest(report, team, week)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = write_digest(md, decisions_path(f"weekly_report_week{week}_{stamp}{'_FAILED' if report['status'] == 'FAILED' else ''}.md"))
    return report, md, path
