"""Training crops for the small ball detector, from our own videos (full resolution: a far ball is only a few pixels).

  python3 make_crops.py VIDEO TN_DIR match.json OUT.npz [--every 2] [--neg 0.25]

TN_DIR holds tn_XXX.json (ball positions from tn_roi.py, 30 per second, in source pixels). Only trusted positions are used:
part of a smooth track (no jump > 80 px from the neighbours, 2 more detections within 3 frames); gaps of 1-3 frames inside
a track are filled by interpolation (the frames the teacher missed are the ones the student should learn).
Each sample: 3 frames (newest first, BGR) cropped to 192 x 288 around the ball (random position inside the crop), or a
crop without the ball (negative). OUT.npz: X (N, 192, 288, 9) uint8, Y (N, 2) float (-1 for no ball), T (N,) time.
"""
import argparse, glob, json, os, subprocess
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("video"); ap.add_argument("tn_dir"); ap.add_argument("match"); ap.add_argument("out")
ap.add_argument("--every", type=int, default=2, help="one positive sample every N labelled frames")
ap.add_argument("--neg", type=float, default=0.25, help="negatives per positive")
ap.add_argument("--clips", default="", help="comma separated clip numbers (default all)")
def rect(value):
    try:
        x0, y0, x1, y1 = map(int, value.split(","))
        if not (0 <= x0 < x1 <= 1920 and 0 <= y0 < y1 <= 1080):
            raise ValueError
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected x0,y0,x1,y1 within 1920x1080") from exc
    return x0, y0, x1, y1

ap.add_argument("--exclude", action="append", type=rect, default=[], help="exclude crops intersecting x0,y0,x1,y1 (repeatable)")
a = ap.parse_args()
CH, CW, FR, W, H = 192, 288, 30, 1920, 1080
rng = np.random.default_rng(0)
M = json.load(open(a.match))


def excluded(x, y, w=1, h=1):
    return any(x < x1 and x + w > x0 and y < y1 and y + h > y0 for x0, y0, x1, y1 in a.exclude)


def trusted(res):
    """{frame index in the clip decode: (x, y)}; res[j] is frame j + 2"""
    pts = {j + 2: (r[1], r[2]) for j, r in enumerate(res) if r[1] is not None and not excluded(r[1], r[2])}
    keep = {}
    for i, (x, y) in pts.items():
        nb = [pts[k] for k in (i - 1, i + 1) if k in pts]
        if nb and min(np.hypot(x - u, y - v) for u, v in nb) > 80: continue
        if sum(1 for k in range(i - 3, i + 4) if k != i and k in pts) < 2: continue
        keep[i] = (x, y)
    idx = sorted(keep)
    for p, q in zip(idx, idx[1:]):                       # fill short gaps inside a track
        if 1 < q - p <= 4:
            (x0, y0), (x1, y1) = keep[p], keep[q]
            if np.hypot(x1 - x0, y1 - y0) / (q - p) < 60:
                for k in range(p + 1, q):
                    s = (k - p) / (q - p); keep[k] = (x0 + (x1 - x0) * s, y0 + (y1 - y0) * s)
    return {i: (x, y) for i, (x, y) in keep.items() if not excluded(x, y)}


X, Y, T = [], [], []
clips = [int(c) for c in a.clips.split(",")] if a.clips else range(len(M["clips"]))
for k in clips:
    path = os.path.join(a.tn_dir, "tn_%03d.json" % k)
    if not os.path.exists(path): continue
    c = M["clips"][k]; res = json.load(open(path)); lab = trusted(res)
    if not lab: continue
    t0 = c["start"]; dur = c["end"] - c["start"]
    p = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-ss", "%.3f" % t0, "-i", a.video, "-t", "%.3f" % dur,
                          "-vf", "fps=%d,scale=%d:%d" % (FR, W, H), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    ring, i, npos = [], 0, 0
    while True:
        b = p.stdout.read(W * H * 3)
        if len(b) < W * H * 3: break
        ring = (ring + [np.frombuffer(b, np.uint8).reshape(H, W, 3)])[-3:]
        if len(ring) == 3:
            if i in lab and (i % a.every == 0):
                x, y = lab[i]
                ox = int(np.clip(x - rng.integers(16, CW - 16), 0, W - CW)); oy = int(np.clip(y - rng.integers(16, CH - 16), 0, H - CH))
                if not excluded(ox, oy, CW, CH):
                    X.append(np.concatenate([f[oy:oy + CH, ox:ox + CW] for f in reversed(ring)], 2))
                    Y.append((x - ox, y - oy)); T.append(t0 + i / FR); npos += 1
                if rng.random() < a.neg:                                     # a crop elsewhere in the same frames
                    for _ in range(20):
                        ox = int(rng.integers(0, W - CW)); oy = int(rng.integers(H // 4, H - CH))
                        if excluded(ox, oy, CW, CH) or (ox - 20 < x < ox + CW + 20 and oy - 20 < y < oy + CH + 20): continue
                        X.append(np.concatenate([f[oy:oy + CH, ox:ox + CW] for f in reversed(ring)], 2))
                        Y.append((-1, -1)); T.append(t0 + i / FR)
                        break
        i += 1
    if p.wait() != 0: raise RuntimeError(f"ffmpeg failed on clip {k}")
    print("clip", k, "labelled", len(lab), "samples", npos, flush=True)
np.savez(a.out, X=np.array(X, np.uint8), Y=np.array(Y, np.float32), T=np.array(T, np.float32))
print("total", len(X), "->", a.out)
