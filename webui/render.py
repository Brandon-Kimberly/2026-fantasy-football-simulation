"""webui.render -- turn what the tools produce into something a person can read.

Two inputs, two pure functions, no Flask:

  console_blocks(text)  a tool's raw stdout -> a list of blocks. Engine chatter (the
                        logging mirror, the bracketed milestones) is folded into one
                        collapsible block; ALL-CAPS lines become headings; runs of
                        column-aligned lines become tables; the 'logged -> path' line
                        becomes a record link; everything else stays a preformatted
                        paragraph with its indentation intact.

  record_view(data)     a tool's JSON record -> {tool, title, subtitle, tiles, sections}.
                        Every decision tool writes one (docs/WEB_UI.md 2.4), and the record
                        is the readable artefact: a per-tool spec picks the headline
                        numbers and the tables; a generic walk renders any record the
                        specs do not know, so a new tool is never a blank page.

Labels here are for a person: 'P(0 pts)' not 'p_zero', 'Max P(win)' not 'p_max', a
dash where a margin has nothing to be over. Team names pass through untouched -- they
are pseudonyms on disk and the templates apply the overlay (`|real`) at render time.
Nothing here reads the filesystem.
"""
import re

# Column-0 lines that are the engine talking to itself, not the tool talking to the owner:
# logging's `LEVEL | message` mirror (ROSTER HOLES, VEGAS STALE, BANKED RECORD, DEPTH
# WATCHDOG ...), the engine's bracketed milestones, and the imputation notices. Indented
# lines of the same shape are a tool's own content (check_freshness quotes the sync's
# degraded list) and stay.
CHATTER_PREFIXES = ("[INFO]", "[PRE-FLIGHT", "[>>>]", "[SUCCESS]", "[EXPORT COMPLETE]", "[NOTE]",
                    "ERROR |", "WARNING |", "INFO |", "DEBUG |", "Imputed whitelisted")
RECORD_RE = re.compile(r"(?:logged|report|chart|written|recorded|digest|html)\s*->\s*(\S+)")
HEADING_RE = re.compile(r"^\s{0,2}(?:[A-Z][A-Z0-9/&'.]*)(?:\s+[A-Z0-9][A-Z0-9/&'.,-]*){0,7}(?:\s+--.*|:)?\s*$")
UNDERLINE_RE = re.compile(r"^\s*[=\-]{4,}\s*$")
SPLIT_RE = re.compile(r"\s{2,}")

# What a record's file prefix means, for lists of records and job titles.
TOOL_TITLES = {"lineup": "Optimal lineup", "matchup": "Matchup lineups", "waivers": "Waiver targets",
               "roster_grades": "Roster grades", "trade_targets": "Trade targets", "roster_calendar": "Roster calendar",
               "matchup_watch": "Matchup watch", "weekly_report": "Weekly digest", "gameday": "Gameday sheet",
               "compare": "Compare players", "move": "Evaluate move", "trade": "Evaluate trade",
               "draft_review": "Draft review", "season_retrospective": "Season retrospective",
               "optimize_lineup": "Optimal lineup", "matchup_lineup": "Matchup lineups", "waiver_targets": "Waiver targets",
               "find_trades": "Trade targets", "compare_players": "Compare players", "evaluate_trade": "Evaluate trade",
               "evaluate_move": "Evaluate move", "run_simulation": "Simulation run", "check_freshness": "Freshness check",
               "run_windows": "Run windows", "odds_history": "Odds history", "luck_ledger": "Luck ledger",
               "decision_scorecard": "Decision scorecard", "data_health": "Data health", "bid_review": "Bid review",
               "live_matchup": "Live matchup", "trade_leverage": "Trade leverage", "market_sweep": "Market sweep"}
CONSTRUCTION_LABELS = {"p_max": "Max P(win)", "max_mean": "Max mean", "safe": "Safe", "stack": "Stack"}


def tool_title(name):
    name = str(name or "")
    return TOOL_TITLES.get(name) or name.replace("_", " ").strip().capitalize()


def entry_title(entry):
    """A record list row's human title: the tool, plus 'A vs B' for a compare record."""
    name = entry.get("name") or ""
    base = tool_title(entry.get("tool"))
    if entry.get("tool") == "compare":
        m = re.match(r"compare_\d{8}T\d{6}Z_(.+)\.json$", name)
        if m:
            return base + " · " + m.group(1).replace("_vs_", " vs ").replace("_", " ")
    if entry.get("tool") == "weekly_report" and "run" in name:
        m = re.search(r"_(run\d_[a-z_]+?)_\d{8}T", name)
        if m:
            return base + " · " + m.group(1).replace("_", " ")
    return base


