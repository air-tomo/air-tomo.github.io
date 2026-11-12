status: OPEN
request_id: 20261113-ball-model-high-camera
from: Claude Code
created_at: 2026-11-13 JST
type: BUILD（Mac の GPU で重い処理を回す。コードの変更は下に書いた範囲だけ）

# 高い位置の固定カメラ用に、ボール検出の小さいモデルを学習し直す

## 背景
- 小さいボール検出モデル（`golf-swing-app/tools/court3d/train_small.py` の SmallNet）は、低い位置の動画 2 本だけで学習した（F1 0.878）。
- アプリが狙う撮り方（コート後方・高さ 1.8m 以上・コート全体）の公開動画が見つかった:
  Hugging Face `BrianGuo/Tennis_Data`（MIT、001〜015.mp4、1080p 59fps、計 32GB）。SwingVision の書き出し動画。
- 008.mp4 の 60〜70 秒で試した結果: 先生役（TrackNet、半分に縮小）は 298 コマ中 133 コマ（45%）しか見つけない。
  今の小さいモデルは 275 コマ見つけるが、先生役と一致したのは 131 コマ中 85 コマ（別物を拾う）。
- クラウドの Claude 側は GPU がなく 1 コマ 0.7〜1.5 秒かかるので、Mac（MPS）でやってほしい。

## やること
1. `BrianGuo/Tennis_Data` から 4〜6 本ダウンロード（学習用 3〜5 本、評価用に別の 1 本）。動画はリポジトリに入れない。
2. 先生役で正解を作る。TrackNet の重み `model_best.pt` は Google ドライブ（id `1XEYZ4myUN7QT-NeBYJI0xteLsvs-ZAOl`、`gdown` で取得。
   ライセンス不明なのでリポジトリに入れない）。
   - まず 008.mp4 の 60〜70 秒で、`TN_SCALE=1`（縮小なし）と `TN_SCALE=2` を比べ、見つけた割合を報告
     （例: `TN_X0=0 TN_Y0=280 TN_CW=1920 TN_CH=704 TN_SCALE=1 python3 tn_roi.py 008.mp4 60 10 out.json model_best.pt`）。
   - 良い方で各動画 5〜10 分ぶんの正解を作る。`cut_video.py` と同じく `match.json` 形式（clips: start/end）が要るので、
     60 秒ごとの区切りで作ってよい。
3. **SwingVision の重ね表示を学習に入れない**: 右上の落下点の図（おおよそ x>1620, y<350）と右下のロゴ（x>1400, y>930）に
   かかる切り抜き・正解は捨てる（`make_crops.py` に除外範囲の引数を足してよい）。SwingVision の図の内容は正解として使わない。
4. `make_crops.py` → `train_crops.py` で学習。今の重みがあれば `--init` に使う（なければ最初から。既存の低い位置の動画の
   切り抜きは手元にないので、新しい動画だけでよい）。評価用の動画の切り抜きを `--val` に。
5. 学習した `ball_small.pt` / `ball_small.onnx` は Google ドライブの「テニス動画」フォルダに置く（リポジトリには入れない）。

## 完了条件（`handoff/to-claude/20261113-ball-model-high-camera.md` に書く）
- TN_SCALE=1 と 2 の検出率（008.mp4 60〜70 秒）
- 使った動画と区間、切り抜きの数（ボールあり／なし）
- 学習時間・端末、評価用動画での precision / recall / F1（4px 以内）
- 評価用動画の 10 秒で、新しいモデルが見つけたコマ数と、先生役との一致数
- モデルを置いた場所（ドライブのファイル名）
- `make_crops.py` などを変えた場合は、その commit

## 注意
- `tests/run_all.sh` が通ること（コードを変えた場合）。
- 動画・重み・切り抜きデータは commit しない。
