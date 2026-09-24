"""Luck ledger: five pre-registered measurements of how the dice fell, per team.

PRE-REGISTERED 2026-09-21 (docs/LUCK_LEDGER.md). The definitions below are fixed before
the season's data was in. Changing one later, after seeing what it says, destroys the
only thing that makes this a test rather than a story -- so don't.

Every metric compares a team TO THE LEAGUE, never to an absolute. The engine has known
bias (points-backtest: bias -2.12, cover80 0.654 against a nominal 0.80), so scoring a
team's z against zero would re-measure the model's error and label it luck. Differencing
against the league cancels anything shared and leaves only what is specific to one team.

  schedule_luck   actual H2H wins - all-play expected wins        null 0
  opponent_luck   points scored against me - league average PA    null 0
  close_games     record in H2H decided by < CLOSE_MARGIN         null .500
  dnp_luck        starter DNPs per game - league average          null 0
  scoring_luck    my mean weekly z - league mean weekly z         null 0

No combined "luck score" is produced, deliberately: five measurements reported side by
side, exactly as season_retrospective refuses a combined verdict. A single blended number
invites exactly the narrative-fitting this module exists to prevent.

Nothing here touches the network or the engine; callers pass plain dicts.
"""
import math

# PRE-REGISTERED with the definitions (F53, docs/LUCK_LEDGER.md): the minimum completed
# weeks before any INFERENCE is reported. Below it the point estimate and its standard
# error still print -- they are the honest content at any n -- but z, p and the
# significance word are withheld. R2: the renderer used to suppress the WORD and print the
# NUMBER, and `p 0.000` beside `too early` is read as significance, because "too early" is
# a word and 0.000 is four significant figures. It lives here, beside the definitions,
# rather than as a literal in a print helper, so it cannot be edited by someone who does
# not know it was pre-registered.
MIN_WEEKS_FOR_INFERENCE = 6

CLOSE_MARGIN = 10.0          # a H2H decided by less than this is a "close game"
DNP_EPSILON = 1e-9           # a starter scoring exactly 0.00 is the DNP proxy

# Which SIGN of `delta` means the dice were kind. Pre-registered alongside the metrics,
# because a reader who cannot tell good luck from bad at a glance is worse off than one
# with no number at all: facing high-scoring opponents (+) is a beating, while carrying
# fewer DNPs than the league (-) is a gift, and both would otherwise print identically.
LUCKY_SIGN = {
    "schedule_luck": +1,     # more actual wins than the all-play rate earned
    "opponent_luck": -1,     # fewer points scored against me than the league average
    "close_games": +1,       # winning more coin-flips than losing
    "dnp_luck": -1,          # fewer starters sitting out than the league average
    "scoring_luck": +1,      # my starters beat projection by more than the league's
}


def direction(metric_name, delta):
    """'lucky' / 'unlucky' / 'neutral' for a delta, under the registered sign convention."""
    sign = LUCKY_SIGN.get(metric_name, +1)
    if delta is None or abs(delta) < 1e-12:
        return "neutral"
    return "lucky" if (delta * sign) > 0 else "unlucky"


