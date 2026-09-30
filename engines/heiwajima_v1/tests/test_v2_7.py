from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
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
