from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from heiwajima_prediction_engine import calculate
from heiwajima_v2_7_adjustments import evaluate_s16


CONNECTOR = REPO_ROOT / "automation" / "apply_heiwajima_v1.py"
spec = importlib.util.spec_from_file_location("apply_heiwajima_v27_test", CONNECTOR)
connector = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(connector)


def _s16_boats() -> list[dict]:
    boats = [
        {"boat_no": lane, "class": "B1", "season_runs": [], "motor_recent": {}}
        for lane in range(1, 7)
    ]
    boats[0]["kimarite"] = {
        "escape_rate": 0.35,
        "sashare_rate": 0.15,
        "makurare_rate": 0.12,
        "makurare_zashi_rate": 0.10,
    }
    boats[5].update({
        "class": "A2",
        "nat_win_score": 5.9,
        "motor_recent": {"trend": "up", "top2_rate": 30, "top3_rate": 40},
        "season_runs": [{"finish": "2着"}],
    })
    return boats


def test_s16_strong_medium_and_off() -> None:
    boats = _s16_boats()
    strong = evaluate_s16(boats, {"low_water_band": True, "minutes_to_low_tide": 15})
    assert strong["level"] == "strong" and strong["signalCount"] == 5

    medium = evaluate_s16(boats, {"low_water_band": False, "minutes_to_low_tide": 15})
    assert medium["level"] == "medium" and medium["signalCount"] == 4

    off_boats = deepcopy(boats)
    off_boats[5].update({"class": "B1", "nat_win_score": 4.0, "motor_recent": {}, "season_runs": []})
    off = evaluate_s16(off_boats, {})
    assert off["level"] == "off" and off["signalCount"] <= 3


def _tide_payload() -> dict:
    return {
        "tide": {
            "events": [
                {"type": "満潮", "time": "10:00", "level": 180},
                {"type": "干潮", "time": "13:00", "level": 80},
                {"type": "満潮", "time": "18:00", "level": 190},
            ]
        }
    }


def test_tide_context_s16_boundaries_and_safe_fallback() -> None:
    boats = _s16_boats()
    payload = _tide_payload()

    at_120 = connector.tide_context(
        payload, {"deadline": "11:00", "tide_level_band": "やや低潮位(50-99cm)"}
    )
    assert at_120["minutes_to_low_tide"] == 120
    assert evaluate_s16(boats, at_120)["signals"]["lowTideFit"] is True

    at_121 = connector.tide_context(
        payload, {"deadline": "10:59", "tide_level_band": "やや低潮位(50-99cm)"}
    )
    assert at_121["minutes_to_low_tide"] == 121
    assert evaluate_s16(boats, at_121)["signals"]["lowTideFit"] is False

    just_before = connector.tide_context(
        payload, {"deadline": "12:59", "tide_level_band": "やや低潮位(50-99cm)"}
    )
    assert just_before["minutes_to_low_tide"] == 1
    assert evaluate_s16(boats, just_before)["signals"]["lowTideFit"] is True

    after_low = connector.tide_context(
        payload, {"deadline": "13:01", "tide_level_band": "やや低潮位(50-99cm)"}
    )
    assert after_low["minutes_to_low_tide"] is None
    assert evaluate_s16(boats, after_low)["signals"]["lowTideFit"] is False

    missing = connector.tide_context({}, {"deadline": "12:00"})
    assert missing["low_water_band"] is False
    assert missing["low_water_band_source"] == "safe_fallback"
    assert evaluate_s16(boats, missing)["signals"]["lowTideFit"] is False


def test_tide_context_preserves_race_values_and_fills_only_missing() -> None:
    context = connector.tide_context(
        _tide_payload(),
        {
            "deadline": "12:23",
            "tide": {"tide_phase": "race-phase", "minutes_to_low_tide": 7},
            "tide_level_band": "中潮位(100-149cm)",
            "low_water_band": False,
            "tide_cm_est": 123.4,
        },
    )
    assert context["tide_phase"] == "race-phase"
    assert context["minutes_to_low_tide"] == 7
    assert context["tide_level_band"] == "中潮位(100-149cm)"
    assert context["low_water_band"] is False
    assert context["tide_cm_est"] == 123.4
    assert context["previous_tide_type"] == "満潮"
    assert context["next_tide_type"] == "干潮"


