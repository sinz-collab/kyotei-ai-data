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

def extract_tide_type(html: str, date: str) -> str | None:
    target = datetime.strptime(date, "%Y-%m-%d")
    text = normalize_html_text(html)
    pattern = rf"{target.month}\s*月\s*{target.day}\s*日\s*[（(][^）)]*[）)]"
    match = re.search(pattern, text)
    if match:
        nearby = text[match.end():match.end() + 500]
        tide_match = re.search("|".join(map(re.escape, TIDE_TYPES)), nearby)
        if tide_match:
            return tide_match.group(0)
    return None

def add_tide_type(payload: dict[str, Any], html: str, date: str) -> dict[str, Any]:
    tide_type = extract_tide_type(html, date)
    payload["tideType"] = tide_type
    payload["tide_type"] = tide_type
    return payload
