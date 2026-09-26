"""webui.tools -- the launchable tools, their forms, and the argv each form builds (W2).

An ALLOWLIST, not a discovery: only the tools named here can be launched, each through the
exact CLI the owner already types (`<this interpreter> -m scripts.<name> ...`), so the UI
adds no code path into the engine. Every argument is one argv item -- never a shell
string -- and a free-text value that begins with `-` is refused rather than passed, because
argparse would read it as an option.

Excluded on purpose (docs/WEB_UI.md W2): gameday (opens a browser), run_sync (W4),
run_simulation / weekly_report (W3), the backtests and studies (milestone tools), bid_review
--add / --bid and evaluate_move --log-tx (they append to a TRACKED season log, which is not
"the tool's own record under data/decisions/"), and every path-valued option.

Pure stdlib except the team list, which comes from fantasy_sim.config (import-safe).
"""
import sys

MAX_TEXT = 200


class FormError(ValueError):
    """A submitted value the form schema rejects. Rendered back on the form, never launched."""


def _team_choices():
    from fantasy_sim.config import MY_TEAM, TEAM_NAME_MAP
    names = sorted(set(TEAM_NAME_MAP.values()))
    return names, MY_TEAM


class Field:
    def __init__(self, name, kind, label=None, default=None, help="", required=False,
                 positional=False, choices=None):
        self.name, self.kind, self.label = name, kind, label or name.replace("_", " ")
        self.default, self.help, self.required = default, help, required
        self.positional, self.choices = positional, choices

    @property
    def flag(self):
        return "--" + self.name.replace("_", "-")

    def parse(self, raw):
        """The argv items this field contributes for one submitted value ('' = omitted)."""
        raw = "" if raw is None else str(raw).strip()
        if self.kind == "flag":
            return [self.flag] if raw in ("1", "on", "true", "yes") else []
        if raw == "":
            if self.required:
                raise FormError(f"{self.label} is required")
            return []
        if len(raw) > MAX_TEXT:
            raise FormError(f"{self.label}: longer than {MAX_TEXT} characters")
        if raw.startswith("-"):
            raise FormError(f"{self.label}: a value may not begin with '-' (argparse would read it as an option)")
        if "\n" in raw or "\r" in raw or "\x00" in raw:
            raise FormError(f"{self.label}: control characters are not allowed")
        if self.kind == "int":
            try:
                raw = str(int(raw))
            except ValueError:
                raise FormError(f"{self.label}: not a whole number: {raw!r}") from None
        elif self.kind == "float":
            try:
                raw = repr(float(raw))
            except ValueError:
                raise FormError(f"{self.label}: not a number: {raw!r}") from None
        elif self.kind == "team":
            names, _mine = _team_choices()
            if raw not in names:
                raise FormError(f"{self.label}: not a team in this league: {raw!r}")
        elif self.kind != "text":
            raise FormError(f"{self.name}: unknown field kind {self.kind}")
        return [raw] if self.positional else [self.flag, raw]


def team(name="team", required=False, default_mine=True, label=None):
    names, mine = _team_choices()
    return Field(name, "team", label=label, default=(mine if default_mine else None),
                 required=required, choices=names)


WEEK = Field("week", "int", help="NFL week; default = the synced current week")
SEED = Field("seed", "int", help="RNG seed; blank = the tool's default")
CANONICAL = Field("canonical", "flag", label="canonical",
                  help="a deliberate run filed to week_NN/ instead of week_NN/archive/ -- off unless you mean it")
JSON_FLAG = Field("json", "flag", label="JSON output")


class Tool:
    def __init__(self, name, question, fields, note="", heavy=False):
        self.name, self.question, self.fields, self.note, self.heavy = name, question, fields, note, heavy

    @property
    def module(self):
        return "scripts." + self.name

    def argv(self, form, python=None):
        """[python, -m, scripts.<name>, ...] from a submitted mapping. Options first, then
        positionals, so a positional can never be swallowed as an option's value."""
        opts, pos = [], []
        for f in self.fields:
            items = f.parse(form.get(f.name))
            (pos if f.positional else opts).append(items)
        out = [python or sys.executable, "-m", self.module]
        for items in opts + pos:
            out.extend(items)
        return out


def _sims(default, help=""):
    return Field("sims", "int", default=default, help=help or f"simulations; default {default}")


