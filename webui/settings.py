"""webui.settings -- the server's one setting so far: the view mode (docs/WEB_UI.md W8).

Two modes, one codebase, every page rendered in both:

  dev     -- the owner's view: every file, job, log, sync control, verdict code and
             method note. What the UI has been since W1.
  simple  -- the view anyone could use: the matchup, the odds, the league, the
             decisions, the handful of tools that answer a question, and nothing about
             how the machine works -- no file names, no job ids, no verdict codes, no
             F-numbers, no sync. tests.test_webui_modes scans every simple page for
             dev vocabulary, so a change that leaks it fails the suite.

The mode is server-side (data/local/webui/settings.json -- local, never tracked) so every
page agrees, and the toggle is one POST route. Today the server is localhost-only, so
"only the owner can toggle it" is true by construction; when the engine is hosted for
other people, /mode is the one route to put behind the owner's login -- everything else
already keys off the stored mode.
"""
import json
import os
import threading

MODES = ("dev", "simple")
DEFAULT_MODE = "dev"


class Settings:
    def __init__(self, root, default_mode=None):
        self.path = os.path.join(root.local, "webui", "settings.json")
        self._lock = threading.Lock()
        self._default = default_mode if default_mode in MODES else DEFAULT_MODE

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh)
                return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @property
    def mode(self):
        m = self._read().get("mode")
        return m if m in MODES else self._default

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError(mode)
        with self._lock:
            d = self._read()
            d["mode"] = mode
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(d, fh, indent=1)
        return mode

    def toggle(self):
        return self.set_mode("simple" if self.mode == "dev" else "dev")