def pair_digests(entries):
    """A digest is written as .html and .md with one stem: show the html row once, with the
    markdown as its secondary link, instead of two rows that look like two reports."""
    by_stem = {}
    for e in entries:
        stem = e["name"].rsplit(".", 1)[0]
        by_stem.setdefault(stem, {})[e["ext"]] = e
    out = []
    for e in entries:
        stem = e["name"].rsplit(".", 1)[0]
        pair = by_stem.get(stem, {})
        if e["ext"] == "md" and "html" in pair:
            continue
        row = dict(e)
        if e["ext"] == "html" and "md" in pair:
            row["md_link"] = pair["md"]["link"]
        out.append(row)
    return out


# ----------------------------------------------------------------------------- numbers
def fnum(v, nd=1):
    try:
        if v is None:
            return "—"
        if isinstance(v, bool):
            return "yes" if v else "no"
        f = float(v)
        if f != f:
            return "—"
        if nd == 0:
            return f"{f:.0f}"
        return f"{f:.{nd}f}"
    except Exception:            # strings, and a Jinja Undefined whose __float__ raises
        return str(v) if isinstance(v, str) else "—"


def fpct(v, nd=1):
    """0.7624 -> 76.2%; 76.24 (already a percentage) -> 76.2%."""
    try:
        f = float(v)
    except Exception:
        return "—"
    if f != f:
        return "—"
    if -1.0 <= f <= 1.0:
        f *= 100.0
    return f"{f:.{nd}f}%"


def fsigned(v, nd=2):
    try:
        f = float(v)
    except Exception:
        return "—"
    if f != f:
        return "—"
    return f"{f:+.{nd}f}"


def tone(v):
    try:
        f = float(v)
    except Exception:
        return ""
    return "pos" if f > 0 else ("neg" if f < 0 else "")


def _join(v):
    """A value as a person would write it: lists comma-separated, dicts 'k: v', nested
    values recursed -- never a Python repr."""
    if isinstance(v, (list, tuple)):
        return ", ".join(_join(x) for x in v) if v else "—"
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_join(x)}" for k, x in v.items()) if v else "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return fnum(v, 2)
    if v is None or v == "":
        return "—"
    return str(v)


# --------------------------------------------------------------------- console blocks
def _is_chatter(line):
    return line.startswith(CHATTER_PREFIXES)


def _columns(line):
    return [c for c in SPLIT_RE.split(line.strip()) if c != ""]


def _table_run(lines, i):
    """Length of the column-aligned run starting at i (0 if fewer than 3 lines)."""
    counts = []
    j = i
    while j < len(lines):
        ln = lines[j]
        if not ln.strip() or _is_chatter(ln) or RECORD_RE.search(ln):
            break
        cols = _columns(ln)
        if len(cols) < 2 or len(ln) > 260:
            break
        counts.append(len(cols))
        j += 1
    n = j - i
    if n < 3:
        return 0
    if max(counts) - min(counts) > 1:
        return 0
    return n


def console_blocks(text):
    lines = (text or "").splitlines()
    blocks, chatter, para = [], [], []

    def flush_para():
        if para:
            blocks.append({"kind": "para", "text": "\n".join(para).rstrip()})
            para.clear()

    def flush_chatter():
        if chatter:
            blocks.append({"kind": "chatter", "lines": list(chatter)})
            chatter.clear()

    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if _is_chatter(ln):
            flush_para()
            chatter.append(ln)
            i += 1
            continue
        flush_chatter()
        if not ln.strip():
            flush_para()
            i += 1
            continue
        m = RECORD_RE.search(ln)
        if m:
            flush_para()
            blocks.append({"kind": "record", "path": m.group(1), "text": ln.strip(),
                           "name": m.group(1).replace("\\", "/").rsplit("/", 1)[-1]})
            i += 1
            continue
        if i + 1 < len(lines) and UNDERLINE_RE.match(lines[i + 1] or "") and ln.strip():
            flush_para()
            blocks.append({"kind": "heading", "text": ln.strip()})
            i += 2
            continue
        if HEADING_RE.match(ln) and any(c.isalpha() for c in ln):
            flush_para()
            blocks.append({"kind": "heading", "text": ln.strip()})
            i += 1
            continue
        n = _table_run(lines, i)
        if n:
            flush_para()
            rows = [_columns(x) for x in lines[i:i + n]]
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            blocks.append({"kind": "table", "rows": rows})
            i += n
            continue
        para.append(ln)
        i += 1
    flush_para()
    flush_chatter()
    return blocks


