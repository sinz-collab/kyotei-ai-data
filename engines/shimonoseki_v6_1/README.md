# Shimonoseki AI Engine v6.1

Engine ID: `shimonoseki_engine_v6.1`  
Version: `6.1`

## Purpose
v6.0の基礎思想を維持しつつ、以下を追加した下関専用サーバー予想エンジン。

- 実コース再計算
- 1号艇防御力 × 相手の観測/潜在攻撃力
- 決まり手率0を攻撃力0として扱わない
- 条件付き2着・3着連動（1逃げ、2差し、3攻め、4カド、5差し込み、6外残り）
- SABの展開分岐/エントロピー評価
- 仮予想→本予想の `probabilityReview` / delta 出力
- 結果・オッズ非使用

## Calibration
`calibration_mode = identity_pending_chronological_fit`。
時系列OOF予測がこのパッケージにないため、Platt/Isotonicを捏造せずidentityのままにしている。
将来は時系列holdoutでBrier/logloss/ECEを比較後にのみ導入する。

## Production dependency migration
このセッションには現行v6の `shimonoseki_motor_recent10_master_v1.csv` とビルダー本体がローカル保存されていないため、Codex切替時に現行v6から**内容を変えず移行**すること。
runnerはこのrecent10 masterが無い場合、本番実行をfail-closedする。

## Regression fixtures
`tests/test_regression_20260823.py` は2026-08-23の4R/5R/7Rについて、事前/直前入力だけをフィクスチャ化している。結果はスコア入力に含めない。

期待：
- R4 `1-6-3` が10点内
- R5 `1-3-6` が10点内
- R7 `1-4-6` が本線6点内

この期待値は回帰検査用であり、エンジンコードに結果/買い目をハードコードしていない。
