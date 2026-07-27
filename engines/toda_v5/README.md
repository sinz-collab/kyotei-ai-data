# 戸田予想エンジン v5.1 COMPACT

GitHubブラウザの25MB/ファイル上限に対応した小型版です。

## マスター形式
選手×コース・進入ズレ・枠・ST・潮DBを次のSQLiteへ格納しています。

```text
master_json/toda_master_v5.sqlite3
```

Python標準ライブラリ `sqlite3` だけで参照するため、追加パッケージや外部APIは不要です。

## アップロード
このフォルダの中身を、次へアップロードしてください。

```text
kyotei-ai-data/engines/toda_v5/
```

`__pycache__` は含まれていません。

## 実行
```bash
python engines/toda_v5/tools/generate_toda_predictions_v5.py input.json output.json
```
