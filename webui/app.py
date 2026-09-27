"""webui.app -- the Flask application factory (docs/WEB_UI.md sections 2 and 4, phase W1).

W1 is a READ-ONLY VIEWER: every route reads files through webui.paths.Root and writes
nothing. The fantasy_sim modules imported here are the import-safe ones only (config,
storage, freshness, run_windows, positional_tiers, weekly_report through webui.names), and
each is used through its PURE functions -- their own readers are CWD-relative, and this
process never chdir()s, so the reads are done here, against the bound root, and the pure
function is handed the values.

Localhost is an invariant, not a default: every request's Host must name 127.0.0.1 or
localhost (and this server's port when one is configured) or it is refused with 400 --
the DNS-rebinding defence a localhost service otherwise lacks. Every future POST must
carry the per-launch CSRF token (require_csrf). No route in W1 accepts a POST.
"""
import datetime as _dt
import json
import os
import secrets
import subprocess

from flask import Flask, Response, abort, redirect, render_template, request, send_file, url_for

from webui import render
from webui.jobs import RUNNING, JobRefused, JobRunner
from webui.names import Overlay
from webui.paths import PathRefused, Root, normalize
from webui.players import PlayerIndex
from webui.tools import ENGINE, TOOLS, FormError, get as get_tool, label_for, resolve_form

ALLOWED_HOSTNAMES = ("127.0.0.1", "localhost")
R1_SENTENCE = ("R1: one engine process at a time. A crashed run is void -- re-run it alone. "
               "Never run the test suite, the goldens, or a hand tool while a job is running.")
WARNINGS_LOG_NOTE = ("data/current/syndicate_warnings.log holds whatever PROCESS last imported "
                     "the engine (F10) -- a tool run, a hand run, or the test suite -- and is not "
                     "any single run's record. Per-run warnings are in the week's audit JSON.")
TERMINAL_COMMANDS = (
    ("py -3.10 -m scripts.run_sync", "pull live data into data/current/ (H5: the odds key is verified first; "
                                     "on Windows inject the User-scope value if the shell holds a stale one)"),
    ("py -3.10 -m scripts.weekly_report --canonical --embed", "the canonical report, inside a run window"),
    ("py -3.10 -m scripts.check_freshness", "the same verdict as this page, from a terminal"),
)


def _allowed_host(host, port):
    name, _, hport = (host or "").partition(":")
    if name.lower() not in ALLOWED_HOSTNAMES:
        return False
    if port and hport and str(hport) != str(port):
        return False
    return True


def _parse_iso(t):
    return _dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(_dt.timezone.utc)


def _fmt_stamp(s):
    """20260924T165331Z -> 2026-09-24 16:53Z; anything else unchanged."""
    try:
        return _dt.datetime.strptime(str(s), "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%d %H:%MZ")
    except (TypeError, ValueError):
        return s


