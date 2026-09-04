#!/usr/bin/env python3
"""F36 tier 2: the canonical gate -- may an UNATTENDED run quote canonical predictions?

  py -3.10 -m scripts.canonical_gate           # JSON verdict from the sync on disk

Three verdicts, decided by explicit lists rather than judgment (the DEGRADED-judgment
caveat made mechanical, 2026-09-04):

  ABORT         freshness is STALE after the sync: something real broke. The runner
                fails loud and writes nothing.
  REPORT_ONLY   the sync completed but a FORECAST-AFFECTING degradation is present:
                the report still runs and uploads as an artifact for the human, but no
                canonical predictions row is committed.
  CANONICAL_OK  every degraded entry is a known benign resident. Quote canonically.

The classification is allowlist-first: a healthy 2026 sync permanently carries name
collisions, carried priors, the anonymous-defaults count and the depth watchdog -- those
never block. Known forecast-affecting classes block with a remediation key. ANYTHING
UNRECOGNIZED BLOCKS TOO (conservative: a new failure class quotes nothing until a human
classifies it into one of the lists above).

Every blocking key maps to a remediation block written for a reader who has forgotten
everything about this project: what happened, the verbatim command, whether to commit
and push, and how to verify it worked.
"""
import argparse
import json
import sys

ABORT, REPORT_ONLY, CANONICAL_OK = "ABORT", "REPORT_ONLY", "CANONICAL_OK"

# Benign residents of a healthy sync: substring markers, matched anywhere in the entry.
BENIGN_MARKERS = (
    "NAME COLLISION:",
    "; CARRIED ",                    # carried_prior AND carried_log zero-projection carries
    "use the anonymous defaults",
    "DEPTH WATCHDOG:",
    "covered by KNOWN_MISSING_ASSETS",   # whitelisted zero-projection: engine imputes cleanly
)

# Forecast-affecting classes with dedicated remediations.
BLOCKING_MARKERS = (
    ("is NOT in baselines", "missing_baseline"),   # the Jordyn Tyson class
    ("ESPN BLEND", "espn"),
)

VEGAS_REAL_SOURCES = ("odds_api", "week1_verified_table")

# The unrostered-pool floor (2026-09-04): a partial projection fetch that silently drops
# FREE-AGENT players thins the pool, shifts replacement levels, and moves every VORP
# number downstream -- and nothing else fires, because every other detector watches
# rostered players. DERIVED FROM OBSERVED HISTORY, not picked: the recorded populations
# are 964 (the late-August golden fixtures) and 888 (the 2026-09-02 sync-golden
# regeneration AND the 2026-09-04 live sync) -- the pool legitimately moved ~8% through
# roster cutdowns, so the check is ONE-SIDED (thinning is the failure mode) with the
# floor >21% below the smallest observation: a halved fetch trips decisively, normal
# churn never does. REVISIT if the pool changes structurally (Sleeper serving a
# different population, new positions, a different league format).
PROJECTION_POOL_FLOOR = 700


def classify_degraded_entry(entry):
    """'benign' or 'blocking:<key>'. Unrecognized -> 'blocking:unrecognized'."""
    for marker, key in BLOCKING_MARKERS:
        if marker in entry:
            return f"blocking:{key}"
    for marker in BENIGN_MARKERS:
        if marker in entry:
            return "benign"
    return "blocking:unrecognized"


