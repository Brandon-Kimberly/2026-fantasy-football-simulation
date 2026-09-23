"""B22: price a pending scoring change BEFORE it lands.

F49's repricing -- every IDP under `idp_sack` 2.0 and `idp_qb_hit` 0.5 -- was a one-off
scratchpad script. It found the owner's starting DL was the league's biggest loser
(-19.5%) and that he should refuse any trade bringing him a pass rusher. Nobody else can
reproduce that, and the league votes on scoring changes more than once a season.

`sync.py` reads scoring live from the league object, so the moment a change is REAL this
tool becomes a no-op. Its whole value is the window before the vote.

THE ARITHMETIC IS SYNC'S OWN, deliberately. `generate_player_baselines` scores a stat
line as `sum(stats[k] * mult for k, mult in scoring.items())`, divided by games played
for a season-long projection. Reimplementing that differently would price a change the
model would never actually apply, so `score_line` is the same expression and a test
pins it against the real one.

THE OVERRIDE IS PARTIAL. `{"idp_sack": 2.0}` means change that one category and keep
every other setting as it is. A full-replacement reading would silently zero the other
forty-odd categories and report a catastrophe for everyone.

AND A TYPO MUST BE LOUD. `{"idp_sacks": 2.0}` -- plural, not a real key -- would change
nothing at all and print a clean "no impact", which is the most dangerous possible
output for a tool whose job is to warn. Unknown keys raise.

Written before the module existed and confirmed failing (rule 1).
"""
import unittest

SCORING = {"idp_sack": 4.0, "idp_qb_hit": 1.0, "idp_tkl_solo": 1.5, "idp_tkl_loss": 2.0,
           "rec": 1.0, "rec_yd": 0.1}

# Sleeper's projection payload shape: {pid: {"stats": {...}}} or {pid: {...}}
PROJ = {
    "1": {"stats": {"idp_sack": 1.0, "idp_qb_hit": 2.0, "idp_tkl_solo": 4.0,
                    "idp_tkl_loss": 1.0, "gp": 1.0}},
    "2": {"stats": {"rec": 6.0, "rec_yd": 80.0, "gp": 1.0}},
    "3": {"stats": {"idp_tkl_solo": 8.0, "gp": 1.0}},          # tackler, no pass rush
}
DB = {"1": {"first_name": "Pass", "last_name": "Rusher", "position": "DL"},
      "2": {"first_name": "Slot", "last_name": "Receiver", "position": "WR"},
      "3": {"first_name": "Thumper", "last_name": "Backer", "position": "LB"}}
ROSTERS = {"Quantum Ferrets": ["Pass Rusher", "Thumper Backer"],
           "Neon Walruses": ["Slot Receiver"]}


class TestScoreLineMatchesSync(unittest.TestCase):
    def test_it_is_the_same_expression_sync_uses(self):
        from fantasy_sim.reprice import score_line
        stats = PROJ["1"]["stats"]
        expected = sum(stats.get(k, 0.0) * m for k, m in SCORING.items())
        self.assertAlmostEqual(score_line(stats, SCORING), expected)

    def test_a_category_the_line_does_not_have_contributes_nothing(self):
        from fantasy_sim.reprice import score_line
        self.assertAlmostEqual(score_line({"rec": 2.0}, SCORING), 2.0)

    def test_an_empty_line_scores_zero(self):
        from fantasy_sim.reprice import score_line
        self.assertAlmostEqual(score_line({}, SCORING), 0.0)


class TestTheOverrideIsPartial(unittest.TestCase):
    def test_only_the_named_categories_change(self):
        from fantasy_sim.reprice import apply_override
        got = apply_override(SCORING, {"idp_sack": 2.0})
        self.assertAlmostEqual(got["idp_sack"], 2.0)
        self.assertAlmostEqual(got["idp_tkl_solo"], 1.5, msg="untouched settings survive")
        self.assertEqual(set(got), set(SCORING))

    def test_the_original_is_not_mutated(self):
        from fantasy_sim.reprice import apply_override
        apply_override(SCORING, {"idp_sack": 2.0})
        self.assertAlmostEqual(SCORING["idp_sack"], 4.0)

    def test_an_unknown_key_is_refused_loudly(self):
        """A typo would change nothing and print 'no impact' -- the most dangerous output
        a warning tool can produce."""
        from fantasy_sim.reprice import apply_override
        with self.assertRaises(KeyError) as ctx:
            apply_override(SCORING, {"idp_sacks": 2.0})
        self.assertIn("idp_sacks", str(ctx.exception))

    def test_setting_a_category_to_zero_is_allowed(self):
        """Removing a category is a real proposal, and 0.0 must not read as 'unset'."""
        from fantasy_sim.reprice import apply_override
        self.assertAlmostEqual(apply_override(SCORING, {"idp_qb_hit": 0.0})["idp_qb_hit"], 0.0)


