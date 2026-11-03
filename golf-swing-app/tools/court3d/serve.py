"""Analyse one near-side serve: ball track from hit to first bounce -> 3D fit -> landing point and speed."""
import numpy as np, json, sys, cv2
from scipy.optimize import least_squares
sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
import ball, link
cam = np.load(sys.argv[2]); K, R, t = cam["K"], cam["R"], cam["t"]
Hm = cam["H"]
FPS = 59.78
def proj(X):
    X = np.atleast_2d(X); p = (K @ (R @ X.T + t[:, None])).T; return p[:, :2] / p[:, 2:]
def ground(u, v):
    p = cv2.perspectiveTransform(np.array([[[u, v]]], float), np.linalg.inv(Hm))[0, 0]; return p
def smooth_tracks(tracks):
    good = []
    for tr in tracks:
        f = np.array(tr["frames"], float); p = np.array(tr["pts"])
        if len(f) < 6: continue
        res = []
        for k in range(2):
            c = np.polyfit(f, p[:, k], 2); res.append(p[:, k] - np.polyval(c, f))
        rms = float(np.sqrt(np.mean(np.square(res))))
        sp = float(np.median(np.linalg.norm(np.diff(p, axis=0), axis=1) / np.diff(f)))
        if rms < 2.5 and sp > 4: good.append(dict(tr, rms=rms, speed=sp))
    return good
def simulate(p0, v0, ts, k=0.0204, g=9.81, dt=1 / 600):
    out = []; p = np.array(p0, float); v = np.array(v0, float); tcur = 0.0; i = 0
    ts = np.asarray(ts)
    while i < len(ts):
        while tcur + dt <= ts[i] + 1e-12:
            sp = np.linalg.norm(v); a = -k * sp * v + np.array([0, 0, -g])
            v = v + a * dt; p = p + v * dt; tcur += dt
        out.append(p.copy()); i += 1
    return np.array(out)
def fit3d(obs_t, obs_uv):
    """p0 = ball position at the moment of the hit (audio time), v0 = velocity then. Observations are in the air."""
    def res(q):
        p0, v0 = q[:3], q[3:6]
        P = simulate(p0, v0, obs_t)
        r = (proj(P) - obs_uv).ravel()
        # priors: the server hits from just behind the near baseline at about 2.6 m
        prior = [(p0[1] + 0.5) / 0.6, (p0[2] - 2.6) / 0.25]
        return np.concatenate([r, np.array(prior) * 4])
    sol = least_squares(res, np.array([0, -0.5, 2.6, 0, 35.0, -3.0]), loss="soft_l1", f_scale=2.0)
    P = simulate(sol.x[:3], sol.x[3:6], obs_t)
    rms = float(np.sqrt(np.mean(np.square(proj(P) - obs_uv))))
    # extrapolate to the ground
    ts = np.arange(0, 2.0, 1 / 600); Q = simulate(sol.x[:3], sol.x[3:6], ts)
    k = int(np.argmax(Q[:, 2] <= 0)); land = Q[k]
    return sol.x, rms, land, ts[k]

if __name__ == "__main__":
    video, hit = sys.argv[1], float(sys.argv[4])
    t0 = hit - 0.25
    d = {"cands": ball.detect(video, t0, 1.6)}
    tracks = smooth_tracks(link.link(d["cands"]))
    # serve track: starts within 0.15 s after the hit, moves away (image y increasing towards the far court rows), fastest
    t_hit = 0.25 * FPS
    cand = [tr for tr in tracks if t_hit - 3 <= tr["frames"][0] + 1 <= t_hit + 12]
    if not cand: print("no serve track"); sys.exit(1)
    tr = max(cand, key=lambda x: x["speed"] * len(x["frames"]))
    f = np.array(tr["frames"]) + 1; p = np.array(tr["pts"])
    obs_t = (f - t_hit) / FPS; obs_uv = p
    q, rms, land, tl = fit3d(obs_t, obs_uv)
    v0 = q[3:6]
    print(json.dumps({"hit": hit, "frames": [int(f[0]), int(f[-1])], "n_obs": int(len(f)), "fit_rms_px": round(rms, 2),
        "contact_xyz_m": [round(float(x), 2) for x in q[:3]], "landing_xy_m": [round(float(land[0]), 2), round(float(land[1]), 2)],
        "speed_kmh_at_contact": round(float(np.linalg.norm(v0)) * 3.6, 1), "flight_s": round(float(tl), 3)}))
