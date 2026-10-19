# Ashiya Prediction Engine v1.0

芦屋AI v4.2運用ロジックを基準に、v5.1の通常6・3・1買い目と監査情報を統合したリリース候補です。展示タイム補正係数は独立検証資料が不足しているため、本番では無効です。

## 設計

1. LightGBM 3モデルの5-fold平均で着別基礎スコアを生成
2. 選手×実コース、枠、進入ズレDBを信頼度・サンプル数付きで混合
3. BOATERS選手比較の決まり手を `boaters_kimarite` として攻め役・展開判定へ使用
4. 3攻め→4連動、4カド→5連動、外攻め不発→内残り等を弱い上限付き補正
5. 進入変更時は `actual_course` で全工程を再計算
6. SABは予想再現性判定で、10点買い目数から分離
7. 買い目は本線6、ズレ3、シナリオ穴1、重複なし
8. オッズは確率・買い目生成に不使用
9. 一周・回り足のv5.1補正は研究モードで再現可能だが、正式採用まで本番無効

## 注意

モデルは302特徴量を要求します。当日入力で未取得の特徴量は監査ログ `audit.model.missing_features` に明示され、特徴量充足率に応じてモデル比重が自動的に下がります。学習時の完全な特徴量生成コードが未提供のため、v1では安全な部分入力アダプターと選手DBフォールバックを採用しています。

## 実行

```bash
python -m ashiya_engine.cli \
  --input samples/sample_input_pre.json \
  --output samples/sample_output.json \
  --models data/models \
  --player-db data/player_db \
  --stage pre
```

## BOATERS決まり手入力

艇別に以下を追加します。率は0〜1または0〜100のどちらでも受け付けます。

```json
"boaters_kimarite": {
  "escape_rate": 0.62,
  "sashi_rate": 0.08,
  "makuri_rate": 0.15,
  "makurizashi_rate": 0.12,
  "nuki_rate": 0.02,
  "megumare_rate": 0.01
}
```

## GitHub/VPS接続

既存 `automation/apply_ashiya_live_v1.py` が直前・展示・オリ展示・実進入を日付別JSONへ反映した後、本CLIを `--stage live` で呼び出します。出力は一旦別ファイルへ書き、検証成功後に日付別JSONとlatestへ原子的に反映してください。

### 予想履歴

`preds.<race>.prediction_history` に朝は `morning`、直前反映後は `live` として追記します。各スナップショットは通常10点のスコアと `main 6 / deviation 3 / scenario_hole 1`、飛び専用の `strength_25_frozen` と買い目、確率、入力、エンジン版を保存します。飛び10点はLIVE専用のため、朝の履歴は `not_generated_morning` と空配列を保存し、後付け生成しません。確定結果は履歴の入力から除外され、結果取得後も過去の予想を上書きしません。

評価は朝/LIVEを分け、1Rを1回として、通常10点的中率、本線・ズレ・シナリオ穴の的中数、飛び専用10点的中率、および通常＋飛び20点の合算的中率を集計対象とします。飛び10点が未生成のステージは、飛びおよび20点合算の分母に含めません。

## GitHubの日付別会場JSONを一括処理

```bash
PYTHONPATH=. python scripts/predict_venue_json.py \
  --input data/venues/ashiya/20260805.json \
  --output output/ashiya_20260805_predictions.json \
  --models data/models \
  --player-db data/player_db \
  --stage pre
```

1レースだけ検証する場合は `--race 1` を追加します。

## v1.1.0 scenario linkage update
- Course-4 slit-shape override requires both a clear course-3 dent and supporting lap/SUM/motor evidence.
- When course 4 attacks, course 5 and 6 receive bounded head-conditional linkage bonuses based on exhibition, lap, SUM, motor, season form and kimarite.
- When course 3 is dented, course 2 receives a bounded third-place inside-survival bonus under a course-4 head scenario.
- Ticket scoring now uses head-conditional links, not only unconditional marginals.


## v1.5 実戦裏付けスコア
最終日は、節間30%、当地25%、選手格20%、モーター15%、今節同コース10%を一括評価し、スリット・当地ST・展開補正の前段で有界加減点します。
