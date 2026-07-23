from __future__ import annotations

import itertools
import re
from typing import Any


VALID_COMBINATIONS = {
    f"{first}-{second}-{third}"
    for first, second, third in itertools.permutations(range(1, 7), 3)
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
