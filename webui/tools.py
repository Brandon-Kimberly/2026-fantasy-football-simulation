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

# What a form calls an argument. The argv keeps the tool's own flag; the label is for a
# person, so 'sims' is 'simulations' and 'light' says what it does.
LABELS = {"sims": "simulations", "seed": "random seed", "light": "quick mode",
          "k": "risk weight", "no_cross": "skip the cross-construction table", "top": "how many to show",
          "evaluate": "packages to simulate", "batches": "batches", "full": "include the trade finder",
          "embed": "embed the charts", "seller_threshold": "seller threshold (playoff %)", "offline": "offline",
          "all": "every season", "bid": "FAAB bid", "json": "JSON output", "canonical": "canonical run",
          "week": "week", "team": "team", "opponent": "opponent", "positions": "positions", "season": "season"}


class FormError(ValueError):
    """A submitted value the form schema rejects. Rendered back on the form, never launched."""


def _team_choices():
    from fantasy_sim.config import MY_TEAM, TEAM_NAME_MAP
    names = sorted(set(TEAM_NAME_MAP.values()))
    return names, MY_TEAM


class Field:
    def __init__(self, name, kind, label=None, default=None, help="", required=False,
                 positional=False, choices=None, owner_field=None):
        self.name, self.kind, self.label = name, kind, label or LABELS.get(name) or name.replace("_", " ")
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


WEEK = Field("week", "int")
SEED = Field("seed", "int", help="blank uses the tool's own seed")
CANONICAL = Field("canonical", "flag", label="canonical run",
                  help="files the result under week_NN/ instead of week_NN/archive/. Leave off unless this is the week's official run")
# JSON-capable tools always run --json (Tool.forced): the job page renders the JSON as
# tables and the text form is one click away in the log, so the form no longer asks.


class Tool:
    def __init__(self, name, question, fields, note="", heavy=False, forced=(), engine=False, detail=""):
        # `question`: what an owner asks, in plain words (both views). `detail`: how the tool
        # answers it, for the developer view. `note`: cost and side effects, developer view only.
        self.name, self.question, self.fields, self.note, self.heavy = name, question, fields, note, heavy
        self.detail = detail
        # `forced`: argv items every launch carries, before the form's -- the W3 report is
        # ALWAYS --skip-sync (syncing is its own guarded page: docs/WEB_UI.md W4). `engine`: a full
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
    return Field("sims", "int", default=default, help=help)


