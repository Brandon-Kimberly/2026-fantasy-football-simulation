"""webui.tools -- the launchable tools, their forms, and the argv each form builds (W2/W3).

An ALLOWLIST, not a discovery: only the tools named here can be launched, each through the
exact CLI the owner already types (`<this interpreter> -m scripts.<name> ...`), so the UI
adds no code path into the engine. Every argument is one argv item -- never a shell
string -- and a free-text value that begins with `-` is refused rather than passed, because
argparse would read it as an option.

Player-valued fields (`player`, `players`, `myplayer(s)`, `free_players`, `team_players`) are
resolved against webui.players before the argv is built: a typed name that is not an exact
player is either corrected to its one close match (and the job records the correction) or
refused with the candidates listed. The form offers suggestions from the same index.

Excluded on purpose (docs/WEB_UI.md W2): gameday (opens a browser), run_sync (W4), the
backtests and studies (milestone tools), bid_review --add / --bid and evaluate_move --log-tx
(they append to a TRACKED season log, which is not "the tool's own record under
data/decisions/"), and every path-valued option.

Pure stdlib except the team list, which comes from fantasy_sim.config (import-safe).
"""
import sys

from webui.players import Ambiguous, NoMatch, csv_split

MAX_TEXT = 200
PLAYER_KINDS = ("player", "players", "myplayer", "myplayers", "free_players", "team_players")
LIST_KINDS = ("players", "myplayers", "free_players", "team_players")


class FormError(ValueError):
    """A submitted value the form schema rejects. Rendered back on the form, never launched."""


def _team_choices():
    from fantasy_sim.config import MY_TEAM, TEAM_NAME_MAP
    names = sorted(set(TEAM_NAME_MAP.values()))
    return names, MY_TEAM


class Field:
    def __init__(self, name, kind, label=None, default=None, help="", required=False,
                 positional=False, choices=None, owner_field=None):
        self.name, self.kind, self.label = name, kind, label or name.replace("_", " ")
        self.default, self.help, self.required = default, help, required
        self.positional, self.choices, self.owner_field = positional, choices, owner_field

    @property
    def flag(self):
        return "--" + self.name.replace("_", "-")

    @property
    def is_player(self):
        return self.kind in PLAYER_KINDS

    @property
    def is_list(self):
        return self.kind in LIST_KINDS

    def owner_spec(self, form=None):
        """Which roster suggestions and resolution draw from: 'mine', 'free', a team field's
        current value, or None for every player."""
        if self.kind in ("myplayer", "myplayers"):
            return "mine"
        if self.kind == "free_players":
            return "free"
        if self.kind == "team_players" and self.owner_field:
            return (form or {}).get(self.owner_field) or None
        return None

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
        elif self.kind != "text" and not self.is_player:
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
    def __init__(self, name, question, fields, note="", heavy=False, forced=(), engine=False):
        self.name, self.question, self.fields, self.note, self.heavy = name, question, fields, note, heavy
        # `forced`: argv items every launch carries, before the form's -- the W3 report is
        # ALWAYS --skip-sync (the UI never syncs: docs/WEB_UI.md W4). `engine`: a full
        # simulation run; the launch page shows freshness and the run windows first, and a
        # STALE tree refuses the launch before the tool would.
        self.forced, self.engine = tuple(forced), engine

    @property
    def module(self):
        return "scripts." + self.name

    @property
    def title(self):
        return humanize(self.name)

    def argv(self, form, python=None):
        """[python, -m, scripts.<name>, ...] from a submitted mapping. Forced items first,
        then options, then positionals, so a positional can never be swallowed as an
        option's value."""
        opts, pos = [], []
        for f in self.fields:
            items = f.parse(form.get(f.name))
            (pos if f.positional else opts).append(items)
        out = [python or sys.executable, "-m", self.module, *self.forced]
        for items in opts + pos:
            out.extend(items)
        return out


def humanize(name):
    return str(name or "").replace("_", " ").strip().capitalize()


def resolve_form(tool, form, index, mine=None):
    """(corrected form, notes). Every player-valued field is resolved against the index:
    exact names pass, a unique close match is substituted and noted, anything else is a
    FormError that lists the candidates. `index` None = no resolution (tests without a tree)."""
    out = {k: form.get(k, "") for k in (getattr(form, "keys", lambda: [])())}
    for f in tool.fields:
        out.setdefault(f.name, form.get(f.name, ""))
    notes = []
    if index is None or not getattr(index, "players", None):
        return out, notes          # no pool on disk (no sync yet): nothing to resolve against
    for f in tool.fields:
        if not f.is_player:
            continue
        raw = (out.get(f.name) or "").strip()
        if not raw:
            continue
        owner = f.owner_spec(out)
        names = csv_split(raw) if f.is_list else [raw]
        fixed = []
        for n in names:
            try:
                canonical, exact = index.resolve(n, owner, mine)
            except Ambiguous as ex:
                raise FormError(f"{f.label}: '{n}' could be " + ", ".join(ex.options) + " -- pick one") from None
            except NoMatch as ex:
                where = {"mine": "on your roster", "free": "among the free agents"}.get(owner, f"on {owner}" if owner else "in the player pool")
                if ex.elsewhere:
                    raise FormError(f"{f.label}: {n} is on {ex.elsewhere}, not {where.replace('on ', '').replace('among ', '')}") from None
                raise FormError(f"{f.label}: no player named '{n}' {where}") from None
            if not exact:
                notes.append(f"{n} → {canonical}")
            fixed.append(canonical)
        out[f.name] = ", ".join(fixed)
    return out, notes


