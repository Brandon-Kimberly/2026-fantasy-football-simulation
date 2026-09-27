"""py -3.10 -m webui -- the localhost-only viewer (docs/WEB_UI.md).

  py -3.10 -m webui                      # http://127.0.0.1:8765/ over this checkout's data/
  py -3.10 -m webui --port 8800
  py -3.10 -m webui --root C:/copy       # serve a sandbox copy instead
  py -3.10 -m webui --no-real-names      # pseudonyms even locally
  py -3.10 -m webui --hostname syndicatefootball.local --port 80
                                         # http://syndicatefootball.local/ on this machine only, after (as Administrator)
                                         #   Add-Content C:\Windows\System32\drivers\etc\hosts "127.0.0.1 syndicatefootball.local"

Binds 127.0.0.1 and nothing else: there is no --host option, by design (section 2.8).
Real names follow scripts.weekly_report's rule -- ON for a command a human typed, never on
a runner (GITHUB_ACTIONS), and an explicit SHOW_REAL_TEAM_NAMES=0 still wins; the library
default stays off so the suite never reaches the network. MPLBACKEND=Agg is set before
any import because fantasy_sim.storage imports matplotlib.pyplot.
"""
import argparse
import os
import sys

DEFAULT_PORT = 8765
DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pre_import_environment(no_real_names=False):
    os.environ.setdefault("MPLBACKEND", "Agg")
    if no_real_names:
        os.environ["SHOW_REAL_TEAM_NAMES"] = "0"
    elif not os.environ.get("GITHUB_ACTIONS"):
        os.environ.setdefault("SHOW_REAL_TEAM_NAMES", "1")


def main(argv=None):
    if sys.version_info[:2] != (3, 10):
        print(f"webui: this project runs on Python 3.10 (py -3.10); this is {sys.version.split()[0]}. "
              "Plain `python` on the original machine is the retired Store 3.8 -- see CLAUDE.md.",
              file=sys.stderr)
        return 3
    ap = argparse.ArgumentParser(prog="py -3.10 -m webui", description=__doc__, allow_abbrev=False,   # --host must not abbreviate --hostname
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--root", default=DEFAULT_ROOT, help="the checkout (or a copy) whose data/ to serve")
    ap.add_argument("--no-real-names", action="store_true")
    ap.add_argument("--hostname", action="append", default=[], metavar="NAME",
                    help="also answer to this name (W9), e.g. syndicatefootball.local -- after adding "
                         "'127.0.0.1 syndicatefootball.local' to the hosts file; the bind stays 127.0.0.1")
    ap.add_argument("--mode", choices=("dev", "simple"), default=None,
                    help="the view to start in (W8); the stored setting wins once it exists, the header toggles it")
    args = ap.parse_args(argv)          # an unknown option such as --host exits 2 here, before any bind

    _pre_import_environment(args.no_real_names)
    from webui.app import create_app
    from webui.names import Overlay
    from webui.paths import Root

    root = Root(args.root)
    if not os.path.isdir(root.data):
        print(f"webui: {root.data} does not exist -- is --root a checkout with a data/ tree?", file=sys.stderr)
        return 2
    overlay = Overlay.from_environment()
    app = create_app(root, overlay=overlay, port=args.port, default_mode=args.mode, hostnames=args.hostname)
    shown = (args.hostname[0] if args.hostname else "127.0.0.1") + ("" if args.port == 80 else f":{args.port}")
    print(f"webui: http://{shown}/   root={root.root}   "
          f"real names={'on' if overlay.enabled else 'off (pseudonyms)'}   mode={app.config['SETTINGS'].mode}", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
