"""Hand-held (or moving) camera: how each frame maps to the frame used for the court calibration.

  python3 stabilize.py VIDEO REF_TIME match.json out.json [STEP]

For each clip in match.json, every STEP seconds (default 0.2): ORB features on the lower part of the frame (court, fences),
matched to the reference frame, homography with RANSAC (players move; the court does not). out.json:
  {"ref": REF_TIME, "clips": {"k": [[t, h11..h33 (frame -> reference)], ...]}}
tn_post.py (STAB=out.json) moves the ball positions into the reference frame before using the calibration;
render.py (STAB=out.json) moves them back to draw on the moving picture.
"""
import json, subprocess, sys
import numpy as np, cv2

video, ref_t, mj, out = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
step = float(sys.argv[5]) if len(sys.argv) > 5 else 0.2
W, H = 1920, 1080
orb = cv2.ORB_create(4000)
mask = np.zeros((H, W), np.uint8); mask[H * 5 // 12:] = 255          # court and fences; the sky has nothing to hold on to
bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)


def frames(t0, dur, fps):
    p = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "%.3f" % t0, "-i", video, "-t", "%.3f" % dur,
                        "-vf", "fps=%g,format=gray" % fps, "-f", "rawvideo", "-"], capture_output=True, check=True)
    b = p.stdout; n = len(b) // (W * H)
    return [np.frombuffer(b[i * W * H:(i + 1) * W * H], np.uint8).reshape(H, W) for i in range(n)]


ref = frames(ref_t, 0.05, 30)[0]
kr, dr = orb.detectAndCompute(ref, mask)
M = json.load(open(mj)); res = {}
I = np.eye(3).ravel().tolist()
for k, c in enumerate(M["clips"]):
    fs = frames(c["start"], c["end"] - c["start"] + step, 1 / step)
    rows, last = [], I
    for i, g in enumerate(fs):
        t = c["start"] + i * step
        kp, d = orb.detectAndCompute(g, mask)
        Hm = None
        if d is not None and len(kp) > 50:
            m = sorted(bf.match(d, dr), key=lambda x: x.distance)[:800]
            if len(m) > 40:
                A = np.float32([kp[x.queryIdx].pt for x in m]); B = np.float32([kr[x.trainIdx].pt for x in m])
                Hm, inl = cv2.findHomography(A, B, cv2.RANSAC, 3.0)
                if Hm is None or inl.sum() < 60: Hm = None
        h = (Hm / Hm[2, 2]).ravel().tolist() if Hm is not None else last      # lost: keep the last one
        rows.append([round(t, 3)] + [round(x, 7) for x in h]); last = h
    res[str(k)] = rows
    sh = [np.linalg.norm(np.array(r[1:]).reshape(3, 3) @ [960, 700, 1] / (np.array(r[1:]).reshape(3, 3) @ [960, 700, 1])[2] - [960, 700, 1]) for r in rows]
    print("clip", k, len(rows), "frames, shift px: max %.0f" % max(sh), flush=True)
json.dump({"ref": ref_t, "clips": res}, open(out, "w"))
