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
def serve_speed(P, bounce, hit, server_y=-0.5):
    """3D flight from the hit (audio time, contact prior) to the observed bounce point on the ground.
    server_y: where the server stands (-0.5 = behind the near baseline, 24.3 = behind the far baseline)"""
    obs = [(tt, x, y) for tt, x, y in P if hit + 0.02 < tt < bounce["t"] - 0.01]
    if len(obs) < 5: return None
    ts = np.array([o[0] for o in obs]) - hit; uv = np.array([[o[1], o[2]] for o in obs])
    tb = bounce["t"] - hit; gb = np.array(bounce["court"] + [0.0])
    def res(q):
        p0, v0 = q[:3], q[3:]
        Pp = simulate(p0, v0, np.append(ts, tb))
        return np.concatenate([(proj(Pp[:-1]) - uv).ravel(), (Pp[-1] - gb) * 40, [(p0[1] - server_y) / 0.6 * 3, (p0[2] - 2.6) / 0.3 * 3]])
    q0 = np.array([0, server_y, 2.6, (gb[0]) / tb, (gb[1] - server_y) / tb, -1.0])
    s = least_squares(res, q0, loss="soft_l1", f_scale=2.0)
    rms = float(np.sqrt(np.mean(s.fun[:2 * len(obs)] ** 2)))
    # median per-frame error: a few frames on another ball (balls lying around) do not spoil it
    med = float(np.median(np.hypot(s.fun[0:2 * len(obs):2], s.fun[1:2 * len(obs):2])))
    return {"kmh": round(float(np.linalg.norm(s.x[3:])) * 3.6), "rms_px": round(rms, 2), "med_px": round(med, 2),
            "contact": [round(float(v), 2) for v in s.x[:3]]}
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
def ray_height(u, v, y_plane):
    """height (m) of the point on the camera ray through pixel (u, v) where it crosses the plane y = y_plane"""
    Rt = R.T; C = -Rt @ t
    d = Rt @ np.linalg.inv(K) @ np.array([u, v, 1.0])
    if abs(d[1]) < 1e-9: return None
    s_ = (y_plane - C[1]) / d[1]
    return float(C[2] + s_ * d[2]) if s_ > 0 else None
def rally_until(P, bs, i0, t_from, hits, clip_end):
    """bounces of the rally after bs[i0-1] up to the end of the point. Returns (list of bounces, end time).
    The point is over when the ball goes out, bounces twice on one side (or rolls) with no shot in between, is not seen for 2 s,
    or nothing happens (no shot, no bounce) for 3.5 s (a lob takes 3 s): the ball flew out of the picture or into the net, and what the
    detector finds after that is another ball."""
    out, last_t = [], t_from
    prev = bs[i0 - 1] if i0 > 0 and abs(bs[i0 - 1]["t"] - t_from) < 1e-6 else None
    act = t_from
    def quiet(t1):
        ev = [act] + [h for h in hits if act < h < t1] + [t1]
        for a, b in zip(ev, ev[1:]):
            if b - a > 3.5: return a
        return None
    for bb in bs[i0:]:
        g = ball_gap(P, last_t, bb["t"])
        if g is not None: return out, g + 1.0
        q = quiet(bb["t"])
        if q is not None: return out, q + 1.5
        x, y = bb["court"]
        bb["kind"] = "rally"; bb["call"] = call(x, y, (-4.115, 4.115, 0, 23.77))
        out.append(bb); last_t = bb["t"]
        if bb["call"] == "OUT": return out, bb["t"] + 1.5
        if prev is not None and (prev["court"][1] < 11.885) == (y < 11.885) and not any(prev["t"] + 0.08 < h < bb["t"] - 0.05 for h in hits):
            return out, bb["t"] + 1.0
        prev = bb; act = bb["t"]
    g = ball_gap(P, last_t, clip_end)
    if g is not None: return out, g + 1.0
    after = [h for h in hits if 0 < h - last_t < 1.5]
    end = (after[-1] if after else last_t) + 1.5
    q = quiet(clip_end)
    return out, min(clip_end, end, q + 1.5 if q is not None else end)
def returned(t_bounce, hits):
    """was the serve played back? In a rally the shots keep coming (return, next shot...): at least 2 hit sounds within 4.5 s
    of the serve bounce. After a fault there is at most one (the receiver or the server knocking the ball away)."""
    return sum(1 for h in hits if 0.1 < h - t_bounce < 4.5) >= 2
