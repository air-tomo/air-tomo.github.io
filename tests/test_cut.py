"""切り出し（打球音）のテスト。動画は使わず、保存した音量の包絡線（tests/data/*_env.npz）だけで動く。

1. ブラウザ版（cut/detect.js）と Python 版（golf-swing-app/tennis.py）が同じ区間を出すか（4 本）
2. IMG_1127（斜め後ろ・固定）: 目で確かめた正解（最初の 10.7 分、19 ポイント）を全部拾い、余計な区間がないか
3. IMG_0131（真後ろ・低い位置）: ボール追跡で確かめたサーブ（41 ポイント）を全部区間に含むか、サーブのない区間が増えていないか
"""
import json, os, subprocess, sys, tempfile
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "golf-swing-app"))
import tennis, swing  # noqa: E402

D = os.path.join(ROOT, "tests", "data")
JS = r"""
const fs = require('fs'); const D = require(process.argv[2]);
const env = new Float32Array(fs.readFileSync(process.argv[3]).buffer.slice(0));
const r = D.tennisClips(D.prepare(env), Object.assign({}, D.TENNIS));
console.log(JSON.stringify(r.clips.map(c => [c.start, c.end])));
"""
fails = []


def check(name, ok, msg):
    print(("  ok   " if ok else "  FAIL ") + name + ": " + msg)
    if not ok: fails.append(name)


def segments(env):
    return tennis.analyze(env, len(env) * swing.HOP_SEC, tennis.TennisSettings())["segments"]


def ov(a, b): return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


with tempfile.TemporaryDirectory() as tmp:
    js = os.path.join(tmp, "cut.js"); open(js, "w").write(JS)
    for v in ("IMG_0131", "IMG_1127", "IMG_1128", "IMG_4562"):
        env = np.load(os.path.join(D, v + "_env.npz"))["env"].astype(np.float32)
        f = os.path.join(tmp, v + ".f32"); env.tofile(f)
        py = segments(env)
        out = json.loads(subprocess.check_output(["node", js, os.path.join(ROOT, "cut", "detect.js"), f]))
        same = len(py) == len(out) and all(abs(a[0] - b[0]) < 0.02 and abs(a[1] - b[1]) < 0.02 for a, b in zip(py, out))
        check("parity " + v, same, "Python %d 区間 / JS %d 区間" % (len(py), len(out)))

env = np.load(os.path.join(D, "IMG_1127_env.npz"))["env"]
T = json.load(open(os.path.join(D, "IMG_1127_truth.json")))
seg = [s for s in segments(env) if s[0] < T["checked_until_sec"]]
missed = [t for t in T["points"] if not any(ov(t, s) > 0 for s in seg)]
false = [s for s in seg if not any(ov(t, s) > 0 for t in T["points"])]
split = [t for t in T["points"] if sum(1 for s in seg if ov(t, s) > 0) > 1]
check("IMG_1127 truth", not missed and not false, "正解 %d、取りこぼし %d、余計 %d（分割 %d は情報のみ）" % (len(T["points"]), len(missed), len(false), len(split)))
# 終わりの余白: ポイントの最後の打球からアウトまで入るか（正解の終わりを区間が覆う）
short = [t for t in T["points"] if not any(s[0] <= t[1] - 0.5 <= s[1] for s in seg)]
check("IMG_1127 end margin", len(short) <= 1, "終わりが切れている正解 %d" % len(short))

env = np.load(os.path.join(D, "IMG_0131_env.npz"))["env"]
E = json.load(open(os.path.join(ROOT, "golf-swing-app", "tools", "court3d", "data", "IMG_0131_expected.json")))
serves = [s[0] for p in E["points"] for s in p]
seg = segments(env)
miss = [x for x in serves if not any(a <= x <= b for a, b in seg)]
extra = [s for s in seg if s[0] >= 190 and not any(s[0] - 1 <= x <= s[1] for x in serves)]
check("IMG_0131 serves", not miss, "サーブ %d、区間に入らない %d" % (len(serves), len(miss)))
check("IMG_0131 non-points", len(extra) <= 8, "試合部分でサーブのない区間 %d（基準 8 以下）" % len(extra))

print("test_cut:", "OK" if not fails else "NG " + ", ".join(fails))
sys.exit(1 if fails else 0)
