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
        if tb - ta > 0.3: continue
        if not (y1 >= P[i - 1][2] and y1 >= P[i + 1][2] and y1 - ya > 3 and y1 - yb > 3): continue
        if any(abs(t1 - h) < 0.07 for h in loud_hits): continue      # racket of the near player
        # the frame nearest the bounce may miss the lowest point: intersect the falling and the rising paths
        pre = [q for q in P[max(0, i - 3):i + 1] if t1 - q[0] < 0.25]; post = [q for q in P[i:i + 4] if q[0] - t1 < 0.25]
        if len(pre) >= 2 and len(post) >= 2:
            tp = np.array([q[0] for q in pre]); tq = np.array([q[0] for q in post])
            ax, bx = np.polyfit(tp, [q[1] for q in pre], 1); ay, by = np.polyfit(tp, [q[2] for q in pre], 1)
            cx, dx = np.polyfit(tq, [q[1] for q in post], 1); cy, dy = np.polyfit(tq, [q[2] for q in post], 1)
            if abs(ay - cy) > 1e-6:
                tc = (dy - by) / (ay - cy)
                if pre[0][0] - 0.05 <= tc <= post[-1][0] + 0.05:
                    t1, x1, y1 = float(tc), float(ax * tc + bx), float(ay * tc + by)
        g = ground(x1, y1)
        if not (-7 < g[0] < 7 and -2 < g[1] < 26): continue
        if out and t1 - out[-1]["t"] < 0.4: continue
        out.append({"t": round(t1, 3), "uv": [round(x1, 1), round(y1, 1)], "court": [round(float(g[0]), 2), round(float(g[1]), 2)]})
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
ALL_HITS = sorted(times)                       # every racket/ball sound (quiet ones too)
FAR_BOX = lambda x, y: 11.6 <= y <= 19.3 and abs(x) <= 4.7
NEAR_BOX = lambda x, y: 4.9 <= y <= 12.2 and abs(x) <= 4.7
def call(x, y, rect):
    inside = min(x - rect[0], rect[1] - x, y - rect[2], rect[3] - y)
    margin = 0.15 if y < 11.885 else 0.8      # the far side is only accurate to about +-0.8 m with a camera this low
    return "IN" if inside > margin else "OUT" if inside < -margin else "CLOSE"
def launch(P, bb, hits):
    """time the ball left the racket before this bounce: the last hit sound 0.2-1.3 s before, else the start of the track"""
    hs = [h for h in hits if 0.2 <= bb["t"] - h <= 1.3]
    if hs: return hs[-1]
    seq = [q[0] for q in P if q[0] <= bb["t"]]
    t0 = bb["t"]
    for q in reversed(seq):
        if t0 - q > 0.2: break
        t0 = q
    return t0 if bb["t"] - t0 >= 0.2 else None
def serve_at(P, bb, hits):
    """a serve: lands in (or near) a service box, fast (average > 60 km/h from the server at the far/near baseline)"""
    x, y = bb["court"]
    far, near = FAR_BOX(x, y), NEAR_BOX(x, y)
    if not (far or near): return None
    tl = launch(P, bb, hits)
    if tl is None: return None
    sy = -0.5 if far else 24.3; sx = -np.sign(x) * 1.0       # the server stands near the centre mark, serving cross-court
    v = np.hypot(x - sx, y - sy) / max(1e-3, bb["t"] - tl) * 3.6
    return (tl, "far" if far else "near", v) if v >= 60 else None
def ball_gap(P, t0, t1, gap=2.0):
    """first time in (t0, t1) after which the ball is not seen for `gap` seconds, else None"""
    ts = [q[0] for q in P if t0 <= q[0] <= t1]
    ts = [t0] + ts + [t1]
    for a, b in zip(ts, ts[1:]):
        if b - a > gap: return a
    return None
def end_after(cur, hits):
    """the last bounce, or a shot played off it (a hit within 1.5 s), plus 1.5 s"""
    t_end = cur["bounces"][-1]["t"]
    after = [h for h in hits if 0 < h - t_end < 1.5]
    return round((after[-1] if after else t_end) + 1.5, 3)
