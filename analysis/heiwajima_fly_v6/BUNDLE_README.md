# 平和島 Fly v6 再現一式

`fly_v6_best_candidate.json` と `calibrated_stages_result.json` は前回検証時の原典です。
係数、特徴量変換、欠損処理、50%判定は変更していません。

再現コマンド:

```powershell
py -3 analysis/heiwajima_fly_v6_candidate/run_bundle_reproduction.py --output analysis/heiwajima_fly_v6_candidate/bundle_reproduction_report.json
py -3 -m pytest tests/test_heiwajima_fly_v6_candidate.py -q
```

`run_bundle_reproduction.py` は保存済み4データセットから校正結果を再生成し、原典JSONとの
SHA-256一致、9月LIVE 124/173、10月LIVE 51/77を検査します。同時に、保存済みレース別の
通常12点、v2.7飛び8点、Fly v6飛び8点を同一条件で再集計します。通常と飛びの重複は許容し、
20点的中は両集合の和集合で判定します。

`replay_77r.py` はレース入力から保存済みリプレイJSONを作る原コードです。リプレイ再生成には
元リポジトリの平和島エンジン、マスター、LIVE入力、検証CSVが必要です。本ZIP単体で保証する
再現範囲は、同梱済みリプレイJSONからのFly v6判定結果と買い目比較です。