def _norm_sf(z):
    """P(Z >= z) for a standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def two_sided_p(z):
    """Two-sided p-value. Reported so a reader can see the sample is too small."""
    return 2.0 * _norm_sf(abs(z))


def all_play(weekly_scores):
    """{team: (wins, games)} if every team played every other team every week."""
    out = {}
    for week, row in weekly_scores.items():
        teams = [t for t, v in row.items() if v is not None]
        for t in teams:
            w, n = out.get(t, (0.0, 0))
            for o in teams:
                if o == t:
                    continue
                n += 1
                if row[t] > row[o]:
                    w += 1.0
                elif row[t] == row[o]:
                    w += 0.5
            out[t] = (w, n)
    return out


def _mean_sd(vals):
    n = len(vals)
    if n == 0:
        return 0.0, 0.0, 0
    m = sum(vals) / n
    if n < 2:
        return m, 0.0, n
    var = sum((v - m) ** 2 for v in vals) / (n - 1)
    return m, math.sqrt(var), n


def banked_disagreement(weekly_scores, pairs, team, banked_wins):
    """Does the RECOMPUTED record match what the league actually banked? (C4)

    Every score here is what Sleeper serves TODAY, and Sleeper re-scores completed weeks
    under the league's current settings. A mid-season scoring change therefore rewrites
    finished results: F49's IDP cut flipped a 0.24-point loss into a 4.33-point win on
    2026-09-23, and nothing about the recomputed data looks wrong -- head-to-head wins
    still sum correctly across the league every week.

    `banked_wins` is Sleeper's own `settings.wins`, carried in `league_standings.json` as
    `h2h_wins`. THAT NAME IS WRONG and the arithmetic below is why it matters: it is TOTAL
    wins, both legs of a median-scoring week. So the comparison has to be h2h wins PLUS
    median wins, or every median win would look like a discrepancy.

    Returns None when the two agree, or when there is no banked record to compare -- an
    absent record is unknown, not proof of a rewrite.
    """
    if banked_wins is None:
        return None
    h2h = med = 0
    weeks = 0
    for week, ps in (pairs or {}).items():
        row = (weekly_scores or {}).get(week) or {}
        played = [v for v in row.values() if v is not None]
        if not played:
            continue
        weeks += 1
        cut = _median(played)
        for a, b in ps:
            sa, sb = row.get(a), row.get(b)
            if sa is None or sb is None or team not in (a, b):
                continue
            mine, theirs = (sa, sb) if a == team else (sb, sa)
            if mine > theirs:
                h2h += 1
        if row.get(team) is not None and row[team] >= cut:
            med += 1
    total = h2h + med
    if total == int(banked_wins):
        return None
    return {"recomputed": total, "banked": int(banked_wins), "weeks": weeks,
            "h2h_wins": h2h, "median_wins": med,
            "note": ("the recomputed record disagrees with the league's banked one. Every "
                     "score here is re-scored under CURRENT settings, so a mid-season "
                     "scoring change (see docs/EVALUATION_BOUNDARIES.md) rewrites finished "
                     "results. Measurements that depend on who WON are withheld rather "
                     "than printed on rewritten history.")}


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if not n:
        return 0.0
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def ledger(weekly_scores, pairs, team, starter_points=None, projections=None,
           banked_wins=None):
    """Five measurements for one team.

    weekly_scores  {week: {team: points}}
    pairs          {week: [(team_a, team_b), ...]}   H2H pairings only
    starter_points {week: {team: [per-starter points]}}   optional, for dnp_luck
    projections    {week: {team: (expected_total, sd)}}   optional, for scoring_luck

    Every value carries its own standard error. A metric whose inputs are absent comes
    back as None rather than silently defaulting -- 2024/2025 have no contemporaneous
    projections, so scoring_luck is honestly unavailable for them.
    """
    out = {"team": team, "weeks": sorted(weekly_scores)}
    # C4: if the recomputed record disagrees with what the league banked, the scores here
    # are re-scored history. Metrics that depend on WHO WON are withheld; metrics that do
    # not (points against, DNP counts) still report, because withholding them would throw
    # away good evidence.
    disagreement = banked_disagreement(weekly_scores, pairs, team, banked_wins)
    if disagreement:
        out["banked_disagreement"] = disagreement

    # --- schedule luck -------------------------------------------------------
    ap = all_play(weekly_scores)
    w, n = ap.get(team, (0.0, 0))
    rate = (w / n) if n else 0.0
    actual, played, margins = 0, 0, []
    pa_mine, pa_by_team = 0.0, {}
    cw = cl = 0
    for week, ps in pairs.items():
        row = weekly_scores.get(week) or {}
        for a, b in ps:
            sa, sb = row.get(a), row.get(b)
            if sa is None or sb is None:
                continue
            pa_by_team[a] = pa_by_team.get(a, 0.0) + sb
            pa_by_team[b] = pa_by_team.get(b, 0.0) + sa
            if team in (a, b):
                mine, theirs = (sa, sb) if a == team else (sb, sa)
                played += 1
                pa_mine += theirs
                margins.append(mine - theirs)
                if mine > theirs:
                    actual += 1
                if abs(mine - theirs) < CLOSE_MARGIN:
                    if mine > theirs:
                        cw += 1
                    else:
                        cl += 1
    exp_w = rate * played
    # variance of a sum of independent Bernoulli(rate) draws
    se_w = math.sqrt(played * rate * (1 - rate)) if played else 0.0
    out["schedule_luck"] = None if disagreement else {
        "actual_wins": actual, "expected_wins": round(exp_w, 2),
        "delta": round(actual - exp_w, 2), "se": round(se_w, 2),
        "z": round((actual - exp_w) / se_w, 2) if se_w > 0 else None,
        "all_play_pct": round(rate * 100, 1), "games": played,
    }

    # --- opponent luck -------------------------------------------------------
    if pa_by_team and played:
        per_game = {t: v / max(sum(1 for wk, ps in pairs.items()
                                   for x in ps if t in x), 1)
                    for t, v in pa_by_team.items()}
        league_avg = sum(per_game.values()) / len(per_game)
        mine_pg = pa_mine / played
        others = [v for t, v in per_game.items() if t != team]
        _m, sd, k = _mean_sd(others)
        se = sd / math.sqrt(max(k, 1)) if sd else 0.0
        out["opponent_luck"] = {
            "my_pa_per_game": round(mine_pg, 2),
            "league_avg_pa_per_game": round(league_avg, 2),
            "delta": round(mine_pg - league_avg, 2), "se": round(se, 2),
            "z": round((mine_pg - league_avg) / se, 2) if se > 0 else None,
        }
    else:
        out["opponent_luck"] = None

    # --- close games ---------------------------------------------------------
    tot = cw + cl
    se_c = math.sqrt(tot * 0.25) if tot else 0.0
    out["close_games"] = None if disagreement else {
        "wins": cw, "losses": cl, "n": tot,
        "delta": round(cw - tot / 2.0, 2) if tot else 0.0, "se": round(se_c, 2),
        "z": round((cw - tot / 2.0) / se_c, 2) if se_c > 0 else None,
        "margin_threshold": CLOSE_MARGIN,
    }

    # --- DNP luck ------------------------------------------------------------
    if starter_points:
        counts = {}
        for week, row in starter_points.items():
            for t, vals in row.items():
                z = sum(1 for v in vals if abs(float(v or 0.0)) <= DNP_EPSILON)
                c, g = counts.get(t, (0, 0))
                counts[t] = (c + z, g + 1)
        rates = {t: (c / g) for t, (c, g) in counts.items() if g}
        if team in rates and len(rates) > 1:
            mine = rates[team]
            others = [v for t, v in rates.items() if t != team]
            _m, sd, k = _mean_sd(others)
            league = sum(rates.values()) / len(rates)
            se = sd / math.sqrt(max(k, 1)) if sd else 0.0
            out["dnp_luck"] = {
                "my_dnps_per_game": round(mine, 2),
                "league_avg": round(league, 2),
                "delta": round(mine - league, 2), "se": round(se, 2),
                "z": round((mine - league) / se, 2) if se > 0 else None,
            }
        else:
            out["dnp_luck"] = None
    else:
        out["dnp_luck"] = None

    # --- scoring luck --------------------------------------------------------
    if projections:
        zs = {}
        for week, row in projections.items():
            scores = weekly_scores.get(week) or {}
            for t, pair in row.items():
                mu, sd = pair
                got = scores.get(t)
                if got is None or not sd:
                    continue
                zs.setdefault(t, []).append((got - mu) / sd)
        if team in zs and len(zs) > 1:
            mine_m, _s, mine_n = _mean_sd(zs[team])
            other_means = [_mean_sd(v)[0] for t, v in zs.items() if t != team]
            _m2, sd2, k2 = _mean_sd(other_means)
            league_m = sum(_mean_sd(v)[0] for v in zs.values()) / len(zs)
            se = sd2 / math.sqrt(max(k2, 1)) if sd2 else 0.0
            out["scoring_luck"] = {
                "my_mean_z": round(mine_m, 3), "league_mean_z": round(league_m, 3),
                "delta": round(mine_m - league_m, 3), "se": round(se, 3),
                "z": round((mine_m - league_m) / se, 2) if se > 0 else None,
                "n_weeks": mine_n,
            }
        else:
            out["scoring_luck"] = None
    else:
        out["scoring_luck"] = None

    return out
