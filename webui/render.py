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
# One symbol per tool (the sprite in base.html), keyed by tool name and by record prefix.
TOOL_ICONS = {"optimize_lineup": "lineup", "lineup": "lineup", "matchup_lineup": "matchup", "matchup": "matchup",
              "waiver_targets": "waiver", "waivers": "waiver", "matchup_watch": "eye", "live_matchup": "live",
              "market_sweep": "sweep", "compare_players": "compare", "compare": "compare", "evaluate_move": "arrow", "move": "arrow",
              "evaluate_trade": "swap", "trade": "swap", "find_trades": "users", "trade_targets": "users", "trade_leverage": "lever",
              "roster_grades": "award", "roster_calendar": "cal", "check_freshness": "drop", "run_windows": "clock",
              "odds_history": "trend", "luck_ledger": "dice", "decision_scorecard": "score", "data_health": "health",
              "bid_review": "tag", "run_simulation": "engine", "weekly_report": "book", "gameday": "live",
              "draft_review": "award", "season_retrospective": "book", "run_sync": "refresh"}


def tool_icon(name):
    """The sprite symbol id for a tool or a record prefix; a clipboard when unknown."""
    return "i-" + TOOL_ICONS.get(str(name or ""), "clip")


def tool_title(name):
    name = str(name or "")
    return TOOL_TITLES.get(name) or name.replace("_", " ").strip().capitalize()


def sentence(s):
    """First letter up, the rest untouched: 'player A' -> 'Player A' (capitalize() would give 'Player a')."""
    s = "" if s is None else str(s)
    return s[:1].upper() + s[1:]


# The canonical-run windows, as a person names them (run_windows keeps its own ids).
WINDOW_TITLES = {"run1_pre_kickoff": "Pre-kickoff run", "run2_sunday": "Sunday run", "run3_tuesday": "Tuesday run"}


def window_title(name):
    s = str(name or "")
    if s in WINDOW_TITLES:
        return WINDOW_TITLES[s]
    return sentence(re.sub(r"^run\d_", "", s).replace("_", " ")) if s else ""


def job_subtitle(meta):
    """The label without the tool's own name in front: 'Luck ledger · 2026' -> '2026'."""
    meta = meta or {}
    label = str(meta.get("label") or "")
    tool = str(meta.get("tool") or "")
    for prefix in (tool_title(tool), tool.replace("_", " ").capitalize(), tool, tool.replace("_", " ")):
        if prefix and label.lower().startswith(prefix.lower()):
            label = label[len(prefix):]
            break
    return label.strip(" ·-")


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
            return base + " · " + window_title(m.group(1)).lower()
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


def fse(v, unit=""):
    """UI-V6: "±" means ONE STANDARD ERROR on every page, and prints only through here -- two
    decimals below 1 (0.25 stays 0.25, not 0.3), one above. '' when there is none. A score's
    spread is not a standard error and is labelled sd instead."""
    try:
        if v is None:
            return ""
        f = abs(float(v))
    except Exception:
        return ""
    if f != f:
        return ""
    return f"± {f:.2f}{unit}" if f < 1 else f"± {f:.1f}{unit}"


def verdict(delta, se):
    """UI-T2: a paired result stated in standard errors, so a gain inside the noise never
    reads as a gain. Under 2 SE: no measurable change; 2 to 4: modest; above 4: clear. The
    tiers are a DISPLAY convention (docs/WEB_UI.md), not a significance test. None when
    either number is missing or the standard error is zero."""
    try:
        d, s = float(delta), float(se)
    except (TypeError, ValueError):
        return None
    if s <= 0 or d != d or s != s:
        return None
    z = abs(d) / s
    tier = "none" if z < 2 else ("modest" if z <= 4 else "clear")
    word = "gain" if d > 0 else "loss"
    text = "no measurable change" if tier == "none" else f"a {tier} {word}"
    return {"tier": tier, "text": text, "z": round(z, 1), "sign": "pos" if d > 0 else "neg"}


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


