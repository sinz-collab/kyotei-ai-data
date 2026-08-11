# 芦屋 v1.6.1 正式採用手順

1. このディレクトリ一式を `engines/ashiya/ashiya_engine_v1_6/` に配置する。
2. `ASSET_SHA256.txt` の4資産を照合する。LightGBMモデルを省略・置換しない。
3. 朝取得後の `data/venues/ashiya/YYYYMMDD.json` を入力として、`scripts/predict_venue_json.py` で12Rを生成するアダプターを接続する。
4. サイト互換出力では top-level `engine` を `ashiya_prediction_engine_v1.6.1` とし、各Rに win/second/third、SAB、main 6・deviation 2・upset 2を格納する。
5. `automation/build_site_data.py` の prediction gate を通過した後だけ `predictionStatus=ready` とする。
6. オッズは確率・SAB・買い目選定に使用しない。
7. 進入変更時は actual_course で全再計算する。
8. ライブ更新では、朝の基礎確率を保持したまま展示・オリ展示・スリット・風波を後段補正する。
9. `python -m pytest -q` を実行し、既存の朝取得/ライブ取得テストも通す。
10. 今日の芦屋 `latest.json` で `prediction.status=ready`、engine/version、確率3種、SAB、10点を確認する。
