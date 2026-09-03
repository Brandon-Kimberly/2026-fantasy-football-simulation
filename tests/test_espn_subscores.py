"""F29: K/IDP epistemic disagreement from ESPN raw stat lines (docs/AUDIT_PLAN.md).

Written BEFORE the implementation (rule 1) and confirmed failing against the current
behaviour, where K/IDP players get floor-only epistemic because the ESPN blend excludes
them at the points level. The design under test: both sources' stat lines scored under
THIS league's settings on the shared category subset (11 of 12 IDP keys -- TFL excluded,
ESPN's 'Stuffs' measures a narrower quantity; K excludes the per-yard bonus ESPN cannot
compute from bands); the disagreement joins max(floor, spread/2); the MEAN stays
Sleeper-only for K/IDP (ESPN's missing TFL would bias a blend low).
"""
import math
import unittest
from unittest.mock import MagicMock, mock_open, patch

from fantasy_sim.sync import (
    generate_player_baselines, _shared_subscore, IDP_SHARED_DISAGREEMENT_KEYS,
)
from fantasy_sim.clients.espn import espn_idp_subscore, espn_k_subscore
from fantasy_sim.config import EPISTEMIC_ERROR_RATES

IDP_SCORING = {"idp_tkl_solo": 1.5, "idp_tkl_ast": 0.75, "idp_sack": 4.0,
               "idp_qb_hit": 1.0, "idp_tkl_loss": 2.0}
K_SCORING = {"fgm": 3.0, "xpm": 2.0, "fgmiss": -2.0, "xpmiss": -2.0,
             "fgm_yds_over_30": 0.075}


class TestSharedSubscore(unittest.TestCase):
    def test_idp_subscore_uses_shared_keys_only(self):
        # solo 4*1.5 + ast 4*0.75 + qb_hit 2*1.0 = 11.0; tkl_loss deliberately NOT counted
        stats = {"idp_tkl_solo": 4, "idp_tkl_ast": 4, "idp_qb_hit": 2, "idp_tkl_loss": 3}
        self.assertNotIn("idp_tkl_loss", IDP_SHARED_DISAGREEMENT_KEYS)
        self.assertAlmostEqual(_shared_subscore(stats, IDP_SCORING, "LB"), 11.0)

    def test_k_subscore_excludes_per_yard_bonus(self):
        # 2 made FGs *3 + 3 XP *2 + 0.5 missed FG *-2 = 11.0; fgm_yds must contribute nothing
        stats = {"fgm": 2.0, "fga": 2.5, "xpm": 3.0, "xpa": 3.0, "fgm_yds": 80.0}
        self.assertAlmostEqual(_shared_subscore(stats, K_SCORING, "K"), 11.0)


class TestEspnSubscoreMapping(unittest.TestCase):
    def test_idp_breakdown_scores_named_keys_and_id_100_ignores_112(self):
        # 5 solo *1.5 + 1 sack *4 + 2 qb hits (unnamed id '100') *1 = 13.5;
        # id '112' (Stuffs) must be ignored -- narrower than TFL, and TFL is excluded anyway
        breakdown = {"defensiveSoloTackles": 5.0, "defensiveSacks": 1.0,
                     "100": 2.0, "112": 3.0}
        self.assertAlmostEqual(espn_idp_subscore(breakdown, IDP_SCORING), 13.5)

    def test_k_breakdown_uses_made_missed_totals(self):
        breakdown = {"madeFieldGoals": 2.0, "missedFieldGoals": 0.5,
                     "madeExtraPoints": 3.0, "missedExtraPoints": 0.0,
                     "madeFieldGoalsFrom50Plus": 0.4}
        self.assertAlmostEqual(espn_k_subscore(breakdown, K_SCORING), 11.0)


class TestGenerateBaselinesWithSubscores(unittest.TestCase):
    def _fake_response(self, payload, status_code=200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = payload
        return resp

    def _run(self, espn_data):
        players_db = {"77": {"first_name": "Test", "last_name": "Backer",
                             "position": "LB", "team": "DET", "team_bye": 5}}
        live_rosters = {"SomeTeam": [{"name": "Test Backer", "pos": "LB", "team": "DET"}]}
        # full sleeper mean: solo 4*1.5 + ast 4*0.75 + tkl_loss 1*2.0 = 11.0; sub = 9.0
        payload = {"77": {"stats": {"idp_tkl_solo": 4, "idp_tkl_ast": 4, "idp_tkl_loss": 1}}}

        def fake_get(url, timeout=None):
            if "/projections/nfl/regular/2026/1" in url:
                return self._fake_response(payload)
            return self._fake_response({}, status_code=404)

        with patch("os.path.exists", return_value=False), \
             patch("requests.get", side_effect=fake_get), \
             patch("fantasy_sim.sync.fetch_espn_projection_data", return_value=espn_data), \
             patch("builtins.open", mock_open()), \
             patch("json.dump"):
            return generate_player_baselines(IDP_SCORING, players_db, live_rosters,
                                             current_year="2026", week=1)

    def test_idp_disagreement_lifts_epistemic_above_floor(self):
        # espn sub-score 17.0 vs sleeper sub 9.0 -> |D|/2 = 4.0 > floor 0.15*11.0 = 1.65
        result = self._run(({}, {"test backer": 17.0}))
        entry = result["Test Backer"]
        floor = round(EPISTEMIC_ERROR_RATES["LB"] * entry["mean"], 2)
        self.assertAlmostEqual(entry["std_epistemic"], 4.0)
        self.assertGreater(entry["std_epistemic"], floor)

    def test_idp_mean_stays_sleeper_only(self):
        # the huge ESPN sub-score must move ONLY the epistemic term, never the mean
        result = self._run(({}, {"test backer": 17.0}))
        self.assertAlmostEqual(result["Test Backer"]["mean"], 11.0)

    def test_idp_without_espn_falls_back_to_floor(self):
        result = self._run(({}, {}))
        entry = result["Test Backer"]
        self.assertAlmostEqual(entry["std_epistemic"],
                               round(EPISTEMIC_ERROR_RATES["LB"] * 11.0, 2))

    def test_offense_points_blend_unchanged_through_new_seam(self):
        players_db = {"88": {"first_name": "Test", "last_name": "Wideout",
                             "position": "WR", "team": "DET", "team_bye": 5}}
        live_rosters = {"SomeTeam": [{"name": "Test Wideout", "pos": "WR", "team": "DET"}]}
        payload = {"88": {"stats": {"rec": 5, "rec_yd": 50}}}   # sleeper mean 10.0

        def fake_get(url, timeout=None):
            if "/projections/nfl/regular/2026/1" in url:
                return self._fake_response(payload)
            return self._fake_response({}, status_code=404)

        with patch("os.path.exists", return_value=False), \
             patch("requests.get", side_effect=fake_get), \
             patch("fantasy_sim.sync.fetch_espn_projection_data",
                   return_value=({"test wideout": 40.0}, {})), \
             patch("builtins.open", mock_open()), \
             patch("json.dump"):
            result = generate_player_baselines({"rec": 1.0, "rec_yd": 0.1}, players_db,
                                               live_rosters, current_year="2026", week=1)

        entry = result["Test Wideout"]
        self.assertAlmostEqual(entry["mean"], 25.0)            # (10 + 40) / 2, as before
        self.assertAlmostEqual(entry["std_epistemic"], 15.0)   # max(floor, 30/2)
