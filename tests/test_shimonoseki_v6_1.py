from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from engines.shimonoseki_v6_1.shimonoseki_engine_v6_1 import (
    ENGINE_ID,
    ENGINE_VERSION,
    ShimonosekiSiteEngineV61,
)
from engines.shimonoseki_v6_1.tests.test_regression_20260823 import docs, race


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "engines" / "shimonoseki_v6_1" / "master"


class ShimonosekiV61ProductionContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = ShimonosekiSiteEngineV61(MASTER)

    @staticmethod
    def payload() -> dict:
        races = []
        for race_no in range(1, 13):
            item = deepcopy(race(5))
            item["race"] = race_no
            races.append(item)
        return {
            "venueId": "shimonoseki",
            "date": "2026-08-23",
            "races": races,
            "tide": {"events": []},
        }

    def test_preliminary_12r_and_final_publication_contract(self):
        payload = self.engine.apply_preliminary_daily(self.payload())
        self.assertEqual(payload["engine"], ENGINE_ID)
        self.assertEqual(payload["engineVersion"], ENGINE_VERSION)
        self.assertEqual(set(payload["preds"]), {str(x) for x in range(1, 13)})

        target = payload["races"][4]
        prediction_pre = deepcopy(target["predictionPre"])
        direct, exhibition, original = docs(5)
        self.engine.apply_final_race(payload, 5, direct, exhibition, original)

        self.assertEqual(target["predictionPre"], prediction_pre)
        self.assertEqual(target["predictionFinal"], target["prediction"])
        self.assertEqual(target["predictionFinal"], payload["preds"]["5"])
        self.assertTrue(
            any(
                target["predictionFinal"][position][lane]
                != prediction_pre[position][lane]
                for position in ("win", "second", "third")
                for lane in map(str, range(1, 7))
            )
        )

        final = target["predictionFinal"]
        for position in ("win", "second", "third"):
            self.assertAlmostEqual(sum(final[position].values()), 100.0, delta=0.05)
        combos = [
            row["combo"]
            for group in ("main", "deviation", "upset")
            for row in final["tickets"][group]
        ]
        self.assertEqual(len(combos), 10)
        self.assertEqual(len(set(combos)), 10)
        self.assertFalse(final["debug"]["odds_used"])
        self.assertFalse(final["debug"]["result_used"])

        expected_courses = {1: 1, 2: 3, 3: 4, 4: 5, 5: 2, 6: 6}
        self.assertEqual(final["debug"]["actual_course"], expected_courses)
        for lane, course in expected_courses.items():
            remap = final["debug"]["course_remap"][lane]
            self.assertEqual(remap["actual_course"], course)
            self.assertEqual(remap["player_course"], course)
            self.assertEqual(remap["st_course"], course)
            self.assertEqual(remap["type_course"], course)
        for lane, audit in final["debug"]["latent_attack"].items():
            course = expected_courses[lane]
            self.assertEqual(audit["player_course"], course)
            self.assertEqual(audit["st_course"], course)
            self.assertEqual(audit["type_course"], course)

        ok, reason = self.engine.validate_payload(payload, require_all=True)
        self.assertTrue(ok, reason)

    def test_sum_missing_is_neutral_without_fake_rank(self):
        item = race(5)
        pre = self.engine.preliminary_race(item, [])
        direct, exhibition, original = docs(5)
        for entry in original["data"]["entries"]:
            entry.pop("sum", None)

        final = self.engine.final_race(item, pre, direct, exhibition, original, [])
        live = final["debug"]["live"]
        self.assertEqual(live["sum_rank_status"], {lane: "missing" for lane in range(1, 7)})
        for audit in live["boats"].values():
            self.assertEqual(audit["sum_rank"], "missing")
            self.assertEqual(audit["sum_adjustment"], 0)
            self.assertEqual(audit["sum_delta"], [0.0, 0.0, 0.0])

    def test_odds_and_result_injection_cannot_change_final(self):
        clean_race = race(5)
        direct, exhibition, original = docs(5)
        clean_pre = self.engine.preliminary_race(clean_race, [])
        clean = self.engine.final_race(clean_race, clean_pre, direct, exhibition, original, [])

        tainted_race = deepcopy(clean_race)
        tainted_race["odds"] = {"1-2-3": 9999}
        tainted_race["result"] = {"order": [6, 5, 4]}
        tainted_pre = self.engine.preliminary_race(tainted_race, [])
        tainted = self.engine.final_race(
            tainted_race,
            tainted_pre,
            deepcopy(direct),
            deepcopy(exhibition),
            deepcopy(original),
            [],
        )
        for key in ("win", "second", "third", "sab", "tickets"):
            self.assertEqual(clean[key], tainted[key])

    def test_production_runner_preliminary_then_final(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            data_root = temp_root / "data"
            dated = data_root / "venues" / "shimonoseki" / "20260823.json"
            dated.parent.mkdir(parents=True)
            dated.write_text(
                json.dumps(self.payload(), ensure_ascii=False),
                encoding="utf-8",
            )
            runner = ROOT / "automation" / "run_shimonoseki_v6_1.py"
            common = [
                sys.executable,
                str(runner),
                "--date",
                "2026-08-23",
                "--data-root",
                str(data_root),
                "--no-latest",
            ]
            subprocess.run(common + ["--stage", "preliminary"], cwd=ROOT, check=True)

            preliminary = json.loads(dated.read_text(encoding="utf-8"))
            self.assertEqual(len(preliminary["races"]), 12)
            self.assertEqual(set(preliminary["preds"]), {str(x) for x in range(1, 13)})
            prediction_pre = deepcopy(preliminary["races"][4]["predictionPre"])

            live_root = temp_root / "live"
            live_root.mkdir()
            direct, exhibition, original = docs(5)
            for name, document in (
                ("direct", direct),
                ("exhibition", exhibition),
                ("original_exhibition", original),
            ):
                (live_root / f"{name}.json").write_text(
                    json.dumps(document, ensure_ascii=False),
                    encoding="utf-8",
                )
            subprocess.run(
                common
                + [
                    "--stage",
                    "final",
                    "--race",
                    "5",
                    "--live-root",
                    str(live_root),
                ],
                cwd=ROOT,
                check=True,
            )

            final_payload = json.loads(dated.read_text(encoding="utf-8"))
            final_race = final_payload["races"][4]
            self.assertEqual(final_race["predictionPre"], prediction_pre)
            self.assertEqual(final_race["predictionFinal"], final_race["prediction"])
            self.assertEqual(final_race["predictionFinal"], final_payload["preds"]["5"])


if __name__ == "__main__":
    unittest.main()
