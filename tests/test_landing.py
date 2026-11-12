"""落下位置のテスト。動画は使わない。

1. IMG_0131 の手前のバウンド 33 か所: 検出位置（ボールの中心）から出した落下位置が、60 fps・最高画質で測った
   ボールの一番下の位置と合うか（中心を地面と見なすと 0.7m 遠くに出ていた）。Python 版とブラウザ版の両方
2. 画面外の落下予測（predict_land.py）: 合成した打球で、軌道の 60% が見えれば奥行きの誤差が 1.2m 以内か
   （カメラの高さ 0.7m / 1.8m）
"""
import json, os, subprocess, sys
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
C3 = os.path.join(ROOT, "golf-swing-app", "tools", "court3d")
sys.path.insert(0, C3)
import predict_land as PL  # noqa: E402

fails = []


def check(name, ok, msg):
    print(("  ok   " if ok else "  FAIL ") + name + ": " + msg)
    if not ok: fails.append(name)


cam = np.load(os.path.join(C3, "data", "IMG_0131_cam.npz")); K, R, t = cam["K"], cam["R"], cam["t"]
B = json.load(open(os.path.join(ROOT, "tests", "data", "IMG_0131_bounce_bottoms.json")))["bounces"]


def ground_py(u, v, z=0.033):
    Rt = R.T; C = -Rt @ t; d = Rt @ np.linalg.inv(K) @ np.array([u, v, 1.0]); s = (z - C[2]) / d[2]; return C + s * d


err = np.array([ground_py(*b["uv"])[1] - b["bottom_y"] for b in B])
check("bounce depth (Python)", abs(np.median(err)) < 0.2 and np.median(np.abs(err)) < 0.35,
      "%d 点、ずれの中央値 %+.2fm、絶対値の中央値 %.2fm" % (len(B), np.median(err), np.median(np.abs(err))))

js = """const R=require(process.argv[1]);const c=JSON.parse(process.argv[2]);const cam=R.camera(c);
console.log(JSON.stringify(JSON.parse(process.argv[3]).map(b=>cam.ground(b.uv[0],b.uv[1])[1])));"""
camj = json.dumps({"K": K.tolist(), "R": R.tolist(), "t": t.tolist(), "H": cam["H"].tolist()})
ys = json.loads(subprocess.check_output(["node", "-e", js, os.path.join(ROOT, "cut", "rally.js"), camj, json.dumps(B)]))
ej = np.array(ys) - np.array([b["bottom_y"] for b in B])
check("bounce depth (JS)", abs(np.median(ej)) < 0.2 and np.max(np.abs(ej - err)) < 0.01, "ずれの中央値 %+.2fm、Python との差 最大 %.3fm" % (np.median(ej), np.max(np.abs(ej - err))))


def cam_at(h, back=6.3, f=1637, W=1920, H=1080):
    C = np.array([0, -back, h]); tgt = np.array([0, 11.9, 0]); z = (tgt - C) / np.linalg.norm(tgt - C)
    x = np.cross(z, [0, 0, 1]); x /= np.linalg.norm(x); y = np.cross(z, x); Rm = np.stack([x, y, z])
    c = PL.Cam.__new__(PL.Cam); c.K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1.]]); c.R = Rm; c.t = -Rm @ C; return c


rng = np.random.default_rng(0)
for h in (0.7, 1.8):
    c = cam_at(h); errs = []
    for i in range(12):
        p0 = np.array([rng.uniform(-3, 3), rng.uniform(-1, 1), rng.uniform(0.8, 1.2)])
        v0 = np.array([rng.uniform(-3, 3), rng.uniform(18, 30), rng.uniform(2, 6)])
        L, tl = PL.landing(p0, v0, g=PL.G_EFF)
        if L is None or tl < 0.4: continue
        ts = np.arange(0.05, tl - 0.03, 1 / 30); uv = c.proj(PL.simulate(p0, v0, ts, g=PL.G_EFF)) + rng.normal(0, 1.5, (len(ts), 2))
        n = int(len(ts) * 0.6); r = PL.predict(c, [(a, u, v) for a, (u, v) in zip(ts[:n], uv[:n])], 0.0, True)
        if r: errs.append(abs(r[0][1] - L[1]))
    check("landing prediction h=%.1fm" % h, len(errs) >= 8 and np.median(errs) < 1.2, "%d 球、奥行き誤差の中央値 %.2fm" % (len(errs), np.median(errs) if errs else -1))

print("test_landing:", "OK" if not fails else "NG " + ", ".join(fails))
sys.exit(1 if fails else 0)
