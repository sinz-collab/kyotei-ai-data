from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

ENGINE_ROOT = (
    REPO_ROOT
    / "engines"
    / "ashiya"
    / "ashiya_engine_v1_6"
)

ENGINE_PACKAGE_ROOT = ENGINE_ROOT
ENGINE_SCRIPT_ROOT = ENGINE_ROOT / "scripts"

MODELS_DIR = ENGINE_ROOT / "data" / "models"
PLAYER_DB_DIR = ENGINE_ROOT / "data" / "player_db"

# 芦屋エンジン以外へ影響を与えないよう、
# import path はこのrunnerのプロセス内だけで追加する。
sys.path.insert(
    0,
    str(ENGINE_PACKAGE_ROOT),
)

sys.path.insert(
    0,
    str(ENGINE_SCRIPT_ROOT),
)

from ashiya_engine.engine import AshiyaEngine
from predict_venue_json import merge_race_payload


ENGINE_NAME = "ashiya_prediction_engine"
ENGINE_VERSION = "1.6.1"
EXPECTED_RACES = set(range(1, 13))


def load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)

    document = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(document, dict):
        raise RuntimeError(
            f"invalid_json_root: {path}"
        )

    return document


def ashiya_availability(
    data_root: Path,
    expected_date: str,
) -> tuple[str, str]:
    """Classify a missing dated JSON using the current morning metadata."""
    report_path = data_root / "morning_report.json"

    if report_path.is_file():
        report = load_json(report_path)

        if report.get("date") == expected_date:
            venue = (report.get("venues") or {}).get("ashiya")

            if isinstance(venue, dict):
                reason = str(
                    (venue.get("detail") or {}).get("reason")
                    or venue.get("predictionReason")
                    or ""
                )

                if venue.get("open") or venue.get("raceDataAvailable"):
                    return "open", reason or "race_data_available"

                if reason == "not_scheduled" or (
                    not reason
                    and venue.get("predictionStatus") == "not_running"
                ):
                    return "not_open", reason or "not_running"

                return "fetch_failed", reason or "fetch_incomplete"

    manifest_path = data_root / "manifest.json"

    if manifest_path.is_file():
        manifest = load_json(manifest_path)

        if manifest.get("date") == expected_date:
            for venue in manifest.get("venues") or []:
                if not isinstance(venue, dict) or venue.get("slug") != "ashiya":
                    continue

                reason = str(venue.get("availabilityReason") or "")

                if venue.get("open") or venue.get("raceDataAvailable"):
                    return "open", reason or "race_data_available"

                if reason in {"", "not_scheduled", "not_running"}:
                    return "not_open", reason or "not_running"

                return "fetch_failed", reason

    return "unknown", "availability_metadata_missing"


def atomic_write_json(
    path: Path,
    payload: dict,
) -> None:
    """
    同じディレクトリ内に一時ファイルを書き、
    最後にos.replaceする。
    """
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )

            handle.write("\n")
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        os.replace(
            temporary_name,
            path,
        )

    except Exception:
        try:
            os.unlink(
                temporary_name
            )
        except OSError:
            pass

        raise


