"""
fantasy_sim.bid_ledger

What I suggested, what I bid, what it cost, and whether I got him.

B14. Every week-3 claim was priced by intuition after the Roquan overpay, and nothing
recorded *suggested vs placed vs winning vs outcome*. Without that record F61 cannot be
settled -- and F61 is the finding that says settling it is the ONLY way to know whether
any bid heuristic here works.

**WHY THIS IS NOT MORE FIELDS ON THE DECISION LOG.** Measured before building it: all 26
waiver rows in `decision_log.jsonl` are COMPLETED transactions, because a lost waiver
claim never becomes a transaction at all. That log is a record of WINS ONLY. It can never
report the claims I lost, which is exactly the half that calibrates a bid. So a row is
written HERE at bid time, before any outcome exists, and reconciled against the decision
log afterwards.

**THREE THINGS THAT WOULD QUIETLY CORRUPT THE CALIBRATION** and are therefore handled
explicitly:

1. **Absence is UNRESOLVED, not a loss.** The waiver run may not have happened, or nobody
   claimed him. Treating absence as a loss would invent losses and flatter whichever
   heuristic bid low.
2. **Matching is by `player_id`.** The raw cache carries 220 colliding names, seven of
   them involving a player rostered in this league (B17).
3. **The censoring survives.** A winning bid is an upper bound on the price, never the
   price, so scoring goes through `decisions.score_bid_suggestion` rather than averaging
   an absolute distance that means four different things.
4. **A RAISED bid is one claim, not two** (F64, 2026-09-23). The owner placed $25, then
   raised to $29 before the daily run. Both rows are appended, both match the same
   `(player_id, week)`, and both were being reconciled and scored against one outcome --
   once at a price that was never live. `live_rows` collapses each `(player_id, week)` to
   its LATEST row by `placed_at`; the earlier ones survive in the file and are readable
   through `superseded_rows`, because "how often is a bid revised, and which way" is a
   question this ledger should still be able to answer. Append-only stays append-only:
   nothing is mutated or deleted.
"""
import json
import logging
import os
from datetime import datetime

from fantasy_sim.decisions import score_bid_suggestion
from fantasy_sim.storage import BID_LEDGER_FILE

# Below this many RESOLVED claims the ledger reports numbers but names no winner. F61
# measured correlation(VORP, winning bid) = -0.136 at n = 26; declaring a victor on a
# handful would be the same noise-fitting that finding exists to warn against.
MIN_CLAIMS_FOR_A_VERDICT = 15


def record_bid(row, path=BID_LEDGER_FILE):
    """Append one claim AT BID TIME. Returns 1, or 0 if it could not be written.

    A ledger is a record, not a dependency -- the same contract as
    `sync.append_projection_log`. A failure here must never cost the owner a claim.
    """
    try:
        out = dict(row)
        out.setdefault("placed_at", datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        # The outcome genuinely does not exist yet. None is the honest value; False would
        # be a claim about a waiver run that has not happened.
        out.setdefault("won", None)
        out.setdefault("winning_bid_if_visible", None)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(out, sort_keys=True) + "\n")
        return 1
    except Exception as ex:
        logging.warning("BID LEDGER: could not append a row to %s (%s). The claim is "
                        "unaffected; only the calibration record is lost.", path, ex)
        return 0


def _claim_key(row):
    return (str(row.get("player_id")), row.get("week"))


def _placed_sort_key(row):
    """Later sorts higher. A row with no `placed_at` sorts LOWEST, so it can never
    supersede one that has a timestamp -- an undated row cannot be shown to be later.
    """
    stamp = row.get("placed_at")
    return (stamp is not None, stamp or "")


def live_rows(rows):
    """One row per (player_id, week): the LATEST bid placed on that claim.

    F64. Ordered by `placed_at`, not by file order -- rows arrive in order today, but an
    append-only log read by timestamp survives a backfill, and sorting by arrival instead
    of by time is the exact mistake `decision_scorecard` made with file paths.
    """
    latest = {}
    for row in rows or []:
        key = _claim_key(row)
        if key not in latest or _placed_sort_key(row) > _placed_sort_key(latest[key]):
            latest[key] = row
    return list(latest.values())


def superseded_rows(rows):
    """The earlier bids on claims that were revised. Kept, never scored."""
    live = {id(r) for r in live_rows(rows)}
    return [r for r in (rows or []) if id(r) not in live]


