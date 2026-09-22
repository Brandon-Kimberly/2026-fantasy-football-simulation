#!/usr/bin/env python3
"""
F56 one-off: reconstruct sync provenance for projection rows written before it existed.

Every row already carries `synced_at`, and that is one value per sync, so the historical
syncs can be recovered by grouping on it. What CANNOT be recovered is the git commit --
nothing recorded it -- so backfilled rows carry `git_commit: null` and
`schema_version: 0`, which is precisely what marks them as reconstructed rather than
observed. Do not invent a hash for them.

`espn_rows` IS recoverable, because it is a property of the rows themselves, and it is
the field January actually needs: the pre/post-F52 difference (0/152 against 110/150 in
week 2) is the blend boundary.

  py -3.10 -m scripts.backfill_sync_provenance            # report only
  py -3.10 -m scripts.backfill_sync_provenance --write    # append missing rows

IDEMPOTENT: a sync already present in the sidecar is never written twice, and existing
rows are never modified or reordered. Safe to run repeatedly; running it after a normal
sync simply reports nothing to do.
"""
import argparse
import json
import os
import sys
from collections import defaultdict

from fantasy_sim.storage import PROJECTION_LOG_FILE, SYNC_PROVENANCE_FILE


def summarise(projection_log=PROJECTION_LOG_FILE):
    """{(season, week, synced_at): {espn_rows, total_rows}} for every sync in the log."""
    out = defaultdict(lambda: {"espn_rows": 0, "total_rows": 0})
    with open(projection_log, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            key = (str(row.get("season")), row.get("week"), row.get("synced_at"))
            if key[2] is None:
                continue
            out[key]["total_rows"] += 1
            if row.get("espn_mean") is not None:
                out[key]["espn_rows"] += 1
    return out


def existing_stamps(path=SYNC_PROVENANCE_FILE):
    if not os.path.exists(path):
        return set()
    seen = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                seen.add(json.loads(line).get("synced_at"))
            except ValueError:
                continue
    return seen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="append the missing rows")
    a = ap.parse_args(argv)

    if not os.path.exists(PROJECTION_LOG_FILE):
        raise SystemExit(f"no projection log at {PROJECTION_LOG_FILE}")

    syncs = summarise()
    have = existing_stamps()
    missing = {k: v for k, v in syncs.items() if k[2] not in have}

    print(f"projection log: {len(syncs)} distinct syncs")
    print(f"provenance already recorded for {len(have)}; {len(missing)} to backfill\n")
    if missing:
        print(f"  {'season':7s} {'wk':>3s} {'synced_at':22s} {'espn':>6s} {'rows':>6s}")
        for (season, week, stamp), v in sorted(missing.items(), key=lambda x: str(x[0][2])):
            print(f"  {season:7s} {str(week):>3s} {stamp:22s} "
                  f"{v['espn_rows']:6d} {v['total_rows']:6d}")

    if not a.write:
        print("\n(report only; pass --write to append)")
        return 0
    if not missing:
        print("nothing to do")
        return 0

    os.makedirs(os.path.dirname(SYNC_PROVENANCE_FILE) or ".", exist_ok=True)
    with open(SYNC_PROVENANCE_FILE, "a", encoding="utf-8") as handle:
        for (season, week, stamp), v in sorted(missing.items(), key=lambda x: str(x[0][2])):
            handle.write(json.dumps({
                "synced_at": stamp,
                "git_commit": None,        # unrecoverable: nothing recorded it
                "schema_version": 0,       # 0 == reconstructed, not observed
                "season": season,
                "week": week,
                "espn_rows": v["espn_rows"],
                "total_rows": v["total_rows"],
                "backfilled": True,
            }, sort_keys=True) + "\n")
    print(f"\nappended {len(missing)} backfilled rows -> {SYNC_PROVENANCE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
