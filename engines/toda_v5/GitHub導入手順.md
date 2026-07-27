# GitHub導入手順

## 推奨配置
`kyotei-ai-data` または予想生成側リポジトリへ、このフォルダを `engines/toda_v5/` として配置します。

## 朝処理
1. 当日の戸田JSONを取得
2. `generate_toda_predictions_v5.py` を実行
3. `payload.engine` が `toda_prediction_engine_v5_20260727` になった出力JSONを保存
4. サイトは出力JSONを表示するだけにする

## サイト側
現行のブラウザ生成v4は、v5出力が存在する場合は実行しないようにします。

判定例:
```javascript
if (payload.engine === "toda_prediction_engine_v5_20260727") {
  // v5の予想済みJSONをそのまま表示
} else {
  // 予想準備中
}
```

## 直前処理
直前・展示・オリジナル展示JSONが揃った時点で `toda_live_review_v5.apply_live_review()` を実行し、
同じ予想JSONの確率・買い目・補正ログを更新します。
