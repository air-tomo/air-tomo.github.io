"""Ball positions with the small model (train_crops.py) at full resolution. Same output as tn_roi.py ([[t, x, y], ...]).

  python3 small_run.py VIDEO T0 DUR OUT.json MODEL.pt [--y0 300 --h 704] [--thr 0.5]

Looks at rows y0 .. y0+h of the frame (all columns). Frames at 30 per second; the first two frames have no result (3-frame input).
--top K (default 5): also writes OUT with "_cand" before .json: per frame [t, [[score, x, y], ...]] (up to K peaks, every ball in
view: the one in play is chosen later by tracking, see track.py).
"""
import argparse, json, subprocess, time
import numpy as np, torch
from train_small import SmallNet, peak

ap = argparse.ArgumentParser()
ap.add_argument("video"); ap.add_argument("t0", type=float); ap.add_argument("dur", type=float); ap.add_argument("out"); ap.add_argument("model")
ap.add_argument("--y0", type=int, default=300); ap.add_argument("--h", type=int, default=704); ap.add_argument("--thr", type=float, default=0.5); ap.add_argument("--top", type=int, default=5)
a = ap.parse_args()
W, H, FR = 1920, 1080, 30
torch.set_num_threads(int(__import__("os").environ.get("THREADS", 4)))
m = SmallNet(); m.load_state_dict(torch.load(a.model, map_location="cpu", weights_only=True)); m.eval()
p = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-ss", "%.3f" % a.t0, "-i", a.video, "-t", "%.3f" % a.dur,
                      "-vf", "fps=%d,scale=%d:%d,crop=%d:%d:0:%d" % (FR, W, H, W, a.h, a.y0), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                     stdout=subprocess.PIPE)
ring, res, cand, i, t = [], [], [], 0, time.time()


def peaks(hm, k, thr, r=12):
    hm = hm.copy(); out = []
    for _ in range(k):
        j = int(np.argmax(hm)); yy, xx = divmod(j, hm.shape[1]); v = float(hm[yy, xx])
        if v < thr: break
        out.append([round(v, 3), xx, yy + a.y0]); hm[max(0, yy - r):yy + r, max(0, xx - r):xx + r] = 0
    return out
while True:
    b = p.stdout.read(W * a.h * 3)
    if len(b) < W * a.h * 3: break
    ring = (ring + [np.frombuffer(b, np.uint8).reshape(a.h, W, 3)])[-3:]
    if len(ring) == 3:
        x = np.concatenate([ring[2], ring[1], ring[0]], 2).astype(np.float32).transpose(2, 0, 1)[None] / 255
        with torch.no_grad(): heat = torch.sigmoid(m(torch.from_numpy(x)))[0].numpy()
        q = peak(heat, a.thr); cand.append([round(a.t0 + i / FR, 4), peaks(heat, a.top, 0.35)])
        res.append([round(a.t0 + i / FR, 4)] + ([round(float(q[0]), 1), round(float(q[1]) + a.y0, 1)] if q is not None else [None, None]))
    i += 1
json.dump(res, open(a.out, "w")); json.dump(cand, open(a.out.replace(".json", "_cand.json"), "w"))
print("frames", len(res), "detected", sum(r[1] is not None for r in res), "sec/frame %.2f" % ((time.time() - t) / max(1, len(res))))
