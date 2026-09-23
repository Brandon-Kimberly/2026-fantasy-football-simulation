"""
fantasy_sim.data_health

Every source the model consumes, checked independently against what is ON DISK.

B12, promoting a session scratchpad (`health.py`). Each check answers one question and
returns PASS / DEGRADED / FAIL, so a silent fallback -- the failure mode behind F52 and
F54 -- cannot hide behind a successful-looking sync.

**HOW THIS DIFFERS FROM `scripts.check_freshness`, which it deliberately does not
restate.** F57's `sources` block and `check_freshness` answer *"did the last sync
succeed, and what did each source deliver during it?"*. These checks answer *"is the data
I am about to make decisions on healthy?"*, which needs different evidence:

  - ESPN coverage **per week across the season**, read from the projection log rather
    than from one sync's counters. That history is what makes F52's fortnight-long
    outage visible after the fact.
  - F54's posterior reach: how many distinct players have any observed score.
  - Vegas fallback detected **by value** -- teams sitting on the flat 21.5 -- rather than
    by the sync having reported an error, because F52's lesson is that a source can
    succeed and still deliver nothing usable.

Run `check_freshness` for sync status; run this for data fitness. Pure: loaded data in,
verdict dicts out. No files, no network, no engine.
"""
PASS, DEGRADED, FAIL = "PASS", "DEGRADED", "FAIL"
_RANK = {PASS: 0, DEGRADED: 1, FAIL: 2}

# Thresholds. All UNVERIFIED display cutoffs -- they decide what colour a human sees, and
# nothing downstream consumes them. Each is anchored to a number this project measured:
MIN_BASELINES = 800        # a healthy sync writes ~888
THIN_BASELINES = 400       # below this the file is not merely degraded, it is wrong
MIN_BLEND_PLAYERS = 500    # F54 took the posterior from 157 to ~1,018; 157 must not pass
VEGAS_FALLBACK_TOTAL = 21.5   # config.DEFAULT_FALLBACK_TOTALS' flat value
NFL_TEAMS = 32


def _check(name, verdict, detail, **extra):
    return dict({"name": name, "verdict": verdict, "detail": detail}, **extra)


def worst(verdicts):
    """The overall verdict. An EMPTY report is FAIL, not PASS: no checks running means
    they did not run, which is the opposite of a clean bill of health."""
    vs = list(verdicts)
    if not vs:
        return FAIL
    return max(vs, key=lambda v: _RANK.get(v, 2))


def projection_coverage(baselines):
    """How many baseline entries carry a usable mean. Zero is F58's wiped file."""
    entries = {k: v for k, v in (baselines or {}).items() if isinstance(v, dict)}
    n = sum(1 for v in entries.values() if float(v.get("mean") or 0.0) > 0)
    verdict = PASS if n >= MIN_BASELINES else (DEGRADED if n >= THIN_BASELINES else FAIL)
    return _check("Sleeper projections -> baselines", verdict,
                  f"{n} of {len(entries)} entries carry a non-zero mean", players=n)


def espn_coverage_by_week(rows, season):
    """{week: (with_espn, total)} across the whole season, plus any DEAD weeks.

    The per-week history is the point. F52 killed the blend from week 2 onward and every
    individual sync looked fine; only the shape across weeks shows it.
    """
    by_week = {}
    for r in rows or []:
        if str(r.get("season")) != str(season):
            continue
        w = r.get("week")
        have, tot = by_week.get(w, (0, 0))
        by_week[w] = (have + (1 if r.get("espn_mean") is not None else 0), tot + 1)
    if not by_week:
        return _check("ESPN points blend (F52)", FAIL,
                      f"no projection-log rows for season {season}",
                      by_week={}, dead_weeks=[])
    dead = sorted(w for w, (have, _tot) in by_week.items() if have == 0)
    thin = sorted(w for w, (have, tot) in by_week.items() if 0 < have < 0.5 * tot)
    verdict = DEGRADED if (dead or thin) else PASS
    detail = "  ".join(f"wk{w}:{h}/{t}" for w, (h, t) in sorted(by_week.items()))
    if dead:
        detail += f"   DEAD weeks: {dead}"
    return _check("ESPN points blend (F52)", verdict, detail,
                  by_week=by_week, dead_weeks=dead)