TOOLS = {t.name: t for t in (
    Tool("optimize_lineup", "Who should I start this week, and how many points does it add?",
         [team(), WEEK, _sims(1000), SEED, CANONICAL],
         detail="The model's own start/sit rule on this week's projections, against the lineup you have set."),
    Tool("matchup_lineup", "Against this week's opponent, should I play it safe or chase upside?",
         [team(), WEEK, team("opponent", default_mine=False), _sims(5000), SEED,
          Field("k", "float", default=0.5, help="how much to penalise spread; 0 ignores it"),
          Field("no_cross", "flag", label="no cross", help="skip the table that scores each lineup against the others"),
          Field("opponent_lineup", "team_players", help="blank assumes their highest-scoring lineup", owner_field="opponent"),
          CANONICAL],
         detail="Four lineup constructions on one joint sample, each with P(beat opponent) and P(beat median)."),
    Tool("waiver_targets", "Who should I claim, and what should I bid?",
         [team(), WEEK, Field("top", "int", default=15), Field("positions", "text", help="e.g. RB,WR"),
          _sims(2000), SEED, CANONICAL],
         detail="Roster gaps against the free-agent pool, ranked by value over replacement, with P(beats my starter)."),
    Tool("roster_grades", "How good is each roster, really?",
         [team(default_mine=False, label="team (blank for every team)"), WEEK, CANONICAL],
         detail="Tier and VORP per position and overall, and a league table by lineup VORP."),
    Tool("find_trades", "Who should I trade for, and who wants what I have?",
         [team(), WEEK, Field("top", "int", default=10),
          Field("seller_threshold", "float", default=35.0, help="teams below this playoff chance count as sellers"),
          Field("evaluate", "int", default=0, help="each one is a full simulation pair, so minutes apiece"),
          Field("batches", "int", default=3), _sims(1000), CANONICAL], heavy=True,
         detail="Bench players elsewhere who would start for me, and my surplus that another team needs."),
    Tool("compare_players", "Which of two players should I start this week?",
         [Field("a", "player", label="player A", required=True, positional=True),
          Field("b", "player", label="player B", required=True, positional=True),
          WEEK, _sims(2000), SEED,
          Field("light", "flag", help="samples both from their baselines instead of simulating: seconds, not minutes")],
         note="A rostered player triggers a reduced simulation (about 2 minutes); quick mode skips it.", heavy=True,
         detail="P(A outscores B) from their joint simulated distributions."),
    Tool("evaluate_trade", "Is this trade good for me?",
         [team("team_a", label="team A"), Field("a_gives", "team_players", label="A gives", owner_field="team_a"),
          team("team_b", default_mine=False, label="team B"), Field("b_gives", "team_players", label="B gives", owner_field="team_b"),
          Field("a_drops", "team_players", label="A drops", owner_field="team_a"), Field("b_drops", "team_players", label="B drops", owner_field="team_b"),
          Field("a_faab", "int", label="A sends FAAB", default=0), Field("b_faab", "int", label="B sends FAAB", default=0),
          Field("batches", "int", default=10), _sims(300)],
         note="Takes minutes: two full simulations.", heavy=True,
         detail="Two full simulations on the same seeds, with and without the trade; the playoff and title change for both sides and every other team."),
    Tool("evaluate_move", "What would an add (and a drop) do to my playoff and title odds?",
         [team(), Field("add", "free_players"), Field("drop", "myplayers"),
          Field("bid", "int", help="adds what the bid costs from the budget"), Field("batches", "int", default=10), _sims(300)],
         note="Takes minutes: two full simulations.", heavy=True,
         detail="Two full simulations on the same seeds, with and without the move."),
    Tool("matchup_watch", "What should I watch this week?",
         [team(), WEEK, team("opponent", default_mine=False)],
         detail="Both lineups by NFL game: stacks, injury designations, and the games both sides have players in."),
    Tool("roster_calendar", "Which weeks am I short, and who goes when a player comes off IR?", [team()],
         detail="Bye and injury gaps by week, and the roster squeeze when an IR player returns."),
    Tool("live_matchup", "Am I winning right now, and what's still to play?",
         [team(), WEEK, _sims(40000), Field("seed", "int", default=20260913)], forced=("--json",),
         detail="Points banked plus each unplayed starter's projection, simulated. No injury discount before kickoff (F51)."),
    Tool("trade_leverage", "Who should I sell high, and which rivals need what I have?",
         [team(), WEEK, Field("season", "text", default="2026")],
         detail="Sell-high candidates, and rivals' below-replacement slots my surplus could fill."),
    Tool("market_sweep", "Is any free agent better than one of my starters?", [team(), WEEK],
         detail="Every starting slot against the best free agent, on engine values."),
    Tool("check_freshness", "Is the data up to date?",
         [Field("offline", "flag", help="skips the check against Sleeper's current week")],
         detail="Has the sync run this week, and did it succeed? Online, it also checks that the week has not rolled over."),
    Tool("run_windows", "Which of this week's canonical-run windows are open, covered or missed?", [], forced=("--json",)),
    Tool("odds_history", "How have my title odds moved this season?",
         [team()], forced=("--json",), detail="Canonical runs only."),
    Tool("luck_ledger", "Am I actually unlucky?",
         [Field("season", "text"), Field("all", "flag", label="every season"),
          team(default_mine=False, label="team (blank for mine)"), WEEK], forced=("--json",),
         detail="Five pre-registered measures, each against the league average."),
    Tool("decision_scorecard", "How good were a week's start/sit calls?",
         [team(), Field("week", "int", required=True, help="a completed week")], forced=("--json",)),
    Tool("data_health", "Is every data source the model uses in good shape?",
         [Field("season", "text", default="2026"), WEEK], forced=("--json",),
         detail="Each source checked against what is on disk."),
    Tool("bid_review", "How did my bids compare with the suggestions, and what did they cost?",
         [team(), WEEK], forced=("--json",),
         detail="Review only. Recording a claim stays a terminal job."),
)}

# W3: the two engine entry points, through the same runner and the same lock. The report is
# ALWAYS --skip-sync -- sync stays a terminal act (W4) -- and it self-gates on STALE data.
ENGINE = {t.name: t for t in (
    Tool("run_simulation", "Rerun this week's forecast on the current data.",
         [], note="The full 10,000-season run takes minutes and rewrites the week's exports.", heavy=True, engine=True,
         detail="The Monte Carlo engine on the data on disk: exports, charts, boom/bust, floor/ceiling."),
    Tool("weekly_report", "Build this week's digest from the current data.",
         [team(), Field("full", "flag", help="also runs the trade-target finder"),
          Field("sims", "int", default=5000, help="joint samples for the matchup section"),
          Field("evaluate", "int", default=0, help="with the trade finder on: full simulation pairs for the top N packages"),
          Field("embed", "flag", help="puts the charts inside the file so it travels on its own (15-20 MB)"),
          CANONICAL],
         note="Always runs with --skip-sync: syncing has its own page, with a key check and a backup first. "
              "STALE data stops the run; the digest carries a FAILED banner and the job is VOID. "
              "A non-canonical run files under week_NN/archive/ and adds a non-canonical row to the predictions log, "
              "the same as a run from the terminal.",
         heavy=True, forced=("--skip-sync",), engine=True,
         detail="Simulate, then charts, grades, lineup, matchup and waivers."),
)}


# W8: what the simple view offers -- the questions a manager asks, by their plain names.
# Everything else (studies, health checks, the engine runs) is the owner's, in dev mode.
SIMPLE_TOOLS = ("optimize_lineup", "matchup_lineup", "compare_players", "waiver_targets",
                "evaluate_move", "evaluate_trade", "roster_calendar", "live_matchup")
SIMPLE_FIELD_KINDS = ("team",) + PLAYER_KINDS


def simple_fields(tool):
    """The fields the simple view shows: who and when. Sizes, seeds, batches, flags and
    canonical runs keep their terminal defaults and are never asked."""
    return [f for f in tool.fields if f.kind in SIMPLE_FIELD_KINDS or f.name in ("week", "season")]


def get(name):
    if name in TOOLS:
        return TOOLS[name]
    if name in ENGINE:
        return ENGINE[name]
    raise KeyError(name)
