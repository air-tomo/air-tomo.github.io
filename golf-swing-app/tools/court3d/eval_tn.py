"""Validate the 'fit 3D flight, extrapolate to the ground' landing estimate on TrackNet labels (bounce = status 2)."""
import numpy as np, cv2, csv, glob, json, sys, os
from scipy.optimize import least_squares
import refine_court as rc
import os
FPS = float(os.environ.get("FPS", 30.0))
def load(clip):
    rows = list(csv.reader(open(clip + "/Label.csv")))[1:]
    out = []
    for r in rows:
        vis = int(r[1] or 0); x = float(r[2]) if r[2] else np.nan; y = float(r[3]) if r[3] else np.nan
        out.append((vis, x, y, int(r[4]) if r[4] != "" else -1))
    return out
def simulate(p0, v0, ts, k=0.0204, g=9.81, dt=1 / 600):
    out = []; p = np.array(p0, float); v = np.array(v0, float); tc = 0.0
    for T in ts:
        while tc + dt <= T + 1e-12:
            a = -k * np.linalg.norm(v) * v + np.array([0, 0, -g]); v = v + a * dt; p = p + v * dt; tc += dt
        out.append(p.copy())
    return np.array(out)
def simulate_back(p0, v0, T, k=0.0204, g=9.81, dt=1 / 600):
    p = np.array(p0, float); v = np.array(v0, float); tc = 0.0
    while tc + dt <= T + 1e-12:
        a = -k * np.linalg.norm(v) * v + np.array([0, 0, -g]); v = v - a * dt; p = p - v * dt; tc += dt
    return p
def fit_flight(K, R, t, H, ts, uv, hit=None):
    """hit = (time, (u, v)) of the racket contact: the ball is there at a plausible hitting height (prior 1.2 +- 0.8 m)."""
    def proj(X):
        p = (K @ (R @ X.T + t[:, None])).T; return p[:, :2] / p[:, 2:]
    g0 = cv2.perspectiveTransform(uv[:1].reshape(1, 1, 2), np.linalg.inv(H))[0, 0]
    g1 = cv2.perspectiveTransform(uv[-1:].reshape(1, 1, 2), np.linalg.inv(H))[0, 0]
    T = ts[-1] - ts[0] + 1e-3
    q0 = np.array([g0[0], g0[1], 1.0, (g1[0] - g0[0]) / T, (g1[1] - g0[1]) / T, 0.0])
    def res(q):
        r = (proj(simulate(q[:3], q[3:], ts - ts[0])) - uv).ravel()
        if hit is None: return r
        Ph = simulate_back(q[:3], q[3:], ts[0] - hit[0])
        return np.concatenate([r, (proj(Ph[None])[0] - hit[1]) * 1.0, [(Ph[2] - 1.2) / 0.8 * 3]])
    best = None
    for z0 in (0.5, 1.0, 2.0, 3.0):
        q0[2] = z0; s = least_squares(res, q0, loss="soft_l1", f_scale=2.0)
        if best is None or s.cost < best.cost: best = s
    q = best.x; tt = np.arange(0, 3, 1 / 600); Q = simulate(q[:3], q[3:], tt)
    if Q[0, 2] <= 0: return None, None
    i = int(np.argmax(Q[:, 2] <= 0))
    if Q[i, 2] > 0: return None, None
    return Q[i][:2], tt[i] + ts[0]
def main(game, drops=(0, 2, 4)):
    clips = sorted(glob.glob(game + "/Clip*"), key=lambda s: int(s.split("Clip")[-1]))
    H = None; prevc = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None
    res = {d: [] for d in drops}
    for clip in clips:
        img = cv2.imread(clip + "/0000.jpg"); h, w = img.shape[:2]
        corners = prevc if H is None else rc.P(H, [(-5.485, 0), (5.485, 0), (5.485, 23.77), (-5.485, 23.77)])
        H, n, err = rc.fit(img, corners)
        if err is None or err > 4 or n < 10:
            print(os.path.basename(clip), "calibration failed", n, err); H = None; continue
        K, R, t, C = rc.camera_from_H(H, w, h)
        L = load(clip)
        # segments: from the frame after a hit/bounce to the next bounce
        ev = [i for i, l in enumerate(L) if l[3] in (1, 2)]
        for a, b in zip(ev, ev[1:]):
            if L[b][3] != 2: continue
            idx = [i for i in range(a + 1, b) if L[i][0] == 1 and not np.isnan(L[i][1])]
            if len(idx) < 8: continue
            truth = cv2.perspectiveTransform(np.array([[[L[b][1], L[b][2]]]], float), np.linalg.inv(H))[0, 0]
            for d in drops:
                use = idx[:len(idx) - d] if d else idx
                ts = np.array(use, float) / FPS; uv = np.array([[L[i][1], L[i][2]] for i in use])
                hit = (a / FPS, np.array([L[a][1], L[a][2]])) if L[a][3] == 1 and not np.isnan(L[a][1]) else None
                land, tl = fit_flight(K, R, t, H, ts, uv, hit)
                if land is None: continue
                res[d].append(dict(clip=os.path.basename(clip), frame=b, err=float(np.linalg.norm(land - truth)),
                                   ex=float(land[0] - truth[0]), ey=float(land[1] - truth[1]), truth=truth.tolist(), dt=float(tl - b / FPS)))
    for d in drops:
        e = np.array([r["err"] for r in res[d]])
        if len(e): print(f"drop last {d} frames: n={len(e)} median err {np.median(e):.2f} m, 80% {np.percentile(e, 80):.2f} m, "
                         f"depth bias {np.median([r['ey'] for r in res[d]]):+.2f} m, |depth| med {np.median(np.abs([r['ey'] for r in res[d]])):.2f}, |lateral| med {np.median(np.abs([r['ex'] for r in res[d]])):.2f}")
    json.dump(res, open(sys.argv[3] if len(sys.argv) > 3 else "/dev/null", "w"))
if __name__ == "__main__":
    main(sys.argv[1])