def blend_coverage(weekly_actuals):
    """Distinct players with any observed score -- F54's posterior reach.

    No completed weeks is PASS, not FAIL: before a game is played there is nothing to
    observe, and failing there would cry wolf every preseason (F41's shape).
    """
    scored = set()
    for _wk, v in (weekly_actuals or {}).items():
        scored |= set((v or {}).get("player_scores") or {})
    if not weekly_actuals:
        return _check("Bayesian blend coverage (F54)", PASS,
                      "no completed weeks yet -- nothing to observe", players=0)
    n = len(scored)
    verdict = PASS if n >= MIN_BLEND_PLAYERS else DEGRADED
    return _check("Bayesian blend coverage (F54)", verdict,
                  f"{n} players have an observed score (157 was the pre-F54 state)",
                  players=n)


def vegas_health(vegas, week, byes=None):
    """Real market lines, or the flat fallback wearing their clothes.

    DETECTED BY `opponent`, NOT BY THE TOTAL. The first version of this check called any
    team on 21.5 a fallback; DEN's real week-3 line WAS 21.5 -- with a live opponent, a
    2.5 spread and real weather -- so a genuine market line was reported as filler. The
    fallback path in sync.fetch_vegas_implied_totals sets `opponent: "FA"`; a real line
    never does, whatever the number happens to be.

    `byes` ({team: week}) separates the two reasons a team has no game. A bye is not a
    degradation, and counting it as one would cry wolf every bye week (F41's shape).
    """
    meta = (vegas or {}).get("_meta") or {}
    teams = {k: v for k, v in (vegas or {}).items()
             if k not in ("_meta", "FA") and isinstance(v, dict)}
    byes = byes or {}
    no_game = [k for k, v in teams.items() if (v.get("opponent") or "FA") == "FA"]
    on_bye = [k for k in no_game if byes.get(k) == week]
    fallback = len(no_game) - len(on_bye)
    real = len(teams) - len(no_game)
    stamped = meta.get("week")
    if stamped != week:
        return _check("Vegas lines", FAIL,
                      f"stamped week {stamped}, need week {week} -- the engine will "
                      f"reject these and fall back to the ratings model",
                      real=real, fallback=fallback, on_bye=len(on_bye))
    if real == 0:
        return _check("Vegas lines", FAIL,
                      f"no team has an opponent -- no matchup or defensive-tier effect "
                      f"at all (source {meta.get('source')})",
                      real=real, fallback=fallback, on_bye=len(on_bye))
    verdict = PASS if fallback == 0 else DEGRADED
    bye_txt = f", {len(on_bye)} on bye" if on_bye else ""
    return _check("Vegas lines", verdict,
                  f"{real} real / {fallback} with no line{bye_txt}; "
                  f"week {stamped}, source {meta.get('source')}",
                  real=real, fallback=fallback, on_bye=len(on_bye))


def schedule_health(nfl_schedule):
    """A missing week breaks byes and opponents; a missing bye starts a man on his week off."""
    meta = (nfl_schedule or {}).get("_meta") or {}
    failed = list(meta.get("failed_weeks") or [])
    byes = meta.get("byes") or {}
    if failed:
        return _check("NFL schedule", FAIL, f"failed weeks: {failed}",
                      failed_weeks=failed, byes=len(byes))
    verdict = PASS if len(byes) == NFL_TEAMS else DEGRADED
    return _check("NFL schedule", verdict,
                  f"no failed weeks; {len(byes)} of {NFL_TEAMS} teams have a bye",
                  failed_weeks=[], byes=len(byes))


def report(baselines, projection_rows, weekly_actuals, vegas, nfl_schedule, season, week):
    """Every check, plus the overall verdict."""
    checks = [
        projection_coverage(baselines),
        espn_coverage_by_week(projection_rows, season),
        blend_coverage(weekly_actuals),
        vegas_health(vegas, week,
                     byes=((nfl_schedule or {}).get("_meta") or {}).get("byes")),
        schedule_health(nfl_schedule),
    ]
    return {"checks": checks, "verdict": worst(c["verdict"] for c in checks),
            "season": season, "week": week}
