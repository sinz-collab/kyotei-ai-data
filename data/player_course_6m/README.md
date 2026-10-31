# 直近6か月・選手別実進入コース基盤

このディレクトリは予想エンジンおよび公開サイトから独立したデータ基盤である。

- `player_course_6m.sqlite`: 公式競走成績の正規化行、日別バケット、差分更新用6か月キャッシュ、取得元URL・SHA-256・取得時刻
- `player_course_6m.csv`: 登録番号×実進入コースの利用側向けスナップショット（UTF-8 BOM）
- `last_update.json`: 直近処理の成否、最新取得日、欠損、処理時間

実行例（`python` は運用環境のPython 3.11以上を指定）:

```powershell
python automation/player_course_6m.py backfill --start 2026-03-14 --end 2026-10-30
python automation/player_course_6m.py daily --soft-fail
python automation/player_course_6m.py verify --start 2026-03-14 --end 2026-10-30 --compare 4320,3946,4444
```

既存朝処理へ接続する場合は `automation/run_player_course_daily.ps1 -Python <python.exe>` を独立ステップとして呼ぶ。このラッパーは取得失敗時にも終了コード0とし、直前キャッシュを保持する。定義の詳細は `reports/PLAYER_COURSE_6M_DEFINITION.md` を参照。