def fwins(v):
    """A win total: whole numbers bare, a tie's half kept (2 -> "2", 2.5 -> "2.5")."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:
        return "—"
    return f"{f:.0f}" if f == int(f) else f"{f:.1f}"


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
    """Cells of one printed row: a ' | ' divider is a hard column break, two or more
    spaces a soft one -- the shapes the tools actually print (market sweep, live matchup,
    the luck ledger) all reduce to that."""
    out = []
    for part in line.strip().split(" | ") if " | " in line else [line.strip()]:
        out.extend(c for c in SPLIT_RE.split(part.strip()) if c != "")
    return out


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
    if max(counts) - min(counts) > (2 if any(" | " in x for x in lines[i:j]) else 1):
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


def stdout_json(text):
    """The JSON document a --json tool printed, found after any engine chatter: the first
    line that is exactly '{' or '[' starts it. None when there is none or it is broken."""
    import json as _json
    lines = (text or "").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() in ("{", "["):
            try:
                doc, _end = _json.JSONDecoder().raw_decode("\n".join(lines[i:]))
                return doc
            except ValueError:
                return None
    return None


# ------------------------------------------------------------------------ record views
def col(key, label=None, kind="text", nd=1, link=None, lo=None, mid=None, hi=None):
    """link='team' renders the cell as a link to that team on the League page.
    kind='range' draws a lo-hi band with a tick at mid (three row keys), scaled to the
    table's largest hi so every row's bar is comparable."""
    return {"key": key, "label": label or key.replace("_", " "), "kind": kind, "nd": nd, "link": link,
            "lo": lo, "mid": mid, "hi": hi}


def _num(v):
    try:
        f = float(v)
        return None if f != f else f
    except Exception:
        return None


def _cell(row, c):
    v = row.get(c["key"]) if isinstance(row, dict) else None
    k = c["kind"]
    if k == "range":
        lo, mid, hi = (_num(row.get(c["lo"])), _num(row.get(c["mid"])), _num(row.get(c["hi"]))) if isinstance(row, dict) else (None, None, None)
        if lo is None or hi is None:
            return {"text": "—", "num": True, "tone": "", "range": None}
        return {"text": f"{fnum(lo, c['nd'])}–{fnum(hi, c['nd'])}", "num": True, "tone": "",
                "range": {"lo": lo, "mid": mid, "hi": hi, "max": hi}}
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
    out = [{"cells": [_cell(r, c) for c in columns], "me": (r.get(me_key) if me_key and isinstance(r, dict) else None)}
           for r in rows if isinstance(r, dict)]
    for i, c in enumerate(columns):               # one scale per range column, so bars compare across rows
        if c["kind"] == "range":
            top = max([r["cells"][i]["range"]["hi"] for r in out if r["cells"][i].get("range")] or [0])
            for r in out:
                if r["cells"][i].get("range"):
                    r["cells"][i]["range"]["max"] = top
    return {"kind": "table", "title": title, "note": note, "me_key": me_key, "collapsed": collapsed,
            "columns": columns, "rows": out}


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
        "tiles": [tile("expected total", fnum(d.get("expected_total"), 1), "what the 13 starters are expected to score"),
                  tile("questionable starters", str(len(q)), "each has a named fallback" if q else "none", "neg" if q else ""),
                  tile("locked in place", str(d.get("pinned", 0)), "their games have kicked off" if d.get("locks_active") else "no game has kicked off yet"),
                  tile("unfilled slots", str(len(d.get("unfilled") or [])), "no eligible player" if d.get("unfilled") else "every slot filled", "neg" if d.get("unfilled") else "")],
        "sections": [
            table("Starters", [col("slot"), col("name"), col("pos"), col("expected", kind="num"),
                               col("band", "floor–ceiling (p10 · p50 · p90)", "range", nd=0, lo="p10", mid="p50", hi="p90"), col("p_zero", "P(0 pts)", "pct"),
                               col("margin", "margin over bench", "signed", 1), col("alternative", "best alternative"), col("flag", "status", "flag")],
                  starters),
            table("Questionable starters", [col("name"), col("slot"), col("expected", kind="num"), col("fallback"),
                                            col("fallback_expected", "fallback expects", "num"), col("give_up", "cost of sitting", "num")], q)
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
                  rows, note="ranked by P(beat opponent); the top row is the pick")]
    for i, k in enumerate(order):
        if k in cons and cons[k].get("lineup"):
            secs.append(table(f"Lineup — {CONSTRUCTION_LABELS.get(k, k)}" + (" (recommended)" if i == 0 else ""),
                              [col("slot"), col("name"), col("nfl_team", "NFL"), col("expected", kind="num"), col("sd", kind="num"), col("flag", "status", "flag")],
                              cons[k]["lineup"], collapsed=(i > 0)))
    secs.append(table("Opponent's lineup" + (" (assumed: their highest-scoring lineup)" if d.get("opponent_lineup_assumed") else ""),
                      [col("slot"), col("name"), col("expected", kind="num")], d.get("opponent_lineup"), collapsed=True))
    secs.append(text("How this was computed", d.get("note")))
    return {"title": "Matchup lineups", "subtitle": f"{d.get('team')} vs {d.get('opponent')} · week {d.get('week')}",
            "tiles": [tile("recommended", CONSTRUCTION_LABELS.get(order[0], order[0]) if order else "—", "best chance to beat the opponent"),
                      tile("P(beat opponent)", fpct(best.get("p_beat_opponent")), fse(100 * best["se"], "%") if best.get("se") is not None else ""),
                      tile("P(beat median)", fpct(best.get("p_beat_median")), ""),
                      tile("favoured on projections", "yes" if d.get("favoured_by_max_mean") else "no", "", "pos" if d.get("favoured_by_max_mean") else "neg")],
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
                      tile("holes this week", str(len(holes)), _join(holes) if holes else "every slot can be filled", "neg" if holes else ""),
                      tile("holes next week", str(len(d.get("holes_next_week") or [])), _join(d.get("holes_next_week")) if d.get("holes_next_week") else "none"),
                      tile("targets", str(len(rows)), "ranked by value over replacement")],
            "sections": [table("Targets", [col("name"), col("pos"), col("team", "NFL"), col("vorp", "VORP", "signed", 1), col("week_mean", "this week", "num"),
                                           col("bid_point", "bid", "num", 0), col("bid_band", "band"), col("fills"), col("injury_status", "status", "flag")],
                               rows, note="the bid prices each player's edge over your fallback against what rivals are likely to bid (F61)"),
                         table("More on each target", [col("name"), col("tier", kind="num", nd=0), col("mean", "season mean", "num"), col("incumbent"),
                                                       col("week_p90", "p90", "num", 0), col("week_p_zero", "P(0 pts)", "pct"), col("bid_v1", "old bid", "num", 0),
                                                       col("bye", kind="num", nd=0)],
                               rows, collapsed=True, note="the old bid rule, kept for comparison; neither rule is validated yet (F61)"),
                         text("Caveat", d.get("caveat"))]}


def _roster_grades(d):
    teams = (d.get("league") or {}).get("teams") or []
    return {"title": "Roster grades", "subtitle": f"week {(d.get('league') or {}).get('week')}",
            "tiles": [tile("teams", str(len(teams)), "ranked by lineup VORP")],
            "sections": [table("League", [col("rank", kind="num", nd=0), col("team", link="team"), col("lineup_vorp", "lineup VORP", "num"), col("depth_vorp", "depth VORP", "num"),
                                          col("optimal_score", "optimal score", "num"), col("tier1_starters", "tier-1 starters", "num", 0),
                                          col("starters_below_replacement", "below replacement", "num", 0), col("holes", kind="num", nd=0)],
                               teams, me_key="team")]}


def _acceptable_first(rows, who, needs):
    """UI-T6: a deal the other side loses on will not happen -- ideas ordered by the SMALLER of
    the two sides' gains, each with the other side's positional need (trade.needs)."""
    out = []
    for r in rows or []:
        g = [v for v in (r.get("my_gain"), r.get("their_gain")) if v is not None]
        out.append(dict(r, both_gain=min(g) if len(g) == 2 else None, their_need=(needs or {}).get(r.get(who))))
    out.sort(key=lambda r: (r["both_gain"] is None, -(r["both_gain"] or 0.0)))
    return out


def _find_trades(d):
    d = dict(d, buy=_acceptable_first(d.get("buy"), "with", d.get("_needs")),
             sell=_acceptable_first(d.get("sell"), "buyer", d.get("_needs")))
    return {"title": "Trade targets", "subtitle": f"{d.get('team')} · week {d.get('week')}",
            "tiles": [tile("buy ideas", str(len(d.get("buy") or [])), "bench players elsewhere who would start for me"),
                      tile("sell ideas", str(len(d.get("sell") or [])), "my surplus with a buyer"),
                      tile("excluded (pending)", str(len(d.get("excluded_pending") or [])), "players in a pending trade (T3)")],
            "sections": [table("Buy", [col("with", link="team"), col("target"), col("fills_my_slot", "fills"), col("i_give"), col("i_get"),
                                       col("both_gain", "both sides gain at least", "signed"), col("their_need", "their need"), col("my_gain", kind="signed"),
                                       col("their_gain", kind="signed"), col("worth_proposing", "worth it", "bool"), col("acceptable", kind="bool"),
                                       col("their_playoff_pct", "their playoff %", "num"), col("willingness", kind="num", nd=2), col("simulated", kind="bool")],
                               d.get("buy"), me_key="with"),
                         table("Sell", [col("buyer", link="team"), col("they_want"), col("they_give"), col("both_gain", "both sides gain at least", "signed"),
                                        col("their_need", "their need"), col("my_gain", kind="signed"), col("their_gain", kind="signed"),
                                        col("worth_proposing", "worth it", "bool"), col("acceptable", kind="bool"), col("their_playoff_pct", "their playoff %", "num"),
                                        col("willingness", kind="num", nd=2), col("simulated", kind="bool")], d.get("sell")),
                         text("Who counts as a seller", d.get("contention_note")), text("How this was computed", d.get("note"))]}


def _compare(d):
    a, b = d.get("a") or {}, d.get("b") or {}
    an, bn = d.get("a_name"), d.get("b_name")
    rows = [dict(player=an, **a), dict(player=bn, **b)]
    quick = (d.get("path") == "light")
    return {"title": "Start A or B", "subtitle": f"{an} vs {bn} · week {d.get('week')}",
            "tiles": [tile(f"{an} wins", fpct(d.get("p_a")), fse(float(d["se_p"]) * (100 if abs(float(d["se_p"])) <= 1 else 1), "%") if d.get("se_p") else "",
                           "pos" if (d.get("p_a") or 0) > (d.get("p_b") or 0) else "neg"),
                      tile(f"{bn} wins", fpct(d.get("p_b")), f"tie {fpct(d.get('p_tie'))}"),
                      tile("mean difference", fsigned(d.get("mean_diff")), f"{an} minus {bn}", tone(d.get("mean_diff"))),
                      tile("draws", f"{int(d.get('n') or 0):,}", "quick mode: drawn from baselines, not simulated" if quick else "joint simulation")],
            "sections": [table("Distributions", [col("player"), col("mean", kind="num"),
                                                 col("band", "floor–ceiling (p10 · p50 · p90)", "range", lo="p10", mid="p50", hi="p90"),
                                                 col("p25", kind="num"), col("p75", kind="num"), col("p_zero", "P(0 pts)", "pct")], rows),
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
                          f"{fse(r['playoff_se'])} · champion {fsigned(r['champ_delta'])}", tone(r["playoff_delta"])))
    tiles.append(tile("simulations", f"{int(d.get('n_sims') or 0):,}", f"{d.get('batches')} paired batches on the same seeds"))
    spec = d.get(what) or {}
    sub = (f"{spec.get('team_a')} gives {_join(spec.get('a_gives'))} · {spec.get('team_b')} gives {_join(spec.get('b_gives'))}"
           if what == "trade" else f"{spec.get('team')}: add {_join(spec.get('adds'))} · drop {_join(spec.get('drops'))}")
    return {"title": "Trade evaluation" if what == "trade" else "Move evaluation", "subtitle": sub,
            "tiles": tiles,
            "sections": [table("Every team: with the change, minus without", [col("team", link="team"), col("side"), col("playoff_delta", "playoff Δ", "signed", 2), col("playoff_se", "± se", "num", 2),
                                                                   col("playoff_with", "with", "num"), col("playoff_without", "without", "num"),
                                                                   col("champ_delta", "champ Δ", "signed", 2), col("champ_se", "± se", "num", 2),
                                                                   col("wins_delta", "exp. wins Δ", "signed", 2)], rows, me_key="team",
                               note="the two sides first, then every other team; each ± is the spread between paired batches"),
                         kv("The " + what, list(spec.items())), text("How this was computed", d.get("note"))]}


def _calendar(d):
    cal, crunch = d.get("calendar") or {}, d.get("crunch") or {}
    holes = cal.get("holes") or {}
    rows = [dict(r, holes=", ".join(holes.get(str(r.get("week")), []))) for r in cal.get("rows") or []]
    wks = cal.get("weeks") or []
    return {"title": "Roster calendar", "subtitle": f"{cal.get('team')} · weeks {wks[0] if wks else ''}–{wks[-1] if wks else ''}",
            "tiles": [tile("holes ahead", str(len(holes)), "; ".join(f"week {w}: {_join(v)}" for w, v in holes.items()) if holes else "every remaining week can be filled", "neg" if holes else ""),
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
                      tile("mine, total", fnum(sum(g.get("mine_sum") or 0 for g in games), 1), "expected points across these games"),
                      tile("theirs, total", fnum(sum(g.get("theirs_sum") or 0 for g in games), 1), ""),
                      tile("their losing script", ls.get("game") or "—", f"{fpct(ls.get('share'))} of their total" if ls else "")],
            "sections": [table("Games", [col("game"), col("mine_names", "mine"), col("mine_sum", "mine", "num"), col("theirs_names", "theirs"), col("theirs_sum", "theirs", "num"),
                                         col("game_total", "Vegas total", "num"), col("shared"), col("wind_mph", "wind", "num", 0), col("precip_prob", "precip %", "num", 0)], rows),
                         table("Designations on both rosters", [col("name"), col("side"), col("nfl_team", "NFL"), col("expected", kind="num"), col("flag", "status", "flag")], d.get("designations")),
                         table("Stacks", [col("game"), col("side"), col("team", link="team"), col("n", kind="num", nd=0), col("sum", kind="num"), col("players")], d.get("stacks")),
                         text("Weather", d.get("weather_note"))]}


def _live_matchup(d):
    jl = d.get("joint_legs") or {}
    league = [dict(team=t, **v) for t, v in (d.get("league") or {}).items() if isinstance(v, dict)]
    league.sort(key=lambda r: -(r.get("projected") or 0))
    return {"title": "Live matchup", "subtitle": f"{d.get('team')} vs {d.get('opponent')} · week {d.get('week')} · as of {human_time(d.get('as_of'))}",
            "tiles": [tile("banked so far", fnum(d.get("banked"), 1), f"{d.get('starters_left')} starters still to play"),
                      tile("projected finish", fnum(d.get("projected"), 1), "if everyone plays (no availability discount, F51)"),
                      tile("P(win head-to-head)", fpct(d.get("p_head_to_head")), f"{fpct(d.get('p_head_to_head_inflated'))} if same-game swings are widened",
                           "pos" if (d.get("p_head_to_head") or 0) >= 0.5 else "neg"),
                      tile("P(beat the median)", fpct(d.get("p_beat_median")), f"{fnum(jl.get('expected_wins'), 2)} of 2 wins expected this week")],
            "sections": [table("Both games this week, drawn together", [col("outcome"), col("p", "probability", "pct"), col("independent", "if independent", "pct")],
                               [{"outcome": "2–0 (win both)", "p": jl.get("p_2_0"), "independent": jl.get("p_2_0_independent")},
                                {"outcome": "1–1", "p": jl.get("p_1_1"), "independent": None},
                                {"outcome": "0–2 (lose both)", "p": jl.get("p_0_2"), "independent": jl.get("p_0_2_independent")}],
                               note=f"{int(jl.get('n') or 0):,} joint draws; both legs turn on my own score (B11)"),
                         table("Every team right now", [col("team", link="team"), col("banked", kind="num"), col("projected", kind="num"),
                                                        col("left", "still to play", "num", 0), col("p_beat_median", "P(beat median)", "pct")], league, me_key="team")]}


def _luck(d):
    seasons = d.get("seasons") if isinstance(d.get("seasons"), list) else [d]
    secs = []
    for sn in seasons:
        rows = []
        for key, label in (("schedule_luck", "schedule luck"), ("opponent_luck", "opponent luck"), ("close_games", "close games"),
                           ("dnp_luck", "DNP luck"), ("scoring_luck", "scoring luck")):
            m = sn.get(key)
            if isinstance(m, dict):
                rows.append({"measure": label, "delta": m.get("delta"), "se": m.get("se"), "z": m.get("z"), "p": m.get("p"),
                             "detail": "; ".join(f"{k} {fnum(v, 2) if isinstance(v, float) else v}" for k, v in m.items() if k not in ("delta", "se", "z", "p"))})
            elif isinstance(m, str):
                rows.append({"measure": label, "detail": m})
        wk = sn.get("weeks") or []
        secs.append(table(f"{sn.get('team')} · {sn.get('season')} · {len(wk)} completed week{'s' if len(wk) != 1 else ''}",
                          [col("measure"), col("delta", "Δ vs league", "signed", 2), col("se", "± se", "num", 2), col("z", kind="signed", nd=2),
                           col("p", kind="num", nd=3), col("detail")], rows,
                          note="negative means unlucky on that measure; z and p stay blank until six weeks are in (F53)"))
    first = seasons[0] if seasons else {}
    sl = first.get("schedule_luck") or {}
    return {"title": "Luck ledger", "subtitle": "five measures set before the season, each against the league average",
            "tiles": [tile("schedule luck", fsigned((sl or {}).get("delta"), 2) if isinstance(sl, dict) else "—", "actual minus expected wins", tone((sl or {}).get("delta") if isinstance(sl, dict) else None)),
                      tile("seasons", str(len(seasons)), "")],
            "sections": secs}


def _odds_history(d):
    rows = []
    for r in d.get("rows") or []:
        rows.append(dict(r, at=human_time(r.get("at")), moves=", ".join(str(m) for m in (r.get("moves") or [])) or "—"))
    return {"title": "Odds history", "subtitle": f"{d.get('team')} · {d.get('n_canonical')} canonical runs of {d.get('n_total')} logged",
            "tiles": [tile("canonical runs", str(d.get("n_canonical") or 0), "scheduled runs only (F56/B5)"),
                      tile("latest playoff %", fpct((rows[-1].get("playoff_pct") or 0) / 100) if rows else "—", fse(rows[-1].get("playoff_se")) if rows else ""),
                      tile("latest title %", fnum(rows[-1].get("champ_pct"), 1) + "%" if rows else "—", "")],
            "sections": [table("Every canonical run", [col("at", "run"), col("week", kind="num", nd=0), col("playoff_pct", "playoff %", "num"), col("playoff_se", "± se", "num", 2),
                                                       col("d_playoff", "Δ playoff", "signed", 1), col("champ_pct", "title %", "num"), col("d_champ", "Δ title", "signed", 1),
                                                       col("expected_wins", "exp. wins", "num", 2), col("d_wins", "Δ wins", "signed", 2), col("moves", "moves landed in the window")], rows),
                         text("What moved the odds", d.get("causation_note")), text("Why only canonical runs", d.get("canonical_note"))]}


def _data_health(d):
    rows = [{"name": c.get("name"), "verdict": c.get("verdict"), "detail": c.get("detail"), "players": c.get("players")} for c in d.get("checks") or []]
    bad = sum(1 for r in rows if r["verdict"] not in ("PASS", "OK"))
    return {"title": "Data health", "subtitle": f"season {d.get('season')} · week {d.get('week')}",
            "tiles": [tile("verdict", str(d.get("verdict") or "—"), "", "pos" if d.get("verdict") in ("PASS", "OK") else "neg"),
                      tile("checks", str(len(rows)), f"{bad} not passing" if bad else "all passing", "neg" if bad else "")],
            "sections": [table("Every source", [col("name", "check"), col("verdict", kind="flag" if bad else "text"), col("players", kind="num", nd=0), col("detail")],
                               [dict(r, verdict=(r["verdict"] if r["verdict"] not in ("PASS", "OK") else r["verdict"])) for r in rows])]}


def _bid_review(d):
    cal = d.get("calibration") or {}
    v1, v2 = cal.get("v1") or {}, cal.get("v2") or {}
    cols = [col("player"), col("pos"), col("placed_at", "placed", "text"), col("bid_placed", "my bid", "num", 0), col("suggested_v2_point", "suggested", "num", 0),
            col("band", "band"), col("suggested_v1", "old rule", "num", 0), col("bid_mismatch", "miss", "signed", 0), col("vorp_at_bid", "VORP then", "num"),
            col("rivals_needing", "rivals needing", "num", 0), col("remaining_faab", "budget then", "num", 0)]
    def rows(xs):
        return [dict(x, placed_at=human_time(x.get("placed_at")), band=f"{x.get('suggested_v2_low')}–{x.get('suggested_v2_high')}") for x in xs or []]
    return {"title": "Bid review", "subtitle": f"{cal.get('n')} claims scored · verdict {cal.get('verdict')}",
            "tiles": [tile("claims scored", str(cal.get("n") or 0), f"{cal.get('n_exact') or 0} with an exact clearing price"),
                      tile("mean miss, new rule", fnum(v2.get("mean_miss"), 1), f"{v2.get('errors')} errors"),
                      tile("mean miss, old rule", fnum(v1.get("mean_miss"), 1), f"{v1.get('errors')} errors"),
                      tile("verdict", str(cal.get("verdict") or "—"), "")],
            "sections": [table("Claims", cols, rows(d.get("rows"))), table("Superseded claims", cols, rows(d.get("superseded")), collapsed=True),
                         text("How claims are scored", cal.get("note"))]}


def _run_windows(d):
    fr = d.get("freshness") or {}
    rows = [dict(w, start=human_time(w.get("start")), deadline=human_time(w.get("deadline"))) for w in d.get("windows") or []]
    return {"title": "Run windows", "subtitle": f"week {d.get('target_week')} · kickoffs from {d.get('kickoff_source')} · as of {human_time(d.get('now'))}",
            "tiles": [tile("data", str(fr.get("status") or "—"), f"{len(fr.get('reasons') or [])} notes", "neg" if fr.get("status") == "STALE" else ""),
                      tile("flags", str(len(d.get("flags") or [])), _join(d.get("flags")) if d.get("flags") else "nothing to act on")],
            "sections": [table("This week's windows", [col("name", "window"), col("start", "opens"), col("deadline", "closes"), col("status"), col("covered_by", "covered by")], rows),
                         text("Outside the windows", _join(d.get("outside_windows")), collapsed=False),
                         text("Freshness notes", "\n".join(fr.get("reasons") or []))]}


def _scorecard(d):
    sm = d.get("summary") or {}
    rows = [dict(x, slots=_join(x.get("slots")), also=_join(x.get("also_considered_over"))) for x in d.get("decisions") or []]
    return {"title": "Decision scorecard", "subtitle": f"{d.get('team')} · week {d.get('week')} · scored on {d.get('source')}",
            "tiles": [tile("decisions", str(sm.get("decisions") or 0), f"{sm.get('right')} right · {sm.get('wrong')} wrong · {sm.get('unresolved')} unresolved"),
                      tile("hit rate", fpct(sm.get("hit_rate")), ""),
                      tile("points left behind", fnum(sm.get("points_left_behind"), 1), "what the wrong calls cost, in total", "neg" if (sm.get("points_left_behind") or 0) > 0 else "")],
            "sections": [table("Every start/sit call", [col("slot"), col("started"), col("started_points", "scored", "num"), col("alternative", "the alternative"), col("alternative_points", "it scored", "num"),
                                                        col("expected", "expected", "num"), col("margin", "margin at the time", "signed", 1), col("cost", kind="num"), col("outcome", kind="flag"), col("also", "also considered over")],
                               [dict(r, outcome=(r.get("outcome") if r.get("outcome") == "wrong" else "")) for r in rows]),
                         text("Note", sm.get("note"))]}


SPECS = {"optimize_lineup": _lineup, "matchup_lineup": _matchup, "waiver_targets": _waivers, "roster_grades": _roster_grades,
         "find_trades": _find_trades, "compare_players": _compare, "evaluate_trade": lambda d: _paired(d, "trade"),
         "evaluate_move": lambda d: _paired(d, "move"), "roster_calendar": _calendar, "matchup_watch": _watch,
         "live_matchup": _live_matchup, "luck_ledger": _luck, "odds_history": _odds_history,
         "data_health": _data_health, "bid_review": _bid_review, "run_windows": _run_windows, "decision_scorecard": _scorecard}


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


def record_view(data, tool=None):
    """`tool` names the producer when the document does not (a --json tool's stdout); a
    list document (the luck ledger prints one row per season) is wrapped."""
    if isinstance(data, list) and tool:
        data = {"tool": tool, "seasons": data}
    if not isinstance(data, dict):
        return None
    if tool and not data.get("tool"):
        data = dict(data, tool=tool)
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


# What a sync prints as it goes (fantasy_sim.sync.sync_all's own markers).
SYNC_STAGES = (("checking the odds key", ("[ODDS KEY",)),
               ("fetching schedule and results", ("[INIT]",)),
               ("scores and designations", ("[FIRST SCORES]", "[DESIGNATIONS]")),
               ("transactions", ("[DECISION LOG]", "[DRAFT LOG]", "[PENDING TRADES]")),
               ("done", ("[FAAB]", "manifest")))


def progress(text, tool):
    """{stages, reached, stage, last}: the ordered stage names, how many have been reached,
    the current one ('starting' before any), and the last line the tool itself printed."""
    text = text or ""
    stages = {"weekly_report": REPORT_STAGES, "run_simulation": ENGINE_STAGES, "run_sync": SYNC_STAGES}.get(tool, TOOL_STAGES)
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


def duration_short(seconds):
    """For a card: 45 -> '45 s'; 138 -> '2.3 m'; 4000 -> '1.1 h'."""
    try:
        s = max(0.0, float(seconds))
    except Exception:
        return "—"
    if s < 60:
        return f"{int(round(s))} s"
    if s < 3600:
        return f"{s / 60:.1f} m"
    return f"{s / 3600:.1f} h"


def parse_time(value):
    """A UTC datetime from anything the repo writes: an aware or naive datetime, an ISO
    'Z' stamp, a compact 20260924T165331Z stamp, or epoch seconds. None otherwise."""
    import datetime as _dt
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
        except Exception:
            return None
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y%m%dT%H%M%S.%fZ", "%Y-%m-%d %H:%MZ", "%Y-%m-%dT%H:%MZ"):
        try:
            return _dt.datetime.strptime(s, fmt).replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
    return None


def human_time(value, now=None):
    """Local wall-clock time as a person writes it: 'Sep 24, 4:53 pm'; the year is added
    only when it is not this year. Anything unparseable comes back as itself."""
    import datetime as _dt
    dt = parse_time(value)
    if dt is None:
        return str(value or "—")
    local = dt.astimezone()                      # the machine's zone, which is the owner's
    ref = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone()
    h = local.hour % 12 or 12
    ampm = "am" if local.hour < 12 else "pm"
    year = f", {local.year}" if local.year != ref.year else ""
    return f"{local:%b} {local.day}{year}, {h}:{local:%M} {ampm}"


def when(value):
    """The one time filter every template uses (see human_time)."""
    return human_time(value)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").casefold()).strip("-")


def stamp_slug(stamp):
    """20260924T165331Z -> 2026-09-24-165331 (readable in a URL, still unique)."""
    m = re.match(r"(\d{4})(\d{2})(\d{2})T(\d{6})Z$", str(stamp or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}-{m.group(4)}" if m else None


def tool_slug(tool):
    return slug(tool_title(tool))


def pretty_url(entry):
    """A readable address for a record or log entry, or its /file/ link when it has none:
    /records/week-3/optimal-lineup/2026-09-24-165331, /records/week-3/archive/...,
    /records/ad-hoc/compare-players/..., /records/season/draft-review-2026, /logs/decision-log."""
    rel, name = entry.get("rel") or "", entry.get("name") or ""
    stem, ext = name.rsplit(".", 1) if "." in name else (name, "")
    if rel.startswith("logs/") and ext == "jsonl":
        return "/logs/" + slug(stem)
    if not rel.startswith("decisions/"):
        return entry.get("link")
    parts = rel.split("/")
    scope = parts[1]
    if scope.startswith("week_"):
        scope = "week-" + str(int(scope[5:]))
    elif scope == "adhoc":
        scope = "ad-hoc"
    archive = "archive/" if "/archive/" in rel else ""
    st = stamp_slug(entry.get("stamp"))
    if scope == "season" or not st:
        return f"/records/{scope}/{archive}{slug(stem)}" + ("/markdown" if ext == "md" else "")
    tail = "/markdown" if ext == "md" else ""
    extra = ""
    if entry.get("tool") == "compare":                      # keep the A-vs-B in the address
        m = re.match(r"compare_\d{8}T\d{6}Z_(.+)$", stem)
        extra = "/" + slug(m.group(1)) if m else ""
    if entry.get("tool") == "weekly_report":
        m = re.search(r"_(run\d_[a-z_]+?)_\d{8}T", stem)
        extra = "/" + slug(m.group(1)) if m else ""
    return f"/records/{scope}/{archive}{tool_slug(entry.get('tool'))}/{st}{extra}{tail}"


def job_url(meta):
    """/jobs/optimal-lineup/2026-09-26-000000-000001 for id 20260926T000000Z_000001_optimize_lineup."""
    jid = str((meta or {}).get("id") or "")
    m = re.match(r"(\d{8}T\d{6}Z)_([0-9a-f]+)_(.+)$", jid)
    if not m:
        return "/jobs/" + jid
    return f"/jobs/{tool_slug(m.group(3))}/{stamp_slug(m.group(1))}-{m.group(2)}"


# W8: the simple view's words for a job's state, and the scrub that keeps a tool's own
# method notes readable there (the audit codes -- F51, B11, H5, C3 -- mean nothing to a
# manager; the parenthesised ones go, the rest of the sentence stays).
STATE_LABELS = {"RUNNING": "Running", "OK": "Done", "VOID": "Didn't finish"}
_CODE = r"(?:[A-Z]{1,2}\d{1,3})"
_CODE_PARENS = re.compile(r"\s*\((?:see\s+)?" + _CODE + r"(?:\s*[,/;&]\s*" + _CODE + r")*\)")
_CODE_TRAIL = re.compile(r"\s*[-–—]+\s*" + _CODE + r"(?:\s*[,/]\s*" + _CODE + r")*\s*$")


def state_label(state):
    return STATE_LABELS.get(str(state or ""), str(state or ""))


def simplify(text):
    """'ranked by P(beat opponent) (F61)' -> 'ranked by P(beat opponent)'."""
    s = "" if text is None else str(text)
    s = _CODE_PARENS.sub("", s)
    s = _CODE_TRAIL.sub("", s)
    return s


STATUS_ABBR = {"questionable": "Q", "doubtful": "D", "out": "O", "ir": "IR", "pup": "PUP", "sus": "SUS", "suspended": "SUS",
               "na": "NA", "dnr": "DNR", "cov": "COV", "bye": "BYE"}


def status_abbr(status):
    """The way the app abbreviates it: Questionable -> Q, Doubtful -> D, Out -> O; IR stays IR."""
    s = str(status or "").strip()
    return STATUS_ABBR.get(s.casefold(), s[:3].upper() if s else "")


# ------------------------------------------------------------------------------ charts
CHART_FONT_PX = 11.5      # .viz text in base.html (B14: 10.5 was unreadable); label_width() estimates from it


def label_width(text):
    """The room an axis label needs at the chart's font, with a gap: a conservative
    0.56 em per character (digits and lower case are narrower, capitals wider)."""
    return len(str(text)) * CHART_FONT_PX * 0.56 + 10


def nice_step(span, n=4):
    """A 1, 2, 2.5 or 5 times a power of ten that divides `span` into about n intervals."""
    import math
    if not span or span <= 0:
        return 1.0
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw - 1e-12:
            return m * mag
    return 10 * mag


def nice_ticks(lo, hi, n=4, ceiling=None):
    """Round tick values that cover lo..hi. Without a ceiling the range widens to the
    ticks on either side (so the frame is 0 / 20 / 40 / 60, never 0 / 21 / 42 / 63).
    With one -- a percentage's 100 -- the ticks stop there and it is the last tick."""
    import math
    if ceiling is not None:
        hi = ceiling
    step = nice_step(hi - lo, n)
    first = math.floor(lo / step + 1e-9) * step
    ticks = []
    v = first
    while v <= hi + step * (1e-9 if ceiling is not None else 0.999999):
        ticks.append(round(v, 10))
        v += step
        if ceiling is None and ticks[-1] >= hi:
            break
    if ceiling is not None:
        if ceiling - ticks[-1] > 0.3 * step:
            ticks.append(float(ceiling))
        else:
            ticks[-1] = float(ceiling)
    elif ticks[-1] < hi:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def _series_colour(sr):
    """The CSS colour a series draws in: its team hue, else the class's token."""
    hue = sr.get("hue")
    if hue is not None:
        return f"hsl({int(hue)} 62% var(--line-l))"
    return {"me": "var(--turf)", "pos": "var(--pos)", "gold": "var(--gold)"}.get(sr.get("cls") or "", "var(--rule)")


def line_chart(series, labels, unit="", nd=1, width=640, height=220, y_min=0.0, y_max=None, marker=None,
               value_labels=True, legend=False, notes=None):
    """The one SVG line chart, drawn to one scale. `series` is a list of {name, values,
    cls, hue}: cls 'me' | 'pos' | 'gold' | '' (the quiet grey), and a team `hue` draws the
    line in that team's colour (a race). `labels` are the x labels, one per point.

    Ticks are round numbers (nice_ticks); x labels never collide (the last always
    survives, an earlier one gives way) and a run of identical labels is written once;
    the y range gets 12% headroom so the last value label never clips. `value_labels`
    is True (every point when there are 16 or fewer, else the last), 'last', or False.
    `marker` is an x index for a dashed 'now' line. The svg carries data-* the hover
    layer in base.html reads (labels, x positions, every series' values and colour);
    `legend=True` appends a <div class="legend"> naming every series in its colour.
    `notes` (UI-H5) are [{x, series, n, title}]: a numbered marker on that series' point at x,
    with the title as its hover; a note whose point is missing is skipped.
    Returns markup (escapes only names and labels)."""
    import json
    from markupsafe import Markup, escape
    pts_all = [v for sr in series for v in (sr.get("values") or []) if v is not None]
    if not pts_all or not labels:
        return Markup("")
    top = max(pts_all)
    lo0 = min(pts_all) if y_min is None else min(y_min, min(pts_all))
    hi0 = y_max if y_max is not None else top + (top - lo0) * 0.12 + (0.5 if top == lo0 else 0)
    if hi0 <= lo0:
        hi0 = lo0 + 1
    ticks = nice_ticks(lo0, hi0, ceiling=y_max)
    lo, hi = ticks[0], ticks[-1]
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    tnd = 0 if step >= 1 else (1 if step >= 0.1 else 2)
    tick_text = [f"{fnum(t, tnd)}{unit}" for t in ticks]
    n = max(len(labels), max(len(sr.get("values") or []) for sr in series))
    padl = int(max(label_width(t) for t in tick_text)) + 2
    padr, padt, padb = 16, 14, 28
    W, H = width, height

    def x(i):
        return padl + (i * (W - padl - padr) / (n - 1) if n > 1 else (W - padl - padr) / 2)

    def y(v):
        return padt + (H - padt - padb) * (1 - (v - lo) / (hi - lo))
    xs = [x(i) for i in range(n)]
    out = [f'<svg class="viz" viewBox="0 0 {W} {H}" role="img" aria-label="chart" data-labels=\'{json.dumps([str(l) for l in labels]).replace("&", "&amp;").replace("<", "&lt;").replace(chr(39), "&#39;")}\' '
           f'data-xs="{",".join(f"{v:.1f}" for v in xs)}" data-unit="{escape(unit)}" data-nd="{nd}" data-top="{padt}" data-bottom="{H - padb}">']
    for t, txt in zip(ticks, tick_text):
        yy = y(t)
        out.append(f'<line class="ax" x1="{padl}" y1="{yy:.1f}" x2="{W - padr}" y2="{yy:.1f}"/>'
                   f'<text class="yl" x="{padl - 6}" y="{yy + 4:.1f}" text-anchor="end">{txt}</text>')
    if marker is not None and 0 <= marker < n:
        out.append(f'<line class="ax2" x1="{x(marker):.1f}" y1="{padt}" x2="{x(marker):.1f}" y2="{H - padb}" stroke-dasharray="3 3"/>'
                   f'<text x="{x(marker) + 4:.1f}" y="{padt + 9}">now</text>')
    # x labels: a stride that fits the widest label between neighbours, no repeats, no
    # collisions; the last label always survives and so does the first
    plot = W - padl - padr
    widest = max(label_width(l) for l in labels)
    per = plot / (n - 1) if n > 1 else plot
    stride = max(1, math_ceil((widest + 4) / per)) if n > 1 else 1
    cands = [i for i in range(len(labels)) if (i % stride == 0 or i == n - 1) and (i == 0 or labels[i] != labels[i - 1])]

    def extent(i):
        w = label_width(labels[i])
        if i == 0:
            return xs[i], xs[i] + w
        if i == n - 1:
            return xs[i] - w, xs[i]
        return xs[i] - w / 2, xs[i] + w / 2

    def clear(i, j):
        return extent(i)[1] <= extent(j)[0] - 4
    kept = []
    for i in reversed(cands):
        if not kept or clear(i, kept[-1]):
            kept.append(i)
    if cands and cands[0] == 0 and 0 not in kept:
        kept = [i for i in kept if i == n - 1 or clear(0, i)] + [0]
    for i in sorted(kept):
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        out.append(f'<text class="xl" x="{xs[i]:.1f}" y="{H - 9}" text-anchor="{anchor}">{escape(labels[i])}</text>')
    quiet = [sr for sr in series if not sr.get("cls") and sr.get("hue") is None]
    loud = [sr for sr in series if sr.get("cls") or sr.get("hue") is not None]
    end_labels = []                             # (y, x, text, anchor, style) -- dodged below so they never overprint
    for sr in quiet + loud:
        vals = sr.get("values") or []
        pts = [(xs[i], y(v)) for i, v in enumerate(vals) if v is not None]
        if not pts:
            continue
        cls = sr.get("cls") or ("hue" if sr.get("hue") is not None else "")
        col = _series_colour(sr)
        hued = sr.get("hue") is not None
        style = f' style="stroke:{col}"' if hued else ""
        name = escape(sr.get("name") or "")
        if cls in ("me", "pos") and not hued:
            out.append(f'<polygon class="area {cls}" points="{pts[0][0]:.1f},{y(lo):.1f} ' + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + f' {pts[-1][0]:.1f},{y(lo):.1f}"/>')
        vals_json = json.dumps([None if v is None else float(v) for v in vals])
        out.append(f'<polyline class="ln {cls}"{style} data-name="{name}" data-vals="{vals_json}" data-col="{col}" points="'
                   + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + f'"><title>{name}</title></polyline>')
        if not cls:
            continue
        last = max(i for i, v in enumerate(vals) if v is not None)
        every = (value_labels is True and n <= 16)
        for i, v in enumerate(vals):
            if v is None:
                continue
            if hued and i != last and cls != "me":
                continue                        # a race line: only its end is dotted
            fill = f' style="fill:{col}"' if hued else ""
            out.append(f'<circle class="dot {cls}"{fill} cx="{xs[i]:.1f}" cy="{y(v):.1f}" r="{3.6 if i == last else 2.6}"><title>{name} · {escape(labels[i] if i < len(labels) else "")}: {fnum(v, nd)}{unit}</title></circle>')
            if value_labels and (every or i == last):
                txt = f"{fnum(v, nd)}{unit}"
                if i == last:
                    end_labels.append([y(v) - 7, xs[i] + 2, txt, "end", f' style="fill:{col}"' if hued else ""])
                else:
                    out.append(f'<text class="vl" x="{xs[i]:.1f}" y="{y(v) - 7:.1f}" text-anchor="middle">{txt}</text>')
    # the end labels: pushed apart from the top down, then the whole stack lifted if it ran off the bottom
    end_labels.sort(key=lambda t: t[0])
    gap = CHART_FONT_PX + 4                    # +2 was the letter height and read as touching (2026-09-30)
    for k in range(1, len(end_labels)):
        if end_labels[k][0] < end_labels[k - 1][0] + gap:
            end_labels[k][0] = end_labels[k - 1][0] + gap
    if end_labels and end_labels[-1][0] > H - padb - 2:
        shift = end_labels[-1][0] - (H - padb - 2)
        for t in end_labels:
            t[0] -= shift
    for yy, xx, txt, anchor, style in end_labels:
        out.append(f'<text class="vl"{style} x="{xx:.1f}" y="{yy:.1f}" text-anchor="{anchor}">{txt}</text>')
    by_name = {sr.get("name"): sr.get("values") or [] for sr in series}
    stacked = {}
    for nt in notes or []:                                              # UI-H5: numbered story markers
        vals = by_name.get(nt.get("series")) or []
        i = nt.get("x")
        if i is None or i >= len(vals) or vals[i] is None:
            continue
        k = stacked.get((nt.get("series"), i), 0)                       # two notes on one point stack upward
        stacked[(nt.get("series"), i)] = k + 1
        cx, cy = xs[i], y(vals[i]) - 18 * k
        out.append(f'<g class="note"><title>{escape(nt.get("title") or "")}</title><circle cx="{cx:.1f}" cy="{cy:.1f}" r="8"/>'
                   f'<text x="{cx:.1f}" y="{cy + 3.5:.1f}" text-anchor="middle">{int(nt.get("n") or 0)}</text></g>')
    out.append("</svg>")
    if legend:
        out.append('<div class="legend">' + "".join(f'<span><i style="background:{_series_colour(sr)}"></i>{escape(sr.get("name") or "")}</span>' for sr in series) + "</div>")
    # UI-V7: every chart's numbers as a table, for a screen reader and for anyone who wants them
    fmt = lambda v: "—" if v is None else f"{float(v):.{nd}f}{unit}"  # noqa: E731
    out.append('<details class="tview"><summary>view as table</summary><div class="scroller"><table><thead><tr><th></th>'
               + "".join(f"<th>{escape(str(lb))}</th>" for lb in labels) + "</tr></thead><tbody>"
               + "".join(f'<tr><td class="nm">{escape(sr.get("name") or "")}</td>'
                         + "".join(f'<td class="num">{fmt(v)}</td>' for v in (sr.get("values") or [])) + "</tr>" for sr in series)
               + "</tbody></table></div></details>")
    return Markup("".join(out))


def math_ceil(v):
    import math
    return math.ceil(v)


def sparkline(values, hue=None, width=64, height=18):
    """A tiny inline line for a table cell: the values' shape, the last point dotted, no
    axes. Nothing for fewer than two points. In a team's hue when given."""
    from markupsafe import Markup
    pts = [(i, float(v)) for i, v in enumerate(values or []) if v is not None]
    if len(pts) < 2:
        return Markup("")
    n = len(values)
    lo, hi = min(v for _i, v in pts), max(v for _i, v in pts)
    if hi <= lo:
        hi = lo + 1
    def x(i):
        return 2 + i * (width - 6) / (n - 1)
    def y(v):
        return 2 + (height - 4) * (1 - (v - lo) / (hi - lo))
    style = f' style="stroke:hsl({int(hue)} 62% var(--line-l))"' if hue is not None else ""
    fill = f' style="fill:hsl({int(hue)} 62% var(--line-l))"' if hue is not None else ""
    i, v = pts[-1]
    return Markup(f'<svg class="spark" viewBox="0 0 {width} {height}" aria-hidden="true"><polyline{style} points="'
                  + " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in pts) + f'"/><circle{fill} cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.2"/></svg>')


def short_date(value, now=None):
    """'Sep 24' (with the year when it is not this year) -- the axis label a move gets."""
    import datetime as _dt
    dt = parse_time(value)
    if dt is None:
        return str(value or "—")
    local = dt.astimezone()
    ref = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone()
    year = f" {local.year}" if local.year != ref.year else ""
    return f"{local:%b} {local.day}{year}"


# What each season log under data/logs/ is, for the Logs page: (title, one line). Keyed by
# the stem before any _<season> or _<date> suffix. Unknown files fall back to their stem.
LOG_TITLES = {
    "predictions": ("Predictions", "every week's forecast as it was made, canonical or not, kept for scoring later"),
    "decision_log": ("Decision log", "adds, drops, claims and trades this season, with the reasoning at the time"),
    "bid_ledger": ("Bid ledger", "what the model suggested, what was bid, what it cost"),
    "designations": ("Injury designations", "the Saturday injury designations as captured, week by week"),
    "faab_adjustments": ("FAAB adjustments", "budget changes no transaction explains, such as a commissioner's adjustment"),
    "failed_claims": ("Failed claims", "waiver claims that didn't go through"),
    "scoring_settings": ("Scoring settings", "the league's scoring settings, one row each time they change"),
    "as_played_results": ("As-played results", "weeks 1-2 as the league counted them, under the old IDP scoring"),
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


def strip(v, hi, history=None, width=160, height=26):
    """UI-P2: one player's projection drawn as a distribution, on ONE scale 0..hi across `width`
    pixels (a table passes the same hi to every row, so strips compare): the simulation's
    histogram faint behind, the 10th-90th line, the 25th-75th box, a mean tick, and a dot for
    each week already played. A value past the scale sits at its edge. The numbers are also the
    SVG's text (title and aria-label), so the strip never carries a value only as geometry.
    No "chance of zero" pip: nothing on disk is that (player_variance excludes absent weeks and
    its first bin is 0-5.3; tests.test_webui_strip). '' when there is nothing to draw."""
    from markupsafe import Markup, escape
    v = v or {}
    try:
        q = {k: float(v[k]) for k in ("p10", "p25", "p75", "p90")}
        hi = float(hi)
    except (KeyError, TypeError, ValueError):
        return Markup("")
    if hi <= 0:
        return Markup("")
    W, H, mid = float(width), float(height), height / 2.0

    def x(val):
        return round(max(0.0, min(float(val), hi)) / hi * W, 1)
    mean = v.get("mean")
    words = [f"10th {q['p10']:.1f}", f"25th {q['p25']:.1f}"]
    if v.get("p50") is not None:
        words.append(f"median {float(v['p50']):.1f}")
    words += [f"75th {q['p75']:.1f}", f"90th {q['p90']:.1f}"]
    if mean is not None:
        words.append(f"mean {float(mean):.1f}")
    played = [float(h) for h in (history or []) if h is not None]
    if played:
        words.append("weeks played " + ", ".join(f"{h:.1f}" for h in played))
    label = escape(" · ".join(words))
    out = [f'<svg class="dstrip" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{label}"><title>{label}</title>']
    hist = v.get("histogram") or {}
    edges, counts = hist.get("bin_edges") or [], hist.get("counts") or []
    if counts and len(edges) == len(counts) + 1 and max(counts) > 0:
        top = float(max(counts))
        for i, c in enumerate(counts):
            if edges[i] >= hi:
                break
            x0, x1 = x(edges[i]), x(edges[i + 1])
            h = round(c / top * (H - 2), 1)
            out.append(f'<rect class="h" x="{x0}" y="{round(H - h, 1)}" width="{round(max(x1 - x0 - 0.5, 0.5), 1)}" height="{h}"/>')
    out.append(f'<line class="rng" x1="{x(q["p10"])}" y1="{mid}" x2="{x(q["p90"])}" y2="{mid}"/>')
    out.append(f'<rect class="box" x="{x(q["p25"])}" y="{mid - 5}" width="{round(x(q["p75"]) - x(q["p25"]), 1)}" height="10" rx="3"/>')
    if mean is not None:
        out.append(f'<line class="mean" x1="{x(mean)}" y1="{mid - 8}" x2="{x(mean)}" y2="{mid + 8}"/>')
    for h in played:
        out.append(f'<circle class="wk" cx="{x(h)}" cy="{mid}" r="3"/>')
    out.append("</svg>")
    return Markup("".join(out))


# ---- UI-V3: chart primitives. One scale per chart, a hover <title> per mark, a table view
# (the heat grid is itself a table; a percentage bar writes its number), and colour only
# through classes that base.html's --c-* tokens fill -- validated with the dataviz tool in
# both themes (tests.test_webui_primitives pins the values).

def _tview(head, rows):
    from markupsafe import escape
    return ('<details class="tview"><summary>view as table</summary><div class="scroller"><table><thead><tr>'
            + "".join(f"<th>{escape(h)}</th>" for h in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows)
            + "</tbody></table></div></details>")


def _span(values, pad=0.06):
    lo, hi = min(values), max(values)
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    d = (hi - lo) * pad
    return lo - d, hi + d


def slope(rows, left, right, me=None, unit="", nd=1, width=420, height=None, name=str):
    """Each row's value at two moments, both on ONE scale: the owner's line in --c-me, every
    other in the recessive --c-other (identity by the direct label, never by a generated hue).
    Right-hand labels are nudged apart so none overlap. rows: [{name, a, b}]."""
    from markupsafe import Markup, escape
    rows = [r for r in rows if r.get("a") is not None and r.get("b") is not None]
    if not rows:
        return Markup("")
    W, H = width, height or max(160, 24 * len(rows) + 56)
    padt, padb, x1, x2 = 26, 16, 58, width - 150
    lo, hi = _span([float(r["a"]) for r in rows] + [float(r["b"]) for r in rows])

    def y(v):
        return round(padt + (H - padt - padb) * (1 - (float(v) - lo) / (hi - lo)), 1)
    out = [f'<svg class="viz slope" viewBox="0 0 {W} {H}" role="img" aria-label="{escape(left)} to {escape(right)}">',
           f'<text class="yl" x="{x1}" y="14" text-anchor="middle">{escape(left)}</text>',
           f'<text class="yl" x="{x2}" y="14" text-anchor="middle">{escape(right)}</text>']
    order = sorted(rows, key=lambda r: r["name"] == me)                      # the owner drawn last, on top
    placed, last = {}, -1e9
    for yy, r in sorted(((y(r["b"]), r) for r in rows), key=lambda t: t[0]):
        yy = max(yy, last + 12)
        placed[r["name"]], last = yy, yy
    for r in order:
        cls = "sl me" if r["name"] == me else "sl"
        nm = escape(name(r["name"]))
        ya, yb = y(r["a"]), y(r["b"])
        out.append(f'<g><title>{nm}: {fnum(r["a"], nd)}{unit} to {fnum(r["b"], nd)}{unit}</title>'
                   f'<line class="{cls}" x1="{x1}" y1="{ya}" x2="{x2}" y2="{yb}"/>'
                   f'<circle class="{cls}" cx="{x1}" cy="{ya}" r="3"/><circle class="{cls}" cx="{x2}" cy="{yb}" r="3"/>'
                   f'<text class="vl" x="{x1 - 6}" y="{ya + 4}" text-anchor="end">{fnum(r["a"], nd)}</text>'
                   f'<text class="nl{" me" if r["name"] == me else ""}" x="{x2 + 8}" y="{placed[r["name"]] + 4}">{fnum(r["b"], nd)} {nm}</text></g>')
    out.append("</svg>")
    table = [[name(r["name"]), f"{fnum(r['a'], nd)}{unit}", f"{fnum(r['b'], nd)}{unit}",
              f"{float(r['b']) - float(r['a']):+.{nd}f}"] for r in rows]
    return Markup("".join(out) + _tview(["", left, right, "change"], table))


def heat(rows, cols, values, fmt=str, me=None, row_name=str, col_name=str, corner=""):
    """A heat grid that writes its value in every cell: four sequential steps (--c-seq-1..4)
    binned on the grid's own range, the text in the step's own ink token. A missing cell is
    a dash. values: {(row, col): number}. The grid is its own table view."""
    from markupsafe import Markup, escape
    vals = [float(v) for v in values.values() if v is not None]
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
    out = [f'<div class="scroller"><table class="heat2"><thead><tr><th>{escape(corner)}</th>'
           + "".join(f"<th>{escape(col_name(c))}</th>" for c in cols) + "</tr></thead><tbody>"]
    for r in rows:
        mine = ' class="me"' if r == me else ""
        out.append(f'<tr{mine}><th class="nm">{escape(row_name(r))}</th>')
        for c in cols:
            v = values.get((r, c))
            if v is None:
                out.append('<td class="hc h0">—</td>')
                continue
            k = 4 if hi == lo else 1 + min(3, int((float(v) - lo) / (hi - lo) * 4))      # all equal: each is the max (B15)
            text = escape(fmt(v))
            out.append(f'<td class="hc h{k}" title="{escape(row_name(r))} · {escape(col_name(c))}: {text}">{text}</td>')
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return Markup("".join(out))


def dots(bins, per_dot=None, width=420, unit="seasons", hi=(), name=str):
    """A dot histogram: each dot is `per_dot` of the count (chosen so the tallest column holds
    about twelve), columns labelled below, a hover per column (the count and its share).
    Columns named in `hi` wear --c-me, the rest --c-other. bins: [(label, count)]."""
    from markupsafe import Markup, escape
    bins = [(str(lab), int(c or 0)) for lab, c in bins]
    total = sum(c for _lab, c in bins)
    if not bins or not total:
        return Markup("")
    top = max(c for _lab, c in bins)
    per = per_dot or max(1, math_ceil(top / 12))
    r, gap = 5, 3
    n = len(bins)
    colw = max(2 * r + 4, (width - 20) / n)
    tall = max(int(round(c / per)) for _lab, c in bins)
    H = 28 + max(1, tall) * (2 * r + gap) + 20
    out = [f'<svg class="viz dotsh" viewBox="0 0 {width} {H}" role="img" aria-label="dot histogram, each dot {per} {escape(unit)}">']
    for i, (lab, c) in enumerate(bins):
        cx = 10 + colw * i + colw / 2
        k = 0 if c == 0 else max(1, int(round(c / per)))
        cls = "dcol on" if lab in hi else "dcol"
        share = c / total * 100
        circles = "".join(f'<circle cx="{cx:.1f}" cy="{H - 24 - j * (2 * r + gap):.1f}" r="{r}"/>' for j in range(k))
        out.append(f'<g class="{cls}"><title>{escape(name(lab))}: {c:,} {escape(unit)} ({share:.0f}%)</title>'
                   f'<rect class="hit" x="{cx - colw / 2:.1f}" y="0" width="{colw:.1f}" height="{H - 18}"/>{circles}</g>')
        out.append(f'<text class="xl" x="{cx:.1f}" y="{H - 4}" text-anchor="middle">{escape(name(lab))}</text>')
    out.append(f'<text class="yl" x="{width - 4}" y="12" text-anchor="end">each dot {per:,} {escape(unit)}</text></svg>')
    table = [[name(lab), f"{c:,}", f"{c / total * 100:.1f}%"] for lab, c in bins]
    return Markup("".join(out) + _tview(["", unit, "share"], table))


def possessive(name):
    """UI-H5: "Cosmic Badgers'" and "Rocket Panda's"."""
    name = str(name or "")
    return name + ("'" if name.endswith("s") else "'s")


def reliability_chart(rows, width=360, height=320):
    """UI-Q1: a reliability diagram. Forecast probability across, how often it happened up, on
    one 0-100% scale both ways; the diagonal is where a calibrated forecast sits, and each
    bin's grey band is that diagonal +- 2 standard errors at the bin's count -- a dot inside it
    is inside the noise. One dot per non-empty bin, sized by nothing but a hover with the
    numbers; a table view carries every bin, empty ones included."""
    from markupsafe import Markup
    got = [r for r in rows if r.get("n")]
    if not got:
        return Markup("")
    L, R, T, B = 40, 12, 12, 34
    W, H = width - L - R, height - T - B
    x = lambda p: L + p * W                  # noqa: E731
    y = lambda p: T + (1 - p) * H            # noqa: E731
    out = [f'<svg class="viz relia" viewBox="0 0 {width} {height}" role="img" aria-label="reliability diagram: forecast probability against how often it happened">']
    for t in (0, 0.25, 0.5, 0.75, 1.0):
        out.append(f'<line class="grid" x1="{L}" x2="{L + W}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                   f'<text class="yl" x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{int(t * 100)}%</text>'
                   f'<text class="xl" x="{x(t):.1f}" y="{T + H + 16}" text-anchor="middle">{int(t * 100)}%</text>')
    out.append(f'<line class="diag" x1="{x(0):.1f}" y1="{y(0):.1f}" x2="{x(1):.1f}" y2="{y(1):.1f}"/>')
    for r in got:
        f, o, se = r["forecast"], r["observed"], r["se"]
        lo, hi = max(0.0, f - 2 * se), min(1.0, f + 2 * se)
        out.append(f'<rect class="band" x="{x(r["lo"]) + 2:.1f}" width="{(r["hi"] - r["lo"]) * W - 4:.1f}" y="{y(hi):.1f}" height="{y(lo) - y(hi):.1f}"/>')
        out.append(f'<g class="pt"><title>forecasts {int(r["lo"] * 100)}-{int(r["hi"] * 100)}%: {r["n"]} calls, '
                   f'average forecast {f * 100:.0f}%, happened {o * 100:.0f}% (calibrated: {lo * 100:.0f}-{hi * 100:.0f}%)</title>'
                   f'<circle cx="{x(f):.1f}" cy="{y(o):.1f}" r="5"/></g>')
    out.append(f'<text class="xl" x="{L + W / 2:.1f}" y="{height - 4}" text-anchor="middle">forecast chance</text></svg>')
    table = [[f"{int(r['lo'] * 100)}-{int(r['hi'] * 100)}%", str(r["n"]),
              "—" if r["forecast"] is None else f"{r['forecast'] * 100:.0f}%",
              "—" if r["observed"] is None else f"{r['observed'] * 100:.0f}%",
              "—" if r["se"] is None else f"{r['se'] * 100:.0f} pts"] for r in rows]
    return Markup("".join(out) + _tview(["forecasts", "calls", "average forecast", "happened", "standard error"], table))


def fan(labels, bands, unit="", width=640, height=220, nd=1):
    """A fan chart: the 10th-90th and 25th-75th percentile bands (--c-seq-1, --c-seq-2) and the
    median line (--c-me) over the x labels, on one scale with round ticks; a hover per x with
    all five numbers. bands: [{p10, p25, p50, p75, p90}], one per label."""
    from markupsafe import Markup, escape
    keys = ("p10", "p25", "p50", "p75", "p90")
    pts = [(i, b) for i, b in enumerate(bands) if b and all(b.get(k) is not None for k in keys)]
    if len(pts) < 2:
        return Markup("")
    ticks = nice_ticks(min(b["p10"] for _i, b in pts), max(b["p90"] for _i, b in pts))
    lo, hi = ticks[0], ticks[-1]
    padl, padr, padt, padb = 40, 12, 12, 26
    W, H = width, height
    n = len(labels)

    def x(i):
        return round(padl + i * (W - padl - padr) / max(1, n - 1), 1)

    def y(v):
        return round(padt + (H - padt - padb) * (1 - (float(v) - lo) / (hi - lo)), 1)
    out = [f'<svg class="viz fan" viewBox="0 0 {W} {H}" role="img" aria-label="range by {escape(str(labels[0]))} to {escape(str(labels[-1]))}">']
    for t in ticks:
        out.append(f'<line class="ax" x1="{padl}" y1="{y(t)}" x2="{W - padr}" y2="{y(t)}"/>'
                   f'<text class="yl" x="{padl - 6}" y="{y(t) + 4}" text-anchor="end">{fnum(t, 0)}{escape(unit)}</text>')
    for cls, a, b in (("band outer", "p10", "p90"), ("band inner", "p25", "p75")):
        ring = [f"{x(i)},{y(bd[b])}" for i, bd in pts] + [f"{x(i)},{y(bd[a])}" for i, bd in reversed(pts)]
        out.append(f'<polygon class="{cls}" points="{" ".join(ring)}"/>')
    med = " ".join(f"{x(i)},{y(bd['p50'])}" for i, bd in pts)
    out.append(f'<polyline class="med" points="{med}"/>')
    step = (W - padl - padr) / max(1, n - 1)
    for i, bd in pts:
        lab = escape(str(labels[i]))
        u = escape(unit)
        out.append(f'<rect class="hit" x="{x(i) - step / 2:.1f}" y="{padt}" width="{step:.1f}" height="{H - padt - padb}">'
                   f'<title>{lab}: median {fnum(bd["p50"], nd)}{u} · middle half {fnum(bd["p25"], nd)}–{fnum(bd["p75"], nd)} · '
                   f'8 in 10 {fnum(bd["p10"], nd)}–{fnum(bd["p90"], nd)}</title></rect>')
        out.append(f'<text class="xl" x="{x(i)}" y="{H - 8}" text-anchor="middle">{lab}</text>')
    out.append("</svg>")
    table = [[labels[i]] + [fnum(bd[k], nd) for k in keys] for i, bd in pts]
    return Markup("".join(out) + _tview(["", "10th", "25th", "median", "75th", "90th"], table))


def pctbar(p, nd=1):
    """A percentage bar for a table cell: the bar clamped to 0-100%, the number always written."""
    from markupsafe import Markup
    try:
        f = float(p)
    except (TypeError, ValueError):
        return "—"
    w = max(0.0, min(1.0, f)) * 100
    return Markup(f'<span class="pbar"><span class="trk"><i style="width:{w:.1f}%"></i></span><b>{f * 100:.{nd}f}%</b></span>')



SLEEPER_CHAT_LIMIT = 1000     # UNVERIFIED: a conservative message length, not read from Sleeper docs


def chat_parts(text, limit=SLEEPER_CHAT_LIMIT):
    """UI-R3: a post as parts no longer than `limit`, split at line breaks; a single line
    longer than the limit is cut at a space (or hard, when it has none). Nothing is lost."""
    pieces = []
    for line in str(text or "").split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit + 1)
            cut = cut if cut > 0 else limit
            pieces.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        pieces.append(line)
    parts, cur = [], None
    for piece in pieces:
        if cur is None:
            cur = piece
        elif len(cur) + 1 + len(piece) <= limit:
            cur += "\n" + piece
        else:
            parts.append(cur)
            cur = piece
    if cur is not None:
        parts.append(cur)
    return [p for p in parts if p.strip()] or ([] if not str(text or "").strip() else [str(text)])
