"""What to watch in this week's matchup -- the brief that was assembled by hand, twice.

T5 (docs/SCOPED_BACKLOG_2.md). The owner asked what to watch this week; the answer was put
together by hand from four sources -- both lineups grouped by NFL game, the Vegas line per
game, the correlated stacks, the Questionable starters, the windiest game -- and then built
a SECOND time the same evening, because a pending trade and the opponent's empty DL slot
changed it. Every input is mechanical, which is the whole argument for this module.

Five blocks, each answering a question the hand-built version answered:

  games                which real NFL games this matchup actually turns on, and what the
                       market says about each
  stacks               a game holding three or more starters from one side: that side's
                       week is correlated with one football game
  designations         every flagged starter on EITHER roster (B4's rule -- checking only
                       your own is how you get surprised at 12:58)
  shared_games         where the two rosters meet: on the same NFL team (correlated -- you
                       both win or both lose) or on opposite sides (hedged)
  their_losing_script  the one game carrying the largest share of the opponent's total

NOTHING HERE CHANGES A NUMBER. Every expectation comes from `decisions.week_expectation`
and every line from the engine's own `_compute_week_environment`; this module groups and
counts. In particular the weather fields are printed as CONTEXT and are not in any
projection on the page -- F55 is open, and a reader who assumes otherwise would double-count
a windy game they can already see priced into the Vegas total.

Pure with respect to lineup selection: the starter lists are passed in. The caller decides
what a lineup is (`matchup_lineups` has already solved one), so this module cannot disagree
with the tool it is printed underneath.
"""
from fantasy_sim.decisions import _opts, _unavailable_now, injury_flag, week_expectation

NO_GAME = "no game"


def starters_by_expectation(engine, team, week):
    """The engine's own max-expectation lineup for `team`, as names. Deterministic.

    For the standalone brief, where no joint sample has been drawn. `matchup_lineup` passes
    its already-solved lineups instead, so the brief printed under that tool always shows
    the lineup that tool is recommending rather than a second opinion.

    An unfillable slot yields no name. The brief is about which real games matter, and a
    streamer belongs to no game -- inventing one here would put a phantom in a stack count.
    """
    names = [n for n in engine.rosters.get(team, [])
             if not (_entry(engine, n).get("bye") == week
                     or _unavailable_now(_entry(engine, n)))]
    cands = [(n, _opts(engine, n), week_expectation(engine, n, week)) for n in names]
    assigned, _unfilled = engine._solve_optimal_assignment(cands)
    return [n for n, _v, _slot in assigned]


def _entry(engine, name):
    d = engine.baselines.get(name, {})
    return d if isinstance(d, dict) else {}


def _env(engine, week, nfl_team):
    try:
        return engine._compute_week_environment(week, nfl_team) or {}
    except Exception:
        return {}


def game_key(nfl_team, opponent):
    """A stable name for one real NFL game, from either side.

    Sorted rather than home@away: the Vegas payload carries `opponent` but not which side
    is home, and inventing a direction would be a claim the data does not support.
    """
    if not nfl_team or nfl_team in ("FA", None):
        return NO_GAME
    if not opponent or opponent in ("FA", None) or opponent == nfl_team:
        return NO_GAME
    return " vs ".join(sorted((str(nfl_team), str(opponent))))


def _player_row(engine, name, week, side):
    e = _entry(engine, name)
    return {"name": name, "side": side, "nfl_team": e.get("team") or "FA",
            "expected": float(week_expectation(engine, name, week)),
            "flag": injury_flag(e)}


