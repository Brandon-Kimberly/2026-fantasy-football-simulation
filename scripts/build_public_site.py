"""
scripts.build_public_site -- build the public copy of the website for GitHub Pages.

    py -3.10 -m scripts.build_public_site [--out _site] [--base /syndicate-football]

The public site replaced the legacy sample report (owner, 2026-09-30): the web UI's simple
view from the owner's team's point of view, as static pages anyone can open (webui.static_site).
The Pages workflow runs this after each week's official model run, on that run's data.

Before anything is written for publishing it must pass the leak check: every real team name,
username and league name, taken live from Sleeper for every league id in the environment
(SLEEPER_LEAGUE_ID, SLEEPER_LEAGUE_ID_2025, SLEEPER_LEAGUE_ID_2024, and the ESPN id), must be
absent from every page. A hit deletes the build and exits non-zero; with any one of those ids
missing it refuses to build at all. Real names are never on in this process
(SHOW_REAL_TEAM_NAMES=0), and the pages carry no images.
"""
import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_URL = "https://api.sleeper.app/v1"
# Every league whose names can reach a page. All four are required: the first deploy (2026-09-30)
# ran with only the current one on the runner and silently skipped the 2024 and 2025 team names,
# which the history pages carry.
LEAGUE_VARS = ("SLEEPER_LEAGUE_ID", "SLEEPER_LEAGUE_ID_2025", "SLEEPER_LEAGUE_ID_2024", "ESPN_LEAGUE_ID")


def _default_base():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return "/" + (repo.split("/", 1)[1] if "/" in repo else "syndicate-football")


def _default_origin():
    """https://<owner>.github.io, from GITHUB_REPOSITORY on the runner (Pages hosts are lower case)."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    owner = repo.split("/", 1)[0] if "/" in repo else "Brandon-Kimberly"
    return f"https://{owner.lower()}.github.io"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"))
    ap.add_argument("--base", default=_default_base(), help="the site's path on Pages, e.g. /syndicate-football")
    ap.add_argument("--root", default=ROOT, help="the checkout (or copy) whose data/ to render")
    ap.add_argument("--origin", default=_default_origin(), help="the site's scheme and host, for each page's link preview")
    a = ap.parse_args(argv)
    os.environ["SHOW_REAL_TEAM_NAMES"] = "0"

    missing = [k for k in LEAGUE_VARS if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)}: the leak check would skip that league's names, so nothing is built.")
    ids = [os.environ[k] for k in LEAGUE_VARS[:3]]

    import requests
    from fantasy_sim.weekly_report import PRIVATE_MARKER
    from webui.paths import Root
    from webui.static_site import LeakFound, export, forbidden_identities, leak_check

    def fetch(path):
        r = requests.get(BASE_URL + path, timeout=30)
        r.raise_for_status()
        return r.json()

    forbidden = forbidden_identities(ids, fetch)
    forbidden += [os.environ["ESPN_LEAGUE_ID"], PRIVATE_MARKER, "LOCAL VIEW"]

    if os.path.isdir(a.out):
        shutil.rmtree(a.out)
    report = export(Root(a.root), a.out, a.base, origin=a.origin)
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
