
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.wakamatsu_engine import WakamatsuEngine

def load_sample():
    return json.loads((ROOT / "examples" / "sample_input_pre.json").read_text(encoding="utf-8"))

def test_prediction_contract():
    engine = WakamatsuEngine(ROOT / "data" / "wakamatsu_master_v1.sqlite")
    try:
        result = engine.predict(load_sample())
    finally:
        engine.close()
    assert result["validation"]["six_boats"]
    assert abs(result["validation"]["win_sum"] - 1.0) < 1e-5
    assert abs(result["validation"]["second_sum"] - 1.0) < 1e-5
    assert abs(result["validation"]["third_sum"] - 1.0) < 1e-5
    assert result["validation"]["ticket_count"] == 10
    assert result["validation"]["ticket_unique"]
    assert len(result["tickets"]["main"]) == 6
    assert len(result["tickets"]["deviation"]) == 2
    assert len(result["tickets"]["upset"]) == 2
    assert result["sab"]["grade"] in {"S", "A", "B"}
    assert result["validation"]["odds_used"] is False

def test_entry_change_recalculation():
    race = load_sample()
    race["boats"][1]["entry_course"] = 3
    race["boats"][2]["entry_course"] = 2
    engine = WakamatsuEngine(ROOT / "data" / "wakamatsu_master_v1.sqlite")
    try:
        result = engine.predict(race)
    finally:
        engine.close()
    assert result["race_context"]["entry_changed"] is True
    assert result["sab"]["entry_change_penalty"] is True
