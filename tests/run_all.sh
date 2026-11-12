#!/usr/bin/env bash
# テストをまとめて動かす。  bash tests/run_all.sh
# 必要: Python 3（numpy・opencv・scipy）、Node.js、ffmpeg、playwright（Chromium）
set -u
cd "$(dirname "$0")/.."
status=0
run() { echo "== $1"; shift; "$@" || status=1; echo; }
run "court analysis: browser = Python (IMG_0131, 56 clips)" node golf-swing-app/tools/court3d/test_rally.js
run "cut (hit sounds): parity + scores on 4 videos" python3 tests/test_cut.py
run "landing positions + out-of-picture prediction" python3 tests/test_landing.py
run "app end to end (synthetic video, file input and iOS URL path)" node tests/test_app.js
if [ $status -eq 0 ]; then echo "ALL OK"; else echo "SOME TESTS FAILED"; fi
exit $status