CLIPS, ALL_ATTEMPTS = {}, []
for arg in sys.argv[5:]:
    k, path = arg.split(":"); k = int(k); c = M["clips"][k]
    P = clean(json.load(open(path)))
    bs = find_bounces(P)
    hits = [h for h in ALL_HITS if c["start"] - 0.5 <= h <= c["end"] and not any(abs(h - b["t"]) < 0.08 for b in bs)]
    loud = [h for h in loud_hits if c["start"] - 0.5 <= h <= c["end"]]
    # --- serve attempts ---
    # near player: loud hit, then the ball starts high in the picture and comes down fast towards the far court
    attempts = []
    for h in loud:
        seg = [q for q in P if 0.05 < q[0] - h < 0.5]
        if len(seg) < 3 or seg[-1][2] < 400: continue
        # height just after the contact: the first points can be missed, so carry the falling path back to h + 0.05
        y0 = seg[0][2] - (seg[0][0] - h - 0.05) * (seg[1][2] - seg[0][2]) / (seg[1][0] - seg[0][0])
        if min(seg[0][2], y0) > 260: continue
        d = np.hypot(np.diff([q[1] for q in seg]), np.diff([q[2] for q in seg])) / np.diff([q[0] for q in seg]) / 30
        if np.median(d) > 15: attempts.append({"t": h, "side": "far"})
    # far player: lands in (or near) a near service box, fast, hit high (>= 1.8 m) at the far baseline
    for bb in bs:
        x, y = bb["court"]
        if not NEAR_BOX(x, y): continue
        sv = serve_at(P, bb, hits)
        if not sv: continue
        first = [q for q in P if 0 <= q[0] - sv[0] < 0.25]
        z = ray_height(first[0][1], first[0][2], 23.8) if first else None
        if z is None or z < 1.8: continue
        if any(abs(a["t"] - sv[0]) < 1.0 for a in attempts): continue
        attempts.append({"t": sv[0], "side": "near"})
    # near player's serve whose flight was not caught (the toss and the contact are above the picture, the bounce in the far box
    # missed): a loud hit, the far player's return heard 0.5-1.6 s later and that return bouncing in the near half 0.9-2.6 s after
    for h in loud:
        if any(abs(a["t"] - h) < 1.5 for a in attempts): continue
        if not any(0.5 < x - h < 1.6 for x in hits): continue
        if any(0.9 < b["t"] - h < 2.6 and b["court"][1] < 11.885 for b in bs): attempts.append({"t": h, "side": "far", "est": True})
    # far player's serve whose sound and flight were not caught: a bounce in a near service box that the near player returns
    # (loud hit 0.2-1.0 s later); the dead-time check below keeps rally shots out
    for bb in bs:
        x, y = bb["court"]
        if not NEAR_BOX(x, y) or any(abs(a["t"] - (bb["t"] - 0.6)) < 1.2 for a in attempts): continue
        if any(0.2 < h - bb["t"] < 1.0 for h in loud): attempts.append({"t": bb["t"] - 0.6, "side": "near", "est": True})
    attempts.sort(key=lambda a: a["t"])
    # a serve comes after dead time: no shot by the near player and no bounce inside the court in the 4 s before
    # (an overhead or a high ball in the middle of a rally looks like a serve otherwise)
    def dead_before(h):
        if any(0.4 < h - x < 4.0 for x in loud): return False
        # bounces near a baseline are the server bouncing the ball (the far side reads 1-2 m short with a low camera)
        return not any(0.3 < h - b["t"] < 4.0 and 1.5 < b["court"][1] < 20.0 for b in bs)
    attempts = [a_ for a_ in attempts if dead_before(a_["t"])]
    # --- each attempt: serve bounce, returned?, rally ---
    for i, a_ in enumerate(attempts):
        nxt = attempts[i + 1]["t"] if i + 1 < len(attempts) else c["end"]
        sb = next((b for b in bs if 0.25 < b["t"] - a_["t"] < 1.3), None)
        if sb is not None and a_["side"] == "far" and not FAR_BOX(*sb["court"]) and sb["court"][1] > 11.885 and sb["court"][1] < 11.6:
            sb = None
        a_["bounce"] = sb
        if sb is not None:
            x, y = sb["court"]
            box = (-4.115, 0.0) if x < 0 else (0.0, 4.115)
            rect = (box[0], box[1], 11.885, 18.285) if a_["side"] == "far" else (box[0], box[1], 5.485, 11.885)
            sb["kind"] = "serve"
            sb["call"] = "OUT" if (a_["side"] == "far" and y < 11.885) or (a_["side"] == "near" and y > 11.885) else call(x, y, rect)
        a_["returned"] = returned(sb["t"] if sb is not None else a_["t"] + 0.6, hits)
    for a_ in attempts: a_["clip"] = k
    CLIPS[k] = {"P": P, "bs": bs, "hits": hits, "c": c}
    ALL_ATTEMPTS.extend(attempts)
