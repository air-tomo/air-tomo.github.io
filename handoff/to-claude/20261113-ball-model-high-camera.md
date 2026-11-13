status: DONE
request_id: 20261113-ball-model-high-camera
reported_at: 2026-11-14 04:46 JST

## 結果

- Mac: Apple M2 / 16 GB、PyTorch 2.9.1、MPS。
- 学習: 008・004・005、独立評価: 011。各動画の元映像 60–360 秒を使用する設定。
- SwingVision の重ね表示を含む切り抜きは除外。
- 学習ツール: 7149cd8。tests/run_all.sh は ALL OK。
- Google ドライブ: テニス動画/20261113-ball-model-high-camera/ball_small.pt と ball_small.onnx（コピー後に一致を確認）。
- 008 の60–70秒、TN_SCALE=1: 142/298 (47.7%)
- 008 の60–70秒、TN_SCALE=2: 133/298 (44.6%)

測定値は TrackNet の教師ラベルとの比較。手作業の正解とは未照合。

```text
Selected TN_SCALE=1
Training start 2026-11-14T04:11:31+09:00
train 1368 (ball 1095) val 516 (ball 419)
step 2970 loss 0.0073 lr 0.00133 {'precision': 0.779, 'recall': 0.78, 'f1': 0.779}
step 5981 loss 0.0056 lr 0.00066 {'precision': 0.905, 'recall': 0.907, 'f1': 0.906}
step 8936 loss 0.0046 lr 0.00010 {'precision': 0.904, 'recall': 0.902, 'f1': 0.903}
best f1 0.906 -> /private/tmp/tennis-high-01a1d442/trained/ball_small.onnx
Training end 2026-11-14T04:41:36+09:00
008 ball 426 no ball 120
004 ball 401 no ball 95
005 ball 268 no ball 58
011 ball 419 no ball 97
10s student 298 teacher 223 matched <=4px 170
```
