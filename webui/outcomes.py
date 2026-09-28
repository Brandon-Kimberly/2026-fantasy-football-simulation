"""webui.outcomes -- the per-simulation outcome export, read (docs/WEB_UI_ROADMAP.md Decision 1,
UI-E5). Characterisation stub: the signatures the tests pin, nothing computed yet."""

MIN_SEASONS = 200


class Outcomes:
    def __init__(self, doc):
        self.rows = list((doc or {}).get("seasons") or [])
        self.week = None


def parse(doc):
    return Outcomes(doc)


def load(root, at_most=None):
    return None


def picks_from_args(o, args):
    return []


def conditional(o, picks, min_n=MIN_SEASONS):
    return {"n": 0, "total": 0, "refused": True, "teams": {}}


def leverage(o):
    return {"cells": {}, "games": {}}


def rooting(o, me, week):
    return {"games": [], "median": []}


def wins_curve(o, banked, team):
    return []


def markers(o, banked):
    return {}
