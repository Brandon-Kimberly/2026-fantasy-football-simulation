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
import re
import secrets
import sys

from flask import Flask, Response, abort, redirect, render_template, request, send_file, url_for

from webui import accuracy as accuracymod
from webui import brand, compare as comparemod, history as historymod, objects, players_page as playersmod, recap as recapmod, render, trade as trademod
from webui import sync as syncmod
from webui.glance import (decisions_report, freshness_report, home_report, kickoff_report, latest_answers, latest_digests, logs_git_report,
                          odds_at, odds_moves, odds_now, odds_race, records, roster_vorp, team_hue, windows_report,
                          TOOL_RECORD)
from webui.jobs import RUNNING, JobRefused, JobRunner
from webui.live import LiveBoard, expectations
from webui.names import Overlay
from webui.paths import PathRefused, Root, normalize
from webui.settings import MODES, THEMES, Settings
from webui.players import PlayerIndex
from webui.tools import (ENGINE, SIMPLE_TOOLS, TOOLS, FormError, get as get_tool, label_for, resolve_form,
                         simple_fields)

ALLOWED_HOSTNAMES = ("127.0.0.1", "localhost")
LARGE_FILE_BYTES = 1_000_000          # above this a text file is offered as a download, never pretty-printed (B17)
R1_SENTENCE = ("R1: one engine process at a time. A crashed run is void -- re-run it alone. "
               "Never run the test suite, the goldens, or a hand tool while a job is running.")
WARNINGS_LOG_NOTE = ("data/current/syndicate_warnings.log holds whatever PROCESS last imported "
                     "the engine (F10) -- a tool run, a hand run, or the test suite -- and is not "
                     "any single run's record. Per-run warnings are in the week's audit JSON.")
# W8: the two views' navigation, and what the simple view does not serve at all (the
# owner's pages: files, jobs list, logs, system, sync, records). A simple-mode request for
# one of these gets a plain 404 that names the switch.
NAV_DEV = (("/", "Home"), ("/matchups", "Matchups"), ("/league", "League"), ("/players", "Players"), ("/history", "History"), ("/forecasts", "Forecasts"), ("/accuracy", "Accuracy"), ("/decisions", "Decisions"),
           ("/records", "Records"), ("/tools", "Tools"), ("/jobs", "Jobs"), ("/logs", "Logs"), ("/system", "System"),
           ("/sync", "Sync"))
NAV_SIMPLE = (("/", "Home"), ("/matchups", "Matchups"), ("/league", "League"), ("/players", "Players"), ("/history", "History"), ("/forecasts", "Forecast"), ("/decisions", "Decisions"),
              ("/tools", "Tools"))
DEV_ONLY_PREFIXES = ("/system", "/status", "/logs", "/sync", "/records", "/results", "/health", "/jobs",
                     "/accuracy")
DEV_ONLY_EXACT = ("/jobs",)
TERMINAL_COMMANDS = (
    ("py -3.10 -m scripts.run_sync", "pull live data into data/current/ (H5: the odds key is verified first; "
                                     "on Windows inject the User-scope value if the shell holds a stale one)"),
    ("py -3.10 -m scripts.weekly_report --canonical --embed", "the canonical report, inside a run window"),
    ("py -3.10 -m scripts.check_freshness", "the same verdict as this page, from a terminal"),
)


