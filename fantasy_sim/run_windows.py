"""
Canonical-run window computation -- the pure core of scripts.run_windows, a scheduling
ASSISTANT rather than an unattended runner. R1 (AUDIT_PLAN.md) makes fully automated engine
runs unsafe on this machine: a scheduled run cannot apply the void-and-re-run judgment, and
it could fire concurrently with an ad-hoc run -- exactly the multi-process load condition
that produces silent corruption. The canonical records are what most needs to be
trustworthy, so they are produced by a human inside a window this module computes.

Three windows per fantasy week (all boundaries America/Los_Angeles, DST-correct):
  run1_pre_kickoff  cycle start (Wednesday 00:00 PT after the previous week's last game;
                    week 1: the Wednesday at least 3 days before its first kickoff) until
                    the week's EARLIEST kickoff -- taken from real game times, so
                    Wednesday/Thursday/Friday openers (week 1, Thanksgiving, Christmas)
                    shift the deadline automatically.
  run2_sunday       Sunday 00:00 -> 10:00 PT. An earlier real Sunday kickoff (London) is
                    FLAGGED but does not move the deadline: the 10:00 rule is the owner's.
  run3_tuesday      Tuesday 00:00 -> Wednesday 00:00 PT (the waiver clear).

A window is COVERED by a canonical weekly digest whose stamp falls inside it, OPEN while
now is inside it uncovered, UPCOMING before it starts, MISSED after an uncovered deadline.
Canonical digests matching no window are returned in outside_windows rather than silently
counted -- a run made outside every window covered nothing.

POST-RMA AUTOMATION (designed for, not built): once Arm D passes 12/12 on the replacement
CPU, an unattended runner is a small additive consumer of this function's output -- fire
when (an OPEN window is uncovered) and (freshness is OK) and (a single-instance lock is
held, answering the concurrent-load hazard). Nothing here changes.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
RUN2_DEADLINE_LOCAL = time(10, 0)   # Sunday 10:00 PT -- the owner's rule, not a kickoff


DIGEST_STAMP_RE = r"(\d{8}T\d{6}Z)"


def parse_canonical_digest(name, week):
    """The stamp of a canonical weekly digest .md for `week`, accepting both name shapes --
    plain (weekly_report_week1_<stamp>.md) and window-infixed
    (weekly_report_week1_run2_sunday_<stamp>.md). _FAILED digests and other files parse as
    None: a failed run covers nothing."""
    import re
    from datetime import datetime, timezone
    m = re.match(rf"weekly_report_week{week}_(?:[a-z0-9_]+_)?{DIGEST_STAMP_RE}\.md$", name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def load_kickoffs():
    """{week: [aware UTC kickoffs]} from the synced schedule's _meta.kickoffs, with a live
    ESPN fallback (the same endpoint sync uses) until a post-migration sync stores them.
    Returns (kickoffs, source_description). Shared by scripts.run_windows and the
    orchestrator's canonical-window naming."""
    import os
    from datetime import datetime, timezone
    from fantasy_sim.storage import NFL_SCHEDULE_FILE, load_json

    def parse(t):
        return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc)

    meta = {}
    if os.path.exists(NFL_SCHEDULE_FILE):
        meta = load_json(NFL_SCHEDULE_FILE).get("_meta", {}).get("kickoffs") or {}
    if meta:
        return {int(w): [parse(t) for t in ts] for w, ts in meta.items() if ts}, "synced schedule"
    import requests
    out = {}
    for wk in range(1, 19):
        try:
            r = requests.get("http://site.api.espn.com/apis/site/v2/sports/football/nfl/"
                             f"scoreboard?week={wk}&seasontype=2", timeout=5)
            dates = [e["date"] for e in (r.json().get("events") or []) if e.get("date")]
            if dates:
                out[wk] = [parse(t) for t in dates]
        except Exception:
            continue
    return out, "live ESPN fetch (run a sync to persist kickoffs)"


def _pt(dt):
    return dt.astimezone(PT)


def _midnight_pt(d):
    return datetime(d.year, d.month, d.day, tzinfo=PT)


