"""
F15 at-draft analysis (AUDIT_PLAN.md): for each pick in an ingested draft document
(data/logs/draft_{season}.json, sync.ingest_drafts), the drafted player's baseline mean,
VORP (mean minus replacement level at his position) and tier (positional_tiers.compute_tiers,
his standing in the WHOLE pool) versus the best available at that pick -- available meaning
not yet drafted, at a position the drafting roster could still fill under the position limits.

THE WHOLE ANALYSIS IS A PROXY, stated in the result itself (proxy_note), not just here:
today's player_baselines.json is the closest thing to draft-time value on disk (the F7
projection log starts 2026-08-29; the 2026 draft ran 2026-08-22), so every reach/value/steal
verdict is measured against a board the drafters never saw. The verdict labels reuse the tier
convention: a pick is a "steal" or a "reach" only when its VORP gap to the best alternative
exceeds TIER_Z combined standard errors (sqrt(se_drafted^2 + se_alt^2)) -- the same
statistical bar positional_tiers uses to split tiers -- and "value" otherwise. No new
threshold constant is introduced.

Position limits: Sleeper enforced them (settings.enforce_position_limits) but the picks API
does not return the configured caps, so derive_position_caps uses the tightest caps
consistent with the observed draft -- the per-team maximum drafted at each position. That is
a LOWER BOUND on the real caps: a team at the observed maximum may truly have been allowed
one more, so a position can drop off a team's board slightly too early. Positions never
drafted get no cap (an all-zero cap would empty the board, which is wrong).
"""
from datetime import datetime, timezone

from fantasy_sim.config import normalize_position
from fantasy_sim.positional_tiers import TIER_Z, compute_tiers


def derive_position_caps(draft):
    """{pos: per-team maximum drafted at that position in this draft} -- see module
    docstring for why this is a lower bound on Sleeper's enforced caps."""
    per_team = {}
    for p in draft.get("picks", []):
        pos = normalize_position(p.get("pos") or "FLEX")
        counts = per_team.setdefault(p.get("team"), {})
        counts[pos] = counts.get(pos, 0) + 1
    caps = {}
    for counts in per_team.values():
        for pos, n in counts.items():
            caps[pos] = max(caps.get(pos, 0), n)
    return caps