def _allowed_host(host, port, allowed=ALLOWED_HOSTNAMES):
    name, _, hport = (host or "").partition(":")
    if name.lower() not in allowed:
        return False
    if port and hport and str(hport) != str(port):
        return False
    return True


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
    # one table, not two with overlapping columns: forecast + outcomes joined per team
    by_team = {o.get("Team"): o for o in outcomes if isinstance(o, dict)}
    rows = []
    for t in teams:
        fc, cs, o = forecast[t].get("forecast") or {}, forecast[t].get("current_state") or {}, by_team.get(t) or {}
        rows.append({"team": t, "wins": cs.get("actual_wins_banked"), "points": cs.get("actual_points_banked"),
                     "exp_wins": fc.get("expected_final_wins"), "playoff": fc.get("playoff_probability_pct"),
                     "se": fc.get("playoff_standard_error"), "champ": o.get("Champ_Pct"), "toilet": o.get("Toilet_Pct"),
                     "exp_points": o.get("Expected_Points"), "magic": fc.get("approximate_magic_number"), "faab": cs.get("remaining_faab")})
    return {"week": n, "forecast": forecast, "teams": teams, "outcomes": outcomes, "rows": rows,
            "metadata": matrix.get("metadata") or {}, "seeds": matrix.get("finishing_seed_probabilities") or {},
            "h2h": matrix.get("h2h_win_probability_matrix") or {}, "wins_dist": matrix.get("win_distributions") or {},
            "traj": matrix.get("weekly_trajectories") or {}, "score_pct": matrix.get("weekly_score_percentiles") or {},
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
    now = odds_now(root)                        # playoff odds from THE current forecast (UI-E4), so the
    odds_week = now["week"]                     # standings page answers "and where is that going?"
    odds = {t: v["playoff"] for t, v in now["teams"].items()}
    wins = {t: v.get("wins") for t, v in now["teams"].items()}              # UI-O12
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
    return {"standings": table, "rosters": roster_rows, "pending": pending, "state": state, "odds": odds, "odds_week": odds_week,
            "manifest": manifest, "files": root.current(), "records": records(root), "wins": wins}


# ------------------------------------------------------------------------- factory
def _int_or_none(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def create_app(root, overlay=None, csrf_token=None, port=None, runner=None, live=None, key_probe=None, key_reader=None,
               settings=None, default_mode=None, hostnames=()):
    if not isinstance(root, Root):
        root = Root(root)
    overlay = overlay or Overlay()
    runner = runner if runner is not None else JobRunner(root)
    from fantasy_sim.config import MY_TEAM
    # W6: the live scoreboard's in-memory cache. Tests inject one with a fake fetch; the
    # default only reads the network when the league is configured and this is no runner.
    live = live if live is not None else LiveBoard.default(root, MY_TEAM)
    # W4: the sync page's key probe and User-scope reader; tests inject both so no test
    # ever reaches the-odds-api or reads this machine's registry.
    _probe = key_probe if key_probe is not None else syncmod.probe_key
    _read_user = key_reader
    settings = settings if settings is not None else Settings(root, default_mode=default_mode)
    from fantasy_sim.positional_tiers import _TABLE_CSS, _TABLE_JS

    app = Flask(__name__, template_folder="templates", static_folder=None)
    # A local name for this machine (W9: `--hostname syndicatefootball.local` plus a hosts-file
    # line pointing it at 127.0.0.1) joins the two built-in ones; the bind stays 127.0.0.1.
    allowed = tuple(ALLOWED_HOSTNAMES) + tuple(h.strip().lower() for h in hostnames if h and h.strip())
    app.config.update(ROOT=root, OVERLAY=overlay, PORT=port, SETTINGS=settings, HOSTNAMES=allowed,
                      CSRF_TOKEN=csrf_token or secrets.token_urlsafe(32),
                      MY_TEAM=MY_TEAM, TEMPLATES_AUTO_RELOAD=False)
    app.jinja_env.filters["real"] = overlay.text
    app.jinja_env.filters["stamp"] = render.human_time
    app.jinja_env.filters["num"] = _fmt_num
    app.jinja_env.filters["ts"] = render.human_time
    app.jinja_env.filters["pct"] = render.fpct
    app.jinja_env.filters["signed"] = render.fsigned
    app.jinja_env.filters["se"] = render.fse                   # UI-V6: the only way a ± is printed
    app.jinja_env.filters["verdict"] = lambda d, se: render.verdict(d, se)
    app.jinja_env.filters["ago"] = render.ago
    app.jinja_env.filters["dur"] = render.duration
    app.jinja_env.filters["when"] = render.when
    app.jinja_env.filters["slug"] = render.slug
    app.jinja_env.filters["tool_title"] = render.tool_title
    app.jinja_env.filters["entry_title"] = render.entry_title
    app.jinja_env.filters["pair_digests"] = render.pair_digests
    app.jinja_env.filters["initials"] = lambda s: "".join(w[0] for w in str(s or "").split()[:2]).upper() or "?"
    app.jinja_env.filters["hue"] = team_hue
    app.jinja_env.filters["log_title"] = render.log_title
    app.jinja_env.filters["clock"] = render.human_time
    app.jinja_env.filters["dshort"] = render.duration_short
    app.jinja_env.filters["avatar"] = overlay.avatar
    app.jinja_env.filters["pretty"] = render.pretty_url
    app.jinja_env.filters["job_url"] = render.job_url
    app.jinja_env.filters["sabbr"] = render.status_abbr
    app.jinja_env.globals["line_chart"] = render.line_chart
    app.jinja_env.globals["sparkline"] = render.sparkline
    app.jinja_env.filters["state_label"] = render.state_label
    app.jinja_env.filters["sentence"] = render.sentence
    app.jinja_env.filters["tool_icon"] = render.tool_icon
    app.jinja_env.filters["window_title"] = render.window_title
    app.jinja_env.filters["job_subtitle"] = render.job_subtitle
    app.jinja_env.filters["plain"] = render.simplify

    @app.context_processor
    def _ctx():
        mode = settings.mode
        return {"overlay_enabled": overlay.enabled, "brand": brand.NAME, "tagline": brand.TAGLINE,
                "audit": request.args.get("audit") == "1",      # the harness's overflow probe (scripts.webui_audit)
                "mode": mode, "dev": mode == "dev", "nav": NAV_DEV if mode == "dev" else NAV_SIMPLE,
                "private_marker": overlay.marker() if overlay.enabled else None,
                "csrf_token": app.config["CSRF_TOKEN"], "my_team": MY_TEAM,
                "root_path": root.root, "table_css": _TABLE_CSS, "table_js": _TABLE_JS,
                "r1": R1_SENTENCE, "now": render.human_time(_dt.datetime.now(_dt.timezone.utc)),
                "job_now": runner.current() if runner is not None else None,     # U3: the job bar on every page
                "theme": settings.theme, "palette": _palette(mode)}              # U11 / U4

    @app.before_request
    def _host_check():
        if not _allowed_host(request.host, app.config["PORT"], app.config["HOSTNAMES"]):
            abort(400, "this server answers only to " + " / ".join(app.config["HOSTNAMES"]))

    @app.before_request
    def _mode_gate():
        """W8: the simple view has no files, jobs list, logs, system, sync or records."""
        if settings.mode == "dev":
            return None
        path = request.path
        job_page = path.startswith("/jobs/") and path.count("/") >= 2       # a tool's answer is for everyone
        if job_page or path.startswith("/api/") or path == "/mode":
            return None
        if path.startswith("/records/") and path.count("/") >= 3:                    # a record is a tool's answer
            return None
        if path in DEV_ONLY_EXACT or any(path == pfx or path.startswith(pfx + "/") for pfx in DEV_ONLY_PREFIXES if pfx != "/jobs"):
            return render_template("error.html", code=404, message="That page is part of the developer view. Switch to it from the footer to see it."), 404
        if path.startswith("/tools/") and path[len("/tools/"):].split("?")[0].split("/")[0] not in SIMPLE_TOOLS:
            return render_template("error.html", code=404, message="That tool is part of the developer view."), 404
        if path.startswith("/file/") and request.args.get("raw") == "1" and not path.endswith(".png"):
            return render_template("error.html", code=404, message="Raw files are part of the developer view."), 404
        return None

    def _palette(mode):
        """U4: what the command palette can jump to in THIS view -- its pages, its tools,
        the teams. Pseudonyms in; the page applies the overlay before showing them."""
        items = [{"k": "page", "t": label, "h": href} for href, label in (NAV_DEV if mode == "dev" else NAV_SIMPLE)]
        items.append({"k": "page", "t": "Game day (TV view)", "h": "/gameday"})
        for name, t in TOOLS.items():
            if mode == "dev" or name in SIMPLE_TOOLS:
                items.append({"k": "tool", "t": render.tool_title(name), "h": f"/tools/{name}", "d": t.question})
        if mode == "dev":
            for name in ENGINE:
                items.append({"k": "tool", "t": render.tool_title(name), "h": f"/tools/{name}"})
        standings = root.read_json("current/league_standings.json", {}) or {}
        for team in standings:
            items.append({"k": "team", "t": overlay.text(team), "h": f"/team/{render.slug(team)}"})
        return items

    @app.route("/theme", methods=["POST"])
    def set_theme():
        """U11: system / light / dark, stored beside the mode; every page stamps it on <html>."""
        require_csrf()
        want = request.form.get("theme")
        if want not in THEMES:
            abort(400)
        settings.set_theme(want)
        back = request.form.get("back") or "/"
        return redirect(back if back.startswith("/") and not back.startswith("//") else "/")

    @app.route("/manifest.webmanifest")
    def manifest():
        """U12: installs as an app (its own window and icon) from the local name."""
        body = json.dumps({"name": brand.NAME, "short_name": brand.NAME, "start_url": "/", "scope": "/",
                           "display": "standalone", "background_color": "#f9f9f7", "theme_color": "#2e7d4f",
                           "description": brand.TAGLINE,
                           "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]})
        return Response(body, mimetype="application/manifest+json")

    @app.route("/icon.svg")
    def icon():
        svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
               "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='#2e7d4f'/>"
               "<stop offset='.55' stop-color='#2a78d6'/><stop offset='1' stop-color='#6b5bd2'/></linearGradient></defs>"
               "<rect width='64' height='64' rx='14' fill='url(#g)'/>"
               "<ellipse cx='32' cy='32' rx='22' ry='14' transform='rotate(-35 32 32)' fill='#fff' fill-opacity='.95'/>"
               "<path d='M22 38 L42 26 M27 32 l3 3 M31 29.5 l3 3 M35 27 l3 3' stroke='#2e7d4f' stroke-width='2.4' stroke-linecap='round' fill='none'/></svg>")
        return Response(svg, mimetype="image/svg+xml")

    # ---- UI-A1/A2/A3: the pages a manager thinks in
    @app.route("/team/<slug>")
    def team_page(slug):
        team = objects.team_for_slug(root, slug)
        if not team:
            abort(404)
        return render_template("team.html", **objects.team_report(root, team, MY_TEAM))

    @app.route("/player/<pid>")
    def player_page(pid):
        if not re.fullmatch(r"[0-9A-Za-z]{1,12}", pid or ""):       # Sleeper ids; a defence is its team code
            abort(404)
        rep = objects.player_report(root, pid, MY_TEAM)
        if not rep:
            abort(404)
        return render_template("player.html", **rep)

    # ---- UI-P1 / W1: every player, and the waiver board
    POSITIONS = ("all", "QB", "RB", "WR", "TE", "K", "DL", "LB", "DB")

    def _players(board):
        t = playersmod.players_table(root, MY_TEAM)
        rows = t["rows"]
        if board:
            rows = [r for r in rows if r["standing"] in ("free", "waivers")]
            rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, -(r["week_mean"] or 0.0)))
            shows = (("available", "Everyone available"), ("free", "Free agents"), ("waivers", "On waivers"))
        else:
            shows = (("all", "Everyone"), ("available", "Available"), ("mine", "Mine"), ("rostered", "On a team"))
        pos = request.args.get("pos", "all")
        pos = pos if pos in POSITIONS else "all"
        show = request.args.get("show", shows[0][0])
        show = show if show in dict(shows) else shows[0][0]
        if pos != "all":
            rows = [r for r in rows if r["pos"] == pos]
        keep = {"available": ("free", "waivers"), "free": ("free",), "waivers": ("waivers",), "mine": ("mine",),
                "rostered": ("mine", "rostered")}.get(show)
        if keep:
            rows = [r for r in rows if r["standing"] in keep]
        total = len(rows)
        shown_rows = rows if request.args.get("all") == "1" or request.args.get("q") else rows[:200]
        return render_template("players.html", board=board, rows=shown_rows, total=total, shown=len(shown_rows),
                               positions=POSITIONS, pos=pos, shows=shows, show=show, q=request.args.get("q", ""),
                               week=t["week"], targets_stamp=t["targets_stamp"], n_waivers=t["n_waivers"])

    # ---- UI-H1 / H2 / H3: the league's history
    @app.route("/history")
    def history_page():
        gs = historymod.games(root)
        pairs = historymod.rivalries(gs)
        teams = sorted({t for g in gs for t in (g["a"], g["b"])})
        mine = []
        for (a, b), r in pairs.items():
            if MY_TEAM in (a, b):
                o = b if a == MY_TEAM else a
                mine.append({"opponent": o, "w": r["wins"][MY_TEAM], "l": r["wins"][o], "t": r["ties"],
                             "margin": r["avg_margin"][MY_TEAM], "last": r["last"]})
        mine.sort(key=lambda o: (-(o["w"] - o["l"]), -o["margin"]))
        state = root.read_json("current/league_state.json", {}) or {}
        cur = str(state.get("season") or (root.read_json("current/sync_manifest.json", {}) or {}).get("season") or "")
        return render_template("history.html", book=historymod.record_book(gs), n_games=len(gs), pairs=pairs,
                               teams=teams, mine=mine, seasons=sorted({g["season"] for g in gs}), current_season=cur,
                               drafts=historymod.seasons_available(root))

    @app.route("/draft")
    def draft_page():
        seasons = historymod.seasons_available(root)
        want = request.args.get("season") or (seasons[-1] if seasons else None)
        board = historymod.draft_board(root, want) if want in seasons else None
        return render_template("draft.html", board=board, seasons=seasons)

    @app.route("/trade")
    def trade_page():
        """UI-T1: pick a team and players; the estimate renders at once (webui.trade)."""
        base = root.read_json("current/player_baselines.json", {}) or {}
        rosters = root.read_json("current/live_rosters.json", {}) or {}
        teams = [t for t in objects._teams(root) if t != MY_TEAM]
        other = objects.team_for_slug(root, request.args.get("with", "")) if request.args.get("with") else None

        def rows(team):
            out = []
            for e in rosters.get(team) or []:
                b = base.get(e.get("name")) or {}
                out.append({"name": e.get("name"), "key": str(b.get("player_id") or e.get("name")), "pos": b.get("pos") or e.get("pos"),
                            "mean": b.get("mean"), "bye": b.get("bye"),
                            "status": "IR" if (b.get("on_ir") or e.get("on_ir")) else (b.get("injury_status") or "")})
            return sorted(out, key=lambda r: -(float(r["mean"]) if r["mean"] is not None else -1))

        mine = rows(MY_TEAM)
        theirs = rows(other) if other else []
        pick = lambda vals, rs: [r["name"] for r in rs if r["key"] in vals or r["name"] in vals]   # noqa: E731
        give, get = pick(request.args.getlist("give"), mine), pick(request.args.getlist("get"), theirs)
        est = trademod.estimate(root, MY_TEAM, give, other, get) if other and (give or get) else None
        return render_template("trade.html", teams=teams, other=other, mine=mine, theirs=theirs,
                               give=give, get=get, est=est)

    @app.route("/players")
    def players_list():
        return _players(False)

    @app.route("/waivers")
    def waivers_board():
        return _players(True)

    @app.route("/matchups")
    @app.route("/matchups/week-<int:week>")
    def matchups_page(week=None):
        cur = objects._current_week(root)
        rep = objects.week_games(root, week or cur or 1, MY_TEAM)
        if week is not None and week not in rep["weeks"]:
            abort(404)
        started = bool(kickoff_report(root, rep["week"]).get("started")) if rep["week"] == cur else False
        review = recapmod.week_recap(root, rep["week"], MY_TEAM)                     # UI-R1 / R2
        return render_template("matchups.html", live_enabled=live.enabled, started=started, review=review, **rep)

    @app.route("/favicon.ico")
    def favicon():
        # a page with no icon link (or a browser probing anyway) gets the app icon, not a 404
        return redirect("/icon.svg", code=301)

    @app.route("/api/player")
    def api_player():
        """U5: one player's card -- position, NFL team, owner, projection, bye, status, and
        VORP when the newest roster_grades record carries him. 404 when unknown."""
        name = (request.args.get("name") or "").strip()
        if not name:
            abort(404)
        idx = PlayerIndex.for_root(root)
        p = idx._by_fold.get(name.casefold())
        if not p:
            abort(404)
        base = (root.read_json("current/player_baselines.json", {}) or {}).get(p["name"]) or {}
        status = base.get("injury_status")
        if not status and p.get("owner"):
            for e in (root.read_json("current/live_rosters.json", {}) or {}).get(p["owner"]) or []:
                if e.get("name") == p["name"]:
                    status = e.get("injury_status")
        vorp = tier = None
        rv = roster_vorp(root)
        if rv and p.get("owner"):
            pr = (rv["players"].get(p["owner"]) or {}).get(p["name"]) or {}
            vorp, tier = pr.get("vorp"), pr.get("tier")
        # UI-F6: `mean` is the SEASON baseline; a lineup or matchup record for the current
        # week prices the player for this week, with live.expectations' precedence
        wk = freshness_report(root)["week"]
        priced = next((r for r in expectations(root, wk).values() if r["name"] == p["name"]), None) if wk else None
        if priced and priced["source"] == "baseline":
            priced = None
        return {"name": p["name"], "pos": p.get("pos"), "nfl": p.get("nfl"),
                "owner": overlay.text(p["owner"]) if p.get("owner") else None,
                "mean": base.get("mean"), "bye": base.get("bye"), "status": status, "on_ir": bool(base.get("on_ir")),
                "vorp": vorp, "tier": tier, "week": int(wk) if wk else None, "pid": base.get("player_id"),
                "week_mean": round(priced["mean"], 2) if priced else None,
                "week_source": priced["source"] if priced else None, "week_stamp": priced.get("stamp") if priced else None}

    @app.route("/mode", methods=["POST"])
    def set_mode():
        """The one route that changes the view; when the engine is hosted for others this
        is the route that goes behind the owner's login (docs/WEB_UI.md W8)."""
        require_csrf()
        want = request.form.get("mode")
        settings.set_mode(want if want in MODES else ("simple" if settings.mode == "dev" else "dev"))
        back = request.form.get("back") or "/"
        return redirect(back if back.startswith("/") and not back.startswith("//") else "/")

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
    def home():
        rep = home_report(root, MY_TEAM, runner)
        return render_template("home.html", live=_live_payload(live.peek()), **rep)

    def _live_payload(p):
        """The board's payload with the overlay applied to the two team names -- the JSON
        the panel's script consumes, so real names reach it the same way they reach HTML."""
        snap = p.get("snapshot")
        if snap:
            snap = dict(snap)
            for k in ("team", "opponent"):
                if snap.get(k):
                    snap[k] = overlay.text(snap[k])
            p = dict(p, snapshot=snap)
        return p

    @app.route("/gameday")
    def gameday():
        """The TV view: a full-screen live scoreboard, both views, from the cached
        snapshot (the page's own script refreshes it through /api/live)."""
        rep = home_report(root, MY_TEAM, runner)
        return render_template("gameday.html", live=_live_payload(live.peek()), **rep)

    @app.route("/api/live")
    def api_live():
        """The live scoreboard (W6). ?refresh=1 asks the board to re-read Sleeper and the
        scoreboard, subject to its minimum interval; nothing is written anywhere."""
        wk = freshness_report(root)["week"]
        if not wk:
            return {"enabled": False, "snapshot": None, "error": "no sync week on disk", "age_seconds": None}
        return _live_payload(live.get(wk, refresh=request.args.get("refresh") == "1"))

    @app.route("/system")
    def system():
        fr = freshness_report(root)
        return render_template("status.html", fr=fr, windows=windows_report(root, fr["week"]),
                               git=logs_git_report(root), digests=latest_digests(root),
                               commands=TERMINAL_COMMANDS, warnings_note=WARNINGS_LOG_NOTE)

    @app.route("/status")
    def status():
        return redirect(url_for("system"))

    @app.route("/health")
    def health():
        fr = freshness_report(root)
        return {"ok": True, "week": fr["week"], "freshness": fr["status"], "weeks": root.weeks(),
                "real_names": overlay.enabled}

    @app.route("/forecasts")
    @app.route("/weeks")
    def weeks():
        """One row per export, carrying MY forecast from that export -- so the page reads as
        the odds across the season's runs, not as a directory listing."""
        rows = []
        for n in root.weeks():
            mine = odds_at(root, n).get(MY_TEAM) or {}                      # UI-E4
            m = root.read_json(f"weeks/week_{n:02d}/syndicate_comprehensive_matrix_week_{n}.json", {}) or {}
            rows.append({"week": n, "playoff": mine.get("playoff"), "se": mine.get("playoff_se"),
                         "exp_wins": mine.get("exp_wins"), "champ": mine.get("champ"), "banked": mine.get("banked"),
                         "sims": (m.get("metadata") or {}).get("simulations"),
                         "mtime": root.mtime(f"weeks/week_{n:02d}/syndicate_comprehensive_matrix_week_{n}.json")})
        return render_template("weeks.html", rows=rows, race=odds_race(root, MY_TEAM), moves=odds_moves(root))

    @app.route("/forecasts/week-<int:week>")
    @app.route("/weeks/<int:week>")
    def week(week):
        if week not in root.weeks():
            abort(404)
        return render_template("week.html", **week_report(root, week))

    @app.route("/records")
    def records_index():
        wks = root.decision_weeks()
        if wks:
            return redirect(url_for("decisions", week=wks[-1]))
        return render_template("records.html", week=None, listing=None, weeks=[],
                               adhoc=root.adhoc(), season=root.season(), title="Records")

    @app.route("/records/week-<int:week>")
    @app.route("/decisions/<int:week>")
    def decisions(week):
        if week not in root.decision_weeks():
            abort(404)
        return render_template("records.html", week=week, listing=root.decisions(week),
                               weeks=root.decision_weeks(), adhoc=None, season=None,
                               title=f"Week {week} records")

    @app.route("/records/ad-hoc")
    @app.route("/decisions/adhoc")
    def adhoc():
        return render_template("records.html", week=None, listing=None, weeks=root.decision_weeks(),
                               adhoc=root.adhoc(), season=root.season(), title="Ad-hoc and season records")

    def _all_record_entries():
        out = []
        for wk in root.decision_weeks():
            d = root.decisions(wk)
            out.extend(d["canonical"] + d["archive"])
        return out + root.adhoc() + root.season()

    @app.route("/records/<scope>/<path:rest>")
    def record_pretty(scope, rest):
        """/records/week-3/optimal-lineup/2026-09-24-165331 and friends: the readable
        address every list links to. Resolved by matching the entry whose pretty URL is
        this path, so the mapping lives in one place (render.pretty_url)."""
        want = f"/records/{scope}/{rest}"
        for e in _all_record_entries():
            if render.pretty_url(e) == want:
                return file_view(e["rel"])
        abort(404)

    @app.route("/accuracy")
    def accuracy_page():
        """W17: what the model quoted before each week's games, against what happened."""
        return render_template("accuracy.html", **accuracymod.report(root))

    @app.route("/decisions")
    def decisions_tab():
        """Every logged move and what the paired evaluation said it did to the team's
        chances -- the decision log, joined, summed, and charted."""
        rep_ = decisions_report(root, MY_TEAM)
        series = rep_["series"]
        chart = render.line_chart([{"name": "my playoff odds, cumulative effect of my moves", "values": [x["value"] for x in series], "cls": "me"}],
                                  [render.short_date(x["at"]) for x in series],
                                  unit=" pts", nd=1, y_min=None, height=200) if len(series) > 1 else ""
        return render_template("decisions.html", chart=chart, **rep_)

    @app.route("/results")
    def results():
        return render_template("results.html", results=root.results())

    @app.route("/league")
    @app.route("/current")
    def current():
        return render_template("current.html", vorp=roster_vorp(root), extras=objects.league_extras(root, MY_TEAM),
                               **current_report(root))

    @app.route("/logs")
    def logs():
        return render_template("logs.html", logs=root.logs())

    @app.route("/logs/<name>")
    def log(name):
        """/logs/decision-log (the readable address) or /logs/decision_log.jsonl (the file)."""
        if "." not in name:
            match = next((e for e in root.logs() if e["ext"] == "jsonl" and render.slug(e["name"].rsplit(".", 1)[0]) == name), None)
            if not match:
                abort(404)
            name = match["name"]
        rel = "logs/" + name
        n = request.args.get("n", "100")
        try:
            n = max(1, min(int(n), 5000))
        except ValueError:
            n = 100
        if not name.endswith(".jsonl"):
            return redirect(url_for("file_view", rel=rel))
        rows, total = root.tail_jsonl(rel, n)
        # a table, not a wall of JSON: one column per top-level scalar key (first eight,
        # in order of appearance), nested values folded into readable text
        cols = []
        for r in rows:
            for k, v in (r.items() if isinstance(r, dict) else []):
                if k not in cols and not isinstance(v, (dict, list)):
                    cols.append(k)
        cols = cols[:8]
        table = []
        for r in rows:
            if not isinstance(r, dict):
                table.append({"cells": [str(r)], "rest": "", "raw": json.dumps(r, ensure_ascii=False)})
                continue
            rest = {k: v for k, v in r.items() if k not in cols}
            table.append({"cells": [render._join(r.get(k)) if r.get(k) is not None else "" for k in cols],
                          "rest": render._join(rest) if rest else ""})
        return render_template("log.html", name=name, cols=cols, rows=table, total=total, n=n, link=root.link(rel),
                               slug=render.slug(name.rsplit(".", 1)[0]))

    @app.route("/file/<path:rel>")
    def file_view(rel):
        full = root.resolve_file(rel)
        ext = os.path.splitext(full)[1].lower()
        raw = request.args.get("raw") == "1"
        if ext == ".png":
            return send_file(full, mimetype="image/png", max_age=0)
        if not raw and ext in (".json", ".jsonl", ".md", ".txt", ".log", ".csv") and os.path.getsize(full) > LARGE_FILE_BYTES:
            # B17: a 24 MB players cache pretty-printed into a page is not a page anyone can open; offer the bytes instead
            return render_template("file.html", rel=rel, body="", kind="large", link=root.link(rel),
                                   size_mb=round(os.path.getsize(full) / 1048576, 1))
        if ext == ".html":
            return Response(overlay.html(root.read_text(rel)), mimetype="text/html")
        if ext == ".json":
            data = root.read_json(rel)
            body = json.dumps(data, indent=1, ensure_ascii=False, sort_keys=False)
            if raw:
                return Response(body, mimetype="application/json")
            if isinstance(data, dict) and data.get("tool"):      # a decision tool's record: tables, not a dump
                return render_template("record.html", rel=normalize(rel), body=body, link=root.link(rel),
                                       view=render.record_view(data), pretty=render.pretty_url(root.entry(rel)))
            return render_template("file.html", rel=rel, body=body, kind="json", link=root.link(rel))
        if ext == ".jsonl":
            if raw:                                            # the whole file, as written
                return Response(root.read_text(rel), mimetype="text/plain",
                                headers={"Content-Disposition": f'attachment; filename="{rel.rsplit("/", 1)[-1]}"'} if request.args.get("dl") == "1" else {})
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
        avg = {n: runner.average_seconds(n) for n in list(TOOLS) + list(ENGINE)}
        tools = list(TOOLS.values()) if settings.mode == "dev" else [TOOLS[n] for n in SIMPLE_TOOLS if n in TOOLS]
        return render_template("tools.html", tools=tools, engine=list(ENGINE.values()) if settings.mode == "dev" else [],
                               current=runner.current(), avg=avg, latest=latest_answers(root), writes=TOOL_RECORD)

    def _compare_args():
        names = [n for n in request.args.getlist("p") if n.strip()]
        if not 2 <= len(names) <= 4:
            abort(400)
        return names, _int_or_none(request.args.get("week"))

    @app.route("/api/compare")
    def api_compare():
        """UI-P4: the quick estimate for two to four players, as JSON. Reads only."""
        names, week = _compare_args()
        return comparemod.estimate(root, names, week)

    @app.route("/tools/compare_players/instant")
    def compare_instant():
        """The same estimate as the compare page's panel body, for the page to swap in."""
        names, week = _compare_args()
        return render_template("_instant.html", inst=comparemod.estimate(root, names, week))

    @app.route("/tools/<name>", methods=["GET", "POST"])
    def tool(name):
        try:
            t = get_tool(name)
        except KeyError:
            abort(404)
        values = {f.name: ("" if f.default is None else str(f.default)) for f in t.fields}
        # A link can pre-fill a form (?a=X&b=Y from a watch-list row); only known fields, GET only.
        if request.method == "GET":
            values.update({f.name: request.args.get(f.name) for f in t.fields if request.args.get(f.name)})
        error = None
        # W3: an engine run shows the freshness verdict and the run windows first, and a
        # STALE tree is refused here, before the tool would refuse it itself.
        fr = freshness_report(root) if t.engine else None
        win = windows_report(root, fr["week"]) if t.engine else None
        if request.method == "POST":
            require_csrf()
            values.update({f.name: request.form.get(f.name, "") for f in t.fields})
            if fr is not None and fr["status"] == "STALE":
                raise JobRefused("the data on disk is STALE -- " + "; ".join(fr["stale_reasons"] or fr["reasons"][:1]) +
                                 " -- sync first, from the Sync page")
            try:
                form, notes = resolve_form(t, request.form, PlayerIndex.for_root(root), MY_TEAM)
                values.update({f.name: form.get(f.name, "") for f in t.fields})
                argv = t.argv(form)
                job_id = runner.launch(argv, tool=name, label=label_for(t, form),
                                       extra={"resolved": notes} if notes else None)
                return redirect(url_for("job", job_id=job_id))
            except FormError as ex:
                error = str(ex)
        inst = None                                    # UI-P4: the quick estimate, at once
        if name == "compare_players" and values.get("a") and values.get("b"):
            inst = comparemod.estimate(root, [values["a"], values["b"]], _int_or_none(values.get("week")))
        return render_template("tool.html", tool=t, values=values, error=error, current=runner.current(), inst=inst,
                               fr=fr, windows=win, avg=runner.average_seconds(t.name),
                               shown=(t.fields if settings.mode == "dev" else simple_fields(t)))

    # ------------------------------------------------------------------ W4: sync
    def _sync_context(probe=None, compare=None):
        fr = freshness_report(root)
        key, source = syncmod.user_scope_key(read_user=_read_user)
        return {"fr": fr, "key_present": bool(key), "key_source": source, "probe": probe,
                "backups": syncmod.list_backups(root), "modes": syncmod.MODES, "current": runner.current(),
                "changes": syncmod.changes(root, compare), "compare": compare,
                "last_job": next((m for m in runner.list() if m.get("tool") in ("run_sync", "weekly_report") and m.get("sync")), None)}

    @app.route("/sync")
    def sync_page():
        """The sync page renders without any network: the probe runs only on launch."""
        return render_template("sync.html", **_sync_context(compare=request.args.get("from")))

    @app.route("/sync/launch", methods=["POST"])
    def sync_launch():
        require_csrf()
        mode = request.form.get("mode", "sync")
        if mode not in syncmod.MODES:
            abort(400)
        key, source = syncmod.user_scope_key(read_user=_read_user)
        probe = dict(_probe(key), source=source)
        if probe["verdict"] != "ok":                       # preflight: nothing written, nothing launched
            return render_template("sync.html", error=probe["detail"], **_sync_context(probe=probe)), 409
        if runner.current():
            raise JobRefused(f"busy: job {runner.current()['id']} is still running")
        name = syncmod.backup(root, reason=f"before {mode}")   # the restore C3 never had
        argv = syncmod.argv_for(mode, python=sys.executable)
        try:
            job_id = runner.launch(argv, tool=syncmod.MODES[mode]["module"].rsplit(".", 1)[-1],
                                   label=syncmod.MODES[mode]["label"],
                                   extra={"sync": True, "backup": name, "key_source": source,
                                          "key_remaining": probe.get("remaining")},
                                   env={"ODDS_API_KEY": key})
        except JobRefused:
            raise
        return redirect(url_for("job", job_id=job_id))

    @app.route("/sync/restore", methods=["POST"])
    def sync_restore():
        require_csrf()
        if runner.current():
            raise JobRefused("a job is running; restore when it has finished")
        try:
            n = syncmod.restore(root, request.form.get("name", ""))
        except (ValueError, FileNotFoundError):
            abort(404)
        return render_template("sync.html", restored=n, restored_name=request.form.get("name"), **_sync_context())

    @app.route("/jobs")
    def jobs():
        jobs = [dict(m, seconds=_elapsed(m)) for m in runner.list()]
        durations = {}                                              # U13: per tool, oldest first, finished OK only
        for m in sorted(jobs, key=lambda m: m.get("started_at") or ""):
            if m.get("state") == "OK" and m.get("seconds") is not None:
                durations.setdefault(m.get("tool"), []).append(m["seconds"])
        durations = {t: v[-12:] for t, v in durations.items() if len(v) >= 2}
        return render_template("jobs.html", jobs=jobs, current=runner.current(), durations=durations)

    @app.route("/jobs/<slug>/<stamp>")
    def job_pretty(slug, stamp):
        """/jobs/optimal-lineup/2026-09-26-000000-000001 -- the readable address."""
        want = f"/jobs/{slug}/{stamp}"
        for m in runner.list():
            if render.job_url(m) == want:
                return job(m["id"])
        abort(404)

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
                b["link"] = render.pretty_url(root.entry(rel)) if root.exists(rel) else None
        view = None
        rec = meta.get("record") or ""
        if rec.startswith("/file/") and rec.endswith(".json"):
            view = render.record_view(root.read_json(rec[len("/file/"):]))
            meta = dict(meta, record=render.pretty_url(root.entry(rec[len("/file/"):])))
        elif meta.get("state") != RUNNING and "--json" in (meta.get("args") or []):
            view = render.record_view(render.stdout_json(log_text), tool=meta.get("tool"))   # a --json tool: its document, as tables
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