def watch(engine, team, opponent, week, my_names, opp_names, stack_min=3):
    """The five blocks, for one matchup. `my_names`/`opp_names` are starter names."""
    rows = ([_player_row(engine, n, week, "mine") for n in my_names]
            + [_player_row(engine, n, week, "theirs") for n in opp_names])

    games = {}
    for r in rows:
        env = _env(engine, week, r["nfl_team"])
        key = game_key(r["nfl_team"], env.get("opponent"))
        g = games.setdefault(key, {
            "game": key, "nfl_teams": {}, "mine": [], "theirs": [],
            "mine_sum": 0.0, "theirs_sum": 0.0, "game_total": None,
            "wind_mph": None, "precip_in": None, "precip_prob": None, "weather_source": None,
        })
        g["mine" if r["side"] == "mine" else "theirs"].append(r)
        g["mine_sum" if r["side"] == "mine" else "theirs_sum"] += r["expected"]
        if key == NO_GAME:
            continue
        # BOTH sides of the game, not only the team that happens to field a starter. The
        # implied total is per NFL TEAM, so a game total needs the pair; looking up one
        # side left seven of twelve games on the first live page with no total at all.
        for side, side_env in ((r["nfl_team"], env),
                               (env.get("opponent"), _env(engine, week, env.get("opponent")))):
            if not side or side in g["nfl_teams"]:
                continue
            g["nfl_teams"][side] = {"implied_total": side_env.get("total"),
                                    "spread": side_env.get("spread")}
            # Weather belongs to the GAME, not the team, and both sides carry the same
            # forecast -- take it from whichever side we saw first and do not average.
            # `precip_prob` is a PERCENTAGE (sync stores Open-Meteo's 0-100 value); it is
            # carried through unscaled and every renderer must print it as one.
            if g["weather_source"] is None and side_env.get("weather_source"):
                g["wind_mph"] = side_env.get("wind_mph")
                g["precip_in"] = side_env.get("precip_in")
                g["precip_prob"] = side_env.get("precip_prob")
                g["weather_source"] = side_env.get("weather_source")

    for g in games.values():
        totals = [v["implied_total"] for v in g["nfl_teams"].values() if v["implied_total"] is not None]
        # Only a FULL pair of implied totals sums to a game total. One side is half a
        # number and printing it as the game's total would be wrong by about 21 points.
        g["game_total"] = round(sum(totals), 2) if len(totals) == 2 else None
        my_nfl = {r["nfl_team"] for r in g["mine"]}
        their_nfl = {r["nfl_team"] for r in g["theirs"]}
        same = bool(my_nfl & their_nfl)
        opposed = any(a != b for a in my_nfl for b in their_nfl)
        g["shared"] = (("both" if same and opposed else "correlated" if same
                        else "opposed" if opposed else None)
                       if (my_nfl and their_nfl and g["game"] != NO_GAME) else None)
        for k in ("mine", "theirs"):
            g[k].sort(key=lambda r: -r["expected"])
        g["mine_sum"] = round(g["mine_sum"], 2)
        g["theirs_sum"] = round(g["theirs_sum"], 2)

    ordered = sorted(games.values(),
                     key=lambda g: (g["game"] == NO_GAME,
                                    -(g["mine_sum"] + g["theirs_sum"]), g["game"]))

    stacks = []
    for g in ordered:
        if g["game"] == NO_GAME:
            continue
        for side, label in (("mine", team), ("theirs", opponent)):
            if len(g[side]) >= stack_min:
                stacks.append({"game": g["game"], "side": side, "team": label,
                               "n": len(g[side]), "sum": g[f"{side}_sum"],
                               "players": [r["name"] for r in g[side]]})
    stacks.sort(key=lambda s: -s["sum"])

    designations = sorted((r for r in rows if r["flag"]),
                          key=lambda r: (r["side"] != "mine", -r["expected"]))
    shared = [g for g in ordered if g["shared"]]

    played = [g for g in ordered if g["game"] != NO_GAME and g["theirs"]]
    script = max(played, key=lambda g: g["theirs_sum"], default=None)
    losing_script = None
    if script is not None:
        total_theirs = sum(g["theirs_sum"] for g in ordered) or 1.0
        losing_script = {
            "game": script["game"], "sum": script["theirs_sum"],
            "share": round(script["theirs_sum"] / total_theirs, 4),
            "players": [r["name"] for r in script["theirs"]],
        }

    return {"team": team, "opponent": opponent, "week": week,
            "games": ordered, "stacks": stacks, "designations": designations,
            "shared_games": shared, "their_losing_script": losing_script,
            "weather_note": ("wind and precipitation are shown as CONTEXT only -- they are "
                             "fetched but not modelled (F55), and no projection on this page "
                             "includes them. The Vegas total already prices the forecast, so "
                             "reading the wind as an extra discount double-counts it.")}


def render_lines(w, name_of=None):
    """The brief as plain lines. `name_of` maps a fantasy team name for display, which is
    how the real-name overlay reaches this without the library ever knowing a real name."""
    def T(x):
        return (name_of or {}).get(x, x) if isinstance(name_of, dict) else x

    out = [f"WHAT TO WATCH -- {T(w['team'])} vs {T(w['opponent'])}, week {w['week']}"]
    out.append("  game                 tot    mine  theirs  wind  precip  who")
    for g in w["games"]:
        tot = f"{g['game_total']:.1f}" if g["game_total"] is not None else "  -  "
        wind = f"{g['wind_mph']:.0f}" if g["wind_mph"] is not None else "-"
        pp = f"{g['precip_prob']:.0f}%" if g["precip_prob"] is not None else "-"
        who = []
        if g["mine"]:
            who.append("me: " + ", ".join(r["name"] for r in g["mine"]))
        if g["theirs"]:
            who.append("them: " + ", ".join(r["name"] for r in g["theirs"]))
        out.append(f"  {g['game']:18s} {tot:>5s} {g['mine_sum']:7.1f} {g['theirs_sum']:7.1f} "
                   f"{wind:>5s} {pp:>6s}  {'; '.join(who)}"
                   + (f"   [{g['shared']}]" if g["shared"] else ""))
    for s in w["stacks"]:
        out.append(f"  STACK: {T(s['team'])} has {s['n']} starters in {s['game']} "
                   f"({s['sum']:.1f} points) -- that side's week rides on one football game.")
    for d in w["designations"]:
        out.append(f"  DESIGNATION ({T(w['team']) if d['side'] == 'mine' else T(w['opponent'])}): "
                   f"{d['name']} {d['flag']} -- {d['expected']:.1f} expected, undiscounted (F51).")
    for g in w["shared_games"]:
        out.append(f"  SHARED: {g['game']} is {g['shared']} -- "
                   + "; ".join(f"{k}: {', '.join(r['name'] for r in g[k])}"
                               for k in ("mine", "theirs") if g[k]))
    ls = w.get("their_losing_script")
    if ls:
        out.append(f"  THEIR LOSING SCRIPT: {ls['game']} carries {100 * ls['share']:.0f}% of "
                   f"the expected total for {T(w['opponent'])} ({ls['sum']:.1f} from "
                   f"{', '.join(ls['players'])}).")
    out.append("  " + w["weather_note"])
    return out
