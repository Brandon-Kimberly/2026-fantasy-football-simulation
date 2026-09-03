"""The sync-stage golden: pins generate_player_baselines byte-exactly from committed
fixture inputs.

WHY THIS EXISTS (F28, 2026-09-02): a 44% change to DL's weekly sd produced byte-identical
ENGINE goldens, because VOLATILITY_CONSTANTS acts at sync time -- the engine consumes the
std_aleatoric already baked into player_baselines.json, and the engine golden pins
post-sync inputs. Every sync-time constant (VOLATILITY_CONSTANTS, EPISTEMIC_ERROR_RATES,
BASE_STREAMER_MEANS, the blend and its EMA prior) shipped unverified by any golden. This
harness closes that: committed pre-sync inputs -> baseline generation -> hashed outputs,
so a sync-time change either leaves these hashes byte-identical or regenerates them with
the deltas explained in the commit (MAJOR per the release policy's sync-time clause).

COVERAGE LIMIT, stated plainly: the ESPN snapshot is taken at the
fetch_espn_projections return-value boundary, so the ESPN client's PARSING sits outside
this golden (its own unit tests cover it). The seam between stage goldens is where
changes ship unverified -- the same pattern F28 documented for the engine golden.

Hermeticity: runs in a throwaway temp workdir (storage paths are CWD-relative, the same
isolation backtest_season uses), with requests.get faked (an unexpected URL raises --
a new fetch inside baseline generation must be added to the fixtures, loudly), ESPN
patched at the function boundary, and the clock frozen so synced_at is reproducible.

Regenerate (an intended behaviour change, MAJOR): py -3.10 -m tests.golden_sync --regenerate
Check without regenerating:                       py -3.10 -m tests.golden_sync
"""
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures", "golden_sync")
EXPECTED = os.path.join(FIX, "expected.json")


def _jz(name):
    with gzip.open(os.path.join(FIX, name), "rt", encoding="utf-8") as f:
        return json.load(f)


def _j(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


def run_golden_sync():
    """One hermetic baseline-generation run; returns the hash dict compared to expected.json."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from fantasy_sim import sync

    meta = _j("meta.json")
    proj = _jz("projections.json.gz")
    players_db = _jz("players_db.json.gz")
    espn = _j("espn_projections.json")
    scoring = _j("scoring_settings.json")
    live_rosters = _j("live_rosters.json")
    byes = _j("byes.json")

    frozen = datetime.strptime(meta["frozen_utcnow"], "%Y-%m-%dT%H:%M:%SZ")

    class FrozenDatetime(datetime):
        @classmethod
        def utcnow(cls):
            return frozen

    weekly_url = f"{sync.BASE_URL}/projections/nfl/regular/{meta['season']}/{meta['week']}"

    class _Resp:
        status_code = 200

        def json(self):
            return proj

    def fake_get(url, *args, **kwargs):
        if url == weekly_url:
            return _Resp()
        raise AssertionError(
            f"golden_sync: unexpected network call {url!r} -- a new fetch inside baseline "
            "generation must be captured into the fixtures, not silently allowed")

    workdir = tempfile.mkdtemp(prefix="golden_sync_")
    original_cwd = os.getcwd()
    try:
        os.makedirs(os.path.join(workdir, "data", "current"))
        os.makedirs(os.path.join(workdir, "data", "logs"))
        shutil.copy(os.path.join(FIX, "prior_baselines.json"),
                    os.path.join(workdir, "data", "current", "player_baselines.json"))
        with gzip.open(os.path.join(FIX, "prior_projection_log.jsonl.gz"), "rt",
                       encoding="utf-8") as f:
            prior_log = f.read()
        with open(os.path.join(workdir, "data", "logs", "projection_log.jsonl"), "w",
                  encoding="utf-8", newline="") as f:
            f.write(prior_log)

        os.chdir(workdir)
        with patch.object(sync.requests, "get", fake_get), \
             patch.object(sync, "fetch_espn_projections", lambda year, week: dict(espn)), \
             patch.object(sync, "datetime", FrozenDatetime):
            baselines = sync.generate_player_baselines(
                scoring, players_db, live_rosters, meta["season"], meta["week"],
                rostered_pids=set(meta["rostered_pids"]), byes=byes,
                reserve_pids=set(meta["reserve_pids"]))

        result = {}
        with open(os.path.join(workdir, "data", "current", "player_baselines.json"), "rb") as f:
            result["baselines_sha256"] = hashlib.sha256(f.read()).hexdigest()
        with open(os.path.join(workdir, "data", "logs", "projection_log.jsonl"), "rb") as f:
            result["projection_log_sha256"] = hashlib.sha256(f.read()).hexdigest()
        result["n_baselines"] = len(baselines)
        return result
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    result = run_golden_sync()
    print(json.dumps(result, indent=2))
    if "--regenerate" in sys.argv:
        with open(EXPECTED, "w", encoding="utf-8", newline="\n") as f:
            json.dump(result, f, indent=2)
        print(f"regenerated -> {EXPECTED}")
    elif os.path.exists(EXPECTED):
        with open(EXPECTED, encoding="utf-8") as f:
            expected = json.load(f)
        print("matches expected:", result == expected)
    else:
        print("no expected.json committed yet -- run with --regenerate")
