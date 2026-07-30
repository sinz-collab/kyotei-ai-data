# 常滑自動予想エンジン v1.6

## Engine information

Engine:
tokoname_engine

Version:
1.6

Purpose:
Tokoname prediction engine

Odds:
Not used for probability calculation

Tickets:
10 tickets

既存の Win / Top2 / Top3 RandomForestモデルを基礎確率に使用し、常滑向けの直前展示・スリット・オリジナル展示・当地/モーター補正を保守的に加算します。

## 実行
```bash
python -m engine.predictor samples/tokoname_20260730_R01_input.json -o samples/tokoname_20260730_R01_output.json
```

## 固定仕様
- SABは予測再現性の評価で、買い目数と分離
- 買い目は本線6・ズレ2・荒れ2の10点固定、重複なし
- オッズは予測入力に使用しない
- 進入変更時は course 特徴量から全再計算
- 補正は確率へ適用し、買い目を直接固定しない

## 現時点の留意点
潮位実測と節間成績が入力にない場合は data_flags=false として明示します。v1.0は簡易代替ではなく既存学習モデルを核にしますが、常滑v7.1統合DBの全テーブルをSQLiteへ移植する工程は次版です。

## v1.1 買い目生成修正

本線は全120通りの単純積上位ではなく、主シナリオの役割順で生成する。

1. 1着率最上位を主頭に固定
2. その頭に対する2着率最上位艇を主2着に固定
3. 残る艇を最終3着率の高い順に並べる
4. 主シナリオの代表目を本線1番手にする
5. その後に次点の2着候補へ展開する

SABと買い目数は引き続き分離し、本線6点・ズレ2点・荒れ2点、重複なしを維持する。
