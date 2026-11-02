#!/bin/bash
# ゴルフスイング自動切り抜き 起動スクリプト (macOS)
# 初回は仮想環境の作成とライブラリのインストールも行う。
set -e
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "FFmpeg が見つかりません。Homebrew で「brew install ffmpeg」を実行してから再度起動してください。"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Python 仮想環境を作成しています..."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python app.py "$@"
