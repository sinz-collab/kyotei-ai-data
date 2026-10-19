from copy import deepcopy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
if str(AUTOMATION) not in sys.path:
    sys.path.insert(0, str(AUTOMATION))

from run_ashiya_v16 import append_prediction_history


def prediction(stage="pre"):
    return {
        "win": {str(i): 100 / 6 for i in range(1, 7)},
        "second": {str(i): 100 / 6 for i in range(1, 7)},
        "third": {str(i): 100 / 6 for i in range(1, 7)},
        "lane1FlyProbability": 42.5,
        "ticketSelection": {
            "modelVersion": "ashiya_v51_631",
            "allocation": {"main": 6, "deviation": 3, "scenario_hole": 1},
            "ordered": [
                {
                    "combination": f"1-2-{(index % 4) + 3}",
                    "score": 0.1 - index / 1000,
                    "category": "main" if index < 6 else "deviation" if index < 9 else "scenario_hole",
                    "selection_order": index + 1,
                }
                for index in range(10)
            ],
        },
        "flyPrediction": {
            "status": "live" if stage == "live" else "not_generated_morning",
            "modelVersion": "strength_25_frozen",
            "tickets": [
                {"combo": f"2-1-{(index % 4) + 3}", "probability": 2.5, "rank": index + 1}
                for index in range(10)
            ] if stage == "live" else [],
        },
    }


def engine_result(stage):
    return {
        "engine": {
            "name": "ashiya_prediction_engine",
            "version": "1.7.0",
            "master_version": "v5.1",
            "stage": stage,
            "generated_at": f"2026-10-19T0{8 if stage == 'pre' else 9}:00:00+09:00",
        }
    }


def test_morning_and_live_predictions_are_appended_without_overwrite():
    morning = prediction("pre")
    morning_native = {}
    source_input = {
        "date": "2026-10-19",
        "race_no": 1,
        "racers": [{"lane": lane} for lane in range(1, 7)],
        "prediction": {"must": "not be archived as input"},
        "result": {"trifecta": "9-9-9"},
    }
    append_prediction_history(
        morning,
        morning_native,
        {},
        engine_result("pre"),
        source_input,
        "pre",
    )
    saved_morning = deepcopy(morning["prediction_history"]["morning"][0])
    assert len(saved_morning["normalTickets"]) == 10
    assert saved_morning["ticketAllocation"] == {"main": 6, "deviation": 3, "scenario_hole": 1}
    assert saved_morning["flyTickets"] == []
    assert saved_morning["flyModelVersion"] == "strength_25_frozen"
    assert "prediction" not in saved_morning["inputData"]
    assert "result" not in saved_morning["inputData"]

    live = prediction("live")
    live_native = {}
    append_prediction_history(
        live,
        live_native,
        morning,
        engine_result("live"),
        {**source_input, "live": {"exhibition": "captured"}},
        "live",
    )
    history = live["prediction_history"]
    assert history["morning"] == [saved_morning]
    assert len(history["live"]) == 1
    assert len(history["live"][0]["normalTickets"]) == 10
    assert len(history["live"][0]["flyTickets"]) == 10
    assert history["live"][0]["flyModelVersion"] == "strength_25_frozen"
    assert live_native["prediction_history"] == history

    live["result"] = {"status": "ok", "trifecta": "2-1-3"}
    assert live["prediction_history"]["morning"][0] == saved_morning
