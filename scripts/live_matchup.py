#!/usr/bin/env python3
"""
Am I winning RIGHT NOW, and what is still to come? -- live in-game tracking, read-only.

Every other tool in this repo quotes a week BEFORE it starts. This one answers the
question that actually gets asked on a Sunday, and it is a different question: points
already scored are KNOWN, so the only uncertainty left is the remainder.

  points_so_far                                         certain
  a starter whose game has not kicked off      ->  full mean, full sd
  a starter mid-game, fraction f of clock left ->  mean*f, sd*sqrt(f)
  a starter whose game is final                ->  nothing, zero variance

Scoring accumulates over game time, so expectation scales with time and VARIANCE scales
with time -- hence sd*sqrt(f). Margin is Normal(muA-muB, sqrt(varA+varB)); the head-to-
head probability is Phi of that. The median leg is drawn jointly over all eight rosters
(Monte Carlo) rather than compared against a point estimate, because the median is
itself a random variable.

  py -3.10 -m scripts.live_matchup                    # my matchup + median leg + league
  py -3.10 -m scripts.live_matchup --team "Polar Yetis"
  py -3.10 -m scripts.live_matchup --review           # every roster: over/under, benched
  py -3.10 -m scripts.live_matchup --json

SHOW_REAL_TEAM_NAMES=1 renders the owner's real-name legend, exactly as weekly_report
does; unset (the default, and always on a runner) it stays pseudonymous.

Three stated limits. Players in the same NFL game are correlated and this treats them as
independent, which UNDERSTATES the spread and pulls probabilities toward 0/100 -- so
--inflate reports the same number at widened margin sd. The remaining-time model is
linear in clock: it knows nothing about game script, blowouts, or a back getting benched
in garbage time. And a pre-game starter is carried at his FULL week expectation with no
discount for being inactive or going down early (AUDIT_PLAN F51) -- deliberate, not an
oversight: over a season that risk is actuarial and the engine prices it, but inside one
matchup it is a decision the owner hedges by hand, on the Questionable tag the engine
cannot see. So this number reads "if everyone plays", which is why it sits ABOVE the
simulation's expected_total. It is the right quantity for a Sunday and the wrong one
for a season. This reports; it never writes a prediction row (AUDIT_PLAN F43).
"""
import argparse
import json
import math
from datetime import datetime, timezone

import requests

from fantasy_sim.config import ANON_EPISTEMIC_RATE, BASE_URL, LEAGUE_ID, MY_TEAM, TEAM_NAME_MAP
from fantasy_sim.decisions import week_expectation, questionable_count
from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import load_json
from fantasy_sim.weekly_report import real_name_overlay

SCOREBOARD = ("http://site.api.espn.com/apis/site/v2/sports/football/nfl/"
              "scoreboard?week={week}&seasontype=2")

# ESPN and Sleeper disagree on two abbreviations; Sleeper still carries the pre-move OAK
# for some Raiders rows. Both spellings are registered so a lookup never silently
# defaults to "has not played" and credits a full game that is already over.
ABBR_ALIASES = {"WSH": "WAS", "LV": "OAK"}


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


def game_clocks(week, fetch=None):
    """{nfl abbr: (fraction remaining, human status)} for every team this week."""
    payload = (fetch or _fetch_json)(SCOREBOARD.format(week=week))
    out = {}
    for ev in (payload or {}).get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        status = comp.get("status") or {}
        kind = status.get("type") or {}
        frac = clock_fraction(status.get("period"), status.get("displayClock"),
                              kind.get("state"), kind.get("completed"))
        if kind.get("completed"):
            label = "final"
        elif kind.get("state") == "in":
            label = f"Q{status.get('period')} {status.get('displayClock')}"
        else:
            label = "pregame"
        for c in comp.get("competitors", []):
            abbr = (c.get("team") or {}).get("abbreviation")
            if not abbr:
                continue
            for key in {abbr, ABBR_ALIASES.get(abbr, abbr)}:
                out[key] = (frac, label)
    return out