def _fmt_epoch(t):
    """epoch seconds -> 2026-09-24 16:53Z; None -> em dash."""
    try:
        return _dt.datetime.fromtimestamp(float(t), _dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    except Exception:            # None, a string, or a Jinja Undefined (whose __float__ raises)
        return "—"


def _elapsed(meta):
    """Seconds from started_at to finished_at (or now), or None."""
    try:
        a = _dt.datetime.strptime(meta["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
        b = (_dt.datetime.strptime(meta["finished_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
             if meta.get("finished_at") else _dt.datetime.now(_dt.timezone.utc))
        return max(0, int((b - a).total_seconds()))
    except (KeyError, TypeError, ValueError):
        return None


def _fmt_num(v, nd=1):
    try:
        if v is None:
            return "—"
        f = float(v)
        if f != f:  # NaN
            return "—"
        return f"{f:.{nd}f}"
    except Exception:            # a Jinja Undefined raises UndefinedError from __float__
        return "—" if not isinstance(v, str) else v


# ------------------------------------------------------------------------- readers
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
    return {"status": status, "reasons": list(reasons), "manifest": manifest, "week": week,
            "vegas_week": meta.get("week"), "vegas_stale_since": meta.get("stale_since")}


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
        return {"source": None, "result": None, "verdict": None,
                "note": "no kickoffs in the synced schedule: run a sync to persist them (the CLI would "
                        "live-fetch ESPN here; this server never reaches the network for it)"}
    kicks = {int(w): [_parse_iso(t) for t in ts] for w, ts in raw.items() if ts}
    now = _dt.datetime.now(_dt.timezone.utc)
    probe = compute_windows(now, kicks, [], state_week=state_week)
    target = probe.get("target_week")
    if target is None:
        return {"source": "synced schedule", "result": probe, "verdict": None, "note": None}
    result = compute_windows(now, kicks, canonical_stamps(root, target), state_week=state_week,
                             next_week_stamps=canonical_stamps(root, target + 1))
    return {"source": "synced schedule", "result": result,
            "verdict": watch_verdict(result, now), "note": None}


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


def week_report(root, week):
    n = int(week)
    d = f"weeks/week_{n:02d}"
    forecast = root.read_json(f"{d}/live_season_forecast_week_{n}.json", {}) or {}
    matrix = root.read_json(f"{d}/syndicate_comprehensive_matrix_week_{n}.json", {}) or {}
    insights = root.read_json(f"{d}/syndicate_insights_week_{n}.json", {}) or {}
    audit = root.read_json(f"{d}/simulation_audit_log_sim0_week_{n}.json", {}) or {}
    listing = root.week_files(n)
    charts = [e for e in listing["files"] if e["ext"] == "png"]
    jsons = [e for e in listing["files"] if e["ext"] == "json"]
    outcomes = matrix.get("season_outcomes") or []
    if isinstance(outcomes, dict):
        outcomes = [dict(Team=k, **v) for k, v in outcomes.items()]
    teams = sorted(forecast, key=lambda t: -float((forecast[t].get("forecast") or {}).get("playoff_probability_pct") or 0))
    return {"week": n, "forecast": forecast, "teams": teams, "outcomes": outcomes,
            "metadata": matrix.get("metadata") or {}, "seeds": matrix.get("finishing_seed_probabilities") or {},
            "insights": insights, "warnings": audit.get("warnings") or [],
            "charts": charts, "jsons": jsons, "subdirs": listing["subdirs"]}


def current_report(root):
    standings = root.read_json("current/league_standings.json", {}) or {}
    rosters = root.read_json("current/live_rosters.json", {}) or {}
    base = root.read_json("current/player_baselines.json", {}) or {}
    pending = root.read_json("current/pending_trades.json", {}) or {}
    state = root.read_json("current/league_state.json", {}) or {}
    manifest = root.read_json("current/sync_manifest.json", {}) or {}
    table = sorted(standings.items(),
                   key=lambda kv: (-float(kv[1].get("h2h_wins") or 0), -float(kv[1].get("points_scored") or 0)))
    roster_rows = {}
    for team, entries in rosters.items():
        rows = []
        for e in entries or []:
            b = base.get(e.get("name")) or {}
            rows.append({"name": e.get("name"), "pos": b.get("pos") or e.get("pos"),
                         "nfl": b.get("team") or e.get("team"), "mean": b.get("mean"),
                         "bye": b.get("bye"),
                         "status": "IR" if (b.get("on_ir") or e.get("on_ir")) else (b.get("injury_status") or e.get("injury_status") or "")})
        rows.sort(key=lambda r: -(float(r["mean"]) if r["mean"] is not None else -1))
        roster_rows[team] = rows
    return {"standings": table, "rosters": roster_rows, "pending": pending, "state": state,
            "manifest": manifest, "files": root.current()}


# ------------------------------------------------------------------------- factory
def create_app(root, overlay=None, csrf_token=None, port=None, runner=None):
    if not isinstance(root, Root):
        root = Root(root)
    overlay = overlay or Overlay()
    runner = runner if runner is not None else JobRunner(root)
    from fantasy_sim.config import MY_TEAM
    from fantasy_sim.positional_tiers import _TABLE_CSS, _TABLE_JS

    app = Flask(__name__, template_folder="templates", static_folder=None)
    app.config.update(ROOT=root, OVERLAY=overlay, PORT=port,
                      CSRF_TOKEN=csrf_token or secrets.token_urlsafe(32),
                      MY_TEAM=MY_TEAM, TEMPLATES_AUTO_RELOAD=False)
    app.jinja_env.filters["real"] = overlay.text
    app.jinja_env.filters["stamp"] = _fmt_stamp
    app.jinja_env.filters["num"] = _fmt_num
    app.jinja_env.filters["ts"] = _fmt_epoch
    app.jinja_env.filters["pct"] = render.fpct
    app.jinja_env.filters["signed"] = render.fsigned

    @app.context_processor
    def _ctx():
        return {"overlay_enabled": overlay.enabled,
                "private_marker": overlay.marker() if overlay.enabled else None,
                "csrf_token": app.config["CSRF_TOKEN"], "my_team": MY_TEAM,
                "root_path": root.root, "table_css": _TABLE_CSS, "table_js": _TABLE_JS,
                "r1": R1_SENTENCE, "now": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")}

    @app.before_request
    def _host_check():
        if not _allowed_host(request.host, app.config["PORT"]):
            abort(400, "this server answers only to 127.0.0.1 / localhost")

    @app.after_request
    def _headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def require_csrf():
        """For every future POST: the per-launch token, or 403."""
        if request.form.get("_csrf") != app.config["CSRF_TOKEN"]:
            abort(403, "missing or stale CSRF token -- reload the page and try again")
    app.require_csrf = require_csrf

    @app.errorhandler(PathRefused)
    def _refused(ex):
        return render_template("error.html", code=400, message=f"refused: {ex}"), 400

    @app.errorhandler(FileNotFoundError)
    def _missing(ex):
        return render_template("error.html", code=404, message=f"not on disk: {ex}"), 404

    @app.errorhandler(404)
    def _404(ex):
        return render_template("error.html", code=404, message="no such page"), 404

    @app.errorhandler(400)
    def _400(ex):
        return render_template("error.html", code=400, message=str(getattr(ex, "description", ex))), 400

    # ---------------------------------------------------------------- routes (GET only)
    @app.route("/")
    def status():
        fr = freshness_report(root)
        wk = fr["week"]
        return render_template("status.html", fr=fr, windows=windows_report(root, wk),
                               git=logs_git_report(root), digests=latest_digests(root),
                               weeks=root.weeks(), commands=TERMINAL_COMMANDS,
                               warnings_note=WARNINGS_LOG_NOTE)

    @app.route("/health")
    def health():
        fr = freshness_report(root)
        return {"ok": True, "week": fr["week"], "freshness": fr["status"], "weeks": root.weeks(),
                "real_names": overlay.enabled}

    @app.route("/weeks")
    def weeks():
        rows = []
        for n in root.weeks():
            f = root.read_json(f"weeks/week_{n:02d}/live_season_forecast_week_{n}.json", {}) or {}
            rows.append({"week": n, "teams": len(f), "files": len(root.week_files(n)["files"]),
                         "mtime": root.mtime(f"weeks/week_{n:02d}/syndicate_comprehensive_matrix_week_{n}.json")})
        return render_template("weeks.html", rows=rows)

    @app.route("/weeks/<int:week>")
    def week(week):
        if week not in root.weeks():
            abort(404)
        return render_template("week.html", **week_report(root, week))

    @app.route("/decisions")
    def decisions_index():
        wks = root.decision_weeks()
        if wks:
            return redirect(url_for("decisions", week=wks[-1]))
        return render_template("decisions.html", week=None, listing=None, weeks=[],
                               adhoc=root.adhoc(), season=root.season(), title="Decisions")

    @app.route("/decisions/<int:week>")
    def decisions(week):
        if week not in root.decision_weeks():
            abort(404)
        return render_template("decisions.html", week=week, listing=root.decisions(week),
                               weeks=root.decision_weeks(), adhoc=None, season=None,
                               title=f"Week {week} decisions")

    @app.route("/decisions/adhoc")
    def adhoc():
        return render_template("decisions.html", week=None, listing=None, weeks=root.decision_weeks(),
                               adhoc=root.adhoc(), season=root.season(), title="Ad-hoc and season records")

    @app.route("/results")
    def results():
        return render_template("results.html", results=root.results())

    @app.route("/current")
    def current():
        return render_template("current.html", **current_report(root))

    @app.route("/logs")
    def logs():
        return render_template("logs.html", logs=root.logs())

    @app.route("/logs/<name>")
    def log(name):
        rel = "logs/" + name
        n = request.args.get("n", "200")
        try:
            n = max(1, min(int(n), 5000))
        except ValueError:
            n = 200
        if not name.endswith(".jsonl"):
            return redirect(url_for("file_view", rel=rel))
        rows, total = root.tail_jsonl(rel, n)
        pretty = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]
        return render_template("log.html", name=name, rows=pretty, total=total, n=n, link=root.link(rel))

    @app.route("/file/<path:rel>")
    def file_view(rel):
        full = root.resolve_file(rel)
        ext = os.path.splitext(full)[1].lower()
        raw = request.args.get("raw") == "1"
        if ext == ".png":
            return send_file(full, mimetype="image/png", max_age=0)
        if ext == ".html":
            return Response(overlay.html(root.read_text(rel)), mimetype="text/html")
        if ext == ".json":
            data = root.read_json(rel)
            body = json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False)
            if raw:
                return Response(body, mimetype="application/json")
            if isinstance(data, dict) and data.get("tool"):      # a decision tool's record: tables, not a dump
                return render_template("record.html", rel=normalize(rel), body=body, link=root.link(rel),
                                       view=render.record_view(data))
            return render_template("file.html", rel=rel, body=body, kind="json", link=root.link(rel))
        if ext == ".jsonl":
            return redirect(url_for("log", name=rel.rsplit("/", 1)[-1])) if rel.startswith("logs/") \
                else render_template("file.html", rel=rel, body=root.read_text(rel), kind="text", link=root.link(rel))
        body = root.read_text(rel)
        if raw:
            return Response(body, mimetype="text/plain")
        return render_template("file.html", rel=rel, body=body, kind="text", link=root.link(rel))

    # ---------------------------------------------------------------- W2: the launcher
    app.runner = runner

    @app.errorhandler(JobRefused)
    def _refused_job(ex):
        return render_template("error.html", code=409, message=str(ex), current=runner.current()), 409

    @app.route("/tools")
    def tools():
        return render_template("tools.html", tools=list(TOOLS.values()), engine=list(ENGINE.values()),
                               current=runner.current())

    @app.route("/tools/<name>", methods=["GET", "POST"])
    def tool(name):
        try:
            t = get_tool(name)
        except KeyError:
            abort(404)
        values = {f.name: ("" if f.default is None else str(f.default)) for f in t.fields}
        error = None
        # W3: an engine run shows the freshness verdict and the run windows first, and a
        # STALE tree is refused here, before the tool would refuse it itself.
        fr = freshness_report(root) if t.engine else None
        win = windows_report(root, fr["week"]) if t.engine else None
        if request.method == "POST":
            require_csrf()
            values.update({f.name: request.form.get(f.name, "") for f in t.fields})
            if fr is not None and fr["status"] == "STALE":
                raise JobRefused("the data on disk is STALE -- " + "; ".join(fr["reasons"]) +
                                 " -- run scripts.run_sync from a terminal first (this UI never syncs)")
            try:
                form, notes = resolve_form(t, request.form, PlayerIndex.for_root(root), MY_TEAM)
                values.update({f.name: form.get(f.name, "") for f in t.fields})
                argv = t.argv(form)
                job_id = runner.launch(argv, tool=name, label=label_for(t, form),
                                       extra={"resolved": notes} if notes else None)
                return redirect(url_for("job", job_id=job_id))
            except FormError as ex:
                error = str(ex)
        return render_template("tool.html", tool=t, values=values, error=error, current=runner.current(),
                               fr=fr, windows=win)

    @app.route("/jobs")
    def jobs():
        return render_template("jobs.html", jobs=runner.list(), current=runner.current())

    @app.route("/jobs/<job_id>")
    def job(job_id):
        meta = runner.read(job_id)
        if not meta:
            abort(404)
        log_text = runner.log_text(job_id)
        blocks = render.console_blocks(log_text)
        for b in blocks:                       # a 'logged -> path' line becomes a link only if served
            if b["kind"] == "record":
                rel = normalize(b["path"])
                b["link"] = root.link(rel) if root.exists(rel) else None
        view = None
        rec = meta.get("record") or ""
        if rec.startswith("/file/") and rec.endswith(".json"):
            view = render.record_view(root.read_json(rec[len("/file/"):]))
        return render_template("job.html", job=meta, blocks=blocks, view=view, size=len(log_text.encode("utf-8")),
                               running=(meta.get("state") == RUNNING), progress=render.progress(log_text, meta.get("tool")),
                               typical=runner.typical_seconds(meta.get("tool")), elapsed=_elapsed(meta))

    @app.route("/jobs/<job_id>.json")
    def job_status(job_id):
        """What the job page polls: state, elapsed, the stage reached, the last line the
        tool printed, and the log tail -- updated in place, no page reload until the end."""
        meta = runner.read(job_id)
        if not meta:
            abort(404)
        log_text = runner.log_text(job_id)
        tail = [ln for ln in log_text.splitlines() if ln.strip() and not render._is_chatter(ln)][-30:]
        return {"state": meta.get("state"), "rc": meta.get("rc"), "started_at": meta.get("started_at"),
                "finished_at": meta.get("finished_at"), "elapsed": _elapsed(meta),
                "typical": runner.typical_seconds(meta.get("tool")), "record": meta.get("record"),
                "note": meta.get("note"), "progress": render.progress(log_text, meta.get("tool")),
                "tail": [overlay.text(ln) for ln in tail]}

    @app.route("/api/players")
    def api_players():
        """Suggestions for a player field: ?q=partial&owner=all|free|mine|<team>. Pseudonymous
        in, pseudonymous out; the page applies the overlay to what it shows."""
        q = request.args.get("q", "")
        owner = request.args.get("owner", "all")
        idx = PlayerIndex.for_root(root)
        return {"players": [{"name": p["name"], "pos": p.get("pos"), "nfl": p.get("nfl"),
                             "owner": overlay.text(p.get("owner")) if p.get("owner") else None}
                            for p in idx.search(q, owner, MY_TEAM, limit=12)]}

    @app.route("/jobs/<job_id>/log")
    def job_log(job_id):
        if not runner.read(job_id):
            abort(404)
        return Response(runner.log_text(job_id), mimetype="text/plain")

    @app.route("/jobs/<job_id>/cancel", methods=["POST"])
    def job_cancel(job_id):
        require_csrf()
        if not runner.read(job_id):
            abort(404)
        runner.cancel(job_id)
        return redirect(url_for("job", job_id=job_id))

    return app
