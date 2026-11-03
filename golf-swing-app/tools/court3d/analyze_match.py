"""Per clip: ball tracks + bounces (image V points mapped to the court). Usage: analyze_match.py video env.npy cam.npz start_sec out.json"""
import sys, json, os, numpy as np, cv2
from multiprocessing import Pool
sys.path.insert(0, "/home/user/air-tomo.github.io/golf-swing-app")
import swing, tennis, ball, link
VIDEO, ENV, CAM, START, OUT = sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), sys.argv[5]
FPS = 59.78
CACHE = os.path.join(os.path.dirname(OUT), "cands"); os.makedirs(CACHE, exist_ok=True)
cam = np.load(CAM); Hinv = np.linalg.inv(cam["H"])
def ground(u, v): return cv2.perspectiveTransform(np.array([[[u, v]]], float), Hinv)[0, 0]
def ball_tracks(tracks):
    good = []
    for tr in tracks:
        f = np.array(tr["frames"], float); p = np.array(tr["pts"])
        if len(f) < 6: continue
        res = np.concatenate([p[:, k] - np.polyval(np.polyfit(f, p[:, k], 2), f) for k in range(2)])
        steps = np.linalg.norm(np.diff(p, axis=0), axis=1) / np.diff(f)
        span = np.linalg.norm(p[-1] - p[0])
        if len(f) >= 9 and np.sqrt(np.mean(res ** 2)) < 1.6 and np.median(steps) > 5 and span > 80: good.append(tr)
    return good
def bounces(tr_list, hit_times, t0):
    """V points inside one ball track (image y: descending then rising), at most one per interval between hits (the sharpest)."""
    cands = []
    for tr in tr_list:
        f = np.array(tr["frames"]) + 1; p = np.array(tr["pts"])
        if len(f) < 8: continue
        for i in range(2, len(f) - 2):
            if f[i] - f[i - 2] > 4 or f[i + 2] - f[i] > 4: continue
            y = p[i, 1]
            if not (y >= p[i - 1, 1] and y >= p[i + 1, 1]): continue
            sharp = (y - p[i - 2, 1]) + (y - p[i + 2, 1])
            if sharp < 6: continue
            t = t0 + f[i] / FPS
            if any(abs(t - h) < 0.08 for h in hit_times): continue
            g = ground(*p[i])
            if not (-7 < g[0] < 7 and -2 < g[1] < 26): continue
            cands.append((t, sharp, p[i], g))
    # V across two tracks: the linker usually breaks at the bounce (track A falls, track B rises right after)
    for A in tr_list:
        fa = np.array(A["frames"]) + 1; pa = np.array(A["pts"])
        if len(fa) < 5 or pa[-1, 1] - pa[-3, 1] < 2: continue
        for B in tr_list:
            fb = np.array(B["frames"]) + 1; pb = np.array(B["pts"])
            if B is A or len(fb) < 5 or not (0 < fb[0] - fa[-1] <= 5) or pb[0, 1] - pb[2, 1] < 2: continue
            gap = fb[0] - fa[-1]; va = (pa[-1] - pa[-3]) / max(1, fa[-1] - fa[-3])
            if np.linalg.norm(pa[-1] + va * gap - pb[0]) > 25 + 3 * gap: continue
            uv = pa[-1] if pa[-1, 1] >= pb[0, 1] else pb[0]
            sharp = (uv[1] - pa[-3, 1]) + (uv[1] - pb[2, 1])
            t = t0 + (fa[-1] if uv is pa[-1] else fb[0]) / FPS
            if any(abs(t - h) < 0.08 for h in hit_times): continue
            g = ground(*uv)
            if not (-7 < g[0] < 7 and -2 < g[1] < 26): continue
            cands.append((t, sharp, uv, g))
    # the ball bounces at most once between two shots (>= 0.5 s apart): keep the sharpest V in each 0.5 s neighbourhood
    keep = []
    for c in sorted(cands, key=lambda x: -x[1]):
        if all(abs(c[0] - k[0]) >= 0.5 for k in keep): keep.append(c)
    out = [{"t": round(t, 3), "uv": [round(float(uv[0]), 1), round(float(uv[1]), 1)], "court": [round(float(g[0]), 2), round(float(g[1]), 2)]}
           for t, sharp, uv, g in sorted(keep, key=lambda x: x[0])]
    seq = {}
    for tr in sorted(tr_list, key=lambda x: -len(x["frames"])):
        for f, pt in zip(tr["frames"], tr["pts"]): seq.setdefault(f + 1, pt)
    return out, sorted((f, pt[0], pt[1]) for f, pt in seq.items())
def work(args):
    k, a, b, hits = args
    cache = os.path.join(CACHE, "c%03d.json" % k)
    if os.path.exists(cache): c = json.load(open(cache))
    else:
        c = ball.detect(VIDEO, a, b - a); json.dump(c, open(cache, "w"))
    tr = ball_tracks(link.link(c))
    bs, seq = bounces(tr, hits, a)
    return {"clip": k, "start": a, "end": b, "ball": [(round(a + f / FPS, 4), round(x, 1), round(y, 1)) for f, x, y in seq], "bounces": bs}
if __name__ == "__main__":
    env = np.load(ENV); dur = len(env) * swing.HOP_SEC
    r = tennis.analyze(env, dur, tennis.TennisSettings())
    times, _ = swing.detect_hits(env, 6, 0.3)
    lv = np.array([env[int(round(t / swing.HOP_SEC - 0.5))] for t in times]); loud = np.percentile(lv, 90) - 6
    loud_hits = [t for t, l in zip(times, lv) if l >= loud]
    segs = [(i, a, b) for i, (a, b) in enumerate(r["segments"]) if a >= START]
    jobs = [(i, a, b, [h for h in loud_hits if a <= h <= b]) for i, a, b in segs]
    with Pool(4) as pool: res = pool.map(work, jobs)
    json.dump({"segments": [[a, b] for _, a, b in segs], "labels": [r["labels"][i] for i, _, _ in segs], "clips": res}, open(OUT, "w"))
    print("clips", len(res), "bounces", sum(len(c["bounces"]) for c in res), "ball frames", sum(len(c["ball"]) for c in res))