class TestRepricing(unittest.TestCase):
    NEW = {"idp_sack": 2.0, "idp_qb_hit": 0.5}

    def _rows(self):
        from fantasy_sim.reprice import reprice_players
        return {r["name"]: r for r in reprice_players(PROJ, DB, SCORING, self.NEW)}

    def test_the_pass_rusher_loses_the_most(self):
        """F49's shape: a solo sack was 8.5 points and becomes 6.0."""
        rows = self._rows()
        self.assertLess(rows["Pass Rusher"]["delta"], 0)
        self.assertLess(rows["Pass Rusher"]["pct"], rows["Thumper Backer"]["pct"])

    def test_a_player_with_no_affected_stat_is_unchanged(self):
        rows = self._rows()
        self.assertAlmostEqual(rows["Thumper Backer"]["delta"], 0.0)
        self.assertAlmostEqual(rows["Slot Receiver"]["delta"], 0.0)

    def test_the_arithmetic_is_exact(self):
        rows = self._rows()
        r = rows["Pass Rusher"]
        # before: 1*4.0 + 2*1.0 + 4*1.5 + 1*2.0 = 14.0
        # after:  1*2.0 + 2*0.5 + 4*1.5 + 1*2.0 = 11.0
        self.assertAlmostEqual(r["before"], 14.0)
        self.assertAlmostEqual(r["after"], 11.0)
        self.assertAlmostEqual(r["delta"], -3.0)
        self.assertAlmostEqual(r["pct"], -3.0 / 14.0 * 100.0)

    def test_it_divides_by_games_played_for_a_season_projection(self):
        from fantasy_sim.reprice import reprice_players
        season = {"1": {"stats": dict(PROJ["1"]["stats"], gp=16.0)}}
        r = reprice_players(season, DB, SCORING, self.NEW, per_game=True)[0]
        self.assertAlmostEqual(r["before"], 14.0 / 16.0)

    def test_a_zero_scoring_player_reports_no_percentage_rather_than_dividing_by_zero(self):
        from fantasy_sim.reprice import reprice_players
        r = reprice_players({"9": {"stats": {}}},
                            {"9": {"first_name": "No", "last_name": "Stats"}},
                            SCORING, self.NEW)[0]
        self.assertIsNone(r["pct"])

    def test_rows_are_ranked_worst_hit_first(self):
        from fantasy_sim.reprice import reprice_players
        rows = reprice_players(PROJ, DB, SCORING, self.NEW)
        self.assertEqual(rows[0]["name"], "Pass Rusher")


class TestTeamImpact(unittest.TestCase):
    def test_it_sums_only_rostered_players(self):
        from fantasy_sim.reprice import reprice_players, team_impact
        rows = reprice_players(PROJ, DB, SCORING, {"idp_sack": 2.0, "idp_qb_hit": 0.5})
        t = {x["team"]: x for x in team_impact(rows, ROSTERS)}
        self.assertAlmostEqual(t["Quantum Ferrets"]["delta"], -3.0)
        self.assertAlmostEqual(t["Neon Walruses"]["delta"], 0.0)

    def test_an_unrostered_player_is_excluded(self):
        from fantasy_sim.reprice import reprice_players, team_impact
        rows = reprice_players(PROJ, DB, SCORING, {"idp_sack": 2.0})
        total = sum(abs(x["delta"]) for x in team_impact(rows, {"Quantum Ferrets": []}))
        self.assertAlmostEqual(total, 0.0)

    def test_teams_are_ranked_by_damage(self):
        from fantasy_sim.reprice import reprice_players, team_impact
        rows = reprice_players(PROJ, DB, SCORING, {"idp_sack": 2.0, "idp_qb_hit": 0.5})
        self.assertEqual(team_impact(rows, ROSTERS)[0]["team"], "Quantum Ferrets")


if __name__ == "__main__":
    unittest.main()
