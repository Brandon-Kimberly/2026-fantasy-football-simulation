"""F34 free-add study: the 2025 churn measurement, committed (2026-09-04).

  py -3.10 -m scripts.free_add_study

F30's pattern -- measurement only: no constant, no golden, no gate. This script exists
because the readiness audit's 152-zero-cost-adds figure rested on session-scratchpad
scripts, the exact fragility F35 fixed for rate measurement. It re-pulls the completed
2025 league's transaction history from Sleeper (a finished season: the inputs are
frozen server-side, so re-runs reproduce), joins it against the committed
data/logs/season_2025.json matchups (weekly active rosters, starters, realized points),
and derives everything the F34 build arc (scheduled at the F32 unlock) will calibrate
against:

  * per-team free-add counts     -> the 2025 prior for a per-manager add_activity
                                    parameter (blended with the live 2026 decision log,
                                    F31's exact pattern)
  * weekly timing curve          -> when zero-cost churn actually happens
  * position mix of added players
  * retention curve              -> the "61% started within 2 weeks" figure, pinned
                                    with its exact window definition
  * drop-selection behavior      -> what managers actually cut (trailing realized ppg
                                    rank, position match, recent-starter share)
  * active-roster occupancy      -> the IR-economics half: how often a team was at
                                    full active capacity (an add REQUIRED a drop) vs
                                    had space

Results are printed and written to data/logs/free_add_study_2025.json (committed, so
the derivation is reviewable without a network). The script self-checks against the
F31/F35 committed aggregates (99 claims, 152 free-agent transactions, 11 trades, 728 FAAB)
and says so loudly if the re-pull disagrees with what the audit recorded.
"""
import datetime as _dt
import json
import sys
from collections import Counter

import requests

from fantasy_sim.config import BASE_URL, normalize_position
from fantasy_sim.storage import load_json

SEASON_LOG = "data/logs/season_2025.json"
PLAYERS_CACHE = "data/current/sleeper_players_cache.json"
OUT_PATH = "data/logs/free_add_study_2025.json"

# The audit's committed aggregates (AUDIT_PLAN.md F31/F34; behavior_check REAL_2025).
# NOTE the first run's correction (2026-09-04): the audit's "152 zero-cost adds" was the
# count of free_agent TRANSACTIONS -- 122 of them add a player, 30 are drop-only roster
# management. The retention figure ("61% of all adds started within 2 weeks") reproduces
# exactly on the corrected 221-add base (99 paid + 122 free), confirming 122 is the real
# add count. 152 stays here as the self-check target because it is what the re-pull must
# reproduce; the study reports both numbers.
EXPECTED = {"waiver_claims": 99, "free_agent_txs": 152, "trades": 11, "faab_spent": 728}

# Weeks pulled: Sleeper serves transactions per leg; 1-18 covers the full season.
WEEKS = range(1, 19)
RETENTION_HORIZONS = (1, 2, 3)   # started within add week + (h-1) following weeks
TRAILING_WEEKS = 3               # drop-selection: trailing realized-ppg window


def fetch_transactions(league_id):
    txs = []
    for wk in WEEKS:
        resp = requests.get(f"{BASE_URL}/league/{league_id}/transactions/{wk}", timeout=30)
        resp.raise_for_status()
        for t in resp.json() or []:
            t["_pulled_week"] = wk
            txs.append(t)
    return txs


def build_weekly_tables(matchups):
    """Per (week, roster_id): active player set, starter set, realized points."""
    active, starters, points = {}, {}, {}
    for wk_str, rows in matchups.items():
        wk = int(wk_str)
        for r in rows:
            key = (wk, r["roster_id"])
            active[key] = set(r.get("players") or [])
            starters[key] = set(r.get("starters") or [])
            points[key] = r.get("players_points") or {}
    return active, starters, points