def review_draft(draft, baselines, replacement_levels, z=TIER_Z):
    """The at-draft board walk. Returns picks (each with drafted value, best available
    alternative, vorp_gap and a steal/value/reach/unresolved label), per-manager and
    per-round roll-ups, the unresolved list, the pick-1 sanity check from F15's acceptance
    criterion, and the proxy_note every renderer must show."""
    caps = derive_position_caps(draft)
    tier_by_name = {p["name"]: (p["tier"], p["rank"])
                    for players in compute_tiers(baselines).values() for p in players}
    by_pid = {str(e.get("player_id")): n for n, e in baselines.items()
              if isinstance(e, dict) and e.get("player_id") is not None}

    def vorp_of(name):
        e = baselines[name]
        return float(e.get("mean", 0.0)) - replacement_levels.get(
            normalize_position(e.get("pos") or "FLEX"), 4.0)

    pool = sorted((n for n, e in baselines.items() if isinstance(e, dict)),
                  key=lambda n: (-vorp_of(n), n))
    taken, counts, picks, unresolved = set(), {}, [], []

    for p in draft.get("picks", []):
        team, raw_pos = p.get("team"), normalize_position(p.get("pos") or "FLEX")
        name = by_pid.get(str(p.get("player_id"))) or (p["name"] if p.get("name") in baselines else None)
        t_counts = counts.setdefault(team, {})

        def fillable(pos):
            cap = caps.get(pos, 0)
            return cap <= 0 or t_counts.get(pos, 0) < cap

        board = [n for n in pool if n not in taken and n != name
                 and fillable(normalize_position(baselines[n].get("pos") or "FLEX"))]
        alt = board[0] if board else None
        best_alt = None
        if alt is not None:
            ae = baselines[alt]
            at, ar = tier_by_name.get(alt, (None, None))
            best_alt = {"name": alt, "pos": normalize_position(ae.get("pos") or "FLEX"),
                        "mean": float(ae.get("mean", 0.0)), "vorp": round(vorp_of(alt), 2),
                        "tier": at}
        row = {"pick_no": p.get("pick_no"), "round": p.get("round"), "team": team,
               "name": name or p.get("name"), "pos": raw_pos, "best_alt": best_alt,
               "mean": None, "vorp": None, "tier": None, "rank": None,
               "vorp_gap": None, "combined_se": None, "label": "unresolved"}
        if name is not None:
            e = baselines[name]
            tier, rank = tier_by_name.get(name, (None, None))
            row.update(mean=float(e.get("mean", 0.0)), vorp=round(vorp_of(name), 2),
                       tier=tier, rank=rank)
            if best_alt is not None:
                gap = vorp_of(name) - vorp_of(alt)
                se = (float(e.get("std_epistemic", 0.0)) ** 2
                      + float(baselines[alt].get("std_epistemic", 0.0)) ** 2) ** 0.5
                row.update(vorp_gap=round(gap, 2), combined_se=round(se, 2),
                           label="steal" if gap > z * se else ("reach" if gap < -z * se else "value"))
            else:
                row["label"] = "value"    # an empty board: nothing left to compare against
            taken.add(name)
        else:
            unresolved.append(p.get("name"))
        t_counts[raw_pos] = t_counts.get(raw_pos, 0) + 1
        picks.append(row)

    def rollup(rows):
        gaps = [p["vorp_gap"] for p in rows if p["vorp_gap"] is not None]
        return {"picks": len(rows),
                "steals": sum(p["label"] == "steal" for p in rows),
                "values": sum(p["label"] == "value" for p in rows),
                "reaches": sum(p["label"] == "reach" for p in rows),
                "mean_gap": round(sum(gaps) / len(gaps), 2) if gaps else None}

    managers = []
    for team in dict.fromkeys(p["team"] for p in picks):     # draft-order stable
        rows = [p for p in picks if p["team"] == team]
        m = {"team": team, **rollup(rows),
             "unresolved": sum(p["label"] == "unresolved" for p in rows),
             "total_vorp": round(sum(p["vorp"] for p in rows if p["vorp"] is not None), 1)}
        managers.append(m)
    managers.sort(key=lambda m: -(m["mean_gap"] if m["mean_gap"] is not None else -1e9))
    # The chart/report reference point: vorp_gap is bounded above by 0 BY CONSTRUCTION (a
    # pick cannot beat the best available), so absolute gaps all read negative. The league
    # median per-pick gap is the "typical pick" reference; rel_gap re-centres each manager
    # on it so better-than-median drafting is genuinely positive.
    all_gaps = sorted(p["vorp_gap"] for p in picks if p["vorp_gap"] is not None)
    league_median_gap = None
    if all_gaps:
        mid = len(all_gaps) // 2
        league_median_gap = round(all_gaps[mid] if len(all_gaps) % 2
                                  else (all_gaps[mid - 1] + all_gaps[mid]) / 2.0, 2)
    for m in managers:
        m["rel_gap"] = (round(m["mean_gap"] - league_median_gap, 2)
                        if m["mean_gap"] is not None and league_median_gap is not None else None)
    rounds = [{"round": rnd, **rollup([p for p in picks if p["round"] == rnd])}
              for rnd in dict.fromkeys(p["round"] for p in picks)]

    sanity = None
    if picks:
        p1 = picks[0]
        rank = (1 + pool.index(p1["name"])) if p1["name"] in baselines else None
        sanity = {"pick1": p1["name"], "pick1_board_rank_by_vorp": rank, "board_size": len(pool),
                  "note": "F15 acceptance check: pick 1 should sit at or near the top of the board"}

    started = draft.get("start_time")
    when, lag = "unknown date", None
    if started:
        t0 = datetime.fromtimestamp(started / 1000.0, tz=timezone.utc)
        when = t0.strftime("%Y-%m-%d")
        lag = (datetime.now(timezone.utc) - t0).days
    proxy_note = (f"AT-DRAFT VALUE IS A PROXY: the board is TODAY'S baseline pool, not the "
                  f"draft-day board -- the draft ran {when}, {lag if lag is not None else '?'} days "
                  f"before this snapshot. Every reach/value/steal verdict inherits that gap.")
    return {"season": draft.get("season"), "draft_id": draft.get("draft_id"),
            "proxy_note": proxy_note, "caps": caps,
            "caps_note": ("caps are the tightest limits consistent with the observed draft -- a "
                          "lower bound on Sleeper's enforced caps, which the picks API does not return"),
            "picks": picks, "unresolved": unresolved, "sanity": sanity,
            "league_median_gap": league_median_gap,
            "managers": managers, "rounds": rounds}


