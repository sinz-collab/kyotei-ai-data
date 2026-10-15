"""Publish the safety-gated Wakamatsu v3 probability into production JSON."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

try:
    from .apply_wakamatsu_v2 import atomic_write_json
    from .run_wakamatsu_v3_staging import build_staging_payload, load_json
    from .wakamatsu_v3_live import fetch_validated_tide
except ImportError:  # pragma: no cover - direct script execution
    from apply_wakamatsu_v2 import atomic_write_json
    from run_wakamatsu_v3_staging import build_staging_payload, load_json
    from wakamatsu_v3_live import fetch_validated_tide


REPO_ROOT = Path(__file__).resolve().parents[1]
FEATURE_FLAG_PATH = Path(__file__).with_name("wakamatsu_v3_feature_flags.json")
FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


def feature_enabled() -> bool:
    override = os.environ.get("WAKAMATSU_V3_ENABLED")
    if override is not None:
        normalized = override.strip().lower()
        if normalized in FALSE_VALUES:
            return False
        if normalized in TRUE_VALUES:
            return True
        raise RuntimeError("wakamatsu_v3_feature_flag_invalid")
    config = load_json(FEATURE_FLAG_PATH)
    return config.get("enabled") is True


def _paths(data_root: Path, target_date: str) -> tuple[Path, Path]:
    venue = data_root / "venues" / "wakamatsu"
    return venue / f"{target_date.replace('-', '')}.json", venue / "latest.json"


def _write_public_payload(
    dated_path: Path,
    latest_path: Path,
    payload: dict[str, Any],
) -> bool:
    atomic_write_json(dated_path, payload)
    latest_updated = False
    if latest_path.is_file():
        latest = load_json(latest_path)
        if latest.get("date") == payload.get("date"):
            atomic_write_json(latest_path, payload)
            latest_updated = True
    return latest_updated


def mark_live_v3_unavailable(
    target_date: str,
    data_root: Path,
    reason: str,
) -> dict[str, Any]:
    """Fail closed for LIVE probability while preserving every other field."""
    dated_path, latest_path = _paths(data_root, target_date)
    payload = load_json(dated_path)
    affected = 0
    for race in payload.get("races") or []:
        final = race.get("predictionFinal")
        if not isinstance(final, dict):
            continue
        final["lane1FlyProbability"] = None
        final["lane1FlyProbabilitySource"] = "live"
        final["lane1FlyProbabilityModel"] = "wakamatsu_v3_lane1_tide_frozen"
        final["lane1FlyProbabilityStatus"] = "unavailable"
        final["lane1FlyProbabilityReason"] = reason
        final["lane1FlyProbabilityDisplayStage"] = "live"
        final["lane1FlyProbabilityIsProvisional"] = False
        final["lane1FlyLevel"] = None
        if (race.get("prediction") or {}).get("phase") == "final":
            race["prediction"] = json.loads(json.dumps(final, ensure_ascii=False))
        affected += 1
    payload["preds"] = {
        str(int(race.get("race") or 0)): race.get("prediction")
        for race in payload.get("races") or []
    }
    latest_updated = _write_public_payload(dated_path, latest_path, payload)
    return {
        "status": "unavailable",
        "reason": reason,
        "affectedLiveRaces": affected,
        "latestUpdated": latest_updated,
        "productionWritten": True,
    }


def publish_v3(
    target_date: str,
    data_root: Path,
    *,
    tide_provider: Callable[[str], dict[str, Any]] = fetch_validated_tide,
) -> dict[str, Any]:
    data_root = data_root.resolve()
    dated_path, latest_path = _paths(data_root, target_date)
    if not feature_enabled():
        return mark_live_v3_unavailable(target_date, data_root, "v3_feature_disabled")
    source = load_json(dated_path)
    tide_payload = tide_provider(target_date)
    published, report = build_staging_payload(source, target_date, tide_payload)
    published.pop("staging", None)
    latest_updated = _write_public_payload(dated_path, latest_path, published)
    report.update({
        "sourcePath": str(dated_path),
        "productionWritten": True,
        "latestUpdated": latest_updated,
    })
    return report
