# 全場・選手比較「決まり手」反映修正

## 原因
`.github/workflows/morning-data.yml` では `automation/enrich_heiwajima_kimarite.py` だけを実行しており、
平和島以外の会場にボーターズ選手比較ページの決まり手データが反映されていません。

## 適用内容
1. `automation/enrich_all_venues_kimarite.py` を追加
2. `tests/test_enrich_all_venues_kimarite.py` を追加
3. `.github/workflows/morning-data.yml` の2箇所を変更

変更前:
```bash
python automation/enrich_heiwajima_kimarite.py --date "$TARGET_DATE"
```

変更後:
```bash
python automation/enrich_all_venues_kimarite.py --date "$TARGET_DATE"
```

対象箇所:
- Build and validate public data
- Commit and push public data の再ビルド部分

## 出力フィールド
1号艇:
- boaters_kimarite_starts
- boaters_escape_rate
- boaters_sashare_rate
- boaters_makurare_rate
- boaters_makurare_zashi_rate

2〜6号艇:
- boaters_kimarite_starts
- boaters_nigashi_rate
- boaters_sashi_rate
- boaters_makuri_rate
- boaters_makuri_sashi_rate

各会場JSON:
```json
"sourceStatus": {
  "kimarite": {
    "status": "loaded|partial|missing",
    "reflected": 72,
    "expected": 72,
    "source_races": [1,2,3,4,5,6,7,8,9,10,11,12],
    "warnings": []
  }
}
```

## 検証
```bash
python -m unittest tests.test_enrich_all_venues_kimarite -v
python automation/enrich_all_venues_kimarite.py --date 2026-07-29
```

全開催場について `reflected == expected` を確認する。
一部欠損時も処理を継続し、`warnings` にレース番号を記録する。