# --- faults (over the whole match): a serve that is not played back and is followed by another serve within 25 s was a fault ---
ALL_ATTEMPTS.sort(key=lambda a: a["t"])
points, i = [], 0
# a "serve" less than 6 s after a serve that was played back is a shot in that rally (an overhead or a high ball)
kept = []
for a_ in ALL_ATTEMPTS:
    if kept and kept[-1]["returned"] and a_["t"] - kept[-1]["t"] < (15 if a_["side"] != kept[-1]["side"] else 6): continue
    kept.append(a_)
ALL_ATTEMPTS = kept
while i < len(ALL_ATTEMPTS):
    a_ = ALL_ATTEMPTS[i]; parts = [a_]
    nx = ALL_ATTEMPTS[i + 1] if i + 1 < len(ALL_ATTEMPTS) else None
    gap_next = nx["t"] - a_["t"] if nx else 1e9
    # which half the server stood on: serves go cross-court, so the opposite of the box the serve landed in
    half = lambda a: (-np.sign(a["bounce"]["court"][0]) if a["bounce"] is not None and abs(a["bounce"]["court"][0]) > 0.5 else 0)
    same_server = nx is not None and nx["side"] == a_["side"] and gap_next < 30
    if same_server and half(a_) and half(nx):
        fault = half(a_) == half(nx)          # a second serve comes from the same half; the next point from the other half
    elif same_server:
        # not played back and served again within 25 s, or served again within 15 s (no time to play a point): a fault
        fault = (not a_["returned"] and gap_next < 25) or gap_next < 15
    else:
        fault = False                         # the other player serving: not a second serve
    if fault:
        a_["fault"] = True
        if a_["bounce"] is not None: a_["bounce"]["call"] = "OUT"      # they served again, so it was out
        parts.append(ALL_ATTEMPTS[i + 1]); i += 1
    last = parts[-1]
    if len(parts) == 2 and not last["returned"] and last["bounce"] is not None and last["bounce"]["call"] != "IN":
        last["fault"] = True; last["bounce"]["call"] = "OUT"             # double fault
    points.append(parts); i += 1
for k in CLIPS: res[k] = {"ball": CLIPS[k]["P"], "bounces": [], "serve": None, "ranges": [], "points": []}
for parts in points:
    prange = []
    for a_ in parts:
        cd = CLIPS[a_["clip"]]; P, bs, hits, c = cd["P"], cd["bs"], cd["hits"], cd["c"]; r = res[a_["clip"]]
        sb = a_["bounce"]
        if sb is not None: r["bounces"].append(sb)
        if a_.get("fault") or not a_["returned"]:
            end = (sb["t"] + 1.5) if sb is not None else a_["t"] + 2.5
        else:
            i0 = bs.index(sb) + 1 if sb is not None else next((n for n, b in enumerate(bs) if b["t"] > a_["t"] + 0.3), len(bs))
            nxt_t = next((b["t"] for b in ALL_ATTEMPTS if b["t"] > a_["t"]), c["end"])
            bounces_r, end = rally_until(P, [b for b in bs if b["t"] < nxt_t], i0, sb["t"] if sb is not None else a_["t"], hits, c["end"])
            r["bounces"].extend(bounces_r)
        rg = [round(max(c["start"], a_["t"] - 1.2), 3), round(min(c["end"], end), 3)]
        prange.append([a_["clip"]] + rg); r["ranges"].append(rg)
        if sb is not None and not a_.get("fault") and not a_.get("est"):
            sp = serve_speed(P, sb, a_["t"], -0.5 if a_["side"] == "far" else 24.3)
            if sp: sp.update(t=round(a_["t"], 3), bounce=sb["court"]); a_["speed"] = sp; r["serve"] = r["serve"] or sp
    pt = {"ranges": prange, "serves": [{"t": round(a_["t"], 3), "side": a_["side"], "fault": bool(a_.get("fault")), "speed": a_.get("speed")} for a_ in parts]}
    for a_ in parts: res[a_["clip"]]["points"].append(pt)
    print("point", [(s_["t"], s_["side"], "FAULT" if s_["fault"] else "") for s_ in pt["serves"]], [rg[1:] for rg in prange])
for k in res: res[k]["bounces"].sort(key=lambda b: b["t"])
json.dump(res, open(OUT, "w"))
