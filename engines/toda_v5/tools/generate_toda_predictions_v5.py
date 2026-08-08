
import argparse,json
from pathlib import Path
from toda_prediction_engine_v5 import TodaPredictionEngineV5, ENGINE_ID

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("input_json"); ap.add_argument("output_json")
    args=ap.parse_args()
    payload=json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    engine=TodaPredictionEngineV5()
    payload["engine"]=ENGINE_ID
    payload.setdefault("preds",{})
    for race in payload.get("races",[]):
        context={
            "wind_speed":(race.get("weather") or {}).get("wind_speed"),
            "wave_height":(race.get("weather") or {}).get("wave_height"),
            "tide_phase":race.get("tide_phase") or (payload.get("tide") or {}).get("phase"),
            "tide_type":(payload.get("tide") or {}).get("tideType")
        }
        payload["preds"][str(race["race"])]=engine.predict(race,context)
    Path(args.output_json).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
if __name__=="__main__":main()
