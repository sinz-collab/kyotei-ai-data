from __future__ import annotations
import re
from datetime import datetime
from typing import Any

TIDE_TYPES = ("大潮", "中潮", "小潮", "長潮", "若潮")

def normalize_html_text(html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "\n", text)
    return re.sub(r"[ \t\r\f\v]+", " ", text)

def extract_tide_type(
    html: str,
    date: str,
) -> str | None:
    target = datetime.strptime(date, "%Y-%m-%d")
    text = normalize_html_text(html)

    date_patterns = (
        rf"{target.month}\s*月\s*{target.day}\s*日",
        rf"{target.month:02d}\s*月\s*{target.day:02d}\s*日",
    )

    for pattern in date_patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        nearby = text[
            match.end():
            match.end() + 1000
        ]

        tide_match = re.search(
            r"大潮|中潮|小潮|長潮|若潮",
            nearby,
        )

        if tide_match:
            return tide_match.group(0)

    # ページが対象日だけを表示している場合
    tide_matches = re.findall(
        r"大潮|中潮|小潮|長潮|若潮",
        text,
    )

    unique = list(dict.fromkeys(tide_matches))

    if len(unique) == 1:
        return unique[0]

    return None

def add_tide_type(payload: dict[str, Any], html: str, date: str) -> dict[str, Any]:
    tide_type = extract_tide_type(html, date)
    payload["tideType"] = tide_type
    payload["tide_type"] = tide_type
    return payload