def current_starters_for(team, week, fetch=None):
    """The set of player NAMES `team` currently has in its starting lineup, from Sleeper.

    B3. The optimizer needs this to tell a locked STARTER (who cannot be benched) from a
    locked BENCH player (who cannot be promoted) -- the two constraints point opposite
    ways and the distinction is the whole point.

    Names, not slots: Sleeper's `starters` array is index-aligned to its own
    `roster_positions` order, which is NOT config.REQUIRED_STARTING_SLOTS' order, so
    carrying a slot across would silently mis-slot people. decisions.optimize_lineup
    re-solves the pinned men's slots itself.
    """
    fetch = fetch or _fetch_json
    if not LEAGUE_ID:
        raise SystemExit("SLEEPER_LEAGUE_ID is not set -- lock detection needs the league.")
    rosters = fetch(f"{BASE_URL}/league/{LEAGUE_ID}/rosters")
    names = {str(r["roster_id"]): TEAM_NAME_MAP.get(str(r["roster_id"]), f"roster {r['roster_id']}")
             for r in rosters}
    players = load_json("data/current/sleeper_players_cache.json")
    out = set()
    for m in fetch(f"{BASE_URL}/league/{LEAGUE_ID}/matchups/{int(week)}") or []:
        if names.get(str(m.get("roster_id"))) != team:
            continue
        for pid in (m.get("starters") or []):
            info = players.get(str(pid)) or {}
            nm = f"{info.get('first_name','')} {info.get('last_name','')}".strip()
            if nm:
                out.add(nm)
    return out


def week_projections(engine, week, expect=week_expectation):
    """pid -> {'mean': this week's expectation, 'sd': predictive sd}, for the tracker.

    F50. The live tracker must quote the SAME number every other decision tool quotes.
    That number is week_expectation() -- the engine's blended baseline scaled by this
    week's environment ratio and script multiplier -- not the raw season `mean` sitting
    in player_baselines.json, which predates both the Bayesian update against observed
    scores (applied at engine init) and the week's Vegas line. Reading the file directly
    made this the only tool in the repo answering from tier one of a three-tier number.

    sd combines aleatoric and epistemic, matching decisions._sample_week_scores: over a
    single week the player's true mean is itself unknown, so the predictive spread has
    to carry parameter uncertainty as well as game-to-game noise.

    `expect` is injectable for the same reason `fetch` is: it keeps the unit tests
    hermetic without standing up an engine.
    """
    out = {}
    for name, entry in (engine.baselines or {}).items():
        if not isinstance(entry, dict):
            continue
        pid = entry.get("player_id")
        if pid is None:
            continue
        mu_0 = float(entry.get("mean") or 0.0)
        sig_e = float(entry.get("std_epistemic", mu_0 * ANON_EPISTEMIC_RATE) or 0.0)
        std_a = float(entry.get("std_aleatoric") or 0.0)
        out[str(pid)] = {"mean": float(expect(engine, name, week)),
                         "sd": math.hypot(std_a, sig_e)}
    return out


def team_states(matchups, rosters, clocks, players, projections):
    """Per roster: banked points, the remaining mean/sd, and each starter's line.

    `projections` is the pid-keyed map from week_projections() -- week-adjusted means
    and full predictive sds. It is deliberately NOT the raw baselines file (F50).
    """
    by_pid = projections or {}
    names = {str(r["roster_id"]): TEAM_NAME_MAP.get(str(r["roster_id"]), f"roster {r['roster_id']}")
             for r in rosters}
    out = {}
    for m in matchups:
        team = names.get(str(m["roster_id"]), "?")
        scored = {str(k): float(v or 0.0) for k, v in (m.get("players_points") or {}).items()}
        starters = [str(x) for x in (m.get("starters") or []) if x and x != "0"]
        mu = var = 0.0
        rows = []
        for pid in starters:
            info = players.get(pid) or {}
            base = by_pid.get(pid) or {}
            nfl = info.get("team") or "FA"
            frac, label = clocks.get(nfl, (1.0, "unknown"))
            r_mu, r_sd = remaining(float(base.get("mean") or 0.0),
                                   float(base.get("sd") or 0.0), frac)
            mu += r_mu
            var += r_sd ** 2
            rows.append({
                "pid": pid,
                "name": f"{info.get('first_name','')} {info.get('last_name','')}".strip() or pid,
                "pos": info.get("position") or "?", "nfl": nfl, "status": label,
                "scored": scored.get(pid, 0.0), "left": r_mu,
                "mean": float(base.get("mean") or 0.0),
                "sd": float(base.get("sd") or 0.0), "frac": frac,
            })
        banked = float(m.get("points") or 0.0)
        out[team] = {"banked": banked, "rem_mu": mu, "rem_sd": math.sqrt(var),
                     "proj": banked + mu, "rows": rows, "mid": m.get("matchup_id"),
                     "players": [str(x) for x in (m.get("players") or [])],
                     "starters": starters, "scored": scored,
                     "left": sum(1 for r in rows if r["frac"] > 0)}
    return out


