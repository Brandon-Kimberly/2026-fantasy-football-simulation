"""webui -- the localhost-only web interface (docs/WEB_UI.md).

A READER and a LAUNCHER over the project's existing surfaces, never a second code path
into the engine. Three rules this package is built around, each pinned by a test:

  * The web process never imports fantasy_sim.simulation, .decisions or .sync (the first
    truncates data/current/syndicate_warnings.log at import -- F10; the second imports the
    first; the third is network + writes). tests/test_webui_imports.py runs the check in a
    subprocess, because the suite's own process has already imported the engine.
  * The process never chdir()s. storage's helpers are CWD-relative strings; webui.paths
    binds one absolute root and joins every relative path onto it through one chokepoint
    that refuses anything outside data/ and everything under data/local/.
  * Pseudonyms everywhere on disk, in URLs, and in logs. Real names are an in-memory
    overlay (webui.names) substituted into response bodies only.

This module imports nothing, so `import webui` is free of Flask and matplotlib -- the
pure helpers (paths, names) stay usable, and testable, without the web dependencies.
"""
