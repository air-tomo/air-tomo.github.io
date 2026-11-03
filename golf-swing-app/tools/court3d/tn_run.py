"""Run TrackNet on a time range of a 1920x1080 video. Frames: every Nth to get ~30 fps; crop 1280x720 around the court, resize to 640x360."""
import sys, json, subprocess, time, numpy as np, torch, cv2, tnet
video, t0, dur, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
import os
X0, Y0, CW = 320, 120, 1280
CH = int(os.environ.get("TN_CH", 512))   # rows analysed: the court and the air above it (512 = 30% less work than the full 720)
FR = int(os.environ.get("TN_FPS", 30))   # frames per second given to the model
dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
BATCH = 16 if dev != "cpu" else 1
m = tnet.load(sys.argv[5] if len(sys.argv) > 5 else "model_best.pt").to(dev); torch.set_num_threads(4)
hw = ["-hwaccel", "cuda"] if dev == "cuda" else []   # decode HEVC on the GPU too (Colab)
cmd = ["ffmpeg", "-nostdin", "-v", "error"] + hw + ["-ss", f"{t0:.3f}", "-i", video, "-t", f"{dur:.3f}", "-vf",
       f"fps={FR},crop={CW}:{CH}:{X0}:{Y0},scale=640:{CH // 2}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
p = subprocess.Popen(cmd, stdout=subprocess.PIPE); sz = 640 * (CH // 2) * 3
frames = []
while True:
    b = p.stdout.read(sz)
    if len(b) < sz: break
    frames.append(np.frombuffer(b, np.uint8).reshape(CH // 2, 640, 3).astype(np.float32) / 255)
res = []; t = time.time()
idx = list(range(2, len(frames)))
with torch.no_grad():
    for s0 in range(0, len(idx), BATCH):
        part = idx[s0:s0 + BATCH]
        x = np.stack([np.concatenate([frames[i], frames[i - 1], frames[i - 2]], 2).transpose(2, 0, 1) for i in part])
        hms = m(torch.from_numpy(x).to(dev)).argmax(1).to(torch.uint8).cpu().numpy()  # class map (0-255) per frame
        for i, hm in zip(part, hms):
            xy = tnet.heat_to_xy(hm)
            res.append([round(t0 + i / FR, 4)] + ([round(X0 + xy[0] * 2, 1), round(Y0 + xy[1] * 2, 1)] if xy else [None, None]))
json.dump(res, open(out, "w"))
print("frames", len(res), "detected", sum(r[1] is not None for r in res), "sec/frame", round((time.time() - t) / max(1, len(res)), 2))