def _wednesday_on_or_before(d):
    return d - timedelta(days=(d.weekday() - 2) % 7)


def _cycle_start(week, kickoffs_by_week):
    """Wednesday 00:00 PT after the previous week's last game; without a previous week
    (week 1, or missing data), the Wednesday at least 3 days before this week's first
    kickoff -- the preseason lead-in has no waiver-clear boundary to anchor to."""
    prev = kickoffs_by_week.get(week - 1)
    if prev:
        last = _pt(max(prev))
        d = _wednesday_on_or_before(last.date()) + timedelta(days=7)
        return _midnight_pt(d)
    first = _pt(min(kickoffs_by_week[week]))
    d = _wednesday_on_or_before(first.date())
    while _midnight_pt(d) + timedelta(days=3) > first:
        d -= timedelta(days=7)
    return _midnight_pt(d)


def _windows_for(week, kickoffs_by_week):
    kicks = sorted(kickoffs_by_week[week])
    first = _pt(kicks[0])
    start = _cycle_start(week, kickoffs_by_week)
    sunday = first.date() + timedelta(days=(6 - first.weekday()) % 7)
    sun0 = _midnight_pt(sunday)
    windows = [
        {"name": "run1_pre_kickoff", "start": start, "deadline": first},
        {"name": "run2_sunday", "start": sun0,
         "deadline": datetime.combine(sunday, RUN2_DEADLINE_LOCAL, tzinfo=PT)},
        {"name": "run3_tuesday", "start": _midnight_pt(sunday + timedelta(days=2)),
         "deadline": _midnight_pt(sunday + timedelta(days=3))},
    ]
    return windows, kicks, sunday


def compute_windows(now_utc, kickoffs_by_week, canonical_stamps, state_week=None):
    """The window report. kickoffs_by_week: {int week: [aware UTC datetimes]};
    canonical_stamps: [(marker name, aware UTC datetime)] -- the canonical weekly digests
    already on disk for whatever week ends up targeted; state_week: Sleeper's current_week
    if known (drives the Tuesday week-roll flag). Pure: no I/O, fully testable."""
    weeks = sorted(w for w in kickoffs_by_week if kickoffs_by_week[w])
    target = None
    for w in weeks:
        windows, _kicks, _sun = _windows_for(w, kickoffs_by_week)
        if now_utc < windows[-1]["deadline"]:
            target = w
            break
    if target is None:
        return {"target_week": None, "windows": [], "outside_windows": [], "flags":
                ["no remaining run windows in the supplied schedule"]}

    windows, kicks, sunday = _windows_for(target, kickoffs_by_week)
    claimed = set()
    for win in windows:
        covering = [n for n, dt in canonical_stamps if win["start"] <= dt < win["deadline"]]
        win["covered_by"] = covering[-1] if covering else None
        claimed.update(covering)
        if covering:
            win["status"] = "COVERED"
        elif now_utc >= win["deadline"]:
            win["status"] = "MISSED"
        elif now_utc >= win["start"]:
            win["status"] = "OPEN"
        else:
            win["status"] = "UPCOMING"

    flags = []
    sunday_kicks = [k for k in kicks if _pt(k).date() == sunday]
    early = [k for k in sunday_kicks
             if _pt(k).time() < RUN2_DEADLINE_LOCAL]
    if early:
        flags.append(f"early Sunday kickoff at {_pt(min(early)).strftime('%H:%M')} PT "
                     f"(London window?) -- before the 10:00 run-2 deadline; the deadline "
                     f"stays 10:00 by rule, but lineups lock earlier for those players")
    run3 = windows[2]
    if state_week is not None and run3["start"] <= now_utc < run3["deadline"]             and state_week == target:
        flags.append(f"week-roll: Sleeper still reports current_week={state_week}; it "
                     f"typically rolls Wednesday, so a sync now prices next week's waivers "
                     f"with this week's state -- check again before trusting waiver targets")

    return {"target_week": target,
            "cycle": (windows[0]["start"], windows[2]["deadline"]),
            "windows": windows,
            "outside_windows": [n for n, _dt in canonical_stamps if n not in claimed],
            "flags": flags}
