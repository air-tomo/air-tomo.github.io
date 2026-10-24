# ゴルフスイング自動切り抜き

iPhone で撮影したゴルフ練習動画をアップロードすると、**打球音**からスイングを検出し、
すべてのスイングを **1本の MP4** に結合してダウンロードできる Web アプリです。

- 日本語 UI / iPhone Safari 対応（写真ライブラリから直接アップロード可）
- 切り出し範囲のデフォルトは **打球の 3 秒前〜2 秒後**（画面で変更可）
- アップロード・検出・切り出し・結合の進捗をパーセント表示
- iPhone の HEVC・縦向き・HDR 動画に対応（HDR は SDR の H.264 に変換）

## 必要なもの（Mac）

| ソフト | 確認コマンド | ない場合 |
| --- | --- | --- |
| Python 3.9 以上 | `python3 --version` | `xcode-select --install` または `brew install python` |
| FFmpeg | `ffmpeg -version` | `brew install ffmpeg` |
| Homebrew（FFmpeg を入れる場合） | `brew --version` | https://brew.sh の手順でインストール |

## 起動手順

```bash
cd golf-swing-app
./start.sh
```

初回は `.venv`（Python 仮想環境）の作成とライブラリ（Flask / waitress / numpy）の
インストールを自動で行います。手動で行う場合は次のとおりです。

```bash
cd golf-swing-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

起動するとアクセス用の URL が表示されます。

```
  この Mac から : http://localhost:8000
  iPhone から   : http://192.168.x.x:8000
  (または)      : http://<Macの名前>.local:8000
```

終了は `Ctrl + C` です。

## iPhone からアクセスする

1. iPhone と Mac を**同じ Wi-Fi** に接続します。
2. 起動時に表示された `http://192.168.x.x:8000` を iPhone の Safari で開きます。
   - Mac の IP アドレスは `ipconfig getifaddr en0` でも確認できます。
3. 初回起動時に macOS から「"python3" へのネットワーク受信接続を許可しますか？」と
   表示されたら **「許可」** を選んでください（ファイアウォールが有効な場合）。
   - 誤って拒否した場合: システム設定 → ネットワーク → ファイアウォール → オプション で
     python3 を「受信接続を許可」に変更します。
4. つながらない場合は、ゲスト Wi-Fi や「プライバシー分離」機能のあるルーターでないか確認してください。

> **セキュリティについて**: アプリは認証なしで同じネットワーク内の端末からアクセスできます。
> 自宅など信頼できる Wi-Fi でのみ使い、公共 Wi-Fi では `./start.sh --host 127.0.0.1`
> （この Mac からのみ接続可）で起動してください。インターネットには公開されません。

## 使い方

1. 「タップして動画を選択」から練習動画を選ぶ
2. 必要に応じて設定を変更
   - **打球の前／後**: 切り出す秒数（デフォルト 3 秒／2 秒）
   - **検出感度**: スイングを見逃すときは上げる、関係ない音を拾うときは下げる
   - **最短の打球間隔**: これより短い間隔の音は同じスイングとして扱う
3. 「切り抜きを開始」→ 完了するとプレビューと「MP4 をダウンロード」が表示されます
4. iPhone では Safari の「ダウンロード」から共有ボタン →「ビデオを保存」で写真アプリに保存できます

処理結果はアプリフォルダの `jobs/` に保存され、24 時間後に自動で削除されます。

## 動作確認用のテスト動画

打球音に似た音を 5 回含むテスト動画（iPhone と同じ HEVC・縦向きの .mov）を作れます。

```bash
.venv/bin/python tools/make_test_video.py test_videos/test.mov
```

アップロードすると 0:07、0:19、0:31、0:44、0:55 の 5 スイング（合計 25 秒）が出力されます。

## 検出のしくみ

1. FFmpeg で音声をモノラル 22.05kHz に変換
2. 高域を強調した約 5ms ごとの音量 (dB) を計算
3. 周囲 2 秒の中央値より大きく、かつ 15ms 以内に急激に立ち上がる音を打球とみなす
4. 「最短の打球間隔」内の候補は 1 つにまとめ、最も大きい点を打球時刻とする
5. 各打球の前後を H.264/AAC で切り出し、FFmpeg の concat で 1 本に結合

打ちっぱなしで隣の打席の音を拾う場合は、検出感度を下げるか、iPhone を自分の近くに置いて撮影してください。

## ファイル構成

```
golf-swing-app/
├── app.py              Web サーバー（Flask + waitress）
├── swing.py            打球音検出・切り出し・結合
├── templates/index.html 画面（日本語・iPhone 対応）
├── tools/make_test_video.py テスト動画の作成
├── requirements.txt
└── start.sh            Mac 用起動スクリプト
```