# ------------------------------------------------------------------------ record views
def col(key, label=None, kind="text", nd=1, link=None):
    """link='team' renders the cell as a link to that team on the League page."""
    return {"key": key, "label": label or key.replace("_", " "), "kind": kind, "nd": nd, "link": link}


def _cell(row, c):
    v = row.get(c["key"]) if isinstance(row, dict) else None
    k = c["kind"]
    if k == "num":
        return {"text": fnum(v, c["nd"]), "num": True, "tone": ""}
    if k == "pct":
        return {"text": fpct(v, c["nd"]), "num": True, "tone": ""}
    if k == "signed":
        return {"text": fsigned(v, c["nd"]), "num": True, "tone": tone(v)}
    if k == "bool":
        return {"text": "yes" if v else "no", "num": False, "tone": "pos" if v else ""}
    if k == "flag":
        return {"text": v or "", "num": False, "tone": "neg" if v else ""}
    return {"text": _join(v), "num": False, "tone": "", "link": c.get("link") if v else None, "raw": v if isinstance(v, str) else None}


def table(title, columns, rows, note=None, me_key=None, collapsed=False):
    rows = rows or []
    return {"kind": "table", "title": title, "note": note, "me_key": me_key, "collapsed": collapsed,
            "columns": columns,
            "rows": [{"cells": [_cell(r, c) for c in columns], "me": (r.get(me_key) if me_key and isinstance(r, dict) else None)}
                     for r in rows if isinstance(r, dict)]}


def kv(title, items, note=None):
    return {"kind": "kv", "title": title, "items": [(str(k).replace("_", " "), _join(v)) for k, v in items], "note": note}


def text(title, body, collapsed=True):
    """Reference prose (a tool's own method note): collapsed by default -- it is there
    when wanted and never a wall on the page."""
    return {"kind": "text", "title": title, "text": body or "", "collapsed": collapsed}


def tile(k, v, s="", tn=""):
    return {"k": k, "v": v, "s": s, "tone": tn}


# ---- per-tool specs ------------------------------------------------------------------
def _lineup(d):
    q = d.get("questionable_starters") or []
    starters = []
    for r in d.get("lineup") or []:
        r = dict(r)
        if not r.get("alternative"):
            r["margin"] = None            # nothing to be over: a dash, not the whole expectation
        starters.append(r)
    return {
        "title": "Optimal lineup", "subtitle": f"{d.get('team')} · week {d.get('week')}",
        "tiles": [tile("expected total", fnum(d.get("expected_total"), 1), "pre-game expectation of the 13 starters"),
                  tile("questionable starters", str(len(q)), "each with a named fallback" if q else "none", "neg" if q else ""),
                  tile("locked in place", str(d.get("pinned", 0)), "starters whose game has kicked off" if d.get("locks_active") else "no games have kicked off"),
                  tile("unfilled slots", str(len(d.get("unfilled") or [])), "no eligible player" if d.get("unfilled") else "every slot filled", "neg" if d.get("unfilled") else "")],
        "sections": [
            table("Starters", [col("slot"), col("name"), col("pos"), col("expected", kind="num"), col("p10", kind="num", nd=0),
                               col("p50", kind="num", nd=0), col("p90", kind="num", nd=0), col("p_zero", "P(0 pts)", "pct"),
                               col("margin", "margin over bench", "signed", 1), col("alternative", "best alternative"), col("flag", "status", "flag")],
                  starters),
            table("Questionable starters", [col("name"), col("slot"), col("expected", kind="num"), col("fallback"),
                                            col("fallback_expected", "fallback exp.", "num"), col("give_up", "cost of sitting", "num")], q)
            if q else None,
            table("Bench", [col("name"), col("pos"), col("expected", kind="num"), col("available", kind="bool"), col("reason"), col("flag", "status", "flag")],
                  d.get("bench")),
            text("How this was computed", d.get("note")),
        ]}