def test_20260930_race4_tide_to_s16_read_only() -> None:
    fixture = REPO_ROOT / "data" / "venues" / "heiwajima" / "20260930.json"
    before = hashlib.sha256(fixture.read_bytes()).hexdigest()
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    race = next(row for row in payload["races"] if int(row["race"]) == 4)

    tide = connector.tide_context(payload, race)
    assert race["deadline"] == "12:23"
    assert tide["minutes_to_low_tide"] == 37
    assert tide["minutes_from_previous_tide"] == 293
    assert tide["previous_tide_type"] == "満潮"
    assert tide["next_tide_type"] == "干潮"
    assert tide["previous_tide_level"] == 201
    assert tide["next_tide_level"] == 93
    assert tide["low_water_band"] is True

    engine_input, _ = connector.engine_input_for(
        payload, race, connector.build_player_index(), stage="pre", live_context={}
    )
    assert engine_input["tide"]["minutes_to_low_tide"] == 37
    assert "result" not in engine_input
    result = calculate(engine_input)
    assert result["s16"]["signals"]["lowTideFit"] is True
    assert result["s16"]["signalCount"] == 5
    assert result["s16"]["level"] == "strong"
    assert result["s16DedicatedTicket"]["combination"] == "6-1-2"

    replay = deepcopy(payload)
    replay["preds"] = {}
    for replay_race in replay["races"]:
        replay_race.pop("result", None)
    with tempfile.TemporaryDirectory() as temp_dir:
        published = connector.apply_heiwajima_v1(replay, "2026-09-30", Path(temp_dir))
    public_race4 = published["preds"]["4"]
    assert public_race4["engine"] == "heiwajima_complete_v2_7_20260930"
    assert public_race4["tideContext"]["minutes_to_low_tide"] == 37
    assert public_race4["s16"]["signals"]["lowTideFit"] is True
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == before


def test_result_updates_hit_log_without_rewriting_prediction() -> None:
    prediction = {
        "engine": connector.ENGINE_ID,
        "win": {str(lane): 100 / 6 for lane in range(1, 7)},
        "second": {str(lane): 100 / 6 for lane in range(1, 7)},
        "third": {str(lane): 100 / 6 for lane in range(1, 7)},
        "sab": "A",
        "ai": [{"combo": "6-1-2"}],
        "aiUpset": [],
        "tickets": [{"combo": "6-1-2"}],
        "coverageNeed": {"selectedTicketCount": 10},
        "s16": {"level": "strong"},
    }
    frozen = deepcopy(prediction)
    updated = connector.update_accuracy_only(
        prediction, {"result": {"trifecta": "6-1-2"}}
    )
    for key, value in frozen.items():
        assert updated[key] == value
    assert updated["accuracyLog"]["hit"] is True
    assert updated["accuracyLog"]["result"] == "6-1-2"


def test_20260929_read_only_regression() -> None:
    fixture = REPO_ROOT / "data" / "venues" / "heiwajima" / "20260929.json"
    before = hashlib.sha256(fixture.read_bytes()).hexdigest()
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    player_index = connector.build_player_index()
    expected = {
        4: "6-1-2",
        5: "1-3-4",
        6: "1-4-5",
        7: "1-6-2",
        12: "1-3-6",
    }

    old_hits = 0
    for race_no, combo in expected.items():
        old = payload["preds"][str(race_no)]
        old_tickets = [row["combo"] for row in (old.get("ai") or []) + (old.get("aiUpset") or [])]
        old_hits += combo in old_tickets

    top10_hits = 0
    coverage_hits = 0
    final_hits = 0
    total_tickets = 0
    hit_races = []
    for race in payload["races"]:
        engine_input, _ = connector.engine_input_for(
            payload, race, player_index, stage="pre", live_context={}
        )
        # race.result is deliberately not part of engine_input_for.
        assert "result" not in engine_input
        result = calculate(engine_input)
        for key in ("win_prob", "second_prob", "third_prob"):
            assert abs(sum(row[key] for row in result["probabilities"]) - 1.0) < 1e-5
        assert result["sab"]["ticket_count_used"] is False

        race_no = int(race["race"])
        tickets = [row["combination"] for row in result["tickets"]]
        normal_count = int(result["coverageNeed"]["selectedTicketCount"])
        total_tickets += len(tickets)
        if race_no in expected:
            combo = expected[race_no]
            top10_hits += combo in tickets[:10]
            coverage_hits += combo in tickets[:normal_count]
            final_hits += combo in tickets
            if combo in tickets:
                hit_races.append(race_no)
        assert len(tickets) == len(set(tickets))
        assert len(tickets) <= 19

        if race_no == 4:
            assert result["s16"]["level"] == "strong"
            assert result["s16DedicatedTicket"]["combination"] == "6-1-2"

    assert old_hits == 1
    assert top10_hits == 2
    assert coverage_hits == 4
    assert final_hits == 5
    assert hit_races == [4, 5, 6, 7, 12]
    assert total_tickets == 197
    payout = 66080
    assert round(payout / (total_tickets * 100) * 100, 1) == 335.4
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == before
