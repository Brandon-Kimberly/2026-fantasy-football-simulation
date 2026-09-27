"""webui.render -- turn what the tools produce into something a person can read.

Two inputs, two pure functions, no Flask:

  console_blocks(text)  a tool's raw stdout -> a list of blocks. Engine chatter ([INFO],
                        PRE-FLIGHT, the VEGAS STALE / BANKED RECORD warnings) is folded
                        into one collapsible block; ALL-CAPS lines become headings; runs
                        of column-aligned lines become tables; the 'logged -> path' line
                        becomes a record link; everything else stays a preformatted
                        paragraph with its indentation intact.

  record_view(data)     a tool's JSON record -> {tool, title, subtitle, tiles, sections}.
                        Every decision tool writes one (docs/WEB_UI.md 2.4), and the record
                        is the readable artefact: a per-tool spec picks the headline
                        numbers and the tables; a generic walk renders any record the
                        specs do not know, so a new tool is never a blank page.

Team names pass through untouched -- they are pseudonyms on disk and the templates apply
the overlay (`|real`) at render time. Nothing here reads the filesystem.
"""
import re

CHATTER_PREFIXES = ("[INFO]", "[PRE-FLIGHT", "[>>>]", "[SUCCESS]", "[EXPORT COMPLETE]",
                    "ERROR | VEGAS", "WARNING | BANKED", "INFO |", "[NOTE] window", "Imputed whitelisted")
RECORD_RE = re.compile(r"(?:logged|report|chart|written|recorded|digest|html)\s*->\s*(\S+)")
HEADING_RE = re.compile(r"^\s{0,2}(?:[A-Z][A-Z0-9/&'.]*)(?:\s+[A-Z0-9][A-Z0-9/&'.,-]*){0,7}(?:\s+--.*|:)?\s*$")
UNDERLINE_RE = re.compile(r"^\s*[=\-]{4,}\s*$")
SPLIT_RE = re.compile(r"\s{2,}")


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
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) if v else "—"
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items()) if v else "—"
    if v is None or v == "":
        return "—"
    return v


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
            blocks.append({"kind": "record", "path": m.group(1), "text": ln.strip()})
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
def col(key, label=None, kind="text", nd=1):
    return {"key": key, "label": label or key.replace("_", " "), "kind": kind, "nd": nd}


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
    return {"text": _join(v), "num": False, "tone": ""}


def table(title, columns, rows, note=None, me_key=None):
    rows = rows or []
    return {"kind": "table", "title": title, "note": note, "me_key": me_key,
            "columns": columns,
            "rows": [{"cells": [_cell(r, c) for c in columns], "me": (r.get(me_key) if me_key and isinstance(r, dict) else None)}
                     for r in rows if isinstance(r, dict)]}


def kv(title, items, note=None):
    return {"kind": "kv", "title": title,
            "items": [(k, _join(v) if not isinstance(v, float) else fnum(v, 2)) for k, v in items], "note": note}


def text(title, body):
    return {"kind": "text", "title": title, "text": body or ""}


def tile(k, v, s="", tn=""):
    return {"k": k, "v": v, "s": s, "tone": tn}


# ---- per-tool specs ------------------------------------------------------------------
def _lineup(d):
    q = d.get("questionable_starters") or []
    return {
        "title": "Optimal lineup", "subtitle": f"{d.get('team')} · week {d.get('week')}",
        "tiles": [tile("expected total", fnum(d.get("expected_total"), 1), "pre-game expectation of the 13 starters"),
                  tile("questionable starters", str(len(q)), "with a named fallback each", "neg" if q else ""),
                  tile("pinned", str(d.get("pinned", 0)), "locked starters kept in place" if d.get("locks_active") else "no locks active"),
                  tile("unfilled", str(len(d.get("unfilled") or [])), "slots with no eligible player", "neg" if d.get("unfilled") else "")],
        "sections": [
            table("Starters", [col("slot"), col("name"), col("pos"), col("expected", kind="num"), col("p10", kind="num", nd=0),
                               col("p50", kind="num", nd=0), col("p90", kind="num", nd=0), col("p_zero", "P(zero)", "pct"),
                               col("margin", "margin over bench", "signed", 1), col("alternative", "best alternative"), col("flag", kind="flag")],
                  d.get("lineup")),
            table("Questionable starters", [col("name"), col("slot"), col("expected", kind="num"), col("fallback"),
                                            col("fallback_expected", "fallback exp.", "num"), col("give_up", "cost of sitting", "num")], q)
            if q else None,
            table("Bench", [col("name"), col("pos"), col("expected", kind="num"), col("available", kind="bool"), col("reason"), col("flag", kind="flag")],
                  d.get("bench")),
            text("Method", d.get("note")),
        ]}