def _matchup(d):
    cons = d.get("constructions") or {}
    order = d.get("ranking_by_p_beat_opponent") or list(cons)
    rows = [dict(construction=CONSTRUCTION_LABELS.get(k, k), **{kk: vv for kk, vv in (cons.get(k) or {}).items() if kk != "lineup"}) for k in order if k in cons]
    best = cons.get(order[0]) if order and order[0] in cons else {}
    secs = [table("Constructions", [col("construction"), col("mean", kind="num"), col("sd", kind="num"), col("p_beat_opponent", "P(beat opponent)", "pct"),
                                    col("p_beat_median", "P(beat median)", "pct"), col("margin_mean", "margin", "signed", 1), col("margin_sd", "margin sd", "num")],
                  rows, note="ranked by P(beat opponent); the first row is the recommendation")]
    for i, k in enumerate(order):
        if k in cons and cons[k].get("lineup"):
            secs.append(table(f"Lineup — {CONSTRUCTION_LABELS.get(k, k)}" + (" (recommended)" if i == 0 else ""),
                              [col("slot"), col("name"), col("nfl_team", "NFL"), col("expected", kind="num"), col("sd", kind="num"), col("flag", "status", "flag")],
                              cons[k]["lineup"], collapsed=(i > 0)))
    secs.append(table("Opponent's lineup" + (" (assumed: his max-expectation lineup)" if d.get("opponent_lineup_assumed") else ""),
                      [col("slot"), col("name"), col("expected", kind="num")], d.get("opponent_lineup"), collapsed=True))
    secs.append(text("How this was computed", d.get("note")))
    return {"title": "Matchup lineups", "subtitle": f"{d.get('team')} vs {d.get('opponent')} · week {d.get('week')}",
            "tiles": [tile("recommended", CONSTRUCTION_LABELS.get(order[0], order[0]) if order else "—", "by P(beat opponent)"),
                      tile("P(beat opponent)", fpct(best.get("p_beat_opponent")), f"± {fpct(best.get('se'), 1)}" if best.get("se") is not None else ""),
                      tile("P(beat median)", fpct(best.get("p_beat_median")), ""),
                      tile("favoured on means", "yes" if d.get("favoured_by_max_mean") else "no", "", "pos" if d.get("favoured_by_max_mean") else "neg")],
            "sections": secs}


def _waivers(d):
    rows = []
    for t in d.get("targets") or []:
        r = dict(t)
        wk, bid = t.get("week") or {}, t.get("bid") or {}
        v2 = bid.get("v2") or {}
        r.update(week_mean=wk.get("mean"), week_p90=wk.get("p90"), week_p_zero=wk.get("p_zero"),
                 bid_point=v2.get("point"), bid_band=(f"{v2.get('low')}–{v2.get('high')}" if v2 else None), bid_v1=bid.get("suggested"))
        rows.append(r)
    holes = d.get("holes") or []
    return {"title": "Waiver targets", "subtitle": f"{d.get('team')} · week {d.get('week')}",
            "tiles": [tile("FAAB remaining", fnum(d.get("remaining_faab"), 0), f"league average {fnum(d.get('league_avg_faab'), 0)}"),
                      tile("holes this week", str(len(holes)), _join(holes) if holes else "every slot fillable", "neg" if holes else ""),
                      tile("holes next week", str(len(d.get("holes_next_week") or [])), _join(d.get("holes_next_week")) if d.get("holes_next_week") else "none"),
                      tile("targets", str(len(rows)), "ranked by value over replacement")],
            "sections": [table("Targets", [col("name"), col("pos"), col("team", "NFL"), col("tier", kind="num", nd=0), col("mean", "season mean", "num"),
                                           col("vorp", "VORP", "signed", 1), col("fills"), col("incumbent"), col("week_mean", "this week", "num"),
                                           col("week_p90", "p90", "num", 0), col("week_p_zero", "P(0 pts)", "pct"), col("bid_point", "bid", "num", 0),
                                           col("bid_band", "band"), col("bid_v1", "old bid", "num", 0), col("bye", kind="num", nd=0), col("injury_status", "status", "flag")],
                               rows, note="bid = the margin over the fallback priced against actual competition; old bid = the earlier heuristic. Neither is validated (F61)"),
                         text("Caveat", d.get("caveat"))]}


