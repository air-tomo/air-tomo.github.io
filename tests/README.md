# テスト

`bash tests/run_all.sh` でまとめて動く（数十秒）。main への push でも GitHub Actions（`.github/workflows/tests.yml`）が動く。

| テスト | 何を確かめるか |
|---|---|
| `golf-swing-app/tools/court3d/test_rally.js` | 試合解析（バウンド・IN/OUT・サーブ・ポイント）がブラウザ版と Python 版で同じ（IMG_0131 の 56 クリップ・41 ポイント） |
| `tests/test_cut.py` | 打球音の切り出しがブラウザ版と Python 版で同じ（4 本）／IMG_1127 の正解 19 ポイントを取りこぼし 0・余計 0／IMG_0131 のサーブを全部含む |
| `tests/test_landing.py` | 落下位置が、60 fps で測ったボールの一番下と合う（33 点、ずれ 0.2m 以内）／画面外の落下予測の誤差 |
| `tests/test_app.js` | アプリの中身を本物のブラウザで: 撮り方ガイド → 動画選択 → 検出 → 書き出し（ファイル選択と iOS の URL 読みの両方） |

データ（`tests/data/`）は音量の包絡線と、目で確かめた正解・測定値だけ。試合の動画そのものは入れない（公開リポジトリのため）。
新しい試合で正解を作ったら `tests/data/<動画名>_truth.json` を足し、`test_cut.py` に採点を足す。
