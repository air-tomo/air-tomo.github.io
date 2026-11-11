"""Where does a ball land when its bounce is not seen (it flies out of the picture, or is hidden)? Fit a 3D flight (gravity +
air drag, the same model as the serve speed) to the part of the track that is seen, then fly it on to the ground.

Gravity fixes the scale, so one camera is enough; priors keep it sane when the seen part is short: the ball leaves the racket
about 1 m up, near the hitter's baseline side (the near player's shots are the loud ones).

  python3 predict_land.py cam.npz tn_post.json match_expected.json      # self-test on bounces that ARE seen
Self-test: hide the end of the flight before each seen rally bounce (keep 40 / 60 / 80 % of it), predict, compare.
"""
import json, sys
import numpy as np
from scipy.optimize import least_squares

# Rally balls carry topspin and drop faster than gravity alone: fitting g on IMG_0131's seen flights gave 12 m/s^2 (median).
# With 9.81 every prediction landed about 4 m short; with 12 the bias is gone (depth error median 2.0 m with 80 % of the flight
# seen, 2.9 m with 60 %; across the court 0.6 m). The camera there is only 0.7 m high, which makes depth hard.
G_EFF = 12.0


def simulate(p0, v0, ts, k=0.0204, g=9.81, dt=1 / 300):
    out = []; p = np.array(p0, float); v = np.array(v0, float); tc = 0.0
    for T in ts:
        while tc + dt <= T + 1e-12:
            a = -k * np.linalg.norm(v) * v + np.array([0, 0, -g]); v = v + a * dt; p = p + v * dt; tc += dt
        out.append(p.copy())
    return np.array(out)


def landing(p0, v0, k=0.0204, g=9.81, dt=1 / 300, tmax=4.0):
    p = np.array(p0, float); v = np.array(v0, float); t = 0.0
    while t < tmax:
        a = -k * np.linalg.norm(v) * v + np.array([0, 0, -g]); vn = v + a * dt; pn = p + vn * dt
        if pn[2] <= 0.033 and vn[2] < 0:                 # the ball's centre is 3.3 cm up when it touches the ground
            s = (p[2] - 0.033) / max(1e-9, p[2] - pn[2]); return p + (pn - p) * s, t + dt * s
        p, v, t = pn, vn, t + dt
    return None, None


class Cam:
    def __init__(self, path):
        c = np.load(path); self.K, self.R, self.t = c["K"], c["R"], c["t"]

    def proj(self, X):
        X = np.atleast_2d(X); p = (self.K @ (self.R @ X.T + self.t[:, None])).T; return p[:, :2] / p[:, 2:]


def _fit(cam, ts, uv, near_hitter, g):
    y0 = 0.0 if near_hitter else 23.77
    vy = 25.0 if near_hitter else -25.0

    def res(q):
        p0, v0 = q[:3], q[3:]
        r = (cam.proj(simulate(p0, v0, ts, g=g)) - uv).ravel()
        return np.concatenate([r, [(p0[1] - y0) / 3.0 * 4, (p0[2] - 1.0) / 0.5 * 4]])
    best = None
    for vz in (1.0, 4.0, 8.0):            # flat drive, rally ball, lob
        s = least_squares(res, np.array([0, y0, 1.0, 0, vy, vz]), loss="soft_l1", f_scale=3.0)
        if best is None or s.cost < best.cost: best = s
    return best


def predict(cam, obs, hit, near_hitter=None, g=G_EFF):
    """obs: [(t, u, v)] after the hit (seconds, pixels). near_hitter: True / False, or None to let the flight decide
    (both are tried; a flight that does not cross the net to the other side is not accepted).
    Returns (landing [x, y], time, fit info) or None."""
    if len(obs) < 5: return None
    ts = np.array([o[0] for o in obs]) - hit; uv = np.array([[o[1], o[2]] for o in obs])
    cands = []
    for near in ([near_hitter] if near_hitter is not None else [True, False]):
        f = _fit(cam, ts, uv, near, g)
        land, tl = landing(f.x[:3], f.x[3:], g=g)
        if land is None: continue
        if (land[1] > 11.885) != near: continue          # a shot lands on the other side of the net
        med = float(np.median(np.hypot(f.fun[0:2 * len(obs):2], f.fun[1:2 * len(obs):2])))
        cands.append((med, near, land, tl, f))
    if not cands: return None
    med, near, land, tl, f = min(cands, key=lambda c: c[0])
    return [float(land[0]), float(land[1])], float(tl), {"med_px": med, "kmh": float(np.linalg.norm(f.x[3:])) * 3.6,
                                                         "contact": [round(float(v), 2) for v in f.x[:3]], "near_hitter": near}


if __name__ == "__main__":
    cam = Cam(sys.argv[1]); res = json.load(open(sys.argv[2])); E = json.load(open(sys.argv[3])); hits = sorted(E["times"])
    lv = dict(zip(E["times"], E["levels"])); loud = np.percentile(E["levels"], 90) - 6
    rows = []
    for k, c in res.items():
        P = [q for q in c["ball"] if q[1] is not None]
        for b in c["bounces"]:
            if b.get("kind") != "rally": continue
            hs = [h for h in hits if 0.35 <= b["t"] - h <= 1.8]
            if not hs: continue
            h = hs[-1]; obs = [q for q in P if h + 0.03 < q[0] < b["t"] - 0.03]
            if len(obs) < 10: continue
            near = lv[h] >= loud - 4                # the near player's shots are the loud ones
            for frac in (0.4, 0.6, 0.8):
                n = max(5, int(len(obs) * frac))
                r = predict(cam, obs[:n], h)
                if r is None: continue
                (x, y), tl, info = r
                rows.append((frac, near, b["court"][0], b["court"][1], x, y, abs(tl + h - b["t"]), info["med_px"]))
    A = np.array([[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]] for r in rows], float)
    for frac in (0.4, 0.6, 0.8):
        for near in (1, 0):
            m = (A[:, 0] == frac) & (A[:, 1] == near)
            if not m.any(): continue
            dx = np.abs(A[m, 4] - A[m, 2]); dy = np.abs(A[m, 5] - A[m, 3]); dt = A[m, 6]
            print("seen %d%%  %s hitter  n=%d  error x: median %.2f m  depth: median %.2f m  p80 %.2f m  time: median %.2f s"
                  % (frac * 100, "near" if near else "far ", m.sum(), np.median(dx), np.median(dy), np.percentile(dy, 80), np.median(dt)))