def _roster_grades(d):
    teams = (d.get("league") or {}).get("teams") or []
    return {"title": "Roster grades", "subtitle": f"week {(d.get('league') or {}).get('week')}",
            "tiles": [tile("teams", str(len(teams)), "ranked by lineup VORP")],
            "sections": [table("League", [col("rank", kind="num", nd=0), col("team", link="team"), col("lineup_vorp", "lineup VORP", "num"), col("depth_vorp", "depth VORP", "num"),
                                          col("optimal_score", "optimal score", "num"), col("tier1_starters", "tier-1 starters", "num", 0),
                                          col("starters_below_replacement", "below replacement", "num", 0), col("holes", kind="num", nd=0)],
                               teams, me_key="team")]}


def _find_trades(d):
    return {"title": "Trade targets", "subtitle": f"{d.get('team')} · week {d.get('week')}",
            "tiles": [tile("buy ideas", str(len(d.get("buy") or [])), "buried players who would start for me"),
                      tile("sell ideas", str(len(d.get("sell") or [])), "my surplus with a buyer"),
                      tile("excluded (pending)", str(len(d.get("excluded_pending") or [])), "players in a pending trade (T3)")],
            "sections": [table("Buy", [col("with", link="team"), col("target"), col("fills_my_slot", "fills"), col("i_give"), col("i_get"), col("my_gain", kind="signed"),
                                       col("their_gain", kind="signed"), col("worth_proposing", "worth it", "bool"), col("acceptable", kind="bool"),
                                       col("their_playoff_pct", "their playoff %", "num"), col("willingness", kind="num", nd=2), col("simulated", kind="bool")],
                               d.get("buy"), me_key="with"),
                         table("Sell", [col("buyer", link="team"), col("they_want"), col("they_give"), col("my_gain", kind="signed"), col("their_gain", kind="signed"),
                                        col("worth_proposing", "worth it", "bool"), col("acceptable", kind="bool"), col("their_playoff_pct", "their playoff %", "num"),
                                        col("willingness", kind="num", nd=2), col("simulated", kind="bool")], d.get("sell")),
                         text("Who counts as a seller", d.get("contention_note")), text("How this was computed", d.get("note"))]}


def _compare(d):
    a, b = d.get("a") or {}, d.get("b") or {}
    an, bn = d.get("a_name"), d.get("b_name")
    rows = [dict(player=an, **a), dict(player=bn, **b)]
    quick = (d.get("path") == "light")
    return {"title": "Start A or B", "subtitle": f"{an} vs {bn} · week {d.get('week')}",
            "tiles": [tile(f"{an} wins", fpct(d.get("p_a")), f"± {fpct(d.get('se_p'))}" if d.get("se_p") else "",
                           "pos" if (d.get("p_a") or 0) > (d.get("p_b") or 0) else "neg"),
                      tile(f"{bn} wins", fpct(d.get("p_b")), f"tie {fpct(d.get('p_tie'))}"),
                      tile("mean difference", fsigned(d.get("mean_diff")), f"{an} minus {bn}", tone(d.get("mean_diff"))),
                      tile("draws", f"{int(d.get('n') or 0):,}", "quick mode: baseline parameters, no simulation" if quick else "joint simulation")],
            "sections": [table("Distributions", [col("player"), col("mean", kind="num"), col("p10", kind="num"), col("p25", kind="num"), col("p50", kind="num"),
                                                 col("p75", kind="num"), col("p90", kind="num"), col("p_zero", "P(0 pts)", "pct")], rows),
                         text("Caveat", d.get("note"))]}


