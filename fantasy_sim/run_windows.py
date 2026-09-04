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


RELEASE_MILESTONES = (
    (5, 6, "week 5-6: F25's quoted-vs-realized calibration is first measurable"),
    (11, 12, "week 11: trade deadline"),
    (15, 16, "week 15: playoffs"),
    (17, 99, "season end: F7/F8/F18/F19 unblock together"),
)


def release_advice(latest_tag, goldens_changed_since_tag, week):
    """Release-policy reminders (CLAUDE.md), pure and small: fires in the status tool the
    owner already reads, never as a commit-time gate -- tagging is a release-time act and
    a suite failure between an intended golden regeneration and its tag would only teach
    people to ignore the guard. Three signals:
      - no LOCAL tag: say 'git fetch --tags' rather than 'untagged' (GitHub's release UI
        tags server-side; the clone is blind until a fetch -- the exact state this shipped in);
      - goldens regenerated since the latest tag: an intended behaviour change is riding
        untagged -> MAJOR pending;
      - a season milestone week has arrived (two-week window, then quiet -- no nagging).
    week None means compute_windows found no remaining windows: the season is over."""
    msgs = []
    if latest_tag is None:
        return ["no local tags; if a release exists on GitHub, run `git fetch --tags` "
                "(server-side tags are invisible to this clone until then)"]
    if goldens_changed_since_tag:
        msgs.append(f"MAJOR tag pending: golden master regenerated since {latest_tag} -- "
                    f"an intended behaviour change is riding untagged (release policy, CLAUDE.md)")
    if week is None:
        msgs.append("season end: F7/F8/F18/F19 unblock together -- tag the season-end "
                    "snapshot per the release policy")
        return msgs
    for lo, hi, label in RELEASE_MILESTONES:
        if lo <= week <= hi:
            msgs.append(f"milestone reached ({label}) -- cut at least a PATCH tag so the "
                        f"checkpoint is addressable (release policy, CLAUDE.md)")
    return msgs


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


# ----------------------------------------------------------- Actions watcher (tier 1)
def stamps_from_predictions_rows(rows, week):
    """Pure: canonical predictions-log rows -> compute_windows stamps (2026-09-04).
    A GitHub Actions runner has no data/decisions (untracked), so coverage there comes
    from the COMMITTED predictions log: weekly_report's logs_push step pushes at
    canonical time, so a canonical row's logged_at inside a window means that window
    was covered by a durable record -- which is exactly the thing worth verifying.
    Malformed rows are skipped, never fatal: this feeds a reminder, not a gate."""
    out = []
    for r in rows or []:
        if r.get("record_type") != "week_predictions" or not r.get("canonical"):
            continue
        try:
            if int(r.get("week", -1)) != int(week):
                continue
            dt = datetime.strptime(r["logged_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=ZoneInfo("UTC"))
        except (KeyError, TypeError, ValueError):
            continue
        out.append((f"predictions@{r['logged_at']}", dt))
    return out


def watch_verdict(result, now_utc, horizon_hours=24.0):
    """Pure: which windows need a human NOW. actionable = OPEN, uncovered, deadline
    inside the horizon; missed = past deadline, uncovered. Quiet otherwise -- a
    reminder that fires three times a week on nothing trains itself to be ignored."""
    actionable, missed = [], []
    for w in result.get("windows") or []:
        if w.get("covered_by"):
            continue
        if w.get("status") == "OPEN":
            hours = (w["deadline"] - now_utc).total_seconds() / 3600.0
            if hours <= horizon_hours:
                actionable.append({"name": w["name"], "hours_left": round(hours, 1),
                                   "deadline": w["deadline"].strftime("%Y-%m-%dT%H:%M:%SZ")})
        elif w.get("status") == "MISSED":
            missed.append({"name": w["name"],
                           "deadline": w["deadline"].strftime("%Y-%m-%dT%H:%M:%SZ")})
    return {"target_week": result.get("target_week"),
            "actionable": actionable, "missed": missed}
