from __future__ import annotations

import html
import re
from typing import Any

from fetch_odds import VENUE_CODES


def official_result_url(base_url: str, venue: str, date: str, race_no: int) -> str:
    code = VENUE_CODES.get(venue)
    if not code:
        raise ValueError(f"unknown venue for official result: {venue}")
    return (
        f"{base_url.rstrip('/')}/owpc/pc/race/raceresult"
        f"?rno={race_no}&jcd={code}&hd={date.replace('-', '')}"
    )


def parse_result(html_text: str) -> dict[str, Any]:
    if "データはありません" in html_text:
        return {"_published": False, "_complete": False}
    finish_order = re.findall(
        r'is-fBold\s+is-boatColor[1-6]">\s*([1-6])\s*</td>',
        html_text,
    )
    trifecta_match = re.search(r"3連単</td>(.*?)</tr>", html_text, re.S)
    section = trifecta_match.group(1) if trifecta_match else ""
    order = re.findall(r"numberSet1_number[^>]*>\s*([1-6])\s*</span>", section)
    payout_match = re.search(r'is-payout1">(?:&yen;|¥)\s*([\d,]+)</span>', section)
    popularity_cells = re.findall(r"<td[^>]*>\s*(\d+)\s*</td>", section)
    kimarite_match = re.search(
        r"<th>決まり手</th>.*?<td[^>]*>\s*([^<]+?)\s*</td>",
        html_text,
        re.S,
    )
    complete = len(order) == 3 and len(set(order)) == 3 and payout_match is not None
    return {
        "order": order[:3],
        "full_order": finish_order[:6],
        "payout3t": f"{payout_match.group(1)}円" if payout_match else None,
        "popularity3t": int(popularity_cells[-1]) if popularity_cells else None,
        "kimarite": html.unescape(kimarite_match.group(1)).strip() if kimarite_match else None,
        "_published": bool(order or finish_order),
        "_complete": complete,
    }
