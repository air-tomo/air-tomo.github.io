"""Camera from points picked on one frame (for a new video). Writes the cam.npz that tn_post.py / render.py read (K, R, t, H).

  python3 calib_points.py points.json W H out_cam.npz

points.json: {"ground": [[[x, y], [u, v]], ...],  "3d": [[[x, y, z], [u, v]], ...]}
  x = across the court (m, centre 0), y = depth from the near baseline (m), z = height (m); u, v = pixels.
  At least 4 ground points (e.g. where the near baseline and the service line cross the sidelines). Net-post tops
  (+-6.399, 11.885, 1.07) help a lot when the camera is low and the far court is squashed.
"""
import json, sys
import numpy as np, cv2
from scipy.optimize import least_squares

pts = json.load(open(sys.argv[1])); W, Hh = int(sys.argv[2]), int(sys.argv[3]); out = sys.argv[4]
G = pts["ground"]; T = pts.get("3d", [])
Hm, _ = cv2.findHomography(np.array([g[0] for g in G], float), np.array([g[1] for g in G], float))
cx, cy = W / 2, Hh / 2


def camera_from_H(H):
    def B(f): return np.linalg.inv(np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])) @ H
    def cost(f):
        b = B(f); return (b[:, 0] @ b[:, 1]) ** 2 / (np.linalg.norm(b[:, 0]) * np.linalg.norm(b[:, 1])) ** 2 + (np.linalg.norm(b[:, 0]) / np.linalg.norm(b[:, 1]) - 1) ** 2
    fs = np.arange(300, 6000, 2.0); f = fs[np.argmin([cost(x) for x in fs])]
    b = B(f); s = 1 / np.linalg.norm(b[:, 0]); r1, r2, t = b[:, 0] * s, b[:, 1] * s, b[:, 2] * s
    if t[2] < 0: r1, r2, t = -r1, -r2, -t
    R = np.stack([r1, r2, np.cross(r1, r2)], 1); U, _, Vt = np.linalg.svd(R)
    return f, U @ Vt, t


f0, R0, t0 = camera_from_H(Hm)
X = np.array([list(g[0]) + [0.0] for g in G] + [t_[0] for t_ in T], float)
U = np.array([g[1] for g in G] + [t_[1] for t_ in T], float)


def proj(q, X):
    K = np.array([[q[0], 0, cx], [0, q[0], cy], [0, 0, 1]]); R, _ = cv2.Rodrigues(q[1:4])
    p = (K @ (R @ X.T + q[4:7, None])).T; return p[:, :2] / p[:, 2:]


rv0, _ = cv2.Rodrigues(R0)
sol = least_squares(lambda q: (proj(q, X) - U).ravel(), np.concatenate([[f0], rv0.ravel(), t0]), loss="soft_l1")
q = sol.x; f = q[0]; R, _ = cv2.Rodrigues(q[1:4]); t = q[4:7]
K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])
H = K @ np.stack([R[:, 0], R[:, 1], t], 1); H /= H[2, 2]
err = np.hypot(*(proj(q, X) - U).T)
C = -R.T @ t
print("f %.0f (from H %.0f)  camera x %.2f y %.2f height %.2f m  error px: max %.1f median %.1f" % (f, f0, C[0], C[1], C[2], err.max(), np.median(err)))
np.savez(out, K=K, R=R, t=t, H=H)
