"""webui.live -- the live scoreboard on the landing page (docs/WEB_UI.md W6).

READ-ONLY and OFF-DISK, by construction. A refresh fetches two public endpoints -- the
league's matchups for the week from Sleeper (points banked so far, each roster's
starters) and the NFL scoreboard from ESPN (how much of each game is left) -- holds the
result in this process's memory, and writes nothing: not under data/, not to a log, not
to a cache file. The engine reads data/current/ (written only by sync) and the season
logs (written only by the tools); this module touches neither, so the model's inputs,
its predictions log and its later evaluation are unaffected by any number of refreshes.
Auto-refresh is therefore safe to leave on all Sunday.

The win probability it quotes is a QUICK ESTIMATE, and the panel says so: points
banked, plus for every starter whose game is still running the remaining fraction of a
pre-game expectation (this week's lineup or matchup record where the tool has one, else
the baseline mean), Normal-approximated. It is not the engine's number -- F50 pins that
to scripts.live_matchup, which quotes week_expectation() and draws a joint median leg --
and that module imports the engine, which is why the three small formulas below are
copied from it (with their docstrings) rather than imported.
"""
import datetime as _dt
import math
import threading
import time

# ESPN's public scoreboard (copied from scripts.live_matchup, which cannot be imported here:
# it imports the engine). Two abbreviations differ between ESPN and Sleeper.
SCOREBOARD = ("http://site.api.espn.com/apis/site/v2/sports/football/nfl/"
              "scoreboard?week={week}&seasontype=2")
ABBR_ALIASES = {"WSH": "WAS", "LV": "OAK"}
Z80 = 2.5631          # p90 - p10 of a Normal, in sd units: the record's band -> an sd


def clock_fraction(period, display_clock, state, completed):
    """Fraction of the 60-minute game clock still to run. 1.0 pregame, 0.0 final.

    Overtime (period >= 5) is capped at ten minutes of remaining exposure: a team in OT
    has already played a full game, and treating the extra period as another 15 minutes
    would credit a starter with more upside than a pregame one."""
    if completed:
        return 0.0
    if state != "in":
        return 1.0
    try:
        mm, ss = (display_clock or "15:00").split(":")
        sec_in_period = int(mm) * 60 + int(ss)
    except (AttributeError, ValueError):
        sec_in_period = 900
    period = int(period or 1)
    if period >= 5:
        return max(0.0, min(sec_in_period, 600) / 3600.0)
    return max(0.0, min(1.0, (sec_in_period + (4 - period) * 900) / 3600.0))


def remaining(mean, sd, frac):
    """(expected points, sd) still to come for one starter."""
    return mean * frac, sd * math.sqrt(frac)


def win_probability(mu_a, sd_a, mu_b, sd_b, inflate=1.0):
    """P(A finishes above B) for independent Normal totals, margin sd scaled by
    `inflate` to show how soft the number is under same-game correlation."""
    sd = math.hypot(sd_a, sd_b) * inflate
    if sd <= 0:
        return 1.0 if mu_a > mu_b else (0.0 if mu_a < mu_b else 0.5)
    return 0.5 * (1.0 + math.erf((mu_a - mu_b) / sd / math.sqrt(2.0)))


def _fetch_json(url, timeout=15):
    import requests
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def scoreboard(week, fetch):
    """(clocks, games): {nfl abbr: (fraction remaining, human status)} for every team this
    week, and one row per game -- home, away, both scores, the clock label and its state
    ('pre' / 'in' / 'post') -- for the gameday board."""
    payload = fetch(SCOREBOARD.format(week=int(week)))
    clocks, games = {}, []
    for ev in (payload or {}).get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        status = comp.get("status") or {}
        kind = status.get("type") or {}
        frac = clock_fraction(status.get("period"), status.get("displayClock"),
                              kind.get("state"), kind.get("completed"))
        if kind.get("completed"):
            label, state = "final", "post"
        elif kind.get("state") == "in":
            label, state = f"Q{status.get('period')} {status.get('displayClock')}", "in"
        else:
            label, state = "pregame", "pre"
        home = away = home_score = away_score = None
        for c in comp.get("competitors", []):
            abbr = (c.get("team") or {}).get("abbreviation")
            if not abbr:
                continue
            for key in {abbr, ABBR_ALIASES.get(abbr, abbr)}:
                clocks[key] = (frac, label)
            try:
                score = int(float(c.get("score"))) if c.get("score") not in (None, "") else None
            except (TypeError, ValueError):
                score = None
            if c.get("homeAway") == "away":
                away, away_score = abbr, score
            else:
                home, home_score = abbr, score
        if home or away:
            games.append({"home": home, "away": away, "home_score": home_score, "away_score": away_score,
                          "label": label, "state": state, "frac": round(frac, 3)})
    return clocks, games


