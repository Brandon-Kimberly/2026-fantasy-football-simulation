#!/usr/bin/env python3
"""
Is the engine's streamer worth what the free-agent pool is actually worth? (C5)

  py -3.10 -m scripts.streamer_study            # measure and print
  py -3.10 -m scripts.streamer_study --record   # ... and append one row to the history
  py -3.10 -m scripts.streamer_study --json

An unfillable slot is scored `max(0, N(m_str, 2.2))` with
`m_str = max(replacement_level[pos] * 0.8, BASE_STREAMER_MEANS[pos])`. Every evaluation of
a hole is priced against that: an add at the position, the season simulation for a team
carrying it, and the matchup and League tables since C2. If `m_str` is below what is
genuinely claimable for nothing, filling the hole looks better than it is.

MEASUREMENT ONLY. No constant is changed by this tool. `BASE_STREAMER_MEANS` is read at
engine init by every hole evaluation, so moving it is a **MAJOR** release and the owner's
decision, not a study's side effect.

The `capped` column is what a pool-derived streamer WOULD be under Phase 4's rule -- never
above the replacement level, because a streamer that out-projects a rostered starter makes
a hole worth more than a player.

`--record` appends to data/logs/streamer_levels.jsonl. The backlog assumed
`projection_log.jsonl` carried this history; it does not (one line per ROSTERED player, so
a season-long free agent never appears). Running this weekly is what makes a multi-sync
comparison possible.

Reads data/current/ only.
"""
import argparse
import datetime as _dt
import json
import os

from fantasy_sim.simulation import FantasySimulationEngine
from fantasy_sim.storage import STREAMER_LEVELS_FILE
from fantasy_sim.streamer_study import render_lines, streamer_gap


def record(study, week, path=STREAMER_LEVELS_FILE):
    """Append one row. A log is a record, not a dependency: never raises."""
    try:
        row = {"recorded_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "week": int(week), "top_n": study["top_n"],
               "rows": [{k: r[k] for k in ("pos", "base", "replacement", "m_str",
                                           "pool_n", "top_mean", "gap", "derived_capped",
                                           "cap_binds", "derived_gap")}
                        for r in study["rows"]]}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return 1
    except Exception as ex:
        print(f"  (streamer history not recorded: {type(ex).__name__}: {ex})")
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=3,
                    help="how many free agents per position define the claimable level")
    ap.add_argument("--record", action="store_true",
                    help="append one row to data/logs/streamer_levels.jsonl")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    engine = FantasySimulationEngine()
    study = streamer_gap(engine, top_n=a.top)
    study["week"] = engine.current_week
    if a.json:
        print(json.dumps(study, indent=1, sort_keys=True))
    else:
        print()
        for line in render_lines(study):
            print(line)
    if a.record:
        n = record(study, engine.current_week)
        if n and not a.json:
            print(f"  recorded -> {STREAMER_LEVELS_FILE}")
    return study


if __name__ == "__main__":
    main()