def canonical_gate(freshness_status, degraded, vegas_source, baselines_count=None):
    """Pure. Returns {verdict, blocking: [{key, entry}], benign_count, reasons}.
    baselines_count: total player_baselines.json entries (None = not checked)."""
    if freshness_status == "STALE":
        return {"verdict": ABORT, "blocking": [{"key": "stale", "entry": "freshness STALE"}],
                "benign_count": 0}
    blocking, benign = [], 0
    for e in degraded or []:
        c = classify_degraded_entry(e)
        if c == "benign":
            benign += 1
        else:
            blocking.append({"key": c.split(":", 1)[1], "entry": e})
    if vegas_source not in VEGAS_REAL_SOURCES:
        blocking.append({"key": "odds",
                         "entry": f"vegas source is '{vegas_source}' (not a real-lines source)"})
    if baselines_count is not None and baselines_count < PROJECTION_POOL_FLOOR:
        blocking.append({"key": "thin_projections",
                         "entry": f"projection pool has {baselines_count} entries, below the "
                                  f"observed-history floor {PROJECTION_POOL_FLOOR} -- partial fetch?"})
    return {"verdict": CANONICAL_OK if not blocking else REPORT_ONLY,
            "blocking": blocking, "benign_count": benign}


# ------------------------------------------------------------------ remediation catalog
# Every block: WHAT HAPPENED / RUN THIS (verbatim) / COMMIT & PUSH? / VERIFY.
REMEDIATIONS = {
    "odds": """### Odds API key missing, expired, or out of quota
**What happened:** the sync could not get real Vegas lines, so the forecast would be
matchup-blind (every team a flat 21.5 total). Not committed as canonical.

**Run this:**
1. Get a (free) key at https://the-odds-api.com -- the existing key may just be out of
   monthly quota, which resets on its own; the response detail in the sync output says
   401 (bad/expired) vs 429 (quota).
2. Set it in BOTH places:
   - locally: `setx ODDS_API_KEY <the-key>` -- then open a **NEW terminal** (setx does
     not update already-open shells).
   - for the runner: repo **Settings -> Secrets and variables -> Actions ->
     ODDS_API_KEY -> Update** (or, with GitHub CLI: `gh secret set ODDS_API_KEY`).
3. Re-run: `py -3.10 -m scripts.weekly_report --canonical` (new terminal), or re-run
   the workflow from the Actions tab (**canonical-run -> Run workflow**).

**Commit & push:** no -- the canonical run pushes its own logs.
**Verify:** `py -3.10 -m scripts.check_freshness` no longer lists a vegas fallback, and
this issue closes at the next watch tick.
**Safe to skip?** No for a canonical row -- but the report artifact on this run is still
readable if you need to make lineup decisions from a matchup-blind forecast.""",

    "espn": """### ESPN projections unreachable
**What happened:** the ESPN fetch failed, so every player fell back to Sleeper-only --
the blend and the disagreement-driven epistemic term are missing this sync.

**First, know this:** the dedicated ESPN league is PUBLIC and **no credentials are
needed** in normal operation (verified live: the blend matches without any cookies).
This warning means the fetch itself failed -- almost always ESPN being down or slow.

**Run this:**
1. Most likely: do nothing -- the retry run succeeds on its own once ESPN responds.
2. ONLY if the league was actually made private (you would know: it is your league on
   espn.com), two cookies become needed: log into espn.com, DevTools -> Application ->
   Cookies -> espn.com, copy `espn_s2` and `SWID`. Then:
   - locally: `setx ESPN_S2 <value>` and `setx ESPN_SWID <value>`, then a **NEW terminal**.
   - for the runner: repo **Settings -> Secrets and variables -> Actions**, add both
     (or `gh secret set ESPN_S2` / `gh secret set ESPN_SWID`).
3. Re-run: `py -3.10 -m scripts.weekly_report --canonical`.

**Commit & push:** no.
**Verify:** the sync manifest's degraded list has no `ESPN BLEND` entry
(`py -3.10 -m scripts.check_freshness` shows the count).
**Safe to skip?** Once, semi-safe: the mean stays Sleeper's; you lose one week of the
K/IDP epistemic signal. Repeated skips degrade F22's eventual derivation.""",

    "missing_baseline": """### A rostered player has no projection and no whitelist entry (engine would abort)
**What happened:** someone rosters a player Sleeper projects at zero who has no prior
anywhere. The engine refuses to invent a number (house rule: every constant is sourced
or marked unverified). **This is a judgment call only you can make.**

**Run this:** add an entry to `KNOWN_MISSING_ASSETS` inside `SIM_CONFIG` in
`fantasy_sim/config.py`, shaped exactly like the existing one -- field for field:

    "Jordyn Tyson": {"mean": 6.5, "std_aleatoric": 3.0, "std_epistemic": 1.17, "pos": "WR", "team": "NO", "bye": 0}

with the player's name as the key, `team` matching his roster's NFL team, and a comment
citing where the mean comes from (a projection source, or "unverified, judgment").

**Commit & push:** yes:
    git add fantasy_sim/config.py
    git commit -m "config: KNOWN_MISSING_ASSETS entry for <player>"
    git push
Then re-run from the Actions tab (**canonical-run -> Run workflow**) or locally:
`py -3.10 -m scripts.weekly_report --canonical`.

**Verify:** the new run's sync warnings show the player as whitelisted/imputed, not
"NOT in baselines"; the canonical row commits.
**If you skip:** no canonical row this window -- a permanent gap in the quoted-
predictions record (F25). The projections and transaction snapshots were still captured
by the scheduled log push, so the model's data loses nothing.""",

    "stale": """### Sync STALE -- either the week has not rolled, or something real broke
**What happened:** after the sync, freshness still reads STALE. The reasons are quoted
below this block, verbatim.

**How to tell which:** if a reason says "week rolled" / "Sleeper reports week N", it is
Tuesday-night week-roll timing -- **wait**; the scheduled retry handles it. Any other
reason means a step failed.

**Run this (broken case):** `py -3.10 -m scripts.run_sync` locally and read its output
-- the failure names its step. If Sleeper/ESPN are down entirely, wait for the retry
(its fire time is in this issue) unless the deadline below is closer than ~2 hours, in
which case run `py -3.10 -m scripts.weekly_report --canonical` locally once the sites
respond.

**Commit & push:** no (a local canonical run pushes its own logs).
**Verify:** `py -3.10 -m scripts.check_freshness` says OK or DEGRADED (not STALE).""",

    "unrecognized": """### An unrecognized degradation (new failure class)
**What happened:** the sync tolerated a failure the gate has never seen (quoted below,
verbatim). By design it refuses to quote canonical predictions until a human classifies
it: benign classes go in `BENIGN_MARKERS`, forecast-affecting ones in
`BLOCKING_MARKERS`, both in `scripts/canonical_gate.py`.

**Run this:** read the entry. If it is clearly harmless, add its stable prefix to
`BENIGN_MARKERS` (with a comment saying why), commit, push; the next scheduled run
quotes canonically. If it affects the forecast, fix the underlying issue first.

**Commit & push:** yes, if you edited the gate:
    git add scripts/canonical_gate.py
    git commit -m "canonical gate: classify <entry class>"
    git push

**Verify:** re-run the workflow (Actions tab -> canonical-run -> Run workflow); the
gate JSON in the run log shows the entry as benign.
**Safe to skip?** The report artifact is still produced and readable either way.""",

    "thin_projections": """### The projection pool came back thin (partial fetch?)
**What happened:** the sync wrote far fewer player baselines than the pool has ever
had (the count and floor are in the entry below). A partial Sleeper fetch silently
thins the FREE-AGENT pool, which shifts replacement levels and every VORP number
downstream -- rostered players all look fine, which is why only this check catches it.

**Run this:** re-run the sync once Sleeper looks healthy:
    py -3.10 -m scripts.run_sync
then check the count:
    py -3.10 -m scripts.canonical_gate
(the JSON shows the pool count; data/current/player_baselines.json is the file).

**Commit & push:** no.
**Verify:** the gate JSON reports the count back above the floor, and the verdict is
no longer blocked on thin_projections.
**If it persists:** the pool may have changed STRUCTURALLY (Sleeper serving a smaller
population). If the smaller count is genuinely the new normal, re-derive
PROJECTION_POOL_FLOOR in scripts/canonical_gate.py from the new observations -- its
comment records the current derivation -- and commit that with the reasoning.""",

    "push_conflict": """### The log push conflicted
**What happened:** the runner (or your machine) could not push data/logs -- usually a
race between a local push and a scheduled capture.

**Run this (on your machine):**
    git pull --rebase origin main
    git push
The union-merge attribute resolves log races automatically. If a conflict remains, it
is NOT in a log file -- `git status` names the file; look at it before touching it.

**Verify:** `py -3.10 -m scripts.check_freshness` ends with "logs: committed and pushed".""",
}


