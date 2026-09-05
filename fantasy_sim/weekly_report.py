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
import logging
import os
import time
import traceback

import base64
from html import escape

from fantasy_sim.freshness import read_manifest, read_export_mtime   # module attrs: patchable in tests
from fantasy_sim.positional_tiers import _TABLE_CSS, _TABLE_JS       # the sortable-table pattern, reused as-is
from fantasy_sim.storage import (
    SYNC_MANIFEST_FILE, VEGAS_FILE, load_json, predictions_log_file, decisions_adhoc_path, decisions_week_path,
    ensure_dir_for, decisions_path, season_outcomes_chart_path, all_teams_trajectories_chart_path,
    win_trajectory_chart_path, expected_wins_chart_path, power_rankings_chart_path, h2h_heatmap_chart_path,
    seeding_distribution_path, weekly_scoring_density_path, boom_bust_chart_path, floor_ceiling_chart_path,
    tier_chart_path, positional_tiers_table_path, sos_roster_chart_path, sos_team_summary_chart_path,
)


class StepFailed(Exception):
    """A gate refused: the previous step did not leave the data it was supposed to."""


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------- runner
def _git_head():
    """The current commit hash, or None outside a working git checkout."""
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10, check=True).stdout.strip() or None
    except Exception:
        return None


def append_predictions_log(week, season_outcomes, outlook, path=None, manifest=None,
                           commit=None, backfilled=False, provenance=None, canonical=False):
    """The weekly prediction record (storage.predictions_log_file) F18's decision
    retrospective and F19's cross-week trajectory both read from: one JSON line per week
    carrying the season-outcome table (all teams' Playoff_Pct / Champ_Pct / Expected_Wins,
    plus the SE and points columns the table already has), this week's matchup win
    probabilities and each team's P(>= median), the commit hash, and the sync manifest's
    timestamps. Tracked in git, unlike data/weeks/ -- the point is surviving a machine loss.

    Append-only: a re-run within a week appends again; consumers keep the last row per
    (season, week), the projection log's convention. DIVERGENCE from append_projection_log's
    warn-never-raise, on purpose: this runs as an orchestrator STEP and the orchestrator's
    contract is fail-loud -- a silent miss would be a hole in the F18/F19 record nobody
    notices until January. Returns rows appended (always 1)."""
    if manifest is None:
        manifest = load_json(SYNC_MANIFEST_FILE)
    season = str(manifest.get("season") or "")
    if path is None:
        path = predictions_log_file(season)
    record = {
        "record_type": "week_predictions", "season": season, "week": week,
        "logged_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": commit, "backfilled": bool(backfilled), "canonical": bool(canonical),
        "sync_started_at": manifest.get("started_at"), "sync_finished_at": manifest.get("finished_at"),
        "season_outcomes": season_outcomes,
        "matchups": [{"a": m.get("a"), "b": m.get("b"), "p_a": m.get("p_a"), "p_b": m.get("p_b"),
                      "se": m.get("se")} for m in (outlook.get("matchups") or [])],
        "median": {t: {"opponent": d.get("opponent"), "p_beat_median": d.get("p_beat_median"),
                       "expected_total": d.get("expected_total"), "sd_total": d.get("sd_total")}
                   for t, d in (outlook.get("teams") or {}).items()},
        "outlook_sims": outlook.get("n"), "outlook_cross": outlook.get("cross"),
    }
    if provenance is not None:
        record["provenance"] = provenance
    import json as _json
    ensure_dir_for(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(_json.dumps(record, sort_keys=True) + "\n")
    return 1


def _run_git(args):
    """subprocess seam for commit_and_push_logs; returns (returncode, combined output)."""
    import subprocess
    out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=120)
    return out.returncode, (out.stdout or "") + (out.stderr or "")


def commit_and_push_logs(week, git=_run_git):
    """The canonical run's durability step (owner, 2026-09-04): commit the git-tracked
    data/logs files if anything changed, scoped with an explicit pathspec so a user's
    unrelated staged work is never swept into an automated commit, then push. The logs
    are the season's only unrecoverable data (Sleeper serves projections for the current
    week only); the push is what survives a machine loss (AUDIT_PLAN.md R1).

    Warn-never-fail, a DOCUMENTED divergence from the orchestrator's fail-loud contract:
    a push failure (network down, remote refusing) does not invalidate the report the run
    just produced, and the failure still surfaces twice -- in the digest's housekeeping
    line and as check_freshness's ACTION line -- so it cannot rot silently."""
    result = {"committed": 0, "pushed": False}
    try:
        rc, out = git(["add", "--", "data/logs"])
        if rc != 0:
            result["warning"] = f"git add failed: {out.strip()}"
            logging.warning("LOGS PUSH: %s", result["warning"])
            return result
        rc, _ = git(["diff", "--cached", "--quiet", "--", "data/logs"])
        if rc != 0:   # exit 1 = staged changes under data/logs
            rc, out = git(["commit", "-m", f"Logs: week {int(week):02d} canonical capture",
                           "--", "data/logs"])
            if rc != 0:
                result["warning"] = f"git commit failed: {out.strip()}"
                logging.warning("LOGS PUSH: %s", result["warning"])
                return result
            result["committed"] = 1
        else:
            rc, out = git(["rev-list", "--count", "@{u}..HEAD", "--", "data/logs"])
            if rc == 0 and (out or "").strip() == "0":
                return result   # nothing new locally and nothing unpushed
        rc, out = git(["push"])
        if rc != 0:
            result["warning"] = f"git push failed (committed locally): {out.strip()}"
            logging.warning("LOGS PUSH: %s", result["warning"])
            return result
        result["pushed"] = True
        return result
    except Exception as ex:
        result["warning"] = f"logs push errored: {ex}"
        logging.warning("LOGS PUSH: %s", result["warning"])
        return result


def run_provenance(manifest, vegas_meta):
    """F36's DEGRADED-judgment mitigation, made durable (2026-09-04): the sync state a
    prediction row was quoted under. The manifest is untracked and overwritten every
    sync, so the row's provenance is the only record of it that survives."""
    import os as _os
    return {"vegas_source": (vegas_meta or {}).get("source"),
            "degraded": len((manifest or {}).get("degraded") or []),
            "runner": bool(_os.environ.get("GITHUB_ACTIONS"))}