def _paired(d, what):
    teams = d.get("teams") or {}
    rows = []
    for name, t in teams.items():
        side = str(t.get("side") or "").casefold()
        rows.append({"team": name, "side": {"a": "A", "b": "B", "mover": "mover"}.get(side, side or "bystander"),
                     "_side": side,
                     "champ_with": (t.get("champ_pct") or {}).get("with"), "champ_without": (t.get("champ_pct") or {}).get("without"),
                     "champ_delta": (t.get("champ_pct") or {}).get("delta"), "champ_se": (t.get("champ_pct") or {}).get("se"),
                     "playoff_with": (t.get("playoff_pct") or {}).get("with"), "playoff_without": (t.get("playoff_pct") or {}).get("without"),
                     "playoff_delta": (t.get("playoff_pct") or {}).get("delta"), "playoff_se": (t.get("playoff_pct") or {}).get("se"),
                     "wins_delta": (t.get("expected_wins") or {}).get("delta")})
    order = {"a": 0, "mover": 0, "b": 1, "bystander": 2}
    rows.sort(key=lambda r: (order.get(r["_side"], 3), -(abs(r["playoff_delta"] or 0))))
    principals = [r for r in rows if order.get(r["_side"], 3) < 2]
    tiles = []
    for r in principals[:2]:
        tiles.append(tile(f"{r['team']} · playoff", fsigned(r["playoff_delta"]) + " pts",
                          f"± {fnum(r['playoff_se'], 2)} · champion {fsigned(r['champ_delta'])}", tone(r["playoff_delta"])))
    tiles.append(tile("simulations", f"{int(d.get('n_sims') or 0):,}", f"{d.get('batches')} paired batches, same seeds"))
    spec = d.get(what) or {}
    sub = (f"{spec.get('team_a')} gives {_join(spec.get('a_gives'))} · {spec.get('team_b')} gives {_join(spec.get('b_gives'))}"
           if what == "trade" else f"{spec.get('team')}: add {_join(spec.get('adds'))} · drop {_join(spec.get('drops'))}")
    return {"title": "Trade evaluation" if what == "trade" else "Move evaluation", "subtitle": sub,
            "tiles": tiles,
            "sections": [table("Every team, with minus without", [col("team", link="team"), col("side"), col("playoff_delta", "playoff Δ", "signed", 2), col("playoff_se", "± se", "num", 2),
                                                                   col("playoff_with", "with", "num"), col("playoff_without", "without", "num"),
                                                                   col("champ_delta", "champ Δ", "signed", 2), col("champ_se", "± se", "num", 2),
                                                                   col("wins_delta", "exp. wins Δ", "signed", 2)], rows, me_key="team",
                               note="the two sides first, then every bystander; a paired delta's SE is the batch-to-batch spread"),
                         kv("The " + what, list(spec.items())), text("How this was computed", d.get("note"))]}


def _calendar(d):
    cal, crunch = d.get("calendar") or {}, d.get("crunch") or {}
    holes = cal.get("holes") or {}
    rows = [dict(r, holes=", ".join(holes.get(str(r.get("week")), []))) for r in cal.get("rows") or []]
    wks = cal.get("weeks") or []
    return {"title": "Roster calendar", "subtitle": f"{cal.get('team')} · weeks {wks[0] if wks else ''}–{wks[-1] if wks else ''}",
            "tiles": [tile("holes ahead", str(len(holes)), "; ".join(f"week {w}: {_join(v)}" for w, v in holes.items()) if holes else "every remaining week fillable", "neg" if holes else ""),
                      tile("active", f"{crunch.get('active_now')} of {crunch.get('limit')}", f"{len(crunch.get('ir') or [])} on IR"),
                      tile("must drop on return", str(sum(int(r.get('must_drop') or 0) for r in crunch.get('returns') or [])), _join([r.get('name') for r in crunch.get('returns') or []]))],
            "sections": [table("Week by week", [col("week", kind="num", nd=0), col("n_startable", "startable", "num", 0), col("holes", kind="flag"), col("on_bye"),
                                                col("on_bye_ir", "on bye (IR)"), col("bye_starters"), col("covers"), col("unfilled", kind="flag")], rows),
                         table("Load-bearing bench (covers a bye — do not drop)", [col("name"), col("covers_weeks", "covers weeks")], crunch.get("load_bearing")),
                         table("Droppable bench (covers no bye; not a value ranking)", [col("name"), col("pos"), col("mean", kind="num")], crunch.get("droppable")),
                         table("IR returns", [col("name"), col("active_on_return", "active on return", "num", 0), col("over_limit", kind="bool"), col("must_drop", kind="num", nd=0)], crunch.get("returns")),
                         text("Note", crunch.get("note"))]}


