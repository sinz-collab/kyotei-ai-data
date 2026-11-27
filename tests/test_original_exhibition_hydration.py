from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fetch_live_race import LiveSourceClient


class OriginalExhibitionHydrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_until_complete(self) -> None:
        page = Mock()
        page.content = AsyncMock(return_value="html")
        page.locator.return_value.inner_text = AsyncMock(return_value="body")
        page.wait_for_timeout = AsyncMock()
        client = LiveSourceClient(None, {}, Mock(), Mock())
        incomplete = {"entries": [], "_published": False, "_complete": False}
        complete = {"entries": [{"lane": lane} for lane in range(1, 7)], "_published": True, "_complete": True}
        with patch("fetch_live_race.parse_original_exhibition", side_effect=[incomplete, complete]) as parser:
            result = await client._read_original_exhibition(page, {"race_no": 4, "venue": "wakamatsu"})
        self.assertIs(result, complete)
        self.assertEqual(parser.call_count, 2)
        page.wait_for_timeout.assert_awaited_once_with(700)

    async def test_remains_pending_without_fabricating_data(self) -> None:
        page = Mock()
        page.content = AsyncMock(return_value="html")
        page.locator.return_value.inner_text = AsyncMock(return_value="body")
        page.wait_for_timeout = AsyncMock()
        logger = Mock()
        client = LiveSourceClient(None, {}, logger, Mock())
        incomplete = {"entries": [], "_published": False, "_complete": False}
        with patch("fetch_live_race.parse_original_exhibition", return_value=incomplete) as parser:
            result = await client._read_original_exhibition(page, {"race_no": 4, "venue": "wakamatsu"})
        self.assertIs(result, incomplete)
        self.assertEqual(parser.call_count, 3)
        self.assertEqual(page.wait_for_timeout.await_count, 2)
        self.assertEqual(logger.info.call_args.kwargs["extra"]["entry_count"], 0)


if __name__ == "__main__":
    unittest.main()
