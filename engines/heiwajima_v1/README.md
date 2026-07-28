# 平和島自動予想エンジン v1（Codexロジック統合・上書き版）

この版は旧v1を置き換える正式版です。実行対象はこのフォルダ1つに限定します。

## 主要変更

- 1コース基礎率と選手1コース実績の重複加点を除去
- 確率先行ではなく、基礎確率→展開シナリオ→位置別確率再配分の順に変更
- 頭候補ごとに2着・3着連動を再計算
- 展示ST単独の強補正を禁止
- 潮・風・波は攻め筋の成立補正として使用
- 終盤・最終日は節間比重を上げ、展示単発比重を下げる
- オッズは予想確率へ使用しない
- 進入変更時はactual_courseへ再配置して完全再計算

## 実行

```bash
python heiwajima_prediction_engine.py examples/sample_input_pre.json -o examples/sample_output.json
pytest -q
```

## 正式配置

GitHubでは `engines/heiwajima_v1/` へフォルダ内容を上書きし、旧版・fixed・new等の並行フォルダを作らないでください。