def _decision_log_summary(week, log_path=None):
    """The decision log, finally rendered: this week's transactions joined to their
    evaluation records, with the contemporaneity split computed from data -- which frozen
    snapshots were actually recorded at decision time. Backfilled (retro) rows' projections
    were never contemporaneous, which F18's retrospective must know without inferring it
    from per-row flags. Read-only; None when there is no log."""
    import json as _json
    if log_path is None:
        from fantasy_sim.storage import DECISION_LOG_FILE as log_path  # noqa: F811
    try:
        with open(log_path, encoding="utf-8") as f:
            rows = [_json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return None
    evals = {r.get("transaction_id"): r for r in rows if r.get("record_type") == "evaluation"}
    txs, _seen = [], set()   # first row per transaction_id: union-merge tolerance (2026-09-04)
    for r in rows:
        if r.get("record_type") is not None:
            continue
        tid = r.get("transaction_id")
        if tid is not None and tid in _seen:
            continue
        if tid is not None:
            _seen.add(tid)
        txs.append(r)
    if not txs:
        return None

    def players(entries):
        return [(e.get("name"), (e.get("projection") or {}).get("mean")) for e in entries or []]

    out_rows = []
    for t in txs:
        if t.get("week") != week:
            continue
        ev = evals.get(t.get("transaction_id"))
        ev_summary = None
        if ev:
            mover = (t.get("teams") or [None])[0]
            d = (ev.get("teams") or {}).get(mover) or {}
            ev_summary = {"champ_delta": (d.get("champ_pct") or {}).get("delta"),
                          "champ_se": (d.get("champ_pct") or {}).get("se"),
                          "playoff_delta": (d.get("playoff_pct") or {}).get("delta"),
                          "playoff_se": (d.get("playoff_pct") or {}).get("se")}
        out_rows.append({"transaction_id": t.get("transaction_id"), "created": t.get("created"),
                         "team": (t.get("teams") or ["?"])[0], "type": t.get("type"),
                         "adds": players(t.get("adds")), "drops": players(t.get("drops")),
                         "faab_bid": t.get("faab_bid"), "is_mine": bool(t.get("is_mine")),
                         "retro": bool(t.get("snapshot_is_retroactive")),
                         "lag_days": t.get("snapshot_lag_days"), "eval": ev_summary})
    return {"week": week, "rows": out_rows,
            "older_unevaluated": sum(1 for t in txs if t.get("week") != week
                                     and t.get("transaction_id") not in evals),
            "mine_count": sum(1 for r in out_rows if r["is_mine"]),
            "contemporaneous_mine": sum(1 for r in out_rows if r["is_mine"] and not r["retro"]),
            "contemporaneous_other": sum(1 for r in out_rows if not r["is_mine"] and not r["retro"]),
            "retro_count": sum(1 for r in out_rows if r["retro"])}


def _declog_caveat(dl):
    return (f"Frozen snapshots are contemporaneous only for moves made after the log's first "
            f"ingestion: {dl['contemporaneous_mine']} of my {dl['mine_count']} and "
            f"{dl['contemporaneous_other']} league move(s) this week; the {dl['retro_count']} "
            f"retro-flagged row(s) were backfilled -- their projections were never a record of "
            f"what the model thought at decision time (F18). Evaluations are paired simulations "
            f"under CURRENT projections. Catch up: "
            f"py -3.10 -m scripts.evaluate_move --evaluate-unevaluated [--mine-only]")


def _declog_cells(r):
    """(in, out, bid, mine, snapshot, evaluation) display strings shared by both renderers."""
    def pl(lst):
        return ", ".join(f"{n} ({m:.1f})" if m is not None else str(n) for n, m in lst) or "-"
    snap = (f"retro +{r['lag_days']:.1f}d" if r["retro"]
            else (f"{r['lag_days']:.2f}d" if r["lag_days"] is not None else "-"))
    if r["eval"]:
        e = r["eval"]
        ev = (f"Champ {e['champ_delta']:+.2f}+-{e['champ_se']:.2f} / "
              f"PO {e['playoff_delta']:+.2f}+-{e['playoff_se']:.2f}")
    else:
        ev = f"unevaluated -- py -3.10 -m scripts.evaluate_move --log-tx {r['transaction_id']}"
    return (pl(r["adds"]), pl(r["drops"]),
            r["faab_bid"] if r["faab_bid"] is not None else "-",
            "yes" if r["is_mine"] else "", snap, ev)


def read_predictions_log(season, path=None):
    """THE consumer entry point for the predictions log (F18/F19): {week: selected row} for
    `season`. Per week the LAST CANONICAL row wins; only a week with no canonical row falls
    back to its last row of any kind. Rows predating the canonical field count as
    non-canonical. The predictions log is the AUTHORITATIVE per-week forecast record;
    data/weeks/ is a working directory overwritten by any run, canonical or not."""
    import json as _json
    if path is None:
        path = predictions_log_file(season)
    last_any, last_canon = {}, {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = _json.loads(line)
                if str(r.get("season")) != str(season):
                    continue
                last_any[r.get("week")] = r
                if r.get("canonical"):
                    last_canon[r.get("week")] = r
    except FileNotFoundError:
        return {}
    return {wk: last_canon.get(wk, row) for wk, row in last_any.items()}


def _archive_superseded(week_dir, windows, current_window, keep_stamp, tolerance_s=300):
    """A new canonical run REPLACES what it supersedes, explicitly. Archived (moved to
    week_dir/archive/, never deleted): files whose stamps fall inside the CURRENT window,
    and stray canonical leftovers belonging to NO window at all (e.g. a pre-cycle set --
    run_windows already reports those as covering nothing). Kept: the new run's own set --
    a run's files span stamps (tool JSONs land seconds before the digest; the real
    2026-09-02 run split at a one-second boundary under exact-stamp matching), so
    everything within tolerance_s of keep_stamp is protected (runs are chain-length
    minutes apart, so 300 s cannot bridge two runs) -- and any OTHER window's files: run
    1's canonical record must survive run 2's supersede. Called only AFTER the new digest
    wrote successfully. Returns the moved names."""
    import re
    import shutil
    from datetime import datetime, timedelta, timezone
    moved = []
    if not os.path.isdir(week_dir):
        return moved
    keep_dt = datetime.strptime(keep_stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    cur = next((w for w in windows if w["name"] == current_window), None)
    if cur is None:
        return moved
    for name in sorted(os.listdir(week_dir)):
        src = os.path.join(week_dir, name)
        if not os.path.isfile(src):
            continue
        m = re.search(r"(\d{8}T\d{6}Z)", name)
        if not m:
            continue
        dt = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if abs(dt - keep_dt) <= timedelta(seconds=tolerance_s):
            continue                                      # the new run's own set
        in_current = cur["start"] <= dt < cur["deadline"]
        in_other = any(w["start"] <= dt < w["deadline"] for w in windows
                       if w["name"] != current_window)
        if in_other:
            continue                                      # another window's record stays
        if in_current or dt < min(w["start"] for w in windows):
            os.makedirs(os.path.join(week_dir, "archive"), exist_ok=True)
            shutil.move(src, os.path.join(week_dir, "archive", name))
            moved.append(name)
    return moved


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
        md += [_table(["Team", "Playoff%", "Champ%", "Expected wins", "Expected points"],
                      [[("**%s**" % r["Team"]) if r["Team"] == team else r["Team"], f"{r['Playoff_Pct']:.1f}",
                        f"{r['Champ_Pct']:.1f}", f"{r['Expected_Wins']:.1f}", f"{r['Expected_Points']:.0f}"] for r in rows]), ""]

    lg = res.get("league")
    if lg:
        md += [f"## League this week -- all matchups (n={lg.get('n')}, {'cross-roster copula' if lg.get('cross') else 'per-roster copula'})", ""]
        md += [_table(["Matchup", "P(A wins)", "P(B wins)", "± SE", "A expected", "B expected", "Margin std dev"],
                      [[f"{m['a']} v {m['b']}", f"{100 * m['p_a']:.1f}%", f"{100 * m['p_b']:.1f}%", f"{100 * m['se']:.1f}",
                        f"{m['a_expected']:.1f}", f"{m['b_expected']:.1f}", f"{m['margin_sd']:.1f}"] for m in lg["matchups"]]), ""]
        md += [_table(["Team", "Opponent", "P(>= median)", "Expected", "Std dev"],
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
        md += [_table(["#", "Team", "Lineup VORP", "Depth VORP", "Optimal score", "Holes", "Tier-1 starters", "Starters below replacement"],
                      [[t["rank"], ("**%s**" % t["team"]) if t["team"] == team else t["team"], f"{t['lineup_vorp']:.1f}",
                        f"{t['depth_vorp']:.1f}", f"{t['optimal_score']:.1f}", t["holes"], t["tier1_starters"],
                        t["starters_below_replacement"]] for t in lt]), ""]
        bp = (rg.get("team_detail") or {}).get("by_position", {})
        if bp:
            md += [_table(["Position", "Starters", "Bench", "Starter VORP", "Depth VORP", "Best free agent"],
                          [[p, b["n_starters"], b["n_bench"], f"{b['starters_vorp']:.1f}", f"{b['depth_vorp']:.1f}",
                            (f"{b['best_free_agent']['name']} ({b['best_free_agent']['vorp']:+.1f})" if b.get("best_free_agent") else "-")]
                           for p, b in sorted(bp.items())]), ""]

    lu = res.get("lineup")
    if lu:
        md += [f"## Lineup -- expected total {lu['expected_total']:.1f}" + (f", UNFILLED: {lu['unfilled']}" if lu.get("unfilled") else ""), ""]
        md += [_table(["Slot", "Player", "Position", "Expected", "p10", "p50", "p90", "P(zero)", "Margin", "Alternative"],
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
        md += [_table(["Construction", "Mean", "Std dev", "P(beats opponent)", "± SE", "P(>= median)", "Margin", "Margin std dev"],
                      [[k, f"{c[k]['mean']:.1f}", f"{c[k]['sd']:.1f}", f"{100 * c[k]['p_beat_opponent']:.1f}%", f"{100 * c[k]['se']:.1f}",
                        f"{100 * c[k]['p_beat_median']:.1f}%", f"{c[k]['margin_mean']:+.1f}", f"{c[k]['margin_sd']:.1f}"]
                       for k in mu["ranking_by_p_beat_opponent"]]), ""]
        md += ["_P(beats opponent) is computed on this section's own joint sample, independent of the League "
               "table's matchup row; the two estimates differ by sampling noise (SE ~ +-0.7 points), "
               "not signal._", ""]
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
        def _wv_row(t):
            return [t["name"], t["pos"], t.get("tier") or "-", f"{t['mean']:.1f}", f"{t['vorp']:+.1f}", f"{t['week']['mean']:.1f}",
                    f"{t['week']['p10']:.1f}", f"{t['week']['p50']:.1f}", f"{t['week']['p90']:.1f}", t["fills"], t["bid"]["suggested"],
                    (f"{t['incumbent']} / {100 * t['p_beats_incumbent']['p']:.0f}%" if t.get("p_beats_incumbent") else
                     (t.get("incumbent") or "-"))]
        _wv_cols = ["Player", "Position", "Tier", "Season mean", "VORP", "Week mean", "p10", "p50", "p90", "Fills", "Suggested bid*", "Incumbent / P(beats)"]
        main_wv = [t for t in wv["targets"] if t["fills"] != "depth"]
        depth_wv = [t for t in wv["targets"] if t["fills"] == "depth"]
        md += [_table(_wv_cols, [_wv_row(t) for t in main_wv]), "",
               "\\* Suggested bid = UNVERIFIED value heuristic. P(beats incumbent): " + wv.get("caveat", ""), ""]
        if depth_wv:
            md += ["### Depth upgrades", "",
                   "_Beats your worst bench player at the position (named as the natural drop), or "
                   "fills an EMPTY bench behind a lone starter with positive VORP. Separated from the "
                   "starter-facing ranking above; capped at three per position._", ""]
            md += [_table(_wv_cols, [_wv_row(t) for t in depth_wv]), ""]

    tr = res.get("trades")
    if tr:
        md += [f"## Trade targets ({tr.get('contention_note', '')})", ""]
        if not tr.get("buy"):
            md += ["No trades to propose: no buy-side candidates met both sides' acceptance rule this week.", ""]
        else:
            md += [_table(["From", "Target", "Buried behind", "Slot", "I give", "I get", "My gain", "Their gain", "Acceptable", "Playoff%", "Seller", "Willingness"],
                      [[b["with"], b["target"], b.get("buried_behind") or "-", b.get("fills_my_slot") or "-", ", ".join(b["i_give"]),
                        ", ".join(b["i_get"]), f"{b['my_gain']:+.1f}", f"{b['their_gain']:+.1f}", "yes" if b["acceptable"] else "no",
                        (f"{b['their_playoff_pct']:.0f}" if b.get("their_playoff_pct") is not None else "-"),
                            ("yes" if b.get("seller") else "no") if b.get("seller") is not None else "-", b.get("willingness", "-")]
                           for b in tr.get("buy", [])]), ""]
        if tr.get("sell"):
            md += ["Sell side:", ""]
            md += [_table(["From", "Target", "I give", "I get", "My gain", "Their gain"],
                          [[x["buyer"], x["they_want"][0] if x["they_want"] else "-",
                            ", ".join(x["they_want"]), ", ".join(x["they_give"]),
                            f"{x['my_gain']:+.1f}", f"{x['their_gain']:+.1f}"] for x in tr["sell"]]), ""]

    dl = report.get("decision_log")
    if dl and dl["rows"]:
        md += [f"## Decision log -- week {dl['week']} ({len(dl['rows'])} transaction(s))", ""]
        md += [_table(["Date", "Team", "Type", "Added", "Dropped", "Bid", "Mine", "Snapshot", "Evaluation"],
                      [[(r["created"] or "")[:10], r["team"], r["type"], *_declog_cells(r)]
                       for r in dl["rows"]]), ""]
        md += ["_" + _declog_caveat(dl) + "_", ""]
        if dl["older_unevaluated"]:
            md += [f"{dl['older_unevaluated']} older transaction(s) from other weeks remain unevaluated.", ""]

    hk = report.get("housekeeping") or {}
    if hk.get("unevaluated_trades"):
        md += ["## Housekeeping", ""]
        md += [f"- logged trade {t['transaction_id']} (week {t.get('week')}, {' v '.join(t.get('teams') or [])}) has no "
               f"paired evaluation -- run: py -3.10 -m scripts.evaluate_trade --log-tx {t['transaction_id']}"
               for t in hk["unevaluated_trades"]] + [""]
    return "\n".join(md)


def write_digest(md, path):
    ensure_dir_for(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


# ------------------------------------------------------------------------ HTML report
def _is_number(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    try:
        float(str(v).replace("%", "").replace("+", ""))
        return True
    except ValueError:
        return False


def _sort_value(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    t = str(v)
    try:
        return float(t.replace("%", "").replace("+", ""))
    except ValueError:
        return t.lower()


def _is_neutral(v):
    """Placeholder cells ('-' or empty) that must neither decide a column's type nor break
    the alignment of the numbers around them."""
    return str(v).strip() in ("-", "")


def html_table(headers, rows, types=None, sort_keys=None, css_class="", signed_cols=()):
    """A sortable table in positional_tiers' pattern: th[data-key][data-type], td[data-sort].

    Type inference ignores neutral placeholder cells ('-'/empty): a numeric column with a
    few placeholders stays numeric (right-aligned, tabular numerals), and the placeholders
    sort to the numeric bottom. `signed_cols` is the OPT-IN list of column headers whose
    sign-carrying cells get semantic color classes (pos/neg) -- explicit per call site,
    never blanket sign-sniffing, so an SE column's '+-' header can never trigger it."""
    if types is None:
        types = []
        for c in range(len(headers)):
            col = [r[c] for r in rows if c < len(r) and not _is_neutral(r[c])]
            types.append("number" if col and all(_is_number(v) for v in col) else "text")
    signed_idx = {c for c, h in enumerate(headers) if str(h) in signed_cols}
    head = "".join(f'<th data-key="{escape(str(h))}" data-type="{types[c]}">{escape(str(h))}</th>'
                   for c, h in enumerate(headers))
    body = []
    for ri, r in enumerate(rows):
        cells = []
        for c, v in enumerate(r):
            if types[c] == "number" and _is_neutral(v):
                key = "-1e999"          # parseFloat -> -Infinity: placeholders sink in sorts
            else:
                key = sort_keys[ri][c] if sort_keys else _sort_value(v)
            cls = ""
            if types[c] == "number":
                cls = "num"
                if c in signed_idx and not _is_neutral(v):
                    try:
                        signed_val = float(str(v).replace("%", ""))
                        if str(v).lstrip().startswith(("+", "-")) and signed_val != 0:
                            cls += " pos" if signed_val > 0 else " neg"
                    except ValueError:
                        pass
            cls = f' class="{cls}"' if cls else ""
            cells.append(f'<td{cls} data-sort="{escape(str(key))}">{escape(str(v))}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="{css_class}"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _rel(path, anchor=None):
    """Relative link from the HTML file's OWN directory (`anchor`) to `path`. The digests
    now live at data/decisions/week_NN/[archive/], so the anchor is per-file -- the old
    flat data/decisions/ is only the fallback for legacy callers."""
    if anchor is None:
        anchor = os.path.dirname(decisions_path("x"))
    return os.path.relpath(path, start=anchor).replace(os.sep, "/")


def _img(path, caption, embed, anchor=None):
    if not os.path.exists(path):
        return f'<p class="missing">chart not generated: {escape(os.path.basename(path))}</p>'
    if embed:
        src = "data:image/png;base64," + base64.b64encode(_read_bytes(path)).decode("ascii")
    else:
        src = _rel(path, anchor)
    return (f'<figure><img src="{src}" alt="{escape(caption)}" loading="lazy">'
            f'<figcaption>{escape(caption)}</figcaption></figure>')


def _link(path, text, anchor=None):
    if not os.path.exists(path):
        return escape(text) + " (not generated)"
    return f'<a href="{_rel(path, anchor)}">{escape(text)}</a>'


def _digest_name(week, stamp, ext, failed=False, embed=False, window=None):
    """Digest file name: _FAILED marks an aborted chain; _embed marks the deliberately
    large self-contained HTML (charts inlined as data URIs) so it is visible at a glance."""
    win = f"{window}_" if window else ""
    return (f"weekly_report_week{week}_{win}{stamp}{'_FAILED' if failed else ''}"
            f"{'_embed' if embed and ext == 'html' else ''}.{ext}")


_REPORT_CSS = _TABLE_CSS + """
body { max-width: 1400px; margin: 1.5rem auto; padding: 0 1rem; font-size: 15px; line-height: 1.45; }
h1 { font-size: 1.7rem; }
h2 { font-size: 1.35rem; font-weight: 700; margin-top: 2.4rem; margin-bottom: .5rem;
     border-bottom: 1px solid #ddd; padding-bottom: .25rem; }
h3 { font-size: 1.05rem; font-weight: 700; color: #333; margin: 1.5rem 0 .4rem; }
.toc { font-size: .85rem; color: #999; margin: .3rem 0 1.4rem; }
.toc a { color: #2c5f8a; text-decoration: none; }
.toc a:hover { text-decoration: underline; }
.banner { background: #fde2e2; border: 2px solid #c0392b; padding: 1rem; margin: 1rem 0; }
.degraded { background: #fdf6e6; border-left: 4px solid #d68910; padding: .6rem 1rem; margin: 1rem 0; }
.degraded h2 { border: none; margin: 0 0 .3rem; font-size: .95rem; color: #8a5d00; }
.degraded li { font-family: monospace; font-size: .85rem; }
.missing { color: #999; font-style: italic; }
td.pos { color: #1a7a3a; }
td.neg { color: #b3372f; }
figure { margin: .6rem .6rem .6rem 0; max-width: 100%;
         background: #fff; border: 1px solid #e2e2e2; border-radius: 4px; padding: .5rem .5rem .25rem; }
figure img { max-width: 100%; display: block; margin: 0 auto; }
figcaption { font-size: .78rem; color: #666; text-align: center; padding-top: .35rem; }
.charts { display: flex; flex-wrap: wrap; gap: .6rem; align-items: stretch; }
.charts figure { flex: 1 1 45%; height: 460px; display: flex; flex-direction: column; }
.charts figure img { flex: 1; min-height: 0; width: 100%; object-fit: contain; }
/* Dense reference figures (tier charts): full width, one per row, natural height --
   these are the ones actually squinted at, so readability wins over the fixed box. */
.charts.charts-full figure { flex: 1 1 100%; height: auto; }
.charts.charts-full figure img { flex: none; object-fit: unset; }
details { margin: .35rem 0; }
summary { cursor: pointer; font-weight: 600; font-size: .9rem; padding: .35rem .6rem;
          background: #f6f6f6; border-radius: 4px; }
summary:hover { background: #ededed; }
details[open] summary { border-radius: 4px 4px 0 0; }
details table { margin-top: 0; }
pre { background: #efece5; padding: .6rem; overflow-x: auto; font-size: .8rem; }
.note { color: #555; font-size: .9rem; font-weight: 400; text-transform: none; letter-spacing: 0; }
/* Section color (owner's pick): variant C's solid slate header bands on variant A's warm
   off-white paper; tables and figures sit as white cards so they lift off the page. */
body { background: #f7f5f1; }
h2 { background: #3d5a73; color: #fff; border-bottom: none; padding: .45rem .75rem; border-radius: 4px; }
h2 .note { color: #c9d6e2; }
table { background: #fff; box-shadow: 0 0 0 1px #e6e1d6; }
tbody tr:nth-child(even) { background: #faf9f6; }
th { background: #eef1f4; }
summary { background: #eef1f4; }
summary:hover { background: #e2e7ec; }
"""


def render_html(report, team, week, embed=False, anchor_dir=None):
    res = report.get("results", {})
    anchor = anchor_dir or os.path.dirname(decisions_path("x"))
    T = lambda s_: escape(str(s_))  # noqa: E731
    out = [f'<!doctype html><html><head><meta charset="utf-8"><title>Weekly report -- {T(team)}, week {week}</title>'
           f'<style>{_REPORT_CSS}</style></head><body>',
           f'<h1>Weekly report -- {T(team)}, week {week}</h1>',
           f'<p class="note">{T(report.get("started_at", ""))} -> {T(report.get("finished_at", ""))} UTC</p>']

    if report.get("status") == "FAILED":
        planned = report.get("planned") or []
        done = list(res)
        not_run = ", ".join(n for n in planned if n not in done and n != report.get("failed_step"))
        out.append(f'<div class="banner"><h2>FAILED AT STEP <code>{T(report.get("failed_step"))}</code></h2>'
                   f'<p><b>{T(report.get("error"))}</b></p>'
                   '<p>Downstream steps did not run. Nothing below reflects this week\'s data beyond the steps '
                   'listed as completed; fix the failure and re-run.</p>'
                   + (f'<p>Completed: {T(", ".join(done) or "none")}<br>Did not run: {T(not_run)}</p>' if planned else "")
                   + (f'<pre>{T(report["traceback"].strip()[-1500:])}</pre>' if report.get("traceback") else "")
                   + '</div>')

    manifest = (res.get("sync") or {}).get("manifest")
    if manifest:
        out.append(f'<p>Sync for week {T(manifest.get("current_week"))} completed {T(manifest.get("finished_at"))} UTC '
                   f'({T(manifest.get("notices_count", 0))} routine notices).</p>')
        if manifest.get("degraded"):
            out.append('<div class="degraded"><h2>DEGRADED -- the sync tolerated these failures</h2><ul>'
                       + "".join(f"<li>{T(d)}</li>" for d in manifest["degraded"]) + '</ul></div>')
    if res.get("freshness"):
        st, reasons = res["freshness"]["status"], res["freshness"]["reasons"]
        out.append(f'<div class="degraded"><h2>DATA FRESHNESS (sync skipped): {T(st)}</h2><ul>'
                   + "".join(f"<li>{T(r)}</li>" for r in reasons) + '</ul></div>')
    if report.get("status") == "FAILED":
        out.append(f"<script>{_TABLE_JS}</script></body></html>")
        return "".join(out)

    lg = res.get("league")
    if lg:
        cop = "cross-roster copula" if lg.get("cross") else "per-roster copula"
        out.append(f'<h2 id="league">League this week -- all matchups <span class="note">(n={T(lg.get("n"))}, {cop})</span></h2>')
        out.append(html_table(["Matchup", "P(A wins)", "P(B wins)", "± SE", "A expected", "B expected", "Margin std dev"],
                              [[f"{m['a']} v {m['b']}", f"{100 * m['p_a']:.1f}%", f"{100 * m['p_b']:.1f}%", f"{100 * m['se']:.1f}",
                                f"{m['a_expected']:.1f}", f"{m['b_expected']:.1f}", f"{m['margin_sd']:.1f}"] for m in lg["matchups"]]))
        out.append(html_table(["Team", "Opponent", "P(>= median)", "Expected", "Std dev"],
                              [[t, d.get("opponent") or "-", f"{100 * d['p_beat_median']:.1f}%", f"{d['expected_total']:.1f}", f"{d['sd_total']:.1f}"]
                               for t, d in sorted(lg["teams"].items(), key=lambda kv: -kv[1]["p_beat_median"])]))
        out.append("<h3>Assumed optimal lineups (max-expectation, the engine's rule)</h3>")
        for t, d in sorted(lg["teams"].items(), key=lambda kv: (kv[0] != team, kv[0])):
            opened = " open" if t == team else ""
            out.append(f'<details{opened}><summary>{T(t)} -- expected {d["expected_total"]:.1f}, std dev {d["sd_total"]:.1f}, '
                       f'vs {T(d.get("opponent") or "-")}</summary>'
                       + html_table(["Slot", "Player", "NFL", "Expected", "Std dev"],
                                    [[x["slot"], x["name"], x.get("nfl_team") or "-", f"{x['expected']:.1f}", f"{x['sd']:.1f}"] for x in d["lineup"]])
                       + "</details>")
        out.append(f'<p class="note">{T(lg.get("note", ""))}</p>')

    sim = res.get("simulation")
    if sim and sim.get("season_outcomes"):
        rows = sorted(sim["season_outcomes"], key=lambda r: -r["Playoff_Pct"])
        out.append('<h2 id="outlook">Season outlook</h2>')
        out.append(html_table(["Team", "Playoff%", "Champ%", "Expected wins", "Expected points"],
                              [[r["Team"], f"{r['Playoff_Pct']:.1f}", f"{r['Champ_Pct']:.1f}", f"{r['Expected_Wins']:.1f}", f"{r['Expected_Points']:.0f}"]
                               for r in rows]))
        charts = ((season_outcomes_chart_path(week), "Season outcomes"),
                  (all_teams_trajectories_chart_path(week), "Cumulative win trajectories"),
                  (win_trajectory_chart_path(week), "Expected wins over the simulated season"),
                  (expected_wins_chart_path(week), "Expected wins & variance"),
                  (power_rankings_chart_path(week), "Roster value baseline"),
                  (h2h_heatmap_chart_path(week), "Head-to-head win probabilities"),
                  (seeding_distribution_path(week), "Seeding distribution"),
                  (weekly_scoring_density_path(week), "Weekly scoring density"))
        out.append('<div class="charts">' + "".join(_img(p_, c, embed, anchor) for p_, c in charts) + "</div>")

    rg = res.get("roster_grades")
    if rg:
        out.append('<h2 id="grades">Roster grade</h2>')
        lt = rg.get("league", {}).get("teams", [])
        out.append(html_table(["#", "Team", "Lineup VORP", "Depth VORP", "Optimal score", "Holes", "Tier-1 starters", "Starters below replacement"],
                              [[t["rank"], t["team"], f"{t['lineup_vorp']:.1f}", f"{t['depth_vorp']:.1f}", f"{t['optimal_score']:.1f}",
                                t["holes"], t["tier1_starters"], t["starters_below_replacement"]] for t in lt]))
        bp = (rg.get("team_detail") or {}).get("by_position", {})
        if bp:
            out.append(f"<h3>{T(team)} by position</h3>" + html_table(
                ["Position", "Starters", "Bench", "Starter VORP", "Depth VORP", "Best free agent"],
                [[p_, b["n_starters"], b["n_bench"], f"{b['starters_vorp']:.1f}", f"{b['depth_vorp']:.1f}",
                  (f"{b['best_free_agent']['name']} ({b['best_free_agent']['vorp']:+.1f})" if b.get("best_free_agent") else "-")]
                 for p_, b in sorted(bp.items())]))

    lu = res.get("lineup")
    if lu:
        unfilled = f' <span class="note">UNFILLED: {T(lu["unfilled"])}</span>' if lu.get("unfilled") else ""
        out.append(f'<h2 id="lineup">Lineup -- expected total {lu["expected_total"]:.1f}{unfilled}</h2>')
        out.append(html_table(["Slot", "Player", "Position", "Expected", "p10", "p50", "p90", "P(zero)", "Margin", "Alternative"],
                              [[r["slot"], r["name"], r["pos"], f"{r['expected']:.1f}", f"{r['p10']:.1f}", f"{r['p50']:.1f}", f"{r['p90']:.1f}",
                                f"{100 * r['p_zero']:.0f}%", (f"{r['margin']:+.1f}" if r.get("alternative") else "-"), r.get("alternative") or "-"]
                               for r in lu["lineup"]], signed_cols=("Margin",)))
        if lu.get("bench"):
            bench = ", ".join(f"{b['name']} ({b['expected']:.1f}{', ' + b['reason'] if b.get('reason') else ''})" for b in lu["bench"])
            out.append(f"<p>Bench: {T(bench)}</p>")
        out.append('<div class="charts">' + _img(boom_bust_chart_path(team, week), f"{team}: boom/bust by player", embed, anchor)
                   + _img(floor_ceiling_chart_path(team, week), f"{team}: floor/ceiling by player", embed, anchor) + "</div>")

    mu = res.get("matchup")
    if mu:
        c = mu["constructions"]
        fav = "favoured" if mu.get("favoured_by_max_mean") else "underdog"
        cop = "cross-roster copula" if mu.get("cross") else "per-roster copula"
        out.append(f'<h2 id="matchup">Matchup -- vs {T(mu["opponent"])} <span class="note">({fav} on the engine\'s lineup; '
                   f'n={T(mu.get("n"))}, {cop})</span></h2>')
        out.append(html_table(["Construction", "Mean", "Std dev", "P(beats opponent)", "± SE", "P(>= median)", "Margin", "Margin std dev"],
                              [[k, f"{c[k]['mean']:.1f}", f"{c[k]['sd']:.1f}", f"{100 * c[k]['p_beat_opponent']:.1f}%", f"{100 * c[k]['se']:.1f}",
                                f"{100 * c[k]['p_beat_median']:.1f}%", f"{c[k]['margin_mean']:+.1f}", f"{c[k]['margin_sd']:.1f}"]
                               for k in mu["ranking_by_p_beat_opponent"]]))
        out.append("<p class=\"note\">P(beats opponent) is computed on this section's own joint sample, "
                   "independent of the League table's matchup row; the two estimates differ by "
                   "sampling noise (SE ~ +-0.7 points), not signal.</p>")
        lineups = {tuple(sorted(x["name"] for x in v["lineup"])) for v in c.values()}
        if len(lineups) == 1:
            out.append("<p><b>All four constructions pick the same lineup: no variance lever on this roster this week</b> "
                       "(every bench alternative is dominated at its slot).</p>")
        else:
            best = mu["ranking_by_p_beat_opponent"][0]
            base = {x["slot"]: x["name"] for x in c["max_mean"]["lineup"]}
            diffs = [f"{x['slot']}: {base.get(x['slot'])} -> {x['name']}" for x in c[best]["lineup"] if base.get(x["slot"]) != x["name"]]
            changes = (" -- changes vs max_mean: " + T("; ".join(diffs))) if diffs else ""
            out.append(f"<p>Best by P(beat opponent): <b>{T(best)}</b>{changes}</p>")
        assumed = "assumed" if mu.get("opponent_lineup_assumed") else "supplied"
        out.append(f"<details><summary>Opponent lineup ({assumed})</summary>"
                   + html_table(["Slot", "Player", "Expected"], [[x["slot"], x["name"], f"{x['expected']:.1f}"] for x in mu.get("opponent_lineup", [])])
                   + "</details>")
        out.append('<div class="charts">' + _img(sos_roster_chart_path(week), "Strength of schedule by fantasy roster", embed, anchor)
                   + _img(sos_team_summary_chart_path(week), "Strength of schedule -- NFL team ranking", embed, anchor) + "</div>")

    wv = res.get("waivers")
    if wv:
        out.append(f'<h2 id="waivers">Waiver targets <span class="note">FAAB {wv["remaining_faab"]:.0f} (league avg {wv["league_avg_faab"]:.0f}); '
                   f'holes: {T(wv["holes"] or "none")}; next week: {T(wv["holes_next_week"] or "none")}</span></h2>')
        def _wv_row(t):
            return [t["name"], t["pos"], t.get("tier") or "-", f"{t['mean']:.1f}", f"{t['vorp']:+.1f}", f"{t['week']['mean']:.1f}",
                    f"{t['week']['p10']:.1f}", f"{t['week']['p50']:.1f}", f"{t['week']['p90']:.1f}", t["fills"], t["bid"]["suggested"],
                    (f"{t['incumbent']} / {100 * t['p_beats_incumbent']['p']:.0f}%" if t.get("p_beats_incumbent") else
                     (t.get("incumbent") or "-"))]
        _wv_cols = ["Player", "Position", "Tier", "Season mean", "VORP", "Week mean", "p10", "p50", "p90", "Fills", "Suggested bid*", "Incumbent / P(beats)"]
        main_wv = [t for t in wv["targets"] if t["fills"] != "depth"]
        depth_wv = [t for t in wv["targets"] if t["fills"] == "depth"]
        out.append(html_table(_wv_cols, [_wv_row(t) for t in main_wv], signed_cols=("VORP",)))
        out.append(f'<p class="note">* Suggested bid = UNVERIFIED value heuristic. P(beats incumbent): {T(wv.get("caveat", ""))}</p>')
        if depth_wv:
            out.append("<h3>Depth upgrades</h3>"
                       '<p class="note">Beats your worst bench player at the position (named as the '
                       "natural drop), or fills an EMPTY bench behind a lone starter with positive "
                       "VORP. Separated from the starter-facing ranking above; capped at three per "
                       "position.</p>")
            out.append(html_table(_wv_cols, [_wv_row(t) for t in depth_wv], signed_cols=("VORP",)))
        positions = []
        for t in wv["targets"]:
            if t["pos"] not in positions:
                positions.append(t["pos"])
        if positions:
            # Tier charts are the densest figures in the report (60+ player rows each): they
            # get full width, one per row, inside a collapsed details block -- readable when
            # opened beats compact, but they should not dominate the scroll when closed.
            out.append('<h3>Positional tiers for the positions above</h3>'
                       f'<details><summary>Tier charts ({", ".join(positions)}) -- full width, open to read</summary>'
                       '<div class="charts charts-full">'
                       + "".join(_img(tier_chart_path(p_, week), f"{p_} tiers", embed, anchor) for p_ in positions)
                       + "</div></details>"
                       + '<p class="note">Full ranked tables: ' + " | ".join(_link(positional_tiers_table_path(p_, week), p_, anchor) for p_ in positions) + "</p>")

    tr = res.get("trades")
    if tr:
        out.append(f'<h2 id="trades">Trade targets <span class="note">({T(tr.get("contention_note", ""))})</span></h2>')
        if not tr.get("buy"):
            out.append("<p class=\"note\">No trades to propose: no buy-side candidates met both "
                       "sides' acceptance rule this week.</p>")
        else:
            out.append(html_table(["From", "Target", "Buried behind", "Slot", "I give", "I get", "My gain", "Their gain", "Acceptable", "Playoff%", "Seller", "Willingness"],
                              [[b["with"], b["target"], b.get("buried_behind") or "-", b.get("fills_my_slot") or "-", ", ".join(b["i_give"]),
                                ", ".join(b["i_get"]), f"{b['my_gain']:+.1f}", f"{b['their_gain']:+.1f}", "yes" if b["acceptable"] else "no",
                                (f"{b['their_playoff_pct']:.0f}" if b.get("their_playoff_pct") is not None else "-"),
                                    ("yes" if b.get("seller") else "no") if b.get("seller") is not None else "-", b.get("willingness", "-")]
                                   for b in tr.get("buy", [])], signed_cols=("My gain", "Their gain")))
        if tr.get("sell"):
            out.append("<h3>Sell side</h3>")
            out.append(html_table(["From", "Target", "I give", "I get", "My gain", "Their gain"],
                                  [[x["buyer"], x["they_want"][0] if x["they_want"] else "-",
                                    ", ".join(x["they_want"]), ", ".join(x["they_give"]),
                                    f"{x['my_gain']:+.1f}", f"{x['their_gain']:+.1f}"] for x in tr["sell"]],
                                  signed_cols=("My gain", "Their gain")))

    dl = report.get("decision_log")
    if dl and dl["rows"]:
        out.append(f'<h2 id="decision-log">Decision log <span class="note">week {dl["week"]}, '
                   f'{len(dl["rows"])} transaction(s)</span></h2>')
        out.append(html_table(["Date", "Team", "Type", "Added", "Dropped", "Bid", "Mine", "Snapshot", "Evaluation"],
                              [[(r["created"] or "")[:10], r["team"], r["type"], *_declog_cells(r)]
                               for r in dl["rows"]]))
        out.append(f'<p class="note">{T(_declog_caveat(dl))}</p>')
        if dl["older_unevaluated"]:
            out.append(f'<p class="note">{dl["older_unevaluated"]} older transaction(s) from other '
                       f'weeks remain unevaluated.</p>')

    hk = report.get("housekeeping") or {}
    if hk.get("unevaluated_trades"):
        out.append('<h2 id="housekeeping">Housekeeping</h2><ul>'
                   + "".join(f"<li>logged trade {escape(str(t['transaction_id']))} (week {escape(str(t.get('week')))}) has no paired "
                             f"evaluation -- run: <code>py -3.10 -m scripts.evaluate_trade --log-tx {escape(str(t['transaction_id']))}</code></li>"
                             for t in hk["unevaluated_trades"]) + "</ul>")
    # Compact table of contents from the section anchors actually rendered (sections are
    # conditional, so this is derived from the built page, not a hardcoded list), inserted
    # under the h1. Presentation only -- the FAILED path returns above and gets no TOC.
    _TOC_LABELS = (("league", "League"), ("outlook", "Season outlook"), ("grades", "Roster grade"),
                   ("lineup", "Lineup"), ("matchup", "Matchup"), ("waivers", "Waivers"),
                   ("trades", "Trades"), ("decision-log", "Decision log"), ("housekeeping", "Housekeeping"))
    page = "".join(out)
    links = [f'<a href="#{i}">{label}</a>' for i, label in _TOC_LABELS if f'<h2 id="{i}"' in page]
    if links:
        out.insert(3, '<p class="toc">' + " &middot; ".join(links) + "</p>")
    out.append(f"<script>{_TABLE_JS}</script></body></html>")
    return "".join(out)


# ------------------------------------------------------------------- the real chain
def build_steps(team, full=False, skip_sync=False, sims=5000, evaluate=0, canonical=False):
    """Wires the real tools. Each step returns the object the digest renders from."""
    from fantasy_sim.storage import load_json, syndicate_comprehensive_matrix_path, LEAGUE_STATE_FILE
    state = {"run_started": time.time(), "week": None}
    state["tool_extra_argv"] = ["--canonical"] if canonical else []
    state["canonical"] = bool(canonical)

    def step_sync():
        from fantasy_sim.sync import sync_all
        sync_all()
        manifest = gate_sync_fresh(state["run_started"])
        state["week"] = int(manifest["current_week"])
        return {"manifest": manifest}

    def step_freshness():
        from fantasy_sim.freshness import check
        # check_export=False: this entry gate assesses the SYNC on disk; the chain runs
        # its own simulation as the very next step and gate_export_fresh re-checks the
        # export it produces. Demanding a pre-existing export here aborted every fresh
        # runner by construction (found by the force-report chain test, 2026-09-05).
        status, reasons, details = check(offline=False, check_export=False)
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
        state["season_outcomes"] = rows
        return {"season_outcomes": rows}

    def step_positional_tiers():
        from scripts.run_positional_tiers import main as m
        m(); return {"ok": True}

    def step_strength_of_schedule():
        from scripts.run_strength_of_schedule import main as m
        m(); return {"ok": True}

    def step_win_trajectory():
        from scripts.run_win_trajectory import main as m
        m(); return {"ok": True}

    def step_league():
        from fantasy_sim.decisions import league_week_outlook
        from fantasy_sim.simulation import FantasySimulationEngine
        r = league_week_outlook(FantasySimulationEngine(), state["week"], sims=sims)
        state["league_outlook"] = r
        return r

    def step_predictions_log():
        try:
            vegas_meta = (load_json(VEGAS_FILE) or {}).get("_meta")
        except FileNotFoundError:
            vegas_meta = None
        prov = run_provenance(load_json(SYNC_MANIFEST_FILE), vegas_meta)
        n = append_predictions_log(state["week"], state["season_outcomes"], state["league_outlook"],
                                   commit=_git_head(), canonical=state["canonical"],
                                   provenance=prov)
        return {"appended": n, "provenance": prov}

    def step_roster_grades():
        from scripts.roster_grades import main as m
        return m(["--team", team, "--week", str(state["week"])] + state["tool_extra_argv"])

    def step_lineup():
        from scripts.optimize_lineup import main as m
        return m(["--team", team, "--week", str(state["week"])] + state["tool_extra_argv"])

    def step_matchup():
        from scripts.matchup_lineup import main as m
        return m(["--team", team, "--week", str(state["week"]), "--sims", str(sims)] + state["tool_extra_argv"])

    def step_waivers():
        from scripts.waiver_targets import main as m
        return m(["--team", team, "--week", str(state["week"])] + state["tool_extra_argv"])

    def step_trades():
        from scripts.find_trades import main as m
        return m(["--team", team, "--week", str(state["week"]), "--evaluate", str(evaluate)] + state["tool_extra_argv"])

    def step_logs_push():
        return commit_and_push_logs(state["week"])

    steps = [("freshness", step_freshness)] if skip_sync else [("sync", step_sync)]
    steps += [("simulation", step_simulation), ("positional_tiers", step_positional_tiers),
              ("strength_of_schedule", step_strength_of_schedule), ("win_trajectory", step_win_trajectory),
              ("league", step_league), ("predictions_log", step_predictions_log),
              ("roster_grades", step_roster_grades), ("lineup", step_lineup),
              ("matchup", step_matchup), ("waivers", step_waivers)]
    if full:
        steps.append(("trades", step_trades))
    if canonical:
        # Last on purpose: durability must never delay or fail the report itself
        # (commit_and_push_logs is warn-never-fail; see its docstring).
        steps.append(("logs_push", step_logs_push))
    return steps, state


def run_weekly_report(team, full=False, skip_sync=False, sims=5000, evaluate=0, embed=False,
                      canonical=False):
    steps, state = build_steps(team, full=full, skip_sync=skip_sync, sims=sims, evaluate=evaluate,
                               canonical=canonical)
    report = run_steps(steps)
    try:
        from fantasy_sim.decisions import unevaluated_my_trades
        report["housekeeping"] = {"unevaluated_trades": unevaluated_my_trades()}
    except Exception:
        report["housekeeping"] = {"unevaluated_trades": []}
    try:
        report["decision_log"] = (_decision_log_summary(state["week"])
                                  if isinstance(state["week"], int) else None)
    except Exception:
        report["decision_log"] = None
    week = state["week"] or "?"
    md = render_digest(report, team, week)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    failed = report["status"] == "FAILED"
    # week_NN/ for a canonical run, week_NN/archive/ otherwise; a failure before the week is
    # even known has no week directory to belong to and goes to adhoc/.
    if isinstance(week, int):
        out_path = lambda name: decisions_week_path(week, name, canonical=canonical)  # noqa: E731
    else:
        out_path = decisions_adhoc_path
    # Canonical runs are named by the window they cover and REPLACE any earlier canonical
    # run in the same window (superseded sets move to archive/; the owner's intent is a
    # late-window snapshot, so latest-wins is explicit, not emergent). Window resolution
    # failing for any reason -- no kickoff data, a run outside every window -- degrades to
    # the plain stamped name with no supersede, never to a failed report.
    window = win_interval = None
    if canonical and isinstance(week, int):
        try:
            from fantasy_sim.run_windows import compute_windows, load_kickoffs
            kicks, _src = load_kickoffs()
            now_utc = _dt.datetime.now(_dt.timezone.utc)
            all_windows = compute_windows(now_utc, kicks, [])["windows"]
            for w_ in all_windows:
                if w_["start"] <= now_utc < w_["deadline"]:
                    window, win_interval = w_["name"], all_windows
                    break
            if window is None:
                print("[NOTE] canonical run outside every window: plain naming, no supersede.")
        except Exception as ex:
            print(f"[NOTE] window resolution unavailable ({ex}); plain naming, no supersede.")
    path = write_digest(md, out_path(_digest_name(week, stamp, "md", failed=failed, window=window)))
    html_path = None
    if isinstance(week, int):
        html_out = out_path(_digest_name(week, stamp, "html", failed=failed, embed=embed, window=window))
        html_path = write_digest(render_html(report, team, week, embed=embed,
                                             anchor_dir=os.path.dirname(html_out)), html_out)
    if window is not None and not failed:
        moved = _archive_superseded(os.path.dirname(path), win_interval, window, stamp)
        if moved:
            print(f"[NOTE] superseded same-window canonical set -> archive/: {', '.join(moved)}")
    return report, md, path, html_path
