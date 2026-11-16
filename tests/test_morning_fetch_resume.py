from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTOMATION))

import browser_identity  # noqa: E402
import retry_incomplete_morning  # noqa: E402


class MorningFetchResumeTest(unittest.TestCase):
    def test_chromium_user_agent_matches_runtime_version(self) -> None:
        user_agent = browser_identity.chromium_user_agent("139.0.7258.5")
        self.assertIn("X11; Linux x86_64", user_agent)
        self.assertIn("Chrome/139.0.7258.5", user_agent)
        self.assertNotIn("Chrome/124", user_agent)

    def test_security_challenge_is_detected(self) -> None:
        self.assertTrue(
            browser_identity.is_security_challenge(
                "Performing security verification - verifies you are not a bot"
            )
        )
        self.assertFalse(browser_identity.is_security_challenge("出走表 全国勝率 平均ST"))

    def test_retry_selects_only_scheduled_incomplete_venues(self) -> None:
        statuses = [
            ("常滑", "tokoname", True, False, 0),
            ("大村", "omura", True, True, 12),
            ("福岡", "fukuoka", False, False, 0),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for name, slug, scheduled, opened, entry_count in statuses:
                status_dir = root / name / "20261116"
                status_dir.mkdir(parents=True)
                (status_dir / "fetch_status.json").write_text(
                    json.dumps(
                        {
                            "slug": slug,
                            "name": name,
                            "date": "2026-11-16",
                            "open": opened,
                            "entryCount": entry_count,
                            "precheck": {"open": scheduled},
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            self.assertEqual(
                retry_incomplete_morning.retry_slugs(root, "2026-11-16"),
                ["tokoname"],
            )


if __name__ == "__main__":
    unittest.main()
