"""TrackNet points -> clean track, bounces (image V), serve speed. Usage: tn_post.py match.json cam.npz env.npy out.json k1:tn1.json k2:tn2.json ..."""
import sys, json, numpy as np, cv2
from scipy.optimize import least_squares
import os
sys.path.insert(0, os.environ.get("APP_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))
import swing
MATCH, CAM, ENV, OUT = sys.argv[1:5]
cam = np.load(CAM); K, R, t, Hm = cam["K"], cam["R"], cam["t"], cam["H"]; Hinv = np.linalg.inv(Hm)
def ground(u, v): return cv2.perspectiveTransform(np.array([[[u, v]]], float), Hinv)[0, 0]
def proj(X): X = np.atleast_2d(X); p = (K @ (R @ X.T + t[:, None])).T; return p[:, :2] / p[:, 2:]
def simulate(p0, v0, ts, k=0.0204, g=9.81, dt=1 / 600):
    out = []; p = np.array(p0, float); v = np.array(v0, float); tc = 0.0
    for T in ts:
        while tc + dt <= T + 1e-12:
            a = -k * np.linalg.norm(v) * v + np.array([0, 0, -g]); v = v + a * dt; p = p + v * dt; tc += dt
        out.append(p.copy())
    return np.array(out)
env = np.load(ENV)
times, _ = swing.detect_hits(env, 6, 0.3)
lv = np.array([env[int(round(x / swing.HOP_SEC - 0.5))] for x in times]); loud = np.percentile(lv, 90) - 6
loud_hits = [x for x, l in zip(times, lv) if l >= loud]
def clean(pts):
    """drop isolated jumps (> 80 px from both neighbours' interpolation)"""
    P = [(tt, x, y) for tt, x, y in pts if x is not None]
    keep = []
    for i, (tt, x, y) in enumerate(P):
        nb = [P[j] for j in (i - 1, i + 1) if 0 <= j < len(P) and abs(P[j][0] - tt) < 0.1]
        if nb and min(np.hypot(x - a[1], y - a[2]) for a in nb) > 80: continue
        keep.append((tt, x, y))
    return keep
def find_bounces(P):
    out = []
    for i in range(2, len(P) - 2):
        (ta, xa, ya), (t1, x1, y1), (tb, xb, yb) = P[i - 2], P[i], P[i + 2]
        if tb - ta > 0.2: continue
        if not (y1 >= P[i - 1][2] and y1 >= P[i + 1][2] and y1 - ya > 3 and y1 - yb > 3): continue
        if any(abs(t1 - h) < 0.07 for h in loud_hits): continue      # racket of the near player
        g = ground(x1, y1)
        if not (-7 < g[0] < 7 and -2 < g[1] < 26): continue
        if out and t1 - out[-1]["t"] < 0.4: continue
        out.append({"t": round(t1, 3), "uv": [x1, y1], "court": [round(float(g[0]), 2), round(float(g[1]), 2)]})
    return out
def serve_speed(P, bounce, hit):
    """3D flight from the hit (audio time, contact prior) to the observed bounce point on the ground."""
    obs = [(tt, x, y) for tt, x, y in P if hit + 0.02 < tt < bounce["t"] - 0.01]
    if len(obs) < 5: return None
    ts = np.array([o[0] for o in obs]) - hit; uv = np.array([[o[1], o[2]] for o in obs])
    tb = bounce["t"] - hit; gb = np.array(bounce["court"] + [0.0])
    def res(q):
        p0, v0 = q[:3], q[3:]
        Pp = simulate(p0, v0, np.append(ts, tb))
        return np.concatenate([(proj(Pp[:-1]) - uv).ravel(), (Pp[-1] - gb) * 40, [(p0[1] + 0.5) / 0.6 * 3, (p0[2] - 2.6) / 0.3 * 3]])
    q0 = np.array([0, -0.5, 2.6, (gb[0]) / tb, (gb[1] + 0.5) / tb, -1.0])
    s = least_squares(res, q0, loss="soft_l1", f_scale=2.0)
    rms = float(np.sqrt(np.mean(s.fun[:2 * len(obs)] ** 2)))
    return {"kmh": round(float(np.linalg.norm(s.x[3:])) * 3.6), "rms_px": round(rms, 2), "contact": [round(float(v), 2) for v in s.x[:3]]}
M = json.load(open(MATCH)); res = {}
for arg in sys.argv[5:]:
    k, path = arg.split(":"); k = int(k); c = M["clips"][k]
    P = clean(json.load(open(path)))
    bs = find_bounces(P)
    serve = None
    hits = [h for h in loud_hits if c["start"] <= h <= c["end"]]
    if hits and bs:
        h0 = hits[0]; b0 = next((b for b in bs if 0.3 < b["t"] - h0 < 1.2 and b["court"][1] > 11.9), None)
        if b0:
            serve = serve_speed(P, b0, h0)
            if serve: serve.update(t=round(h0, 3), bounce=b0["court"])
    res[k] = {"ball": P, "bounces": bs, "serve": serve}
    print(k, "points", len(P), "bounces", [(b["t"], b["court"]) for b in bs], "serve", serve)
json.dump(res, open(OUT, "w"))