def remediation_markdown(gate_result, window):
    """The issue body: window + deadline first (so the reader knows the time budget),
    then one remediation block per distinct blocking key, then the raw entries."""
    lines = [f"**Window:** `{window['name']}` -- **deadline {window['deadline']} UTC** "
             f"(~{window.get('hours_left', '?')} h left). A canonical row must land "
             "inside the window to count.", ""]
    seen = set()
    for b in gate_result["blocking"]:
        if b["key"] in seen:
            continue
        seen.add(b["key"])
        lines += [REMEDIATIONS.get(b["key"], REMEDIATIONS["unrecognized"]), ""]
    lines += ["---", "**The raw entries that drove this:**", ""]
    lines += [f"- `{b['entry']}`" for b in gate_result["blocking"]]
    return "\n".join(lines)


def compose_issue(gate_path, window_path):
    """The remediation issue body from a saved gate verdict + window: the blocks, where
    the report artifact is, and the freshness reasons verbatim."""
    with open(gate_path, encoding="utf-8") as f:
        gate = json.load(f)
    with open(window_path, encoding="utf-8") as f:
        window = json.load(f)
    body = remediation_markdown(gate, window)
    if gate["verdict"] == REPORT_ONLY:
        body += ("\n\n---\nThe report for this run is attached as this workflow run's "
                 "**artifact** (Actions tab -> this run -> Artifacts) -- readable without "
                 "any commit.")
    else:
        body += "\n\n---\nNo report was produced (ABORT)."
    reasons = (gate.get("freshness") or {}).get("reasons") or []
    if reasons:
        body += "\nFreshness reasons, verbatim:\n" + "\n".join(f"- `{r}`" for r in reasons[:12])
    return body


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--compose-issue", nargs=2, metavar=("GATE_JSON", "WINDOW_JSON"),
                    help="print the remediation issue body for a saved verdict")
    args = ap.parse_args(argv)
    if args.compose_issue:
        print(compose_issue(*args.compose_issue))
        return 0
    from fantasy_sim.freshness import (assess, read_file_mtimes, read_manifest,
                                       read_nfl_week, read_vegas_week)
    from fantasy_sim.storage import VEGAS_FILE, load_json
    manifest_r, sync_start = read_manifest()
    status, reasons = assess(manifest_r, sync_start, read_file_mtimes(), read_vegas_week(),
                             None, read_nfl_week(), check_export=False)
    details = {"manifest": manifest_r}
    manifest = details.get("manifest") or {}
    try:
        vegas_source = (load_json(VEGAS_FILE).get("_meta") or {}).get("source")
    except FileNotFoundError:
        vegas_source = None
    try:
        from fantasy_sim.storage import BASELINES_FILE
        baselines_count = len(load_json(BASELINES_FILE))
    except FileNotFoundError:
        baselines_count = None
    g = canonical_gate(status, manifest.get("degraded") or [], vegas_source,
                       baselines_count=baselines_count)
    g["baselines_count"] = baselines_count
    g["freshness"] = {"status": status, "reasons": reasons}
    g["vegas_source"] = vegas_source
    print(json.dumps(g, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