for arg in sys.argv[5:]:
    k, path = arg.split(":"); k = int(k); c = M["clips"][k]
    P = clean(json.load(open(path)))
    bs = find_bounces(P)
    # hit sounds that are not the bounce itself
    hits = [h for h in ALL_HITS if c["start"] - 0.5 <= h <= c["end"] and not any(abs(h - b["t"]) < 0.08 for b in bs)]
    loud = [h for h in loud_hits if c["start"] - 0.5 <= h <= c["end"]]
    # serves of the near player: loud hit, then the ball starts high in the picture and comes down fast towards the far court
    near_serves = []
    for h in loud:
        seg = [q for q in P if 0.05 < q[0] - h < 0.5]
        if len(seg) < 3 or seg[0][2] > 260 or seg[-1][2] < 400: continue
        d = np.hypot(np.diff([q[1] for q in seg]), np.diff([q[2] for q in seg])) / np.diff([q[0] for q in seg]) / 30
        if np.median(d) > 15: near_serves.append(h)
    events = sorted([(h, "serve_hit", None) for h in near_serves] + [(b["t"], "bounce", b) for b in bs], key=lambda e: e[0])
    points, cur = [], None
    def close(cur, t_end):
        cur["end"] = round(t_end, 3); points.append(cur)
    def start(tl, side):
        return {"serve_t": round(tl, 3), "serve_side": side, "bounces": [], "end": None, "serve": None}
    for t_ev, kind, bb in events:
        if kind == "serve_hit":
            if cur is not None:
                last_t = cur["bounces"][-1]["t"] if cur["bounces"] else cur["serve_t"]
                g = ball_gap(P, last_t, t_ev)
                if g is None: continue                     # the ball stayed in play: an overhead or a high ball, not a serve
                close(cur, g + 1.0); cur = None
            cur = start(t_ev, "far")
            continue
        x, y = bb["court"]
        if cur is not None and cur["bounces"]:
            last = cur["bounces"][-1]
            gap_end = ball_gap(P, last["t"], bb["t"])
            sv = serve_at(P, bb, hits)
            if gap_end is not None:                        # the ball was out of play: the point was over
                close(cur, gap_end + 1.0); cur = None
        if cur is not None and not cur["bounces"] and bb["t"] - cur["serve_t"] > 1.2:
            cur["bounces"].append({"t": cur["serve_t"], "court": [0, -1], "missing": True})   # serve bounce not seen: rally from here
        if cur is None or not cur["bounces"]:
            sv = serve_at(P, bb, hits)
            if cur is None and not sv: continue     # ball bouncing before a serve, or knocking balls back between points
            if cur is None: cur = start(sv[0], sv[1])
            side = cur["serve_side"]
            box = (-4.115, 0.0) if x < 0 else (0.0, 4.115)
            rect = (box[0], box[1], 11.885, 18.285) if side == "far" else (box[0], box[1], 5.485, 11.885)
            if side == "far" and not FAR_BOX(x, y) and y < 11.885:          # a serve that did not reach the far side (net)
                bb["kind"] = "serve"; bb["call"] = "OUT"
            else:
                bb["kind"] = "serve"; bb["call"] = call(x, y, rect)
            cur["bounces"].append(bb)
            if side == "far" and any(abs(cur["serve_t"] - h) < 0.05 for h in loud):
                sp = serve_speed(P, bb, cur["serve_t"])
                if sp: sp.update(t=cur["serve_t"], bounce=bb["court"]); cur["serve"] = sp
            if bb["call"] == "OUT": close(cur, bb["t"] + 1.5); cur = None   # fault
            continue
        bb["kind"] = "rally"; bb["call"] = call(x, y, (-4.115, 4.115, 0, 23.77))
        cur["bounces"].append(bb)
        if bb["call"] == "OUT": close(cur, bb["t"] + 1.5); cur = None
    if cur is not None:
        last_t = cur["bounces"][-1]["t"] if cur["bounces"] else cur["serve_t"]
        g = ball_gap(P, last_t, c["end"])
        close(cur, min(c["end"], (g + 1.0) if g is not None else (end_after(cur, hits) if cur["bounces"] else last_t + 3.0)))
    ranges = [[round(max(c["start"], p["serve_t"] - 1.2), 3), round(min(c["end"], p["end"]), 3)] for p in points]
    keep = [b for p in points for b in p["bounces"] if not b.get("missing")]
    serve = next((p["serve"] for p in points if p["serve"]), None)
    res[k] = {"ball": P, "bounces": keep, "serve": serve, "ranges": ranges, "points": [{k2: v for k2, v in p.items() if k2 != "bounces"} for p in points]}
    print(k, "ranges", ranges, "bounces", [(b["t"], b["court"], b["kind"], b["call"]) for b in keep])
json.dump(res, open(OUT, "w"))