def render_draft_html(review, chart_html=""):
    """The HTML report, weekly-report pattern: the shared sortable-table renderer and CSS,
    the proxy caveat as the FIRST element on the page (the .banner block, before any table),
    per-manager and per-round roll-ups, steals/reaches call-outs, and the full pick table.
    chart_html, if given, is a prebuilt <figure> block from the caller (the script embeds
    its chart there)."""
    from html import escape
    from fantasy_sim.positional_tiers import _TABLE_JS
    from fantasy_sim.weekly_report import _REPORT_CSS, html_table

    r = review
    med = r.get("league_median_gap")
    med_txt = f"{med:+.2f}" if med is not None else "n/a"
    fmt = lambda v, spec="+.2f": (format(v, spec) if v is not None else "-")
    out = [f'<!doctype html><html><head><meta charset="utf-8">'
           f'<title>Draft review -- season {escape(str(r["season"]))}</title>'
           f'<style>{_REPORT_CSS}</style></head><body>',
           f'<div class="banner"><b>{escape(r["proxy_note"])}</b>'
           f'<p class="note">{escape(r["caps_note"])}. Verdict scale: a VORP gap of 0.0 is the '
           f'UNACHIEVABLE ceiling (taking the best available player at every single pick); the '
           f'league median pick sits at {med_txt}, and the manager chart and relGap column are '
           f'relative to that median, so positive means better-than-typical drafting.</p></div>',
           f'<h1>Draft review -- season {escape(str(r["season"]))}, draft {escape(str(r["draft_id"]))}</h1>']
    s_ = r.get("sanity") or {}
    out.append(f'<p class="note">Sanity ({escape(s_.get("note", ""))}): pick 1 '
               f'({escape(str(s_.get("pick1")))}) is #{s_.get("pick1_board_rank_by_vorp")} of '
               f'{s_.get("board_size")} on today\'s board by VORP.</p>')
    if r.get("unresolved"):
        out.append(f'<p class="note">Unresolved (no baseline today, listed per F15\'s acceptance '
                   f'criterion): {escape(", ".join(r["unresolved"]))}</p>')

    out.append(chart_html)
    out.append("<h2>Per manager</h2>")
    out.append(html_table(
        ["team", "picks", "steal", "value", "reach", "unresolved", "meanGap", "relGap", "totVORP"],
        [[m["team"], m["picks"], m["steals"], m["values"], m["reaches"], m["unresolved"],
          fmt(m["mean_gap"]), fmt(m["rel_gap"]), m["total_vorp"]] for m in r["managers"]]))
    out.append("<h2>Per round</h2>")
    out.append(html_table(
        ["round", "picks", "steal", "value", "reach", "meanGap"],
        [[d["round"], d["picks"], d["steals"], d["values"], d["reaches"], fmt(d["mean_gap"])]
         for d in r["rounds"]]))

    scored = [p_ for p_ in r["picks"] if p_["vorp_gap"] is not None]
    for title, rows in (("Biggest steals", sorted(scored, key=lambda p_: -p_["vorp_gap"])[:5]),
                        ("Biggest reaches", sorted(scored, key=lambda p_: p_["vorp_gap"])[:5])):
        out.append(f"<h2>{title}</h2>"
                   f'<p class="note">Late-round verdicts are the most proxy-polluted: today\'s '
                   f'board rates post-draft breakouts highly, so the "best alternative" there is '
                   f'largely hindsight.</p>')
        out.append(html_table(
            ["pick", "round", "team", "player", "pos", "gap", "best alternative"],
            [[p_["pick_no"], p_["round"], p_["team"], p_["name"], p_["pos"], fmt(p_["vorp_gap"]),
              p_["best_alt"]["name"] if p_["best_alt"] else "-"] for p_ in rows]))

    out.append("<h2>All picks</h2>")
    out.append(html_table(
        ["pick", "round", "team", "player", "pos", "mean", "VORP", "tier", "verdict", "gap",
         "best alternative", "alt VORP"],
        [[p_["pick_no"], p_["round"], p_["team"], p_["name"], p_["pos"],
          fmt(p_["mean"], ".1f"), fmt(p_["vorp"]), p_["tier"] if p_["tier"] is not None else "-",
          p_["label"], fmt(p_["vorp_gap"]),
          p_["best_alt"]["name"] if p_["best_alt"] else "-",
          fmt(p_["best_alt"]["vorp"]) if p_["best_alt"] else "-"] for p_ in r["picks"]]))
    out.append(f"<script>{_TABLE_JS}</script></body></html>")
    return "".join(out)
