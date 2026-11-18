"""Browser identity helpers shared by the BOATERS fetcher and tests."""

from __future__ import annotations

import re


SECURITY_CHALLENGE_MARKERS = (
    "Performing security verification",
    "verifies you are not a bot",
    "セキュリティ検証の実行",
    "ボットではないことを確認している間",
)


def chromium_user_agent(browser_version: str) -> str:
    """Keep the advertised Chrome version aligned with Playwright's Chromium."""
    version = browser_version.strip()
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", version):
        raise ValueError(f"unexpected Chromium version: {browser_version!r}")
    return (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Safari/537.36"
    )


def is_security_challenge(text: str) -> bool:
    return any(marker in text for marker in SECURITY_CHALLENGE_MARKERS)