def game_clocks(week, fetch):
    """{nfl abbr: (fraction remaining, human status)} for every team this week."""
    return scoreboard(week, fetch)[0]


# ------------------------------------------------------------------- expectations
def expectations(root, week):
    """pid -> {name, pos, nfl, mean, sd, source} for every player in the baselines, with
    this week's lineup/matchup record overriding the mean where the tool priced the
    player for THIS week (closer to the engine's number than the season baseline)."""
    from webui.glance import _newest
    base = root.read_json("current/player_baselines.json", {}) or {}
    out, by_name = {}, {}
    for name, e in base.items():
        if not isinstance(e, dict) or e.get("player_id") is None:
            continue
        mu = float(e.get("mean") or 0.0)
        sd = math.hypot(float(e.get("std_aleatoric") or 0.0), float(e.get("std_epistemic") or 0.0))
        row = {"name": name, "pos": e.get("pos") or "?", "nfl": e.get("team") or "FA",
               "mean": mu, "sd": sd, "source": "baseline"}
        out[str(e["player_id"])] = row
        by_name[name] = row
    wk = int(week) if week else None
    if wk and wk in root.decision_weeks():
        dec = root.decisions(wk)
        entries = sorted(dec["canonical"] + dec["archive"], key=lambda e: (e["stamp"] or "", e["name"]), reverse=True)
        lineup_e = _newest(entries, "lineup")
        lineup = root.read_json(lineup_e["rel"], {}) if lineup_e else {}
        for r in (lineup.get("lineup") or []) + (lineup.get("bench") or []):
            row = by_name.get(r.get("name"))
            if row and r.get("expected") is not None:
                row["mean"] = float(r["expected"])
                if r.get("p90") is not None and r.get("p10") is not None:
                    row["sd"] = max(0.0, (float(r["p90"]) - float(r["p10"])) / Z80)
                row["source"] = "lineup record"
        matchup_e = _newest(entries, "matchup")
        matchup = root.read_json(matchup_e["rel"], {}) if matchup_e else {}
        for r in matchup.get("opponent_lineup") or []:
            row = by_name.get(r.get("name"))
            if row and r.get("expected") is not None and row["source"] == "baseline":
                row["mean"] = float(r["expected"])
                if r.get("sd") is not None:
                    row["sd"] = float(r["sd"])
                row["source"] = "matchup record"
    return out


def team_state(m, clocks, exp):
    """One roster's line: banked points, what is still to come, and each starter."""
    scored = {str(k): float(v or 0.0) for k, v in (m.get("players_points") or {}).items()}
    starters = [str(x) for x in (m.get("starters") or []) if x and x != "0"]
    mu = var = 0.0
    rows = []
    for pid in starters:
        e = exp.get(pid) or {}
        nfl = e.get("nfl") or "?"
        frac, label = clocks.get(nfl, (1.0, "unknown" if clocks else "pregame"))
        r_mu, r_sd = remaining(float(e.get("mean") or 0.0), float(e.get("sd") or 0.0), frac)
        mu += r_mu
        var += r_sd ** 2
        rows.append({"name": e.get("name") or f"player {pid}", "pos": e.get("pos") or "?", "nfl": nfl,
                     "status": label, "scored": round(scored.get(pid, 0.0), 2), "left": round(r_mu, 2),
                     "frac": round(frac, 3), "expected": round(float(e.get("mean") or 0.0), 2)})
    banked = float(m.get("points") or 0.0)
    return {"banked": banked, "left_mu": mu, "left_sd": math.sqrt(var), "projected": banked + mu, "rows": rows,
            "to_play": sum(1 for r in rows if r["frac"] > 0), "starters": len(rows)}


