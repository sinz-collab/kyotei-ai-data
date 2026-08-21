from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from .shimonoseki_v6_core import LANES, ShimonosekiV6Core, f, i

ENGINE_ID = "shimonoseki_engine_v6.0"
ENGINE_VERSION = "6.0"


def _prob_valid(values: Mapping[str, Any]) -> bool:
    if set(map(str, values)) != {str(x) for x in LANES}:
        return False
    nums = [f(values[str(x)], -1.0) for x in LANES]
    return all(0 <= x <= 100 for x in nums) and abs(sum(nums) - 100.0) <= 0.5


class ShimonosekiSiteEngineV6:
    """Production JSON adapter around the completed v6.0 fix1 core."""

    def __init__(self, master_dir: str | Path):
        self.core = ShimonosekiV6Core(master_dir)

    @staticmethod
    def _maps(probs: Mapping[str, Mapping[int, float]]) -> dict[str, dict[str, float]]:
        return {
            out_key: {str(lane): round(probs[core_key][lane], 2) for lane in LANES}
            for out_key, core_key in (("win", "first"), ("second", "second"), ("third", "third"))
        }

    def preliminary_race(
        self,
        race: MutableMapping[str, Any],
        tide_events: list[Mapping[str, Any]],
        dynamic_motor: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if dynamic_motor:
            for racer in race.get("racers") or []:
                motor = str(racer.get("motor_no") or "").lstrip("0") or "0"
                row = dynamic_motor.get(motor)
                if not row:
                    continue
                racer["motorEvaluation"] = {
                    "available": True,
                    "score": f(row.get("motor_score_100"), 50.0),
                    "rank": str(row.get("motor_rank") or ""),
                    "reliability": f(row.get("reliability"), 0.0),
                    "source": "shimonoseki_v6_preliminary",
                }
                trend_delta = f(row.get("recent_trend_delta"), 0.0)
                racer["motor_recent"] = {
                    "trend": "up" if trend_delta > 0 else ("down" if trend_delta < 0 else "flat"),
                    "top2_rate": f(row.get("recent_top2_rate"), 0.0) * 100.0,
                    "top3_rate": f(row.get("recent_top3_rate"), 0.0) * 100.0,
                }

        actual = {lane: lane for lane in LANES}
        probs, course = self.core.base_actual_course(race, actual)
        probs, motor = self.core.motor_base(probs, race)
        probs, series = self.core.series(probs, race)
        probs, water = self.core.water_tide(probs, race, actual, direct=None, tide_events=tide_events)
        maps = self._maps(probs)
        debug = {
            "actual_course": actual,
            "course_remap": course,
            "motor": motor,
            "series": series,
            "live": {},
            "escape_attack_sum": {},
            "compound_attack": {},
            "water": water,
            "result_used": False,
            "odds_used": False,
        }
        tickets = self.core.build_tickets(maps, race, debug)
        sab, sab_meta = self.core.sab_score(maps, tickets, debug)
        debug["sab"] = sab_meta
        return {
            "engine": ENGINE_ID,
            "engineVersion": ENGINE_VERSION,
            "phase": "preliminary",
            "status": "complete",
            "probabilities": maps,
            **maps,
            "sab": sab,
            "tickets": tickets,
            "ai": [x["combo"] for x in tickets["main"]],
            "balance": [x["combo"] for x in tickets["deviation"]],
            "aiUpset": [x["combo"] for x in tickets["upset"]],
            "debug": debug,
        }

    def final_race(
        self,
        race: MutableMapping[str, Any],
        prediction_pre: Mapping[str, Any],
        direct: Mapping[str, Any],
        exhibition: Mapping[str, Any],
        original: Mapping[str, Any],
        tide_events: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        direct_racers = {i(r.get("lane")): r for r in (direct.get("data") or {}).get("racers", [])}
        for racer in race.get("racers") or []:
            incoming = direct_racers.get(i(racer.get("lane")))
            if incoming and incoming.get("player_id"):
                racer["player_id"] = incoming["player_id"]
        out = self.core.predict_final(race, exhibition, original, direct=direct, tide_events=tide_events)
        out["predictionPre"] = {key: deepcopy(prediction_pre[key]) for key in ("win", "second", "third")}
        out["probabilityReviewStatus"] = "reviewed"
        out["probabilityFlow"] = {"reviewed": True, "actualCourseRemapped": True}
        return out

    @staticmethod
    def legacy_pred(
        prediction: Mapping[str, Any],
        prediction_pre: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        tickets = prediction["tickets"]
        main = [x["combo"] for x in tickets.get("main", [])]
        deviation = [x["combo"] for x in tickets.get("deviation", [])]
        upset = [x["combo"] for x in tickets.get("upset", [])]
        legacy = {
            "win": deepcopy(prediction["win"]),
            "second": deepcopy(prediction["second"]),
            "third": deepcopy(prediction["third"]),
            "sab": prediction["sab"],
            "ai": main,
            "balance": deviation,
            "aiUpset": upset,
            "tickets": main + deviation + upset,
            "engine": ENGINE_ID,
            "engineVersion": ENGINE_VERSION,
            "predictionStage": prediction["phase"],
            "fallback": False,
        }
        if prediction["phase"] == "final" and prediction_pre is not None:
            legacy["predictionPre"] = {
                key: deepcopy(prediction_pre[key]) for key in ("win", "second", "third")
            }
            legacy["probabilityReviewStatus"] = "reviewed"
            legacy["probabilityFlow"] = {"reviewed": True, "actualCourseRemapped": True}
        return legacy

    def apply_preliminary_daily(
        self,
        payload: MutableMapping[str, Any],
        dynamic_motor: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> MutableMapping[str, Any]:
        races = payload.get("races") or []
        if len(races) != 12 or {i(r.get("race")) for r in races} != set(range(1, 13)):
            raise RuntimeError("Shimonoseki 12R payload gate failed")
        tide_events = list((payload.get("tide") or {}).get("events") or [])
        preds: dict[str, Any] = {}
        for race in races:
            pred = self.preliminary_race(race, tide_events, dynamic_motor)
            race["predictionPre"] = deepcopy(pred)
            race["prediction"] = deepcopy(pred)
            race.pop("predictionFinal", None)
            preds[str(race["race"])] = self.legacy_pred(pred)
        payload["preds"] = preds
        payload["engine"] = ENGINE_ID
        payload["engineVersion"] = ENGINE_VERSION
        payload["predictionAvailable"] = True
        payload["predictionStatus"] = "ready"
        return payload

    def apply_final_race(
        self,
        payload: MutableMapping[str, Any],
        race_no: int,
        direct: Mapping[str, Any],
        exhibition: Mapping[str, Any],
        original: Mapping[str, Any],
    ) -> MutableMapping[str, Any]:
        race = next((r for r in payload.get("races") or [] if i(r.get("race")) == race_no), None)
        if race is None:
            raise KeyError(f"race {race_no} not found")
        pre = race.get("predictionPre")
        if not isinstance(pre, Mapping):
            raise RuntimeError(f"race {race_no}: predictionPre missing; run v6 preliminary first")
        tide_events = list((payload.get("tide") or {}).get("events") or [])
        final = self.final_race(race, pre, direct, exhibition, original, tide_events)
        race["predictionFinal"] = deepcopy(final)
        race["prediction"] = deepcopy(final)
        payload.setdefault("preds", {})[str(race_no)] = self.legacy_pred(final, pre)
        payload["engine"] = ENGINE_ID
        payload["engineVersion"] = ENGINE_VERSION
        payload["predictionAvailable"] = True
        payload["predictionStatus"] = "ready"
        return payload

    @staticmethod
    def validate_payload(payload: Mapping[str, Any], require_all: bool = True) -> tuple[bool, str]:
        if payload.get("engine") != ENGINE_ID or payload.get("engineVersion") != ENGINE_VERSION:
            return False, "engine_mismatch"
        preds = payload.get("preds")
        if not isinstance(preds, Mapping):
            return False, "preds_missing"
        expected = {str(x) for x in range(1, 13)} if require_all else set(preds)
        if require_all and set(preds) != expected:
            return False, "race_count_invalid"
        for key in expected:
            pred = preds.get(key)
            if not isinstance(pred, Mapping):
                return False, f"prediction_{key}_missing"
            if pred.get("engine") != ENGINE_ID or pred.get("fallback") is not False:
                return False, f"prediction_{key}_engine_invalid"
            for pos in ("win", "second", "third"):
                if not isinstance(pred.get(pos), Mapping) or not _prob_valid(pred[pos]):
                    return False, f"prediction_{key}_{pos}_invalid"
            tickets = pred.get("tickets")
            if not isinstance(tickets, list) or len(tickets) != 10 or len(set(tickets)) != 10:
                return False, f"prediction_{key}_tickets_invalid"
        return True, "ok"
