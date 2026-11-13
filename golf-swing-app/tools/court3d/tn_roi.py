"""TrackNet with a region of interest: once the ball is found, the next frames only look at a window around the predicted
position (about 1/4 of the frame). Falls back to the whole frame when the ball is lost. Same output format as tn_run.py.
Usage: tn_roi.py video t0 dur out.json model.pt"""
from collections import deque
import sys, os, json, subprocess, time, numpy as np, torch, tnet
video, t0, dur, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
# the part of the frame the detector looks at (scaled to half size, as TrackNet was trained); set per video with TN_X0 etc.
X0, Y0, CW = int(os.environ.get("TN_X0", 320)), int(os.environ.get("TN_Y0", 120)), int(os.environ.get("TN_CW", 1280))
CH = int(os.environ.get("TN_CH", 512)); FR = 30
SC = int(os.environ.get("TN_SCALE", 2))   # 2: half size (as TrackNet was trained); 1: full size for a small, far ball
IW, IH = CW // SC, CH // SC                # model input size for the whole frame (multiples of 8)
RW, RH = int(os.environ.get("ROI_W", 320)), int(os.environ.get("ROI_H", 128))   # window (multiples of 8)
dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
m = tnet.load(sys.argv[5] if len(sys.argv) > 5 else "model_best.pt").to(dev); torch.set_num_threads(int(os.environ.get("THREADS", 4)))
cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t0:.3f}", "-i", video, "-t", f"{dur:.3f}", "-vf",
       f"fps={FR},crop={CW}:{CH}:{X0}:{Y0},scale={IW}:{IH}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
p = subprocess.Popen(cmd, stdout=subprocess.PIPE); sz = IW * IH * 3
frames = deque(maxlen=3)
def run(i, x0, y0, w, h):
    x = np.concatenate([f[y0:y0 + h, x0:x0 + w] for f in reversed(frames)], 2).transpose(2, 0, 1)[None]
    with torch.inference_mode():
        cls = m(torch.from_numpy(np.ascontiguousarray(x)).to(dev)).argmax(1).to(torch.uint8).cpu().numpy()[0]
    xy = tnet.heat_to_xy(cls)
    return None if xy is None else (xy[0] + x0, xy[1] + y0)
res, hist = [], []                           # hist: recent (i, x, y) in model-input pixels
t = time.time(); n_full = 0
i = -1
while True:
    b = p.stdout.read(sz)
    if len(b) < sz: break
    i += 1
    frames.append(np.frombuffer(b, np.uint8).reshape(IH, IW, 3).astype(np.float32) / 255)
    if len(frames) < 3: continue
    pos = None
    recent = [h for h in hist if i - h[0] <= 2]
    if recent:
        if len(recent) >= 2: (i1, x1, y1), (i2, x2, y2) = recent[-2], recent[-1]; k = i - i2
        else: (i2, x2, y2) = recent[-1]; x1, y1, i1, k = x2, y2, i2 - 1, i - i2
        px = x2 + (x2 - x1) / max(1, i2 - i1) * k; py = y2 + (y2 - y1) / max(1, i2 - i1) * k
        x0 = int(np.clip(px - RW / 2, 0, IW - RW)); y0 = int(np.clip(py - RH / 2, 0, IH - RH))
        pos = run(i, x0, y0, RW, RH)
    if pos is None:
        pos = run(i, 0, 0, IW, IH); n_full += 1
    if pos is not None: hist.append((i, pos[0], pos[1]))
    res.append([round(t0 + i / FR, 4)] + ([round(X0 + pos[0] * SC, 1), round(Y0 + pos[1] * SC, 1)] if pos else [None, None]))
if p.wait() != 0 or not res:
    raise RuntimeError("ffmpeg did not decode the requested clip")
json.dump(res, open(out, "w"))
print("frames", len(res), "detected", sum(r[1] is not None for r in res), "full-frame runs", n_full, "sec/frame", round((time.time() - t) / max(1, len(res)), 2))
