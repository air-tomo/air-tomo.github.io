"""Run TrackNet on a time range of a 1920x1080 video. Frames: every Nth to get ~30 fps; crop 1280x720 around the court, resize to 640x360."""
import sys, json, subprocess, time, numpy as np, torch, cv2, tnet
video, t0, dur, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
X0, Y0, CW, CH = 320, 120, 1280, 720
dev = "mps" if torch.backends.mps.is_available() else "cpu"
m = tnet.load(sys.argv[5] if len(sys.argv) > 5 else "model_best.pt").to(dev); torch.set_num_threads(4)
cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t0:.3f}", "-i", video, "-t", f"{dur:.3f}", "-vf",
       f"fps=30,crop={CW}:{CH}:{X0}:{Y0},scale=640:360", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
p = subprocess.Popen(cmd, stdout=subprocess.PIPE); sz = 640 * 360 * 3
frames = []
while True:
    b = p.stdout.read(sz)
    if len(b) < sz: break
    frames.append(np.frombuffer(b, np.uint8).reshape(360, 640, 3).astype(np.float32) / 255)
res = []; t = time.time()
with torch.no_grad():
    for i in range(2, len(frames)):
        x = np.concatenate([frames[i], frames[i - 1], frames[i - 2]], 2).transpose(2, 0, 1)[None]
        hm = m(torch.from_numpy(x).to(dev))[0].cpu().numpy()
        xy = tnet.heat_to_xy(hm)
        res.append([round(t0 + i / 30, 4)] + ([round(X0 + xy[0] * 2, 1), round(Y0 + xy[1] * 2, 1)] if xy else [None, None]))
json.dump(res, open(out, "w"))
print("frames", len(res), "detected", sum(r[1] is not None for r in res), "sec/frame", round((time.time() - t) / max(1, len(res)), 2))
