from __future__ import annotations

import json
from pathlib import Path

from heiwajima_fly_v6_calibration import probability as calibrated_probability
from heiwajima_fly_v6_model import extract_features, fly_tickets, normal_tickets


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "heiwajima_fly_v6_best_candidate.json"
CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def build_fly_v6_prediction(input_data, loader, records, scenarios, history_rows):
    features, missing = extract_features(input_data, loader)
    lane1 = next(row for row in records if int(row["boat_no"]) == 1)
    baseline_fly_probability = 1.0 - float(lane1["win_prob"])
    probability = calibrated_probability(
        {
            "baseline_fly_probability": baseline_fly_probability,
            "candidate": {"features": features},
        },
        CONFIG["features"],
    )
    threshold = float(CONFIG["threshold"])
    stage = "live" if input_data.get("stage") == "final" else "morning"
    return {
        "modelVersion": CONFIG["model_id"],
        "status": stage,
        "probability": round(probability * 100.0, 6),
        "probabilityRaw": round(probability, 8),
        "isFly": probability >= threshold,
        "threshold": round(threshold * 100.0, 6),
        "baselineFlyProbability": round(baseline_fly_probability * 100.0, 6),
        "features": features,
        "missing": missing,
        "normalTickets": normal_tickets(records, limit=12),
        "flyTickets": fly_tickets(records, scenarios, history_rows, limit=8),
        "normalTicketCount": 12,
        "flyTicketCount": 8,
        "normalFlyOverlapAllowed": True,
        "oddsUsedForProbability": False,
        "resultUsedForProbability": False,
        "calibrator": CONFIG["calibrator"],
    }