def _matchup(d):
    cons = d.get("constructions") or {}
    order = d.get("ranking_by_p_beat_opponent") or list(cons)
    rows = [dict(construction=k, **{kk: vv for kk, vv in (cons.get(k) or {}).items() if kk != "lineup"}) for k in order if k in cons]
    best = cons.get(order[0]) if order and order[0] in cons else {}
    secs = [table("Constructions", [col("construction"), col("mean", kind="num"), col("sd", kind="num"), col("p_beat_opponent", "P(beat opponent)", "pct"),
                                    col("p_beat_median", "P(beat median)", "pct"), col("margin_mean", "margin", "signed", 1), col("margin_sd", "margin sd", "num")],
                  rows, note="ranked by P(beat opponent); the first row is the construction the tool recommends")]
    for k in order:
        if k in cons and cons[k].get("lineup"):
            secs.append(table(f"Lineup — {k}", [col("slot"), col("name"), col("nfl_team", "NFL"), col("expected", kind="num"), col("sd", kind="num"), col("flag", kind="flag")],
                              cons[k]["lineup"]))
    secs.append(table("Opponent's lineup" + (" (assumed: his max-expectation lineup)" if d.get("opponent_lineup_assumed") else ""),
                      [col("slot"), col("name"), col("expected", kind="num")], d.get("opponent_lineup")))
    secs.append(text("Method", d.get("note")))
    return {"title": "Matchup lineups", "subtitle": f"{d.get('team')} vs {d.get('opponent')} · week {d.get('week')}",
            "tiles": [tile("best construction", str(order[0]) if order else "—", "by P(beat opponent)"),
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
                      tile("holes next week", str(len(d.get("holes_next_week") or [])), _join(d.get("holes_next_week")) if d.get("holes_next_week") else ""),
                      tile("targets", str(len(rows)), "ranked by value over replacement")],
            "sections": [table("Targets", [col("name"), col("pos"), col("team", "NFL"), col("tier", kind="num", nd=0), col("mean", "season mean", "num"),
                                           col("vorp", "VORP", "signed", 1), col("fills"), col("incumbent"), col("week_mean", "this week", "num"),
                                           col("week_p90", "p90", "num", 0), col("week_p_zero", "P(zero)", "pct"), col("bid_point", "bid v2", "num", 0),
                                           col("bid_band", "band"), col("bid_v1", "bid v1", "num", 0), col("bye", kind="num", nd=0), col("injury_status", "status", "flag")],
                               rows, note="bid v2 prices the margin over the fallback against actual competition; v1 is the older heuristic. Neither is validated (F61)."),
                         text("Caveat", d.get("caveat"))]}


def _roster_grades(d):
    teams = (d.get("league") or {}).get("teams") or []
    return {"title": "Roster grades", "subtitle": f"week {(d.get('league') or {}).get('week')}",
            "tiles": [tile("teams", str(len(teams)), "ranked by lineup VORP")],
            "sections": [table("League", [col("rank", kind="num", nd=0), col("team"), col("lineup_vorp", "lineup VORP", "num"), col("depth_vorp", "depth VORP", "num"),
                                          col("optimal_score", "optimal score", "num"), col("tier1_starters", "tier-1 starters", "num", 0),
                                          col("starters_below_replacement", "below replacement", "num", 0), col("holes", kind="num", nd=0)],
                               teams, me_key="team")]}


def _find_trades(d):
    return {"title": "Trade targets", "subtitle": f"{d.get('team')} · week {d.get('week')}",
            "tiles": [tile("buy ideas", str(len(d.get("buy") or [])), "buried players who would start for me"),
                      tile("sell ideas", str(len(d.get("sell") or [])), "my surplus with a buyer"),
                      tile("excluded (pending)", str(len(d.get("excluded_pending") or [])), "players in a pending trade (T3)")],
            "sections": [table("Buy", [col("with"), col("target"), col("fills_my_slot", "fills"), col("i_give"), col("i_get"), col("my_gain", kind="signed"),
                                       col("their_gain", kind="signed"), col("worth_proposing", "worth it", "bool"), col("acceptable", kind="bool"),
                                       col("their_playoff_pct", "their playoff %", "num"), col("willingness", kind="num", nd=2), col("simulated", kind="bool")],
                               d.get("buy"), me_key="with"),
                         table("Sell", [col("buyer"), col("they_want"), col("they_give"), col("my_gain", kind="signed"), col("their_gain", kind="signed"),
                                        col("worth_proposing", "worth it", "bool"), col("acceptable", kind="bool"), col("their_playoff_pct", "their playoff %", "num"),
                                        col("willingness", kind="num", nd=2), col("simulated", kind="bool")], d.get("sell")),
                         text("Contention", d.get("contention_note")), text("Method", d.get("note"))]}


