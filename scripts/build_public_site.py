"""
scripts.build_public_site -- build the public copy of the website for GitHub Pages.

    py -3.10 -m scripts.build_public_site [--out _site] [--base /syndicate-football]

The public site replaced the legacy sample report (owner, 2026-09-30): the web UI's simple
view from the owner's team's point of view, as static pages anyone can open (webui.static_site).
The Pages workflow runs this after each week's official model run, on that run's data.

Before anything is written for publishing it must pass the leak check: every real team name,
username and league name, taken live from Sleeper for every league id in the environment
(SLEEPER_LEAGUE_ID, SLEEPER_LEAGUE_ID_2025, SLEEPER_LEAGUE_ID_2024, and the ESPN id), must be
absent from every page. A hit deletes the build and exits non-zero; with no league id to check
against it refuses to build at all. Real names are never on in this process
(SHOW_REAL_TEAM_NAMES=0), and the pages carry no images.
"""
import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = "https://api.sleeper.app/v1"


def _default_base():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return "/" + (repo.split("/", 1)[1] if "/" in repo else "syndicate-football")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    ap.add_argument("--base", default=_default_base(), help="the site's path on Pages, e.g. /syndicate-football")
    ap.add_argument("--root", default=ROOT, help="the checkout (or copy) whose data/ to render")
    a = ap.parse_args(argv)
    os.environ["SHOW_REAL_TEAM_NAMES"] = "0"

    ids = [os.environ.get(k) for k in ("SLEEPER_LEAGUE_ID", "SLEEPER_LEAGUE_ID_2025", "SLEEPER_LEAGUE_ID_2024")]
    ids = [x for x in ids if x]
    if not ids:
        sys.exit("No SLEEPER_LEAGUE_ID in the environment: the leak check has nothing to check against, so nothing is built.")

    import requests
    from fantasy_sim.weekly_report import PRIVATE_MARKER
    from webui.paths import Root
    from webui.static_site import LeakFound, export, forbidden_identities, leak_check

    def fetch(path):
        r = requests.get(BASE_URL + path, timeout=30)
        r.raise_for_status()
        return r.json()

    forbidden = forbidden_identities(ids, fetch)
    espn = os.environ.get("ESPN_LEAGUE_ID")
    forbidden += [x for x in (espn, PRIVATE_MARKER, "LOCAL VIEW") if x]

    if os.path.isdir(a.out):
        shutil.rmtree(a.out)
    report = export(Root(a.root), a.out, a.base)
    try:
        n = leak_check(a.out, forbidden)
    except LeakFound as ex:
        shutil.rmtree(a.out, ignore_errors=True)
        print(f"REFUSED: {ex}", file=sys.stderr)
        return 2
    print(f"built {report['pages']} pages into {a.out} for {a.base}/ -- leak check clean against {n} identities"
          + (" (stopped at the page cap)" if report["truncated"] else ""))
    for url, code in report["skipped"][:10]:
        print(f"  skipped {url}: {code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
