# 鳴門V5 2026-10-29 LIVE事後検証

このディレクトリは、2026年10月29日の鳴門1〜12Rについて、取得可能な過去LIVE資料を用いて現在の鳴門V5を再実行した**事後検証**です。当時公開された本予想の再現ではありません。

## 分離方針

- 予測入力は `direct`、`exhibition`、`original_exhibition` のみです。
- 結果、払戻、決まり手、オッズは、12Rすべての予測確定後に照合工程で読み込みます。
- `data/venues/naruto/20261029.json`、既存予想履歴、`v5_first60_metrics.json` は更新しません。
- 通常サイトには公開せず、既存の的中率・回収率へ混入させません。
- 欠損値の推測補完は行いません。必要項目が欠けたRは再計算不能として出力する実装です。

## 復旧元

- 展示ST、展示タイム、進入、風、波、周回、回り足、直線、オリジナル展示: BOATERSの当該日・当該Rの過去ページ
- 結果、払戻、決まり手: BOAT RACE公式の当該日・当該Rの結果ページ
- 取得元URL、取得時刻、完全性、SHA-256は `live_source_inventory.csv` に記録しています。
- 生の取得資料は `live_sources/01`〜`live_sources/12` に分離保存しています。

## 出力

- `naruto_v5_posthoc_20261029.csv`: 各Rの全艇1〜3着率、LIVE実測値、通常・飛び各10点、結果、的中、的中オッズ
- `naruto_v5_posthoc_20261029.json`: 同内容と集計・安全メタデータ
- `live_source_inventory.csv`: 各R・各資料の取得元、取得時刻、欠損、ハッシュ

再生成コマンド:

```text
python scripts/build_naruto_v5_posthoc.py
```