def median_leg(states, sims=40000, seed=20260913):
    """P(beat the league median) per team, drawn jointly. The median moves with every
    other roster's night, so a point estimate would understate how live it is."""
    import numpy as np
    rng = np.random.default_rng(seed)
    names, draws = [], []
    for team, st in states.items():
        total = np.full(sims, st["banked"])
        for row in st["rows"]:
            if row["frac"] <= 0:
                continue
            mu, sd = remaining(row["mean"], row["sd"], row["frac"])
            total += np.maximum(0.0, rng.normal(mu, sd, sims))
        names.append(team)
        draws.append(total)
    matrix = np.vstack(draws)
    med = np.median(matrix, axis=0)
    return {n: float((matrix[i] > med).mean()) for i, n in enumerate(names)}, matrix, names


def _fetch_json(url, timeout=30):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def gather(week=None, fetch=None):
    fetch = fetch or _fetch_json
    if not LEAGUE_ID:
        raise SystemExit("SLEEPER_LEAGUE_ID is not set -- live tracking needs the league.")
    rosters = fetch(f"{BASE_URL}/league/{LEAGUE_ID}/rosters")
    if week is None:
        state = fetch(f"{BASE_URL}/state/nfl")
        week = int(state.get("week") or 1)
    matchups = fetch(f"{BASE_URL}/league/{LEAGUE_ID}/matchups/{week}")
    clocks = game_clocks(week, fetch=fetch)
    players = load_json("data/current/sleeper_players_cache.json")
    # F50: the engine, not the baselines file -- see week_projections().
    # B4: the engine is returned as well, so the caller can count Questionable starters
    # without paying for a second init (R1: one engine at a time).
    engine = FantasySimulationEngine()
    projections = week_projections(engine, week)
    return week, team_states(matchups, rosters, clocks, players, projections), engine


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--team", default=MY_TEAM)
    ap.add_argument("--week", type=int, default=None)
    ap.add_argument("--sims", type=int, default=40000)
    ap.add_argument("--seed", type=int, default=20260913)
    ap.add_argument("--inflate", type=float, default=1.3,
                    help="margin-sd multiplier reported alongside the independent number")
    ap.add_argument("--review", action="store_true",
                    help="every roster: biggest over/under performers and benched points")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    week, states, engine = gather(args.week)
    overlay = real_name_overlay()
    show = (lambda t: f"{overlay[t]}" if t in overlay else t)

    if args.team not in states:
        raise SystemExit(f"{args.team} is not in this league: {', '.join(sorted(states))}")
    me = states[args.team]
    opp_name = next((t for t, s in states.items()
                     if t != args.team and s["mid"] == me["mid"]), None)
    opp = states.get(opp_name) if opp_name else None

    med, _matrix, _names = median_leg(states, sims=args.sims, seed=args.seed)
    p_h2h = (win_probability(me["proj"], me["rem_sd"], opp["proj"], opp["rem_sd"])
             if opp else float("nan"))
    p_infl = (win_probability(me["proj"], me["rem_sd"], opp["proj"], opp["rem_sd"],
                              inflate=args.inflate) if opp else float("nan"))

    if args.json:
        print(json.dumps({
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "week": week, "team": args.team, "opponent": opp_name,
            "banked": me["banked"], "projected": me["proj"],
            "starters_left": me["left"],
            "p_head_to_head": p_h2h, "p_head_to_head_inflated": p_infl,
            "p_beat_median": med.get(args.team),
            "league": {t: {"banked": s["banked"], "projected": s["proj"],
                           "left": s["left"], "p_beat_median": med.get(t)}
                       for t, s in states.items()},
        }, indent=1, sort_keys=True))
        return 0

    now = datetime.now(timezone.utc)
    print(f"\nWeek {week} live -- {now.strftime('%Y-%m-%d %H:%M:%SZ')} "
          f"/ {datetime.now().astimezone().strftime('%H:%M %Z')}")
    if overlay:
        print("  LOCAL VIEW: real team names (SHOW_REAL_TEAM_NAMES set); do not share.")

    if opp:
        print(f"\n  {show(args.team):28s} {me['banked']:7.2f} banked  "
              f"+{me['rem_mu']:6.1f} to come ({me['left']} left)  -> {me['proj']:7.1f}")
        print(f"  {show(opp_name):28s} {opp['banked']:7.2f} banked  "
              f"+{opp['rem_mu']:6.1f} to come ({opp['left']} left)  -> {opp['proj']:7.1f}")
        margin = me["proj"] - opp["proj"]
        print(f"  margin {margin:+.1f}, sd of margin "
              f"{math.hypot(me['rem_sd'], opp['rem_sd']):.1f}")
        print(f"  P(win head-to-head) = {p_h2h:6.1%}   "
              f"at sd x{args.inflate} (same-game correlation) = {p_infl:.1%}")
        # B4 scope 3 + F51. No availability discount is applied to a pre-game starter, so
        # this number reads "if everyone plays" for BOTH teams. That optimism only cancels
        # when the two rosters carry comparable Questionable counts -- so print them.
        q_me = questionable_count(engine, args.team)
        q_opp = questionable_count(engine, opp_name)
        print(f"  Questionable: {show(args.team)} {q_me}, {show(opp_name)} {q_opp}"
              + ("   (comparable -- the optimism cancels)" if q_me == q_opp else
                 f"   ASYMMETRIC by {abs(q_me - q_opp)}: the margin flatters "
                 f"{show(args.team if q_me > q_opp else opp_name)}"))
        print("  No availability discount is applied to a pre-game starter (F51): the "
              "margin is trustworthy")
        print("  only when these counts are comparable. Check the Saturday designations "
              "yourself.")
    print(f"  P(beat league median) = {med.get(args.team, float('nan')):6.1%}")
    if opp:
        both = p_h2h * med.get(args.team, 0.0)
        neither = (1 - p_h2h) * (1 - med.get(args.team, 0.0))
        print(f"  expected wins this week: {p_h2h + med.get(args.team, 0.0):.2f} of 2 "
              f"(2-0 ~{both:.0%}, 0-2 ~{neither:.0%}; legs treated as independent)")

    print(f"\n  {'team':28s} {'banked':>8} {'left':>5} {'proj':>8} {'P(median)':>10}")
    for t, s in sorted(states.items(), key=lambda kv: -kv[1]["proj"]):
        mark = " <<<" if t == args.team else ""
        print(f"  {show(t):28s} {s['banked']:8.2f} {s['left']:5d} {s['proj']:8.1f} "
              f"{med.get(t, float('nan')):9.1%}{mark}")

    if me["left"]:
        print(f"\n  still to play for {show(args.team)}:")
        for row in sorted((r for r in me["rows"] if r["frac"] > 0), key=lambda r: -r["left"]):
            print(f"    {row['name']:24s} {row['pos']:4s} {row['nfl']:4s} "
                  f"{row['status']:12s} +{row['left']:5.1f} expected")

    if args.review:
        _review(states, show)
    return 0