def _compare(d):
    a, b = d.get("a") or {}, d.get("b") or {}
    rows = [dict(player=d.get("a_name"), **a), dict(player=d.get("b_name"), **b)]
    return {"title": "Start A or B", "subtitle": f"{d.get('a_name')} vs {d.get('b_name')} · week {d.get('week')}",
            "tiles": [tile(f"P({d.get('a_name')} > {d.get('b_name')})", fpct(d.get("p_a")), f"± {fpct(d.get('se_p'))}" if d.get("se_p") else "",
                           "pos" if (d.get("p_a") or 0) > (d.get("p_b") or 0) else "neg"),
                      tile("P(B > A)", fpct(d.get("p_b")), f"tie {fpct(d.get('p_tie'))}"),
                      tile("mean difference", fsigned(d.get("mean_diff")), "A minus B", tone(d.get("mean_diff"))),
                      tile("sample", str(d.get("n")), d.get("path") or "")],
            "sections": [table("Distributions", [col("player"), col("mean", kind="num"), col("p10", kind="num"), col("p25", kind="num"), col("p50", kind="num"),
                                                 col("p75", kind="num"), col("p90", kind="num"), col("p_zero", "P(zero)", "pct")], rows),
                         text("Note", d.get("note"))]}


def _paired(d, what):
    teams = d.get("teams") or {}
    rows = []
    for name, t in teams.items():
        rows.append({"team": name, "side": t.get("side"),
                     "champ_with": (t.get("champ_pct") or {}).get("with"), "champ_without": (t.get("champ_pct") or {}).get("without"),
                     "champ_delta": (t.get("champ_pct") or {}).get("delta"), "champ_se": (t.get("champ_pct") or {}).get("se"),
                     "playoff_with": (t.get("playoff_pct") or {}).get("with"), "playoff_without": (t.get("playoff_pct") or {}).get("without"),
                     "playoff_delta": (t.get("playoff_pct") or {}).get("delta"), "playoff_se": (t.get("playoff_pct") or {}).get("se"),
                     "wins_delta": (t.get("expected_wins") or {}).get("delta")})
    order = {"a": 0, "mover": 0, "b": 1, "bystander": 2}
    rows.sort(key=lambda r: (order.get(str(r["side"]), 3), -(abs(r["playoff_delta"] or 0))))
    principals = [r for r in rows if order.get(str(r["side"]), 3) < 2]
    tiles = []
    for r in principals[:2]:
        tiles.append(tile(f"{r['team']} playoff", fsigned(r["playoff_delta"]), f"± {fnum(r['playoff_se'], 2)} · champ {fsigned(r['champ_delta'])}", tone(r["playoff_delta"])))
    tiles.append(tile("simulations", f"{d.get('n_sims')}", f"{d.get('batches')} paired batches"))
    spec = d.get(what) or {}
    return {"title": "Paired evaluation", "subtitle": (f"{spec.get('team_a')} gives {_join(spec.get('a_gives'))} · {spec.get('team_b')} gives {_join(spec.get('b_gives'))}"
                                                        if what == "trade" else f"{spec.get('team')}: add {_join(spec.get('adds'))} · drop {_join(spec.get('drops'))}"),
            "tiles": tiles,
            "sections": [table("Every team, with minus without", [col("team"), col("side"), col("playoff_delta", "playoff Δ", "signed", 2), col("playoff_se", "± se", "num", 2),
                                                                   col("playoff_with", "with", "num"), col("playoff_without", "without", "num"),
                                                                   col("champ_delta", "champ Δ", "signed", 2), col("champ_se", "± se", "num", 2),
                                                                   col("wins_delta", "exp. wins Δ", "signed", 2)], rows, me_key="team"),
                         kv("The " + what, list(spec.items())), text("Method", d.get("note"))]}


def _calendar(d):
    cal, crunch = d.get("calendar") or {}, d.get("crunch") or {}
    holes = cal.get("holes") or {}
    rows = [dict(r, holes=", ".join(holes.get(str(r.get("week")), []))) for r in cal.get("rows") or []]
    return {"title": "Roster calendar", "subtitle": f"{cal.get('team')} · weeks {_join(cal.get('weeks')[:1]) if cal.get('weeks') else ''}–{cal.get('weeks', [''])[-1]}",
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
                         table("Designations on both rosters", [col("name"), col("side"), col("nfl_team", "NFL"), col("expected", kind="num"), col("flag", kind="flag")], d.get("designations")),
                         table("Stacks", [col("game"), col("side"), col("team"), col("n", kind="num", nd=0), col("sum", kind="num"), col("players")], d.get("stacks")),
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
            sections.append(text(k.replace("_", " "), _join(v)))
    return {"title": str(d.get("tool") or "Record").replace("_", " "), "subtitle": "", "tiles": [],
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
