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
        "tiles": [tile("expected total", fnum(d.get("expected_total"), 1), "pre-game expectation of the 13 starters"),
                  tile("questionable starters", str(len(q)), "each with a named fallback" if q else "none", "neg" if q else ""),
                  tile("locked in place", str(d.get("pinned", 0)), "starters whose game has kicked off" if d.get("locks_active") else "no games have kicked off"),
                  tile("unfilled slots", str(len(d.get("unfilled") or [])), "no eligible player" if d.get("unfilled") else "every slot filled", "neg" if d.get("unfilled") else "")],
        "sections": [
            table("Starters", [col("slot"), col("name"), col("pos"), col("expected", kind="num"),
                               col("band", "floor–ceiling (p10 · p50 · p90)", "range", nd=0, lo="p10", mid="p50", hi="p90"), col("p_zero", "P(0 pts)", "pct"),
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


def _live_matchup(d):
    jl = d.get("joint_legs") or {}
    league = [dict(team=t, **v) for t, v in (d.get("league") or {}).items() if isinstance(v, dict)]
    league.sort(key=lambda r: -(r.get("projected") or 0))
    return {"title": "Live matchup", "subtitle": f"{d.get('team')} vs {d.get('opponent')} · week {d.get('week')} · as of {human_time(d.get('as_of'))}",
            "tiles": [tile("banked so far", fnum(d.get("banked"), 1), f"{d.get('starters_left')} starters still to play"),
                      tile("projected finish", fnum(d.get("projected"), 1), "if everyone plays (no availability discount, F51)"),
                      tile("P(win head-to-head)", fpct(d.get("p_head_to_head")), f"{fpct(d.get('p_head_to_head_inflated'))} with same-game swings widened",
                           "pos" if (d.get("p_head_to_head") or 0) >= 0.5 else "neg"),
                      tile("P(beat the median)", fpct(d.get("p_beat_median")), f"expected wins this week {fnum(jl.get('expected_wins'), 2)} of 2")],
            "sections": [table("Both legs, drawn jointly", [col("outcome"), col("p", "probability", "pct"), col("independent", "if independent", "pct")],
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
                          note="negative = unlucky on that measure; z and p are withheld below six weeks (F53)"))
    first = seasons[0] if seasons else {}
    sl = first.get("schedule_luck") or {}
    return {"title": "Luck ledger", "subtitle": "five pre-registered measures, each differenced against the league",
            "tiles": [tile("schedule luck", fsigned((sl or {}).get("delta"), 2) if isinstance(sl, dict) else "—", "actual minus expected wins", tone((sl or {}).get("delta") if isinstance(sl, dict) else None)),
                      tile("seasons", str(len(seasons)), "")],
            "sections": secs}


def _odds_history(d):
    rows = []
    for r in d.get("rows") or []:
        rows.append(dict(r, at=human_time(r.get("at")), moves=", ".join(str(m) for m in (r.get("moves") or [])) or "—"))
    return {"title": "Odds history", "subtitle": f"{d.get('team')} · {d.get('n_canonical')} canonical runs of {d.get('n_total')} logged",
            "tiles": [tile("canonical runs", str(d.get("n_canonical") or 0), "scheduled runs only (F56/B5)"),
                      tile("latest playoff %", fpct((rows[-1].get("playoff_pct") or 0) / 100) if rows else "—", f"± {fnum(rows[-1].get('playoff_se'), 2)}" if rows else ""),
                      tile("latest title %", fnum(rows[-1].get("champ_pct"), 1) + "%" if rows else "—", "")],
            "sections": [table("Every canonical run", [col("at", "run"), col("week", kind="num", nd=0), col("playoff_pct", "playoff %", "num"), col("playoff_se", "± se", "num", 2),
                                                       col("d_playoff", "Δ playoff", "signed", 1), col("champ_pct", "title %", "num"), col("d_champ", "Δ title", "signed", 1),
                                                       col("expected_wins", "exp. wins", "num", 2), col("d_wins", "Δ wins", "signed", 2), col("moves", "moves landed in the window")], rows),
                         text("What moved the odds", d.get("causation_note")), text("Why canonical only", d.get("canonical_note"))]}


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
                      tile("points left behind", fnum(sm.get("points_left_behind"), 1), "sum of the wrong calls' costs", "neg" if (sm.get("points_left_behind") or 0) > 0 else "")],
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
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%MZ", "%Y-%m-%dT%H:%MZ"):
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


STATUS_ABBR = {"questionable": "Q", "doubtful": "D", "out": "O", "ir": "IR", "pup": "PUP", "sus": "SUS", "suspended": "SUS",
               "na": "NA", "dnr": "DNR", "cov": "COV", "bye": "BYE"}


def status_abbr(status):
    """The way the app abbreviates it: Questionable -> Q, Doubtful -> D, Out -> O; IR stays IR."""
    s = str(status or "").strip()
    return STATUS_ABBR.get(s.casefold(), s[:3].upper() if s else "")


# ------------------------------------------------------------------------------ charts
def line_chart(series, labels, unit="", nd=1, width=640, height=220, y_min=0.0, y_max=None, marker=None, value_labels=True):
    """One SVG line chart, drawn to one scale: `series` is a list of {name, values, cls}
    (cls 'me' | 'pos' | 'gold' | '' for the quiet grey), `labels` the x labels (one per
    point). The y range gets 12% headroom so the last value label never clips; four
    gridlines carry tick labels; every point has a <title> tooltip; `marker` is an x
    index to draw a dashed 'now' line at. Returns markup (escape nothing but names)."""
    from markupsafe import Markup, escape
    pts_all = [v for sr in series for v in (sr.get("values") or []) if v is not None]
    if not pts_all or not labels:
        return Markup("")
    top = max(pts_all)
    lo = min(pts_all) if y_min is None else min(y_min, min(pts_all))
    hi = y_max if y_max is not None else top + (top - lo) * 0.12 + (0.5 if top == lo else 0)
    if hi <= lo:
        hi = lo + 1
    n = max(len(labels), max(len(sr.get("values") or []) for sr in series))
    padl, padr, padt, padb = 40, 16, 14, 26
    W, H = width, height
    def x(i):
        return padl + (i * (W - padl - padr) / (n - 1) if n > 1 else (W - padl - padr) / 2)
    def y(v):
        return padt + (H - padt - padb) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg class="viz" viewBox="0 0 {W} {H}" role="img" aria-label="chart">']
    for g in range(5):
        v = lo + (hi - lo) * g / 4
        yy = y(v)
        out.append(f'<line class="ax" x1="{padl}" y1="{yy:.1f}" x2="{W - padr}" y2="{yy:.1f}"/>'
                   f'<text x="{padl - 6}" y="{yy + 4:.1f}" text-anchor="end">{fnum(v, 0 if hi - lo >= 8 else 1)}{unit}</text>')
    if marker is not None and 0 <= marker < n:
        out.append(f'<line class="ax2" x1="{x(marker):.1f}" y1="{padt}" x2="{x(marker):.1f}" y2="{H - padb}" stroke-dasharray="3 3"/>'
                   f'<text x="{x(marker) + 4:.1f}" y="{padt + 9}">now</text>')
    step = 1 if n <= 10 else (2 if n <= 20 else max(1, n // 8))
    for i, lab in enumerate(labels):
        if i % step == 0 or i == n - 1:
            anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
            out.append(f'<text x="{x(i):.1f}" y="{H - 8}" text-anchor="{anchor}">{escape(lab)}</text>')
    quiet = [sr for sr in series if not sr.get("cls")]
    loud = [sr for sr in series if sr.get("cls")]
    for sr in quiet + loud:
        vals = sr.get("values") or []
        pts = [(x(i), y(v)) for i, v in enumerate(vals) if v is not None]
        if not pts:
            continue
        cls = sr.get("cls") or ""
        if cls == "me" or cls == "pos":
            out.append(f'<polygon class="area {cls}" points="{pts[0][0]:.1f},{y(lo):.1f} ' + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + f' {pts[-1][0]:.1f},{y(lo):.1f}"/>')
        out.append(f'<polyline class="ln {cls}" points="' + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) + f'"><title>{escape(sr.get("name") or "")}</title></polyline>')
        if cls:
            for i, v in enumerate(vals):
                if v is None:
                    continue
                out.append(f'<circle class="dot {cls}" cx="{x(i):.1f}" cy="{y(v):.1f}" r="{3.6 if i == len(vals) - 1 else 2.6}"><title>{escape(sr.get("name") or "")} · {escape(labels[i] if i < len(labels) else "")}: {fnum(v, nd)}{unit}</title></circle>')
                if value_labels and (n <= 16 or i == len(vals) - 1):
                    anchor = "end" if i == len(vals) - 1 else "middle"
                    out.append(f'<text x="{x(i) + (2 if i == len(vals) - 1 else 0):.1f}" y="{y(v) - 7:.1f}" text-anchor="{anchor}" style="fill:var(--ink);font-weight:600">{fnum(v, nd)}{unit}</text>')
    out.append("</svg>")
    return Markup("".join(out))


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
