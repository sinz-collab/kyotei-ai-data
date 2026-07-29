from __future__ import annotations
import unittest
from automation.tide_type_parser import add_tide_type, extract_tide_type

class TideTypeParserTest(unittest.TestCase):
    def test_extract(self):
        html = "<div>7月29日（水）</div><div>大潮</div><div>月齢 14.7</div>"
        self.assertEqual(extract_tide_type(html, "2026-07-29"), "大潮")

    def test_fields(self):
        result = add_tide_type({}, "<div>7月29日（水）</div><b>小潮</b>", "2026-07-29")
        self.assertEqual(result["tideType"], "小潮")
        self.assertEqual(result["tide_type"], "小潮")

if __name__ == "__main__":
    unittest.main()
