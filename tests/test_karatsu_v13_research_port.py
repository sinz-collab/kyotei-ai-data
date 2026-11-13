from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "karatsu_v13"
sys.path.insert(0, str(ROOT / "automation"))
sys.path.insert(0, str(ROOT / "engines" / "karatsu_v1"))

from apply_karatsu_v1 import build_race_input, live_wind_eligibility
from karatsu_conditional_second_v1 import select_normal_tickets
from karatsu_fly_v1 import build_fly_prediction, reweight_trifectas
from karatsu_prediction_engine import KaratsuScenarioEngine


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def production_distribution(payload: dict, race: dict, live_weather: dict | None = None) -> dict:
    safe = deepcopy(payload)
    race_no = int(race["race"])
    for item in safe["races"]:
        if int(item["race"]) >= race_no:
            item["result"] = {}
        if int(item["race"]) == race_no and live_weather is not None:
            item.setdefault("live", {})["weather"] = live_weather
    safe_race = next(item for item in safe["races"] if int(item["race"]) == race_no)
    race_input, _, _ = build_race_input(safe, safe_race)
    result = KaratsuScenarioEngine().predict(race_input, ticket_count=10)
    return {
        "ai": [{"combo": combo} for combo in result.tickets[:8]],
        "aiUpset": [{"combo": combo} for combo in result.tickets[8:]],
        "trifectaProbabilities": result.trifecta_probabilities,
    }


class KaratsuResearchPortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.conditional_rows = read_csv(FIXTURE / "conditional_second_126.csv")
        cls.fly_rows = read_csv(FIXTURE / "archived_fly_reference_126.csv")
        cls.by_race: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)
        for row in cls.conditional_rows:
            cls.by_race[(row["date"], int(row["race_no"]))].append(row)

    def test_conditional_second_matches_all_126_in_order(self) -> None:
        exact = hits = base_hits = gains = losses = changed = payout = 0
        for rows in self.by_race.values():
            rows.sort(key=lambda row: int(row["lane"]))
            first = {int(row["lane"]): float(row["live_win_probability_pct"]) / 100 for row in rows}
            second = {int(row["lane"]): float(row["live_second_probability_pct"]) / 100 for row in rows}
            third = {int(row["lane"]): float(row["live_third_probability_pct"]) / 100 for row in rows}
            racers = [
                SimpleNamespace(
                    lane=int(row["lane"]),
                    nat_win=float(row["national_win_rate"] or 0),
                    local_win=float(row["local_win_rate"] or 0),
                    motor_2=float(row["motor_2rate_pct"] or 0),
                )
                for row in rows
            ]
            actual, _ = select_normal_tickets(
                rows[0]["base_tickets"].split("|"), first, second, third, racers,
                float(rows[0]["wind"]), live_wind_eligible=True,
            )
            expected = rows[0]["final_tickets"].split("|")
            exact += actual == expected
            result = rows[0]["result"]
            base_hits += int(rows[0]["base_hit"])
            hits += result in actual
            gains += result in actual and not int(rows[0]["base_hit"])
            losses += result not in actual and bool(int(rows[0]["base_hit"]))
            changed += set(actual) != set(rows[0]["base_tickets"].split("|"))
            if result in actual:
                payout += int(float(rows[0]["payout"]))

        self.assertEqual(exact, 126)
        self.assertEqual((base_hits, hits, gains, losses, changed), (48, 52, 4, 0, 28))
        self.assertEqual(payout, 103_500)
        self.assertAlmostEqual(payout / 126_000 * 100, 82.14285714285714)

    def test_fly_rate_is_frozen_and_overlap_is_allowed(self) -> None:
        old_hits = old_return = new_hits = new_return = overlap_total = overlap_races = 0
        rate_equal = archived_tickets_equal = 0
        for archived in self.fly_rows:
            key = (archived["date"], int(archived["race_no"]))
            rows = self.by_race[key]
            payload = json.loads(
                (ROOT / "data" / "venues" / "karatsu" / f"{key[0].replace('-', '')}.json").read_text(encoding="utf-8")
            )
            race = next(item for item in payload["races"] if int(item["race"]) == key[1])
            production = production_distribution(payload, race)
            weighted = reweight_trifectas(production, race, "fly_v1")
            legacy_normal = list(weighted)[:10]
            legacy = build_fly_prediction(
                production, race, stage="live", normal_tickets=legacy_normal,
                allow_normal_overlap=False,
            )
            normal = rows[0]["final_tickets"].split("|")
            current = build_fly_prediction(
                production, race, stage="live", normal_tickets=normal,
                allow_normal_overlap=True,
            )
            old = [row["combo"] for row in legacy["flyPrediction"]["tickets"]]
            new = [row["combo"] for row in current["flyPrediction"]["tickets"]]
            archived_tickets_equal += "|".join(old) == archived["live_fly_v1_fly10"]
            rate_equal += legacy["lane1FlyProbability"] == current["lane1FlyProbability"]
            result = archived["trifecta_combo"]
            prize = int(float(archived["trifecta_payout_yen"]))
            if result in old:
                old_hits += 1
                old_return += prize
            if result in new:
                new_hits += 1
                new_return += prize
            overlap = len(set(normal).intersection(new))
            overlap_total += overlap
            overlap_races += overlap > 0
            self.assertEqual(len(new), 10)
            self.assertEqual(len(set(new)), 10)

        self.assertEqual((archived_tickets_equal, rate_equal), (126, 126))
        self.assertEqual((old_hits, old_return), (20, 140_270))
        self.assertEqual((new_hits, new_return), (20, 140_270))
        self.assertEqual((overlap_total, overlap_races), (211, 126))

    def test_saved_live_wind_gate_recalculates_126_without_forcing_old_hits(self) -> None:
        eligible = fallback = active = hits = gains = losses = payout = 0
        ticket_diff_races = added = dropped = 0
        for key, rows in self.by_race.items():
            payload = json.loads(
                (ROOT / "data" / "venues" / "karatsu" / f"{key[0].replace('-', '')}.json").read_text(encoding="utf-8")
            )
            race = next(item for item in payload["races"] if int(item["race"]) == key[1])
            live_root = ROOT / "data" / "live" / key[0] / "karatsu" / f"{key[1]:02d}"
            documents = [json.loads((live_root / name).read_text(encoding="utf-8")) for name in (
                "direct.json", "exhibition.json", "original_exhibition.json",
            )]
            direct = documents[0]["data"]
            fetched_at = max(document["fetched_at"] for document in documents)
            audited_race = deepcopy(race)
            audited_race.setdefault("live", {})["weather"] = direct
            is_eligible, _ = live_wind_eligibility(payload, audited_race, fetched_at)
            base = rows[0]["base_tickets"].split("|")
            old = rows[0]["final_tickets"].split("|")
            if is_eligible:
                eligible += 1
                production = production_distribution(payload, race, direct)
                probability = production["trifectaProbabilities"]
                first = {lane: sum(value for combo, value in probability.items() if int(combo[0]) == lane) for lane in range(1, 7)}
                second = {lane: sum(value for combo, value in probability.items() if int(combo[2]) == lane) for lane in range(1, 7)}
                third = {lane: sum(value for combo, value in probability.items() if int(combo[4]) == lane) for lane in range(1, 7)}
                racers = [SimpleNamespace(
                    lane=int(row["lane"]), nat_win=float(row["national_win_rate"] or 0),
                    local_win=float(row["local_win_rate"] or 0), motor_2=float(row["motor_2rate_pct"] or 0),
                ) for row in rows]
                current, _ = select_normal_tickets(
                    base, first, second, third, racers, float(direct["wind_speed"]),
                    live_wind_eligible=True,
                )
            else:
                fallback += 1
                current = base

            active += set(current) != set(base)
            result = rows[0]["result"]
            old_hit = result in old
            current_hit = result in current
            hits += current_hit
            gains += current_hit and not old_hit
            losses += old_hit and not current_hit
            if current_hit:
                payout += int(float(rows[0]["payout"]))
            if set(current) != set(old):
                ticket_diff_races += 1
                added += len(set(current) - set(old))
                dropped += len(set(old) - set(current))

        self.assertEqual((eligible, fallback, active), (119, 7, 26))
        self.assertEqual((hits, gains, losses, payout), (52, 0, 0, 103_500))
        self.assertEqual((ticket_diff_races, added, dropped), (9, 9, 9))
        self.assertAlmostEqual(payout / 126_000 * 100, 82.14285714285714)

    def test_live_wind_gate_requires_pre_deadline_timestamp(self) -> None:
        payload = {"date": "2026-10-27"}
        race = {
            "deadline": "11:22",
            "live": {"weather": {"wind_speed": 5}},
        }
        self.assertEqual(live_wind_eligibility(payload, race, None)[0], False)
        self.assertEqual(live_wind_eligibility(payload, race, "2026-10-27T02:21:59+00:00")[0], True)
        self.assertEqual(live_wind_eligibility(payload, race, "2026-10-27T02:22:00+00:00")[0], False)


if __name__ == "__main__":
    unittest.main()