def main(argv=None):
    season = load_json(SEASON_LOG)
    league_id = season["league_id"]
    roster_map = {int(k): v for k, v in season["roster_map"].items()}
    matchups = season["matchups"]
    active, starters, points = build_weekly_tables(matchups)
    max_week = max(int(w) for w in matchups)
    players_db = load_json(PLAYERS_CACHE)

    def pos_of(pid):
        p = players_db.get(str(pid))
        if not p:
            # Team-defense ids are alpha ("BAL"); anything else missing from the cache.
            return "DEF" if str(pid).isalpha() else "UNK"
        raw = p.get("position") or "UNK"
        # Keep DEF visible: normalize_position collapses it to FLEX, which would hide
        # the 2025 DEF-streaming channel (31 of 122 free adds) -- a channel that does
        # not exist in the 2026 IDP format. The format caveat is recorded in the output.
        return "DEF" if raw == "DEF" else normalize_position(raw)

    def name_of(pid):
        p = players_db.get(str(pid))
        return (p.get("full_name") or f"id:{pid}") if p else str(pid)

    raw_txs = fetch_transactions(league_id)
    txs = [t for t in raw_txs if t.get("status") == "complete"]

    waivers = [t for t in txs if t.get("type") == "waiver" and t.get("adds")]
    free_all = [t for t in txs if t.get("type") == "free_agent"]
    free = [t for t in free_all if t.get("adds")]
    trades = [t for t in txs if t.get("type") == "trade"]
    faab_spent = sum((t.get("settings") or {}).get("waiver_bid") or 0 for t in waivers)
    failed_claims = sum(len(t.get("adds") or {}) for t in raw_txs
                        if t.get("type") == "waiver" and t.get("status") == "failed")

    # ---- self-check against the committed audit aggregates ----------------------------
    got = {"waiver_claims": sum(len(t["adds"]) for t in waivers),
           "free_agent_txs": len(free_all),
           "trades": len(trades), "faab_spent": faab_spent}
    mismatches = {k: (got[k], v) for k, v in EXPECTED.items() if got[k] != v}
    got["free_adds"] = sum(len(t["adds"]) for t in free)          # the corrected count
    got["free_drop_only_txs"] = len(free_all) - len(free)
    got["failed_waiver_claims"] = failed_claims

    # ---- flatten to per-add / per-drop event rows -------------------------------------
    def events(tx_list, kind):
        out = []
        for t in tx_list:
            wk = t.get("leg") or t["_pulled_week"]
            for pid, rid in (t.get("adds") or {}).items():
                out.append({"kind": kind, "week": int(wk), "roster_id": rid, "pid": str(pid),
                            "drops": {str(dp): dr for dp, dr in (t.get("drops") or {}).items()},
                            "bid": (t.get("settings") or {}).get("waiver_bid")})
        return out

    free_adds = events(free, "free")
    paid_adds = events(waivers, "paid")
    all_adds = free_adds + paid_adds

    # ---- per-team counts and weekly timing --------------------------------------------
    per_team_free = Counter(roster_map.get(e["roster_id"], str(e["roster_id"])) for e in free_adds)
    timing_free = Counter(e["week"] for e in free_adds)
    timing_paid = Counter(e["week"] for e in paid_adds)
    w14_share = sum(c for w, c in timing_free.items() if w <= 4) / max(1, len(free_adds))

    # ---- position mix -----------------------------------------------------------------
    pos_mix_free = Counter(pos_of(e["pid"]) for e in free_adds)
    pos_mix_paid = Counter(pos_of(e["pid"]) for e in paid_adds)

    # ---- retention: started within N weeks of the add ---------------------------------
    def retention(adds):
        rates = {}
        for h in RETENTION_HORIZONS:
            eligible = [e for e in adds if e["week"] + h - 1 <= max_week]
            hit = sum(1 for e in eligible
                      if any(e["pid"] in starters.get((w, e["roster_id"]), set())
                             for w in range(e["week"], min(e["week"] + h, max_week + 1))))
            rates[f"within_{h}w"] = {"started": hit, "eligible": len(eligible),
                                     "rate": round(hit / len(eligible), 3) if eligible else None}
        return rates

    retention_all = retention(all_adds)
    retention_free = retention(free_adds)
    retention_paid = retention(paid_adds)

    # ---- drop-selection behavior ------------------------------------------------------
    # For every drop attached to an add: was the cut the roster's realized-ppg tail?
    drop_rows = []
    for e in all_adds:
        for dp, dr in e["drops"].items():
            if dr != e["roster_id"]:
                continue
            wk = e["week"]
            trailing = {}
            for p in active.get((max(1, wk - 1), dr), set()):
                vals = [points[(w, dr)].get(str(p)) for w in range(max(1, wk - TRAILING_WEEKS), wk)
                        if (w, dr) in points and str(p) in points[(w, dr)]]
                if vals:
                    trailing[str(p)] = sum(vals) / len(vals)
            row = {"kind": e["kind"], "week": wk, "pid": dp,
                   "pos_match": pos_of(dp) == pos_of(e["pid"]),
                   "started_prior_2w": any(dp in starters.get((w, dr), set())
                                           for w in range(max(1, wk - 2), wk))}
            if str(dp) in trailing and len(trailing) >= 4:
                rank = sorted(trailing.values()).index(trailing[str(dp)]) + 1
                row["trailing_ppg"] = round(trailing[str(dp)], 2)
                row["trailing_rank_pctile"] = round(rank / len(trailing), 3)  # low = worst
            drop_rows.append(row)

    with_rank = [r for r in drop_rows if "trailing_rank_pctile" in r]
    drop_summary = {
        "adds_with_a_drop": len(drop_rows),
        "adds_total": len(all_adds),
        "drop_share": round(len(drop_rows) / len(all_adds), 3) if all_adds else None,
        "pos_match_share": round(sum(r["pos_match"] for r in drop_rows) / len(drop_rows), 3) if drop_rows else None,
        "started_prior_2w_share": round(sum(r["started_prior_2w"] for r in drop_rows) / len(drop_rows), 3) if drop_rows else None,
        "trailing_rank_measured_n": len(with_rank),
        "bottom_quartile_share": round(sum(r["trailing_rank_pctile"] <= 0.25 for r in with_rank) / len(with_rank), 3) if with_rank else None,
        "median_trailing_rank_pctile": round(sorted(r["trailing_rank_pctile"] for r in with_rank)[len(with_rank) // 2], 3) if with_rank else None,
    }

    # ---- active-roster occupancy (the IR-economics half) ------------------------------
    sizes = [len(v) for v in active.values()]
    cap = max(sizes) if sizes else 0   # the active capacity actually observed
    occupancy = {
        "team_weeks": len(sizes),
        "active_capacity_observed": cap,
        "mean_active": round(sum(sizes) / len(sizes), 2) if sizes else None,
        "share_at_full_active": round(sum(1 for s in sizes if s >= cap) / len(sizes), 3) if sizes else None,
        "share_2plus_below": round(sum(1 for s in sizes if s <= cap - 2) / len(sizes), 3) if sizes else None,
        "note": "sizes are the matchup 'players' list, which the observed max (18 = 16 "
                "active + 2 reserve) shows INCLUDES reserve slots. share_at_full_active "
                "is the fraction of team-weeks where an add required a cut.",
    }

    lg = requests.get(f"{BASE_URL}/league/{league_id}", timeout=30).json()
    slots = {"active_slots": len(lg.get("roster_positions") or []),
             "reserve_slots": (lg.get("settings") or {}).get("reserve_slots"),
             "starting_slots": sum(1 for x in (lg.get("roster_positions") or []) if x != "BN")}

    result = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "league_id": league_id, "season": season["season"], "weeks_covered": max_week,
        "format_caveat": "2025 ran a NON-IDP format (QB/2RB/2WR/TE/3FLEX/K/DEF, 16 active "
                         "+ 2 reserve); 31 of the 122 free adds were DEF streamers, a "
                         "channel absent from the 2026 IDP league. Behavioral rates "
                         "(activity, timing, retention, drop selection) transfer as "
                         "priors; the position mix does NOT transfer directly.",
        "slots": slots,
        "totals": got, "self_check_mismatches": mismatches,
        "per_team_free_adds": dict(sorted(per_team_free.items(), key=lambda kv: -kv[1])),
        "timing_free_by_week": {str(w): timing_free.get(w, 0) for w in range(1, max_week + 1)},
        "timing_paid_by_week": {str(w): timing_paid.get(w, 0) for w in range(1, max_week + 1)},
        "free_weeks_1_4_share": round(w14_share, 3),
        "position_mix_free": dict(pos_mix_free.most_common()),
        "position_mix_paid": dict(pos_mix_paid.most_common()),
        "retention": {"all_adds": retention_all, "free_adds": retention_free,
                      "paid_adds": retention_paid,
                      "definition": "started = appeared in the adding roster's starters in "
                                    "any of the add week + (h-1) following weeks"},
        "drop_selection": drop_summary,
        "occupancy": occupancy,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"F34 free-add study -- 2025 league {league_id}, weeks 1-{max_week}")
    print(f"totals: {got}")
    if mismatches:
        print(f"[SELF-CHECK FAILED] re-pull disagrees with the committed audit aggregates: "
              f"{mismatches} (got, expected) -- investigate before trusting this run")
    else:
        print("[SELF-CHECK OK] matches the committed F31/F35 aggregates exactly")
    print(f"per-team free adds: {result['per_team_free_adds']}")
    print(f"free adds weeks 1-4 share: {result['free_weeks_1_4_share']}")
    print(f"position mix (free): {result['position_mix_free']}")
    for scope in ("all_adds", "free_adds", "paid_adds"):
        print(f"retention {scope}: {result['retention'][scope]}")
    print(f"drop selection: {drop_summary}")
    print(f"occupancy: {occupancy}")
    print(f"written -> {OUT_PATH}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
