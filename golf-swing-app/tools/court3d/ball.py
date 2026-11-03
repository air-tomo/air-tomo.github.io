"""Ball candidates: 3-frame differencing on colour frames; keep small, yellowish blobs away from big moving blobs (players)."""
import numpy as np, cv2, subprocess, sys, json
W, Hh = 1920, 1080
Y0, Y1 = 0, 800  # rows analysed (court + air above it)
def frames(path, t0, dur):
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t0:.3f}", "-i", path, "-t", f"{dur:.3f}",
           "-vf", f"crop={W}:{Y1-Y0}:0:{Y0}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE); sz = W * (Y1 - Y0) * 3
    while True:
        b = p.stdout.read(sz)
        if len(b) < sz: break
        yield np.frombuffer(b, np.uint8).reshape(Y1 - Y0, W, 3)
def candidates(prev, cur, nxt, thr=16):
    gp, gc, gn = (cv2.cvtColor(x, cv2.COLOR_BGR2GRAY) for x in (prev, cur, nxt))
    m = (np.minimum(cv2.absdiff(gc, gp), cv2.absdiff(gc, gn)) > thr).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    big = np.zeros_like(m)
    for i in range(1, n):
        if st[i][4] > 250: big[lab == i] = 1
    big = cv2.dilate(big, np.ones((25, 25), np.uint8))
    hsv = cv2.cvtColor(cur, cv2.COLOR_BGR2HSV)
    yellow = ((hsv[..., 0] >= 20) & (hsv[..., 0] <= 45) & (hsv[..., 1] >= 60) & (hsv[..., 2] >= 110))
    out = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if not (2 <= area <= 250 and w < 40 and h < 40): continue
        cx, cy = cen[i]
        if big[int(cy), int(cx)]: continue
        reg = lab[y:y + h, x:x + w] == i
        yf = float(yellow[y:y + h, x:x + w][reg].mean())
        if yf < 0.25: continue
        out.append((float(cx), float(cy) + Y0, int(area), round(yf, 2)))
    return out
def detect(path, t0, dur):
    buf, res = [], []
    for f in frames(path, t0, dur):
        buf.append(f)
        if len(buf) == 3: res.append(candidates(*buf)); buf.pop(0)
    return res
if __name__ == "__main__":
    path, t0, dur, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
    res = detect(path, t0, dur)
    json.dump({"t0": t0, "fps": 59.78, "cands": res}, open(out, "w"))
    print("frames", len(res), "cands/frame", round(np.mean([len(r) for r in res]), 1))
