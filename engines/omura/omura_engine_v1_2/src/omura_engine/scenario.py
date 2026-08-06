from __future__ import annotations
from .utils import clamp, normalize, safe_float

SCENARIOS = ("nige", "sashi", "makuri", "makurizashi", "outer_upset")

class ScenarioEngine:
    def calculate(self, boats: list[dict], probabilities: dict, race: dict, water: dict) -> dict:
        by_lane = {int(b["lane"]): b for b in boats}
        lane1 = probabilities["boats"][0] if probabilities["boats"] else {}
        p1 = lane1.get("win", 0.0)
        scores = {
            "nige": 0.12 + 0.78 * p1,
            "sashi": 0.18,
            "makuri": 0.16,
            "makurizashi": 0.16,
            "outer_upset": 0.12,
        }
        b1 = by_lane.get(1, {})
        k1 = b1.get("kimarite", {}) or {}
        escape = self._rate(k1.get("nige_rate"))
        sashare = self._rate(k1.get("escaped_against_rate"))
        attack_vulnerability = self._rate(k1.get("attacked_against_rate"))
        scores["nige"] += 0.36 * escape
        scores["nige"] -= 0.26 * sashare + 0.24 * attack_vulnerability
        if escape < 0.30:
            scores["nige"] -= 0.10
        scores["sashi"] += 0.28 * sashare
        scores["makuri"] += 0.22 * attack_vulnerability
        scores["outer_upset"] += 0.10 * attack_vulnerability

        b2 = by_lane.get(2, {})
        b3 = by_lane.get(3, {})
        b4 = by_lane.get(4, {})
        scores["sashi"] += 0.55 * self._rate((b2.get("kimarite") or {}).get("sashi_rate"))
        scores["makuri"] += 0.40 * max(
            self._rate((b3.get("kimarite") or {}).get("makuri_rate")),
            self._rate((b4.get("kimarite") or {}).get("makuri_rate")),
        )
        scores["makurizashi"] += 0.40 * max(
            self._rate((b3.get("kimarite") or {}).get("makurizashi_rate")),
            self._rate((b4.get("kimarite") or {}).get("makurizashi_rate")),
        )

        wind = safe_float(water.get("wind_speed_mps"))
        wave = safe_float(water.get("wave_height_cm"))
        near = safe_float(water.get("minutes_from_nearest_extreme"), 999)
        if wind >= 5 or wave >= 5:
            scores["outer_upset"] += 0.15
            scores["nige"] -= 0.08
        if near <= 45 or water.get("tide_direction") == "stop":
            scores["outer_upset"] += 0.08
            if int(race.get("race_no", 1)) <= 4:
                scores["nige"] -= 0.06

        slit = [str((b.get("exhibition") or {}).get("slit_type", "")) for b in boats]
        if any(x in ("1遅れ", "カベなし", "2・3遅れ", "中凹み") for x in slit):
            scores["nige"] -= 0.10
            scores["makuri"] += 0.06
            scores["makurizashi"] += 0.05
        if any(x in ("外側先行", "ダッシュ先行") for x in slit):
            scores["outer_upset"] += 0.10

        vals = normalize([max(0.01, scores[s]) for s in SCENARIOS])
        return {
            "probabilities": dict(zip(SCENARIOS, vals)),
            "primary": SCENARIOS[max(range(len(vals)), key=vals.__getitem__)],
            "scores_before_normalization": scores,
        }

    @staticmethod
    def _rate(value) -> float:
        x = safe_float(value)
        return x / 100 if x > 1 else x