def _watch(d):
    games = d.get("games") or []
    rows = [dict(g, mine_names=_join([p.get("name") for p in g.get("mine") or []]), theirs_names=_join([p.get("name") for p in g.get("theirs") or []])) for g in games]
    ls = d.get("their_losing_script") or {}
    return {"title": "What to watch", "subtitle": f"{d.get('team')} vs {d.get('opponent')} · week {d.get('week')}",
            "tiles": [tile("games with a stake", str(len(games)), f"{len(d.get('shared_games') or [])} shared"),
                      tile("mine, total", fnum(sum(g.get("mine_sum") or 0 for g in games), 1), "pre-game expectation across games"),
                      tile("theirs, total", fnum(sum(g.get("theirs_sum") or 0 for g in games), 1), ""),
                      tile("their losing script", ls.get("game") or "—", f"{fpct(ls.get('share'))} of their total" if ls else "")],
            "sections": [table("Games", [col("game"), col("mine_names", "mine"), col("mine_sum", "mine", "num"), col("theirs_names", "theirs"), col("theirs_sum", "theirs", "num"),
                                         col("game_total", "Vegas total", "num"), col("shared"), col("wind_mph", "wind", "num", 0), col("precip_prob", "precip %", "num", 0)], rows),
                         table("Designations on both rosters", [col("name"), col("side"), col("nfl_team", "NFL"), col("expected", kind="num"), col("flag", "status", "flag")], d.get("designations")),
                         table("Stacks", [col("game"), col("side"), col("team", link="team"), col("n", kind="num", nd=0), col("sum", kind="num"), col("players")], d.get("stacks")),
                         text("Weather", d.get("weather_note"))]}


SPECS = {"optimize_lineup": _lineup, "matchup_lineup": _matchup, "waiver_targets": _waivers, "roster_grades": _roster_grades,
         "find_trades": _find_trades, "compare_players": _compare, "evaluate_trade": lambda d: _paired(d, "trade"),
         "evaluate_move": lambda d: _paired(d, "move"), "roster_calendar": _calendar, "matchup_watch": _watch}


def _generic(d):
    scalars, sections = [], []
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            scalars.append((k, v))
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            keys = []
            for x in v:
                for kk, vv in x.items():
                    if kk not in keys and not isinstance(vv, (dict, list)):
                        keys.append(kk)
            sections.append(table(k.replace("_", " "), [col(kk, kind="num" if all(isinstance(x.get(kk), (int, float)) and not isinstance(x.get(kk), bool) for x in v if x.get(kk) is not None) else "text") for kk in keys[:14]], v))
        elif isinstance(v, dict) and v and all(isinstance(x, dict) for x in v.values()):
            rows = [dict(key=kk, **{a: b for a, b in x.items() if not isinstance(b, (dict, list))}) for kk, x in v.items()]
            keys = ["key"] + [kk for kk in rows[0] if kk != "key"] if rows else []
            sections.append(table(k.replace("_", " "), [col(kk, kind="num" if kk != "key" and all(isinstance(r.get(kk), (int, float)) and not isinstance(r.get(kk), bool) for r in rows if r.get(kk) is not None) else "text") for kk in keys[:14]], rows))
        elif isinstance(v, dict):
            sections.append(kv(k.replace("_", " "), list(v.items())))
        elif isinstance(v, list):
            sections.append(text(k.replace("_", " "), _join(v), collapsed=False))
    return {"title": tool_title(d.get("tool") or "Record"), "subtitle": "", "tiles": [],
            "sections": [kv("Summary", scalars)] + sections}


def record_view(data):
    if not isinstance(data, dict):
        return None
    spec = SPECS.get(str(data.get("tool") or ""))
    try:
        view = spec(data) if spec else _generic(data)
    except Exception:            # a record whose shape drifted: fall back rather than 500
        view = _generic(data)
    view["tool"] = data.get("tool")
    view["stamp"] = data.get("timestamp_utc")
    view["sections"] = [s for s in view.get("sections") or [] if s and (s.get("kind") != "table" or s.get("rows")) and (s.get("kind") != "text" or s.get("text"))]
    return view


# ----------------------------------------------------------------------------- progress
# What a running job has reached, read off the markers the engine and the tools actually
# print (fantasy_sim/simulation.py's four milestones; each sub-tool's 'logged ->' line; the
# report's 'digest ->'). Honest by construction: a stage is reached only when its marker has
# appeared, and the only estimate on the page is the typical duration of past runs.
ENGINE_STAGES = (("validating projections", ("[PRE-FLIGHT SUCCESS]",)),
                 ("simulating", ("[>>>] EXECUTING",)),
                 ("rendering charts", ("[SUCCESS] Markov",)),
                 ("exports written", ("[EXPORT COMPLETE]",)))
REPORT_STAGES = ENGINE_STAGES + (("roster grades", ("roster_grades_",)), ("lineup", ("lineup_",)),
                                 ("matchup", ("matchup_",)), ("waivers", ("waivers_",)),
                                 ("digest written", ("digest ->",)))