def _parse_stamp(value):
    """An ISO-8601 stamp to a datetime, or None. Both logs write `...Z`."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip().replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


# How far apart a bid and its transaction may sit and still be the same claim. A claim is
# submitted, waits for the next daily 09:00 run, and may be RAISED in between -- the real
# case that produced F65 had the transaction 18 hours BEFORE the ledger row, and an
# observed rival claim waited two days to process. Four days covers that with room, and
# stays well inside the seven that separate one week's claim on a player from the next --
# which is what keeps F64's "same player, different week is a different claim" true.
MATCH_WINDOW_DAYS = 4


def reconcile(ledger_rows, decision_rows):
    """Fill `won` and `winning_bid_if_visible` from completed transactions.

    Matching is on `player_id` plus TIME PROXIMITY, not on (player_id, week) -- F65.

    The week cannot be used. Sleeper stamps a transaction with the `leg` at SUBMISSION and
    the ledger records `current_week` at BID time, and with `daily_waivers: 1` a claim
    routinely sits across a week boundary. Every real claim placed on 2026-09-23 was
    logged as week 3 and returned by Sleeper as week 2, so every one of them resolved to
    nothing while the ledger reported the honest-looking `no resolved claims yet`.

    Widening the week to +/-1 was rejected: it would let two claims a week apart on the
    same player cross-match, breaking F64's rule that those are different claims. Time
    separates them; the week cannot.

    Proximity, NOT ordering. A transaction's `created` is its submission and survives an
    edit, so a bid RAISED before the run leaves the transaction stamped BEFORE the ledger
    row carrying the live price. Any "the transaction must come after the bid" rule would
    reject exactly the claim this was written for.

    A row with no `placed_at` (written before F64 added one) falls back to an exact week
    match, so old rows keep resolving rather than silently stopping.

    A claim the decision log does not mention stays `won: None` -- absence is not a loss.
    """
    by_pid = {}
    for r in decision_rows or []:
        if r.get("type") != "waiver":
            continue
        for add in (r.get("adds") or []):
            pid = add.get("player_id")
            if pid is not None:
                by_pid.setdefault(str(pid), []).append(r)

    out = []
    for row in ledger_rows or []:
        row = dict(row)
        hit = _best_match(row, by_pid.get(str(row.get("player_id"))) or [])
        if hit is None:
            row["won"], row["winning_bid_if_visible"] = None, None
            row["bid_mismatch"] = None
        else:
            row["won"] = bool(hit.get("is_mine"))
            row["winning_bid_if_visible"] = hit.get("faab_bid")
            # The ledger records INTENT; the transaction records what Sleeper charged.
            # When they disagree on a claim I WON, the calibration would otherwise be
            # scored against a bid that was never placed, so surface it.
            charged = hit.get("faab_bid")
            placed = row.get("bid_placed")
            row["bid_mismatch"] = (charged if row["won"] and charged is not None
                                   and placed is not None and charged != placed else None)
        out.append(row)
    return out


def _best_match(row, candidates):
    """The transaction most plausibly belonging to this claim, or None."""
    if not candidates:
        return None
    placed = _parse_stamp(row.get("placed_at"))
    if placed is None:
        # Pre-F64 row with no timestamp: the week is all there is.
        exact = [c for c in candidates if c.get("week") == row.get("week")]
        return exact[0] if exact else None

    scored = []
    for c in candidates:
        created = _parse_stamp(c.get("created"))
        if created is None:
            continue
        gap = abs((created - placed).total_seconds())
        if gap <= MATCH_WINDOW_DAYS * 86400:
            scored.append((gap, c))
    if not scored:
        return None
    return min(scored, key=lambda pair: pair[0])[1]


def calibration(rows):
    """Score v1 and v2 against what actually happened, honouring the censoring.

    Only RESOLVED rows count. An unresolved claim is not evidence either way, and
    counting it as correct is how a heuristic gets to look good by saying nothing.
    """
    # F64: score the CLAIM, not the row. A bid raised before the waiver run is one claim,
    # and counting it twice would score a price that was never live.
    claims = live_rows(rows)
    resolved = [r for r in claims
                if r.get("won") is not None and r.get("winning_bid_if_visible") is not None]
    out = {"n": len(resolved), "unresolved": len(claims) - len(resolved),
           "superseded": len(rows or []) - len(claims)}
    for key, field in (("v1", "suggested_v1"), ("v2", "suggested_v2_point")):
        scored = [score_bid_suggestion(r.get(field) or 0, r["winning_bid_if_visible"],
                                       bool(r["won"])) for r in resolved]
        out[key] = {
            "errors": sum(1 for s in scored if s["error"]),
            "total_miss": float(sum(s["miss"] for s in scored)),
            "mean_miss": (float(sum(s["miss"] for s in scored)) / len(scored)) if scored else 0.0,
        }
    if not resolved:
        out["verdict"] = "no resolved claims yet"
        out["note"] = (f"{out['unresolved']} unresolved row(s) excluded: a claim whose "
                       f"waiver run has not happened is not evidence either way")
        return out

    if len(resolved) < MIN_CLAIMS_FOR_A_VERDICT:
        out["verdict"] = (f"too few claims ({len(resolved)} < {MIN_CLAIMS_FOR_A_VERDICT}) "
                          f"to name a winner")
    elif out["v2"]["total_miss"] < out["v1"]["total_miss"]:
        out["verdict"] = "v2 is closer"
    elif out["v1"]["total_miss"] < out["v2"]["total_miss"]:
        out["verdict"] = "v1 is closer"
    else:
        out["verdict"] = "tied"
    out["note"] = (f"{out['unresolved']} unresolved row(s) excluded. A winning bid is an "
                   f"UPPER BOUND on the price (F61), so a suggestion below a bid I won is "
                   f"not scored as an error. Remember F61's headline: on 26 logged claims "
                   f"the correlation between VORP and winning bid was -0.136, so a "
                   f"VORP-shaped heuristic may simply be the wrong shape.")
    return out


def load(path=BID_LEDGER_FILE):
    """Every recorded claim, oldest first. Missing file reads as empty."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out
