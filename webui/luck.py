"""webui.luck -- the luck ledger, pulled not pushed (docs/WEB_UI_ROADMAP.md UI-R6).

The owner's call: luck is looked up, never put on Sunday's front page. This page renders the
ledger's five PRE-REGISTERED measures (fantasy_sim.luck_ledger, docs/LUCK_LEDGER.md) for one
team -- no sixth measure (Decision 2 governs new ones) and no combined score, exactly as the
module refuses one. It is reached from team pages and the palette and never from Home.

The inputs are the ones scripts.luck_ledger fetches, read from disk instead:
  weekly scores   current/weekly_actuals.json (Sleeper's box scores -- re-scored weeks
                  included, which is why the ledger compares against the banked record)
  pairs           current/league_schedule.json
  projections     logs/predictions_2026.jsonl, the median block (as the script reads it)
  banked wins     current/league_standings.json h2h_wins (the league's total, both legs)
  starters        NOT on disk: weekly_actuals keeps each player's points, not who started,
                  so "starters who did not play" is not measurable here and says so.

fantasy_sim.luck_ledger is pure (it imports only math); the engine is not imported.
"""
import json

from fantasy_sim.luck_ledger import MIN_WEEKS_FOR_INFERENCE, direction, ledger, two_sided_p

REGULAR_WEEKS = 14

# The five, in the ledger's order, with what each measures in plain words. Pre-registered:
# a sixth entry here is a new measure, which Decision 2 has not made (tests.test_webui_ninth_batch).
MEASURES = (
    ("schedule_luck", "Schedule luck", "head-to-head wins minus the wins your all-play rate earns", "wins"),
    ("opponent_luck", "Opponent luck", "points scored against you a game, minus the league's average", "points a game"),
    ("close_games", "Close games", "wins in head-to-head games decided by under 10 points, minus half of them", "wins"),
    ("dnp_luck", "Starters who did not play", "your starters scoring nothing a game, minus the league's average", "a game"),
    ("scoring_luck", "Scoring luck", "how far your weekly totals beat the projection, in spreads, minus the league's", "spreads"),
)


def _inputs(root):
    actuals = root.read_json("current/weekly_actuals.json", {}) or {}
    scores = {}
    for key, wk in actuals.items():
        if not str(key).startswith("week_") or not isinstance(wk, dict):
            continue
        try:
            n = int(str(key)[5:])
        except ValueError:
            continue
        row = {t: float(r["points_scored"]) for t, r in (wk.get("team_results") or {}).items()
               if isinstance(r, dict) and r.get("points_scored") is not None}
        if n <= REGULAR_WEEKS and row:
            scores[n] = row
    sched = root.read_json("current/league_schedule.json", []) or []
    pairs = {n: [tuple(p) for p in sched[n - 1] if len(p) == 2] for n in scores if 0 < n <= len(sched)}
    proj = {}
    try:
        with open(root.resolve_file("logs/predictions_2026.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                med, wk = r.get("median") or {}, r.get("week")
                if not med or wk is None or (wk in proj and not r.get("canonical")):
                    continue
                proj[wk] = {t: (float(v.get("expected_total") or 0.0), float(v.get("sd_total") or 0.0))
                            for t, v in med.items() if isinstance(v, dict)}
    except (FileNotFoundError, ValueError):
        pass
    standings = root.read_json("current/league_standings.json", {}) or {}
    banked = {t: int(float(s["h2h_wins"])) for t, s in standings.items() if isinstance(s, dict) and s.get("h2h_wins") is not None}
    return scores, pairs, (proj or None), banked


def report(root, team):
    scores, pairs, proj, banked = _inputs(root)
    n = len(scores)
    res = ledger(scores, pairs, team, starter_points=None, projections=proj, banked_wins=banked.get(team)) if n else {}
    early = n < MIN_WEEKS_FOR_INFERENCE
    withheld = bool(res.get("banked_disagreement"))
    rows = []
    for key, label, what, unit in MEASURES:
        m = res.get(key)
        row = {"key": key, "label": label, "what": what, "unit": unit, "metric": m, "why_none": None,
               "way": None, "z": None, "p": None, "word": None}
        if m is None:
            if key == "dnp_luck":
                row["why_none"] = "not measurable here: the weekly results on file keep each player's points, not who started"
            elif key in ("schedule_luck", "close_games") and withheld:
                row["why_none"] = "withheld: the league's record and today's box scores disagree about who won (see below)"
            elif key == "scoring_luck":
                row["why_none"] = "not measurable yet: no week has both a pre-game projection and a result"
            else:
                row["why_none"] = "not measurable yet"
        else:
            row["way"] = direction(key, m.get("delta"))
            if not early and m.get("z") is not None:
                row["z"] = m["z"]
                row["p"] = two_sided_p(m["z"])
                row["word"] = "significant" if row["p"] < 0.05 else ("suggestive" if row["p"] < 0.20 else "noise")
        rows.append(row)
    return {"team": team, "weeks": n, "early": early, "min_weeks": MIN_WEEKS_FOR_INFERENCE, "rows": rows,
            "disagreement": res.get("banked_disagreement")}