def label_for(tool, form):
    """A job title a person would write: the tool, then the names that matter -- never
    the numeric knobs. 'Compare players · A vs B', 'Optimize lineup · <team>'."""
    vals = []
    for f in tool.fields:
        if f.kind in ("int", "float", "flag"):
            continue
        v = (form.get(f.name) or "").strip()
        if v:
            vals.append(v)
    if tool.name == "compare_players" and len(vals) >= 2:
        return f"{tool.title} · {vals[0]} vs {vals[1]}"
    return tool.title + (" · " + " · ".join(vals[:3]) if vals else "")


def _sims(default, help=""):
    return Field("sims", "int", default=default, help=help or f"simulations; default {default}")


TOOLS = {t.name: t for t in (
    Tool("optimize_lineup", "What lineup does the engine's own rule set, and by how much?",
         [team(), WEEK, _sims(1000), SEED, CANONICAL]),
    Tool("matchup_lineup", "Against this week's opponent, play safe or swing for variance?",
         [team(), WEEK, team("opponent", default_mine=False), _sims(5000), SEED,
          Field("k", "float", default=0.5, help="risk weight; default 0.5"),
          Field("no_cross", "flag", label="no cross", help="skip the cross-construction table"),
          Field("opponent_lineup", "team_players", help="comma-separated; blank = his max-expectation lineup", owner_field="opponent"),
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
         [Field("a", "player", label="player A", required=True, positional=True),
          Field("b", "player", label="player B", required=True, positional=True),
          WEEK, _sims(2000), SEED,
          Field("light", "flag", help="sample both from baseline parameters; no simulation (seconds, not minutes)")],
         note="a rostered player triggers a reduced simulation (~2 min); --light skips it", heavy=True),
    Tool("evaluate_trade", "Is this specific trade good for me? Two paired full simulations on the same seeds.",
         [team("team_a", label="team A"), Field("a_gives", "team_players", label="A gives", help="comma-separated", owner_field="team_a"),
          team("team_b", default_mine=False, label="team B"), Field("b_gives", "team_players", label="B gives", help="comma-separated", owner_field="team_b"),
          Field("a_drops", "team_players", label="A drops", owner_field="team_a"), Field("b_drops", "team_players", label="B drops", owner_field="team_b"),
          Field("a_faab", "int", label="A sends FAAB", default=0), Field("b_faab", "int", label="B sends FAAB", default=0),
          Field("batches", "int", default=10), _sims(300)],
         note="minutes: two full simulations", heavy=True),
    Tool("evaluate_move", "What is adding X (and dropping Y) worth, in the same paired Champ%/Playoff% terms?",
         [team(), Field("add", "free_players", help="comma-separated free agents"), Field("drop", "myplayers", help="comma-separated rostered players"),
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

# W3: the two engine entry points, through the same runner and the same lock. The report is
# ALWAYS --skip-sync -- sync stays a terminal act (W4) -- and it self-gates on STALE data.
ENGINE = {t.name: t for t in (
    Tool("run_simulation", "Run the Monte Carlo engine on the data on disk: exports, charts, boom/bust, floor/ceiling.",
         [], note="the full run (10,000 simulations): minutes, and the week's exports are rewritten", heavy=True, engine=True),
    Tool("weekly_report", "The weekly digest from the data on disk: simulate -> charts -> grades -> lineup -> matchup -> waivers.",
         [team(), Field("full", "flag", help="also run the trade-target finder"),
          Field("sims", "int", default=5000, help="matchup joint-sample size; default 5000"),
          Field("evaluate", "int", default=0, help="with full: paired evaluations of the top N trade packages"),
          Field("embed", "flag", help="inline the charts as data URIs (portable, 15-20 MB)"),
          CANONICAL],
         note="always --skip-sync: this UI never syncs. STALE data stops the run (the digest carries a FAILED banner and the job is VOID). "
              "A non-canonical run files under week_NN/archive/ and appends a non-canonical row to the predictions log, exactly as a hand run does.",
         heavy=True, forced=("--skip-sync",), engine=True),
)}


def get(name):
    if name in TOOLS:
        return TOOLS[name]
    if name in ENGINE:
        return ENGINE[name]
    raise KeyError(name)