TOOLS = {t.name: t for t in (
    Tool("optimize_lineup", "What lineup does the engine's own rule set, and by how much?",
         [team(), WEEK, _sims(1000), SEED, CANONICAL]),
    Tool("matchup_lineup", "Against this week's opponent, play safe or swing for variance?",
         [team(), WEEK, team("opponent", default_mine=False), _sims(5000), SEED,
          Field("k", "float", default=0.5, help="risk weight; default 0.5"),
          Field("no_cross", "flag", label="no cross", help="skip the cross-construction table"),
          Field("opponent_lineup", "text", help="comma-separated names; blank = his max-expectation lineup"),
          CANONICAL]),
    Tool("waiver_targets", "Who should I claim, and what should I bid?",
         [team(), WEEK, Field("top", "int", default=15), Field("positions", "text", help="comma-separated, e.g. RB,WR"),
          _sims(2000), SEED, CANONICAL]),
    Tool("roster_grades", "How good is each roster, really?",
         [team(default_mine=False, label="team (blank = every team)"), WEEK, CANONICAL]),
    Tool("find_trades", "Who should I be trading for, and who wants what I have?",
         [team(), WEEK, Field("top", "int", default=10),
          Field("seller_threshold", "float", default=35.0),
          Field("evaluate", "int", default=0, help="paired evaluations of the top N packages (each is a full simulation pair)"),
          Field("batches", "int", default=3), _sims(1000), CANONICAL], heavy=True),
    Tool("compare_players", "Start A or B this week? P(A > B) from the joint simulated distributions.",
         [Field("a", "text", label="player A", required=True, positional=True),
          Field("b", "text", label="player B", required=True, positional=True),
          WEEK, _sims(2000), SEED,
          Field("light", "flag", help="sample both from baseline parameters; no simulation (seconds, not minutes)")],
         note="a rostered player triggers a reduced simulation (~2 min); --light skips it", heavy=True),
    Tool("evaluate_trade", "Is this specific trade good for me? Two paired full simulations on the same seeds.",
         [team("team_a", label="team A"), Field("a_gives", "text", label="A gives", help="comma-separated"),
          team("team_b", default_mine=False, label="team B"), Field("b_gives", "text", label="B gives", help="comma-separated"),
          Field("a_drops", "text", label="A drops"), Field("b_drops", "text", label="B drops"),
          Field("a_faab", "int", label="A sends FAAB", default=0), Field("b_faab", "int", label="B sends FAAB", default=0),
          Field("batches", "int", default=10), _sims(300)],
         note="minutes: two full simulations", heavy=True),
    Tool("evaluate_move", "What is adding X (and dropping Y) worth, in the same paired Champ%/Playoff% terms?",
         [team(), Field("add", "text", help="comma-separated free agents"), Field("drop", "text", help="comma-separated rostered players"),
          Field("bid", "int", help="FAAB bid: adds the budget-cost block"), Field("batches", "int", default=10), _sims(300)],
         note="minutes: two full simulations", heavy=True),
    Tool("matchup_watch", "What should I be watching this week?",
         [team(), WEEK, team("opponent", default_mine=False)]),
    Tool("roster_calendar", "Which weeks am I short, and who do I cut when the IR man comes back?", [team()]),
    Tool("live_matchup", "Am I winning right now, and what is still to come?",
         [team(), WEEK, _sims(40000), Field("seed", "int", default=20260913), JSON_FLAG]),
    Tool("trade_leverage", "Sell-high candidates, and rivals' below-replacement slots my surplus could fix.",
         [team(), WEEK, Field("season", "text", default="2026")]),
    Tool("market_sweep", "Every starting slot vs the best free agent, on engine values.", [team(), WEEK]),
    Tool("check_freshness", "Has sync run this week, and did it succeed? (online: checks the week roll)",
         [Field("offline", "flag", help="skip the Sleeper week check")]),
    Tool("run_windows", "This week's canonical-run windows: open, covered, or missed.", [JSON_FLAG]),
    Tool("odds_history", "How have my championship odds moved across canonical runs?",
         [team(), JSON_FLAG]),
    Tool("luck_ledger", "Am I actually unlucky? Five pre-registered measures against the league.",
         [Field("season", "text"), Field("all", "flag", label="every season in the chain"),
          team(default_mine=False, label="team (blank = mine)"), WEEK, JSON_FLAG]),
    Tool("decision_scorecard", "Did we make bad calls? The week's start/sit decisions, scored.",
         [team(), Field("week", "int", required=True, help="the completed week to score"), JSON_FLAG]),
    Tool("data_health", "Every source the model consumes, checked against what is on disk.",
         [Field("season", "text", default="2026"), WEEK, JSON_FLAG]),
    Tool("bid_review", "What I suggested vs what I bid vs what it cost (review only; recording a claim stays a terminal act).",
         [team(), WEEK, JSON_FLAG]),
)}


def get(name):
    if name not in TOOLS:
        raise KeyError(name)
    return TOOLS[name]
