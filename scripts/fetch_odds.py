from __future__ import annotations

import itertools
import re
from typing import Any


VALID_COMBINATIONS = {
    f"{first}-{second}-{third}"
    for first, second, third in itertools.permutations(range(1, 7), 3)
}

VENUE_CODES = {
    "kiryu": "01", "toda": "02", "edogawa": "03", "heiwajima": "04",
    "tamagawa": "05", "hamanako": "06", "gamagori": "07", "tokoname": "08",
    "tsu": "09", "mikuni": "10", "biwako": "11", "suminoe": "12",
    "amagasaki": "13", "naruto": "14", "marugame": "15", "kojima": "16",
    "miyajima": "17", "tokuyama": "18", "shimonoseki": "19", "wakamatsu": "20",
    "ashiya": "21", "fukuoka": "22", "karatsu": "23", "omura": "24",
}


def official_odds_url(base_url: str, venue: str, date: str, race_no: int) -> str:
    code = VENUE_CODES.get(venue)
    if not code:
        raise ValueError(f"unknown venue for official odds: {venue}")
    return (
        f"{base_url.rstrip('/')}/owpc/pc/race/odds3t"
        f"?rno={race_no}&jcd={code}&hd={date.replace('-', '')}"
    )


def parse_official_odds(values: list[str], body_text: str) -> dict[str, Any]:
    if len(values) > 120:
        raise ValueError(f"unexpected official odds count: {len(values)}")
    odds: dict[str, float | str] = {}
    for row_index, value_text in enumerate(values):
        third_index, first_index = divmod(row_index, 6)
        first = first_index + 1
        seconds = [lane for lane in range(1, 7) if lane != first]
        second = seconds[third_index // 4]
        thirds = [lane for lane in range(1, 7) if lane not in {first, second}]
        third = thirds[third_index % 4]
        combination = f"{first}-{second}-{third}"
        value = value_text.strip().replace(",", "")
        if re.fullmatch(r"\d+(?:\.\d+)?", value):
            odds[combination] = float(value)
        elif value in {"-", "発売前", "発売停止", "締切"}:
            odds[combination] = value
    if "発売停止" in body_text:
        sales_status = "stopped"
    elif odds:
        sales_status = "on_sale"
    elif "発売前" in body_text:
        sales_status = "before_sales"
    elif "締切" in body_text:
        sales_status = "closed"
    else:
        sales_status = "pending"
    return {
        "bet_type": "trifecta",
        "sales_status": sales_status,
        "odds": dict(sorted(odds.items())),
        "count": len(odds),
        "missing_count": 120 - len(odds),
        "final": sales_status in {"closed", "stopped"} and len(odds) == 120,
        "_published": bool(odds) or sales_status != "pending",
        "_complete": len(odds) == 120,
    }


def parse_odds(body_text: str) -> dict[str, Any]:
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    odds: dict[str, float | str] = {}
    index = 0
    while index < len(lines):
        match = re.fullmatch(r"([1-6])\.", lines[index])
        if not match:
            index += 1
            continue
        first = int(match.group(1))
        index += 1
        for _ in range(5):
            if index >= len(lines) or not re.fullmatch(r"[1-6]", lines[index]):
                break
            second = int(lines[index])
            index += 1
            for _ in range(4):
                if index + 1 >= len(lines):
                    break
                third_text, value_text = lines[index], lines[index + 1]
                if not re.fullmatch(r"[1-6]", third_text):
                    break
                third = int(third_text)
                combination = f"{first}-{second}-{third}"
                if combination not in VALID_COMBINATIONS:
                    raise ValueError(f"invalid odds combination: {combination}")
                if re.fullmatch(r"\d+(?:\.\d+)?", value_text):
                    odds[combination] = float(value_text)
                elif value_text in {"-", "発売前", "発売停止", "締切"}:
                    odds[combination] = value_text
                else:
                    break
                index += 2
    markers = {
        "sales_before": any(label in body_text for label in ("発売前", "オッズ未確定")),
        "sales_stopped": "発売停止" in body_text,
        "closed": any(label in body_text for label in ("締切", "投票終了")),
    }
    if markers["sales_stopped"]:
        sales_status = "stopped"
    elif markers["closed"]:
        sales_status = "closed"
    elif markers["sales_before"] and not odds:
        sales_status = "before_sales"
    elif odds:
        sales_status = "on_sale"
    else:
        sales_status = "pending"
    return {
        "bet_type": "trifecta",
        "sales_status": sales_status,
        "odds": dict(sorted(odds.items())),
        "count": len(odds),
        "missing_count": 120 - len(odds),
        "final": sales_status in {"closed", "stopped"} and len(odds) == 120,
        "_published": bool(odds) or sales_status != "pending",
        "_complete": len(odds) == 120,
    }


def odds_difference(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    before = (previous or {}).get("data", {}).get("odds", {})
    after = current.get("odds", {})
    if previous and before == after:
        return (previous.get("data", {}).get("difference") or {"changed_count": 0, "changes": []})
    if not previous:
        return {"changed_count": 0, "changes": []}
    changed = []
    for combination in sorted(set(before) | set(after)):
        if before.get(combination) != after.get(combination):
            changed.append(
                {
                    "combination": combination,
                    "previous": before.get(combination),
                    "current": after.get(combination),
                }
            )
    return {"changed_count": len(changed), "changes": changed}
