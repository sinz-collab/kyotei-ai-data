# 全場タイドグラフ「潮種」追加修正

## 追加
- `automation/tide_type_parser.py`
- `tests/test_tide_type_parser.py`

## `automation/fetch_one.py` の変更

importへ追加:

```python
from tide_type_parser import add_tide_type
```

`fetch_tide()` 内の `payload = {...}` の直後へ追加:

```python
payload = add_tide_type(payload, html, date)
```

出力:

```json
"tideType": "大潮",
"tide_type": "大潮"
```

`build_site_data.py` は `tide_today.json` 全体を公開JSONの `tide` に入れているため変更不要です。

## テスト

```bash
python -m unittest tests.test_tide_type_parser -v
python automation/fetch_one.py --venue karatsu --date 2026-07-29
```