TOOL_STAGES = (("validating projections", ("[PRE-FLIGHT SUCCESS]",)),
               ("computing", ("[PRE-FLIGHT SUCCESS]",)),
               ("record written", (" -> ",)))


def progress(text, tool):
    """{stages, reached, stage, last}: the ordered stage names, how many have been reached,
    the current one ('starting' before any), and the last line the tool itself printed."""
    text = text or ""
    stages = REPORT_STAGES if tool == "weekly_report" else (ENGINE_STAGES if tool == "run_simulation" else TOOL_STAGES)
    reached = 0
    for i, (_name, markers) in enumerate(stages):
        if any(m in text for m in markers):
            reached = i + 1
    last = ""
    for ln in reversed(text.splitlines()):
        s = ln.strip()
        if s and not _is_chatter(ln):
            last = s[:160]
            break
    return {"stages": [n for n, _m in stages], "reached": reached,
            "stage": stages[reached - 1][0] if reached else "starting", "last": last}


# --------------------------------------------------------------------------- humanising
def ago(hours):
    """37.7 -> '38 h ago'; 50 -> '2 d ago'; 0.4 -> '24 min ago'."""
    try:
        h = float(hours)
    except Exception:
        return "—"
    if h < 1:
        return f"{max(1, int(round(h * 60)))} min ago"
    if h < 48:
        return f"{int(round(h))} h ago"
    return f"{int(round(h / 24))} d ago"


def duration(seconds):
    """1 -> '1 s'; 95 -> '1 m 35 s'; 3700 -> '1 h 2 m'."""
    try:
        s = max(0, int(float(seconds)))
    except Exception:
        return "—"
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} m {s % 60} s" if s % 60 else f"{s // 60} m"
    return f"{s // 3600} h {(s % 3600) // 60} m"


def when(iso):
    """'2026-09-27T05:59:45Z' -> 'Sep 27 05:59Z'."""
    import datetime as _dt
    try:
        return _dt.datetime.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ").strftime("%b %d %H:%MZ")
    except Exception:
        try:
            return _dt.datetime.strptime(str(iso), "%Y%m%dT%H%M%SZ").strftime("%b %d %H:%MZ")
        except Exception:
            return str(iso or "—")


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").casefold()).strip("-")


# What each season log under data/logs/ is, for the Logs page: (title, one line). Keyed by
# the stem before any _<season> or _<date> suffix. Unknown files fall back to their stem.
LOG_TITLES = {
    "predictions": ("Predictions", "every week's forecast as it was made, canonical and not, for scoring later"),
    "decision_log": ("Decision log", "adds, drops, claims and trades this season, with the reasoning at the time"),
    "bid_ledger": ("Bid ledger", "what the model suggested, what was bid, what it cost"),
    "designations": ("Injury designations", "the Saturday designations as captured, week by week"),
    "faab_adjustments": ("FAAB adjustments", "manager aggression updates learned from this season's claims"),
    "first_recorded_scores": ("First recorded scores", "each week's scores as first seen, before any stat correction"),
    "points_backtest": ("Points backtest", "the projection gate's bias, z and coverage, logged per commit"),
    "projection_log": ("Projection log", "the projections in force each week, for the January calibration"),
    "streamer_levels": ("Streamer levels", "replacement-level means measured from the free-agent pool"),
    "sync_provenance": ("Sync provenance", "which source produced which file, every sync"),
    "draft": ("Draft", "the draft as it happened, pick by pick"),
    "season": ("Season archive", "a completed season's schedule, scores and rosters"),
    "free_add_study": ("Free-add study", "what free-agent churn was worth, measured"),
    "weekly_actuals": ("Weekly actuals", "realised weekly scores under the current IDP scale"),
}


def log_title(name):
    """'predictions_2026.jsonl' -> ('Predictions', '...'); an unknown file -> (its stem, '')."""
    stem = str(name or "").rsplit(".", 1)[0]
    for key in sorted(LOG_TITLES, key=len, reverse=True):
        if stem == key or stem.startswith(key + "_"):
            title, desc = LOG_TITLES[key]
            tail = stem[len(key):].strip("_").replace("_", " ")
            return (f"{title} {tail}".strip(), desc)
    return (stem.replace("_", " "), "")
