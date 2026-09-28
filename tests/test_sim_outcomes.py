"""
tests.test_sim_outcomes -- the per-simulation outcome export (docs/WEB_UI_ROADMAP.md Decision 1,
UI-E5; owner ruling 2026-09-28: an export that only ADDS a file is MINOR).

The playoff machine, leverage, the rooting guide and clinch markers all ask a conditional
question -- "in the simulated seasons where X happened, how often did Y?" -- and the web UI may
not re-simulate. So run_simulation records, for every simulated season, each remaining
regular-season game's result, every team's result against that week's median, the final seeds
and the champion, and writes them through save_json as sim_outcomes_week_<n>.json.

The capture only READS values the loop already computed; it draws nothing, so every existing
output is byte-identical (tests.test_golden_master, after its regeneration in its own commit,
differs from the previous goldens only by the new file's entry). These tests go further than
"the file exists": decoded, the export must reproduce the engine's OWN counts -- each season's
final wins, the seed matrix and the title rates -- run through the golden master's hermetic
sandbox on the week-6 scenario.
"""
import unittest
from unittest.mock import patch

from fantasy_sim.simulation import FantasySimulationEngine
from tests.golden_master import _sandbox

BATCHES, SIMS = 2, 30


def decode(doc):
    """[(weeks: [(h2h digits, median bits)], seeds [team index by rank], champ index)]."""
    out = []
    for row in doc["seasons"]:
        weeks, seeds, champ = row.split(";")
        wk = []
        for code in (weeks.split("|") if weeks else []):
            n = len(code) - doc["median_hex"]
            wk.append((code[:n], int(code[n:], 16)))
        out.append((wk, [int(c) for c in seeds.split(",") if c != ""], int(champ) if champ != "" else None))
    return out


class TestSimOutcomesExport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rec = {}
        real = FantasySimulationEngine.export_and_visualize

        def recording(self, *args):
            rec.setdefault("args", args)
            return real(self, *args)
        with _sandbox("week06", BATCHES, SIMS) as saved:
            eng = FantasySimulationEngine()
            with patch.object(FantasySimulationEngine, "export_and_visualize", recording):
                eng.run_simulation()
            cls.saved = dict(saved)
        cls.eng, cls.args = eng, rec["args"]
        cls.doc = cls.saved.get(f"sim_outcomes_week_{eng.current_week}.json")

    def test_the_export_is_written_through_save_json(self):
        self.assertIsNotNone(self.doc, sorted(self.saved))
        d = self.doc
        self.assertEqual(d["teams"], list(self.eng.team_names))
        self.assertEqual(d["weeks"], list(range(self.eng.current_week, 15)))
        self.assertEqual(len(d["seasons"]), BATCHES * SIMS)
        self.assertEqual(sorted(d["matchups"], key=int), [str(w) for w in d["weeks"]])

    def test_each_seasons_wins_rebuild_from_its_games_and_medians(self):
        d, wins = self.doc, self.args[0]
        teams = d["teams"]
        for s, (weeks, _seeds, _champ) in enumerate(decode(d)):
            total = {t: float(self.eng.actual_total_wins[t]) for t in teams}
            for w, (h2h, med) in zip(d["weeks"], weeks):
                for (t1, t2), digit in zip(d["matchups"][str(w)], h2h):
                    total[t1] += {"1": 1.0, "0": 0.0, "2": 0.5}[digit]
                    total[t2] += {"1": 0.0, "0": 1.0, "2": 0.5}[digit]
                if d["median_enabled"]:
                    for i, t in enumerate(teams):
                        total[t] += (med >> i) & 1
            for t in teams:
                self.assertAlmostEqual(total[t], wins[t][s], places=6, msg=f"season {s}, {t}")

    def test_the_seeds_reproduce_the_seed_matrix(self):
        d, seed_matrix = self.doc, self.args[16]
        counts = {t: [0] * len(d["teams"]) for t in d["teams"]}
        for _w, seeds, _c in decode(d):
            for rank, ti in enumerate(seeds):
                counts[d["teams"][ti]][rank] += 1
        for t in d["teams"]:
            self.assertEqual(counts[t], [int(x) for x in seed_matrix[t]], t)

    def test_the_champions_reproduce_the_title_rates(self):
        d, champ_rates = self.doc, self.args[3]
        n = {t: 0 for t in d["teams"]}
        for _w, _s, c in decode(d):
            n[d["teams"][c]] += 1
        for t in d["teams"]:
            self.assertAlmostEqual(n[t] / (BATCHES * SIMS), sum(champ_rates[t]) / BATCHES, places=9, msg=t)


if __name__ == "__main__":
    unittest.main()
