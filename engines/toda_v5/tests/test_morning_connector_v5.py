from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONNECTOR = ROOT / "automation" / "apply_toda_v5.py"

spec = importlib.util.spec_from_file_location("apply_toda_v5", CONNECTOR)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

sample_path = ROOT / "data" / "venues" / "toda" / "latest.json"
if not sample_path.exists():
    print("SKIP: data/venues/toda/latest.json is not present")
    raise SystemExit(0)

payload = json.loads(sample_path.read_text(encoding="utf-8"))
result = module.apply_toda_v5(payload, payload["date"])

assert result["engine"] == module.ENGINE_ID
assert sorted(map(int, result["preds"])) == list(range(1, 13))
assert all(module.prediction_complete(p) for p in result["preds"].values())
assert all(p["sourceSummary"]["odds_used_for_probability"] is False for p in result["preds"].values())
print("Toda v5 morning connector test passed")
