"""webui.sandbox -- copy data/, run there, throw it away (docs/WEB_UI_ROADMAP.md UI-E6; W5).

A sandbox is a fresh temporary root holding a copy of the served directories (current, weeks,
decisions, logs, results) and the image cache, which the pages show and a sync reuses. Never
data/local -- the secrets and the identity map stay where they are. A marker file names it a
sandbox, and `discard` refuses to remove anything without one, so a mistyped path can never
delete the real tree.

A job run against a sandbox (webui.jobs.JobRunner with `code_root`) works in the copy -- every
relative `data/` write lands there -- and imports the real checkout's code. So a what-if run,
a crawl or a test drive leaves the real tree exactly as it was.
"""
import json
import os
import shutil
import tempfile

from webui.paths import TOP_DIRS, Root

MARKER = ".sandbox.json"


def create(real, base=None):
    """A fresh sandbox Root holding a copy of `real`'s served data."""
    real = real if isinstance(real, Root) else Root(real)
    top = tempfile.mkdtemp(prefix="syn-sandbox-", dir=base)
    data = os.path.join(top, "data")
    os.makedirs(data)
    for d in TOP_DIRS + ("images",):          # the image cache too (audit 2026-09-29): the pages show it and a sync reuses it
        src = os.path.join(real.data, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(data, d))
    with open(os.path.join(top, MARKER), "w", encoding="utf-8") as fh:
        json.dump({"copied_from": real.root}, fh)
    return Root(top)


def is_sandbox(root):
    root = root if isinstance(root, Root) else Root(root)
    return os.path.isfile(os.path.join(root.root, MARKER))


def discard(root):
    """Remove a sandbox; ValueError for anything not marked as one."""
    root = root if isinstance(root, Root) else Root(root)
    if not is_sandbox(root):
        raise ValueError(f"not a sandbox, refusing to remove: {root.root}")
    # The data first and the marker last: a discard that fails partway (a file still held open)
    # leaves a copy that is still marked, so it can be discarded again rather than stranded.
    for name in os.listdir(root.root):
        if name != MARKER:
            p = os.path.join(root.root, name)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    os.remove(os.path.join(root.root, MARKER))
    os.rmdir(root.root)