def _review(states, show):
    """Per roster, from FINISHED games only: the biggest beats and misses against the
    model's mean, and bench points that outscored a started player at the same
    position. Finished-only on purpose -- a player at halftime is not underperforming."""
    print("\n=== roster review (finished games only) ===")
    for team, st in sorted(states.items(), key=lambda kv: -kv[1]["proj"]):
        done = [r for r in st["rows"] if r["frac"] <= 0]
        if not done:
            continue
        print(f"\n  {show(team)}  {st['banked']:.2f}")
        best = sorted(done, key=lambda r: -(r["scored"] - r["mean"]))[:3]
        worst = sorted(done, key=lambda r: (r["scored"] - r["mean"]))[:3]
        print("    over :", ", ".join(f"{r['name']} {r['scored']:.1f} (exp {r['mean']:.1f})"
                                      for r in best))
        print("    under:", ", ".join(f"{r['name']} {r['scored']:.1f} (exp {r['mean']:.1f})"
                                      for r in worst))
        started = set(st["starters"])
        bench_pts = [(pid, st["scored"].get(pid, 0.0)) for pid in st["players"]
                     if pid not in started]
        missed = []
        for pid, pts in bench_pts:
            for row in done:
                if pts > row["scored"] + 3:
                    missed.append((pid, pts, row))
                    break
        if missed:
            print(f"    {len(missed)} bench player(s) outscored a finished starter by 3+")


if __name__ == "__main__":
    raise SystemExit(main())
