# GitHub / VPS導入手順

1. `python heiwajima_master_compiler.py`で実行用SQLiteを生成。
2. 朝データを入力JSONへ変換し、`stage=pre`で事前予想JSONを生成。
3. 展示・実進入取得後、`heiwajima_live_review.apply_live_update`で入力を更新。
4. `stage=final`で再実行し、サイト用final JSONを生成。
5. オッズはJSON表示層で結合し、予想エンジンへ渡さない。

推奨はVPS常駐実行、GitHub Actionsはテスト・マスター更新・JSON同期に限定します。
