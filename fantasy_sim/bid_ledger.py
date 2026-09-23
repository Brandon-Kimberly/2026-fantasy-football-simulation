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


def reconcile(ledger_rows, decision_rows):
    """Fill `won` and `winning_bid_if_visible` from completed transactions.

    Matching is on (player_id, week). A claim the decision log does not mention stays
    `won: None` -- see the module docstring on why absence is not a loss.
    """
    completed = {}
    for r in decision_rows or []:
        if r.get("type") != "waiver":
            continue
        for add in (r.get("adds") or []):
            pid = add.get("player_id")
            if pid is None:
                continue
            completed[(str(pid), r.get("week"))] = r

    out = []
    for row in ledger_rows or []:
        row = dict(row)
        hit = completed.get((str(row.get("player_id")), row.get("week")))
        if hit is None:
            row["won"], row["winning_bid_if_visible"] = None, None
        else:
            row["won"] = bool(hit.get("is_mine"))
            row["winning_bid_if_visible"] = hit.get("faab_bid")
        out.append(row)
    return out


def calibration(rows):
    """Score v1 and v2 against what actually happened, honouring the censoring.

    Only RESOLVED rows count. An unresolved claim is not evidence either way, and
    counting it as correct is how a heuristic gets to look good by saying nothing.
    """
    resolved = [r for r in (rows or [])
                if r.get("won") is not None and r.get("winning_bid_if_visible") is not None]
    out = {"n": len(resolved), "unresolved": len(rows or []) - len(resolved)}
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