def as_race_no(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def validate_source_payload(
    payload: dict,
    expected_date: str,
) -> None:
    """
    エンジンを実行する前に朝取得JSONを検証。
    ここで失敗した場合は既存JSONを書き換えない。
    """
    actual_date = str(
        payload.get("date")
        or ""
    )

    if actual_date != expected_date:
        raise RuntimeError(
            "ashiya_date_mismatch: "
            f"expected={expected_date} "
            f"actual={actual_date}"
        )

    venue_id = str(
        payload.get("venueId")
        or ""
    ).lower()

    venue_name = str(
        payload.get("venue")
        or ""
    )

    if (
        venue_id != "ashiya"
        and "芦屋" not in venue_name
        and venue_name.lower() != "ashiya"
    ):
        raise RuntimeError(
            "ashiya_venue_mismatch: "
            f"venueId={venue_id} "
            f"venue={venue_name}"
        )

    races = payload.get("races")

    if not isinstance(
        races,
        list,
    ):
        raise RuntimeError(
            "ashiya_races_missing"
        )

    race_numbers = {
        as_race_no(
            race.get("race")
            or race.get("race_no")
        )
        for race in races
        if isinstance(race, dict)
    }

    if race_numbers != EXPECTED_RACES:
        raise RuntimeError(
            "ashiya_race_count_invalid: "
            f"{sorted(race_numbers)}"
        )

    for race in races:
        if not isinstance(
            race,
            dict,
        ):
            raise RuntimeError(
                "ashiya_invalid_race_object"
            )

        race_no = as_race_no(
            race.get("race")
            or race.get("race_no")
        )

        racers = (
            race.get("racers")
            or race.get("entries")
            or []
        )

        if (
            not isinstance(
                racers,
                list,
            )
            or len(racers) != 6
        ):
            raise RuntimeError(
                f"ashiya_{race_no:02d}_racers_invalid"
            )


def probability_dict(
    probabilities: list,
    key: str,
) -> dict[str, float]:
    """
    v1.6内部確率 0-1
       ↓
    サイト互換 0-100 (%)

    最後に合計100へ再正規化する。
    """
    values = {}

    for item in probabilities:
        if not isinstance(
            item,
            dict,
        ):
            continue

        lane = as_race_no(
            item.get("lane")
        )

        if lane not in EXPECTED_RACES:
            # laneは1～6だけ
            if lane < 1 or lane > 6:
                continue

        try:
            value = float(
                item.get(key)
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        if not math.isfinite(
            value
        ):
            continue

        values[str(lane)] = max(
            0.0,
            value,
        )

    if set(values) != {
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
    }:
        raise RuntimeError(
            f"probability_{key}_lanes_invalid"
        )

    total = sum(
        values.values()
    )

    if total <= 0:
        raise RuntimeError(
            f"probability_{key}_total_invalid"
        )

    normalized = {
        lane: (
            value
            / total
            * 100.0
        )
        for lane, value
        in values.items()
    }

    rounded = {
        lane: round(
            value,
            4,
        )
        for lane, value
        in normalized.items()
    }

    # 丸め誤差だけ1号艇へ戻す。
    # 予測補正ではなくJSON上の100%整合用。
    diff = round(
        100.0
        - sum(
            rounded.values()
        ),
        4,
    )

    rounded["1"] = round(
        rounded["1"]
        + diff,
        4,
    )

    return rounded


def ticket_combo(
    ticket,
) -> str | None:
    if isinstance(
        ticket,
        str,
    ):
        combo = ticket.strip()

        return combo or None

    if not isinstance(
        ticket,
        dict,
    ):
        return None

    combo = (
        ticket.get("combination")
        or ticket.get("combo")
        or ticket.get("ticket")
    )

    if combo in (
        None,
        "",
    ):
        return None

    return str(
        combo
    ).strip()


def ticket_list(
    tickets,
) -> list[str]:
    if not isinstance(
        tickets,
        list,
    ):
        return []

    output = []

    for ticket in tickets:
        combo = ticket_combo(
            ticket
        )

        if (
            combo
            and combo not in output
        ):
            output.append(
                combo
            )

    return output


def validate_ticket_structure(
    result: dict,
    race_no: int,
) -> tuple[
    list[str],
    list[str],
    list[str],
    list[str],
]:
    tickets = result.get(
        "tickets"
    )

    if not isinstance(
        tickets,
        dict,
    ):
        raise RuntimeError(
            f"ashiya_{race_no:02d}_tickets_missing"
        )

    main = ticket_list(
        tickets.get("main")
    )

    deviation = ticket_list(
        tickets.get("deviation")
    )

    upset = ticket_list(
        tickets.get("upset")
    )

    all_tickets = ticket_list(
        tickets.get("all")
    )

    # allがエンジン側に無い場合のみ
    # 6+2+2から構成。
    if not all_tickets:
        all_tickets = (
            main
            + deviation
            + upset
        )

    if len(main) != 6:
        raise RuntimeError(
            f"ashiya_{race_no:02d}_main_ticket_count:"
            f"{len(main)}"
        )

    if len(deviation) != 2:
        raise RuntimeError(
            f"ashiya_{race_no:02d}_deviation_ticket_count:"
            f"{len(deviation)}"
        )

    if len(upset) != 2:
        raise RuntimeError(
            f"ashiya_{race_no:02d}_upset_ticket_count:"
            f"{len(upset)}"
        )

    if len(all_tickets) != 10:
        raise RuntimeError(
            f"ashiya_{race_no:02d}_ticket_count:"
            f"{len(all_tickets)}"
        )

    if len(
        set(
            all_tickets
        )
    ) != 10:
        raise RuntimeError(
            f"ashiya_{race_no:02d}_ticket_duplicate"
        )

    return (
        main,
        deviation,
        upset,
        all_tickets,
    )


def build_site_prediction(
    result: dict,
    race_no: int,
    stage: str,
) -> tuple[
    dict,
    dict,
]:
    probabilities = result.get(
        "probabilities"
    )

    if (
        not isinstance(
            probabilities,
            list,
        )
        or len(
            probabilities
        ) != 6
    ):
        raise RuntimeError(
            f"ashiya_{race_no:02d}_probabilities_invalid"
        )

    win = probability_dict(
        probabilities,
        "win",
    )

    second = probability_dict(
        probabilities,
        "second",
    )

    third = probability_dict(
        probabilities,
        "third",
    )

    main, deviation, upset, all_tickets = (
        validate_ticket_structure(
            result,
            race_no,
        )
    )

    sab = result.get(
        "sab"
    )

    if not isinstance(
        sab,
        dict,
    ):
        raise RuntimeError(
            f"ashiya_{race_no:02d}_sab_invalid"
        )

    sab_grade = str(
        sab.get("grade")
        or ""
    ).strip()

    if sab_grade not in {
        "S",
        "A",
        "B",
        "見",
    }:
        raise RuntimeError(
            f"ashiya_{race_no:02d}_sab_grade_invalid:"
            f"{sab_grade}"
        )

    # -------------------------
    # legacy / site-compatible
    # top-level preds
    # -------------------------
    legacy = {
        "win": win,
        "second": second,
        "third": third,

        "sab": sab_grade,
        "sabDetail": deepcopy(
            sab
        ),

        # 既存build_site_dataのgate互換
        "ai": deepcopy(
            main
        ),

        "balance": deepcopy(
            deviation
        ),

        "aiUpset": deepcopy(
            upset
        ),

        "tickets": deepcopy(
            all_tickets
        ),

        "predictionStage": stage,
        "active_prediction_stage": stage,

        "engine": ENGINE_NAME,
        "engineVersion": ENGINE_VERSION,

        "fallback": False,
        "fallbackUsed": False,

        "attackStructure": deepcopy(
            result.get(
                "attack_structure"
            )
            or result.get(
                "attackStructure"
            )
            or {}
        ),

        "scenarios": deepcopy(
            result.get(
                "scenarios"
            )
            or []
        ),

        "audit": deepcopy(
            result.get(
                "audit"
            )
            or {}
        ),
    }

    # -------------------------
    # race["prediction"]用
    # v1.6 native形式
    # -------------------------
    native = {
        "status": "ready",

        "engine": ENGINE_NAME,
        "engine_version": ENGINE_VERSION,

        "stage": stage,

        "probabilities": {
            "win": deepcopy(
                win
            ),
            "second": deepcopy(
                second
            ),
            "third": deepcopy(
                third
            ),
        },

        "sab": deepcopy(
            sab
        ),

        "tickets": {
            "main": deepcopy(
                main
            ),
            "deviation": deepcopy(
                deviation
            ),
            "upset": deepcopy(
                upset
            ),
            "all": deepcopy(
                all_tickets
            ),
        },

        "attack_structure": deepcopy(
            result.get(
                "attack_structure"
            )
            or result.get(
                "attackStructure"
            )
            or {}
        ),

        "scenarios": deepcopy(
            result.get(
                "scenarios"
            )
            or []
        ),

        "audit": deepcopy(
            result.get(
                "audit"
            )
            or {}
        ),
    }

    return legacy, native


def run_engine(
    payload: dict,
    stage: str,
) -> dict[int, dict]:
    engine = AshiyaEngine(
        MODELS_DIR,
        PLAYER_DB_DIR,
    )

    output = {}

    for race in sorted(
        payload["races"],
        key=lambda item: as_race_no(
            item.get("race")
            or item.get("race_no")
        ),
    ):
        race_no = as_race_no(
            race.get("race")
            or race.get("race_no")
        )

        merged = merge_race_payload(
            payload,
            race,
        )

        result = engine.predict(
            merged,
            stage=stage,
        )

        if not isinstance(
            result,
            dict,
        ):
            raise RuntimeError(
                f"ashiya_{race_no:02d}_engine_output_invalid"
            )

        output[
            race_no
        ] = result

    if set(
        output
    ) != EXPECTED_RACES:
        raise RuntimeError(
            "ashiya_engine_race_count_invalid"
        )

    return output


def merge_predictions(
    source_payload: dict,
    engine_results: dict[int, dict],
    stage: str,
) -> dict:
    """
    source_payloadのコピーに予想ドメインだけを反映する。

    tide/live/odds/result/racers/setsukan等は
    source_payloadからそのまま保持される。

    stage=pre の再実行時、
    既に live/final まで進んでいるレースは
    preliminary へ巻き戻さず既存予想を保持する。
    """
    payload = deepcopy(
        source_payload
    )

    existing_predictions = (
        source_payload.get("preds")
        or {}
    )

    predictions = {}

    race_by_no = {
        as_race_no(
            race.get("race")
            or race.get("race_no")
        ): race
        for race in payload.get(
            "races"
        )
        or []
    }

    source_race_by_no = {
        as_race_no(
            race.get("race")
            or race.get("race_no")
        ): race
        for race in source_payload.get(
            "races"
        )
        or []
    }

    for race_no in range(
        1,
        13,
    ):
        race_key = str(
            race_no
        )

        race = race_by_no.get(
            race_no
        )

        if race is None:
            raise RuntimeError(
                f"ashiya_{race_no:02d}_merge_race_missing"
            )

        existing_legacy = (
            existing_predictions.get(
                race_key
            )
            or {}
        )

        source_race = (
            source_race_by_no.get(
                race_no
            )
            or {}
        )

        existing_native = (
            source_race.get(
                "prediction"
            )
            or {}
        )

        legacy_stage = str(
            existing_legacy.get(
                "active_prediction_stage"
            )
            or ""
        ).strip().lower()

        native_stage = str(
            existing_native.get(
                "stage"
            )
            or ""
        ).strip().lower()

        preserve_advanced_legacy = (
            stage == "pre"
            and legacy_stage in {
                "live",
                "final",
            }
        )

        result = engine_results[
            race_no
        ]

        legacy, native = (
            build_site_prediction(
                result,
                race_no,
                stage,
            )
        )

        if not preserve_advanced_legacy:
            for preserved_key in (
                "realtime",
                "odds",
                "result",
                "prediction_history",
                "active_prediction_stage",
            ):
                if preserved_key in existing_legacy:
                    legacy[
                        preserved_key
                    ] = deepcopy(
                        existing_legacy[
                            preserved_key
                        ]
                    )

        predictions[
            race_key
        ] = (
            deepcopy(
                existing_legacy
            )
            if preserve_advanced_legacy
            else legacy
        )

        # 既存のlive/odds/resultは触らず
        # predictionだけ差し替える。
        race[
            "prediction"
        ] = (
            deepcopy(
                existing_native
            )
            if (
                stage == "pre"
                and native_stage in {
                    "live",
                    "final",
                }
            )
            else native
        )

    payload[
        "preds"
    ] = predictions

    payload[
        "engine"
    ] = ENGINE_NAME

    payload[
        "engineVersion"
    ] = ENGINE_VERSION

    payload[
        "predictionStatus"
    ] = "ready"

    payload[
        "predictionReason"
    ] = ""

    return payload

def probability_map_valid(
    values,
) -> bool:
    if not isinstance(
        values,
        dict,
    ):
        return False

    if set(
        values
    ) != {
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
    }:
        return False

    try:
        numbers = [
            float(
                values[
                    str(lane)
                ]
            )
            for lane in range(
                1,
                7,
            )
        ]

    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        return False

    return (
        all(
            math.isfinite(
                number
            )
            and 0 <= number <= 100
            for number
            in numbers
        )
        and abs(
            sum(
                numbers
            )
            - 100.0
        )
        <= 0.05
    )


def validate_final_payload(
    payload: dict,
    expected_date: str,
) -> None:
    """
    ファイルへ書く直前の最終ゲート。
    """
    validate_source_payload(
        payload,
        expected_date,
    )

    if (
        payload.get("engine")
        != ENGINE_NAME
    ):
        raise RuntimeError(
            "ashiya_final_engine_invalid"
        )

    if str(
        payload.get(
            "engineVersion"
        )
        or ""
    ) != ENGINE_VERSION:
        raise RuntimeError(
            "ashiya_final_engine_version_invalid"
        )

    predictions = payload.get(
        "preds"
    )

    if (
        not isinstance(
            predictions,
            dict,
        )
        or set(
            predictions
        )
        != {
            str(i)
            for i in range(
                1,
                13,
            )
        }
    ):
        raise RuntimeError(
            "ashiya_final_predictions_invalid"
        )

    for race_no in range(
        1,
        13,
    ):
        prediction = predictions[
            str(
                race_no
            )
        ]

        for key in (
            "win",
            "second",
            "third",
        ):
            if not probability_map_valid(
                prediction.get(
                    key
                )
            ):
                raise RuntimeError(
                    f"ashiya_{race_no:02d}_{key}_invalid"
                )

        if prediction.get(
            "sab"
        ) not in {
            "S",
            "A",
            "B",
            "見",
        }:
            raise RuntimeError(
                f"ashiya_{race_no:02d}_sab_invalid"
            )

        tickets = prediction.get(
            "tickets"
        )

        if (
            not isinstance(
                tickets,
                list,
            )
            or len(
                tickets
            )
            != 10
            or len(
                set(
                    tickets
                )
            )
            != 10
        ):
            raise RuntimeError(
                f"ashiya_{race_no:02d}_tickets_invalid"
            )


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--date",
        required=True,
        help="YYYY-MM-DD",
    )

    parser.add_argument(
        "--data-root",
        default="data",
    )

    parser.add_argument(
        "--stage",
        choices=[
            "pre",
            "live",
            "final",
        ],
        default="pre",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "エンジン実行と検証のみ。"
            "JSONは書き換えない。"
        ),
    )

    parser.add_argument(
        "--require-open",
        action="store_true",
        help=(
            "Fail when Ashiya JSON does not exist instead of skipping."
        ),
    )

    args = parser.parse_args()

    date_key = (
        args.date
        .replace(
            "-",
            "",
        )
    )

    data_root = Path(
        args.data_root
    )

    dated_path = (
        data_root
        / "venues"
        / "ashiya"
        / f"{date_key}.json"
    )

    latest_path = (
        data_root
        / "venues"
        / "ashiya"
        / "latest.json"
    )

    if not dated_path.is_file():
        message = f"Ashiya data is not open: {dated_path}"
        availability, reason = ashiya_availability(
            data_root,
            args.date,
        )

        if args.require_open or availability in {
            "open",
            "fetch_failed",
        }:
            raise FileNotFoundError(
                f"{message} (availability={availability}, reason={reason})"
            )

        print(message)
        return 0

    source_payload = load_json(
        dated_path
    )

    validate_source_payload(
        source_payload,
        args.date,
    )

    engine_results = run_engine(
        source_payload,
        args.stage,
    )

    final_payload = merge_predictions(
        source_payload,
        engine_results,
        args.stage,
    )

    validate_final_payload(
        final_payload,
        args.date,
    )

    sab_counts = {
        "S": 0,
        "A": 0,
        "B": 0,
        "見": 0,
    }

    coverage = []

    for race_no in range(
        1,
        13,
    ):
        result = engine_results[
            race_no
        ]

        grade = str(
            (
                result.get("sab")
                or {}
            ).get(
                "grade"
            )
            or ""
        )

        if grade in sab_counts:
            sab_counts[
                grade
            ] += 1

        audit = result.get(
            "audit"
        ) or {}

        model_audit = audit.get(
            "model"
        ) or {}

        try:
            coverage.append(
                float(
                    model_audit.get(
                        "coverage"
                    )
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            pass

    report = {
        "date": args.date,
        "venue": "ashiya",
        "engine": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "stage": args.stage,
        "races": 12,
        "ticketsPerRace": 10,
        "sab": sab_counts,
        "dryRun": bool(
            args.dry_run
        ),
    }

    if coverage:
        report[
            "coverageMin"
        ] = round(
            min(
                coverage
            ),
            4,
        )

        report[
            "coverageMax"
        ] = round(
            max(
                coverage
            ),
            4,
        )

        report[
            "coverageMean"
        ] = round(
            sum(
                coverage
            )
            / len(
                coverage
            ),
            4,
        )

    if not args.dry_run:
        # 全検証を通過した後だけ書き込む。
        atomic_write_json(
            dated_path,
            final_payload,
        )

        atomic_write_json(
            latest_path,
            final_payload,
        )

        report[
            "datedPath"
        ] = str(
            dated_path
        )

        report[
            "latestPath"
        ] = str(
            latest_path
        )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
