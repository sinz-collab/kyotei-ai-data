# 平和島自動予想エンジン v1

現行の平和島情報源をSQLiteへ正規化し、事前予想と直前差分補正を分離して実行します。オッズは予想確率へ使用しません。

## 重要な進入変更方針

過去進入変更DBを捏造せず、実進入取得時に各艇をactual_courseへ再配置します。その後、選手×実コース、ST、決まり手、展開、確率、SAB、買い目を再計算します。

## 実行

```bash
python heiwajima_master_compiler.py
python heiwajima_prediction_engine.py examples/sample_input_pre.json -o examples/sample_output.json
pytest -q
```

## 現状

データ接続済みのv1実装です。補正係数は初期値で、過去レース入力・結果を用いた時系列バックテストで校正する前提です。欠損は0扱いせずdata_completenessへ出力します。