def snapshot(root, week, my_team, league_id, fetch, base_url="https://api.sleeper.app/v1", now=None):
    """The live picture of my matchup, from two public reads and the data on disk."""
    from fantasy_sim.config import TEAM_NAME_MAP
    wk = int(week)
    matchups = fetch(f"{base_url}/league/{league_id}/matchups/{wk}") or []
    try:
        clocks, games = scoreboard(wk, fetch)
        clocks_ok = bool(clocks)
    except Exception:                       # the scoreboard is a second source; its loss is a caveat, not a failure
        clocks, games, clocks_ok = {}, [], False
    exp = expectations(root, wk)
    by_team = {}
    for m in matchups:
        team = TEAM_NAME_MAP.get(str(m.get("roster_id")))
        if team:
            by_team[team] = (m, team_state(m, clocks, exp))
    if my_team not in by_team:
        return {"ok": False, "week": wk, "error": "my roster is not in this week's matchups"}
    m_mine, mine = by_team[my_team]
    mid = m_mine.get("matchup_id")
    opponent = next((t for t, (m, _s) in by_team.items() if t != my_team and m.get("matchup_id") == mid), None)
    theirs = by_team[opponent][1] if opponent else None
    p = p_wide = None
    if theirs:
        p = win_probability(mine["projected"], mine["left_sd"], theirs["projected"], theirs["left_sd"])
        p_wide = win_probability(mine["projected"], mine["left_sd"], theirs["projected"], theirs["left_sd"], inflate=1.5)
    labels = sorted({r["status"] for r in mine["rows"]} | {r["status"] for r in (theirs or {"rows": []})["rows"]})
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"ok": True, "week": wk, "fetched_at": stamp, "team": my_team, "opponent": opponent,
            "mine": mine, "theirs": theirs, "p_win": None if p is None else round(p, 4),
            "p_win_wide": None if p_wide is None else round(p_wide, 4),
            "clocks_ok": clocks_ok, "statuses": labels, "games": games,
            "sources": sorted({e.get("source") for e in exp.values() if e.get("source") != "baseline"} - {None})}


class LiveBoard:
    """The in-memory cache behind /api/live. One snapshot at a time, refreshed on demand
    and never more often than `min_interval` seconds; a failed refresh keeps the last
    good snapshot and reports the error beside it. Nothing here touches the disk."""

    def __init__(self, root, my_team, league_id=None, fetch=None, min_interval=45, clock=time.monotonic, max_history=300):
        self.root, self.my_team, self.league_id, self.fetch = root, my_team, league_id, fetch
        self.min_interval, self._clock, self.max_history = min_interval, clock, max_history
        self._lock = threading.Lock()
        self._snap, self._error, self._at = None, None, None
        self._history, self._week = [], None      # the win probability through the day (U7): memory only

    @classmethod
    def default(cls, root, my_team):
        """Network reads only when the league is configured and this is not a runner."""
        import os
        from fantasy_sim.config import LEAGUE_ID
        if not LEAGUE_ID or os.environ.get("GITHUB_ACTIONS"):
            return cls(root, my_team, league_id=None, fetch=None)
        return cls(root, my_team, league_id=LEAGUE_ID, fetch=_fetch_json)

    @property
    def enabled(self):
        return bool(self.league_id and self.fetch)

    def peek(self):
        """What is cached, without any network -- what a page render uses."""
        return self._payload()

    def get(self, week, refresh=False):
        if not self.enabled:
            return self._payload()
        with self._lock:
            age = None if self._at is None else self._clock() - self._at
            if self._snap is None or (refresh and (age is None or age >= self.min_interval)) or (age is not None and age > 600):
                try:
                    self._snap = snapshot(self.root, week, self.my_team, self.league_id, self.fetch)
                    self._error = None if self._snap.get("ok") else self._snap.get("error")
                    if self._snap.get("ok"):
                        if self._snap.get("week") != self._week:
                            self._history, self._week = [], self._snap.get("week")
                        self._history.append({"at": self._snap.get("fetched_at"), "p": self._snap.get("p_win"),
                                              "mine": self._snap["mine"]["projected"],
                                              "theirs": (self._snap.get("theirs") or {}).get("projected")})
                        del self._history[:-self.max_history]
                except Exception as ex:
                    self._error = f"{type(ex).__name__}: {ex}"
                self._at = self._clock()
            return self._payload()

    def _payload(self):
        age = None if self._at is None else int(self._clock() - self._at)
        return {"enabled": self.enabled, "snapshot": self._snap, "error": self._error, "age_seconds": age,
                "min_interval": self.min_interval, "history": list(self._history)}
