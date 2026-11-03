"""Refine camera (f, R, t) with 3D points: near-court line intersections, net posts (base/top), net centre, far baseline row."""
import numpy as np, cv2, json, sys
from scipy.optimize import least_squares
cam = np.load(sys.argv[1]); Hm = np.load(sys.argv[2]); out = sys.argv[3]
def P(x, y): return cv2.perspectiveTransform(np.array([[[x, y]]], float), Hm)[0, 0]
pts3, pts2, wts = [], [], []
def add(X, u, w=1.0): pts3.append(X); pts2.append(u); wts.append(w)
for X in [(-5.485, 0), (-4.115, 0), (4.115, 0), (5.485, 0)]: add((*X, 0), P(*X), 0.3)      # extrapolated corners (out of frame)
for X in [(-4.115, 5.485), (4.115, 5.485), (0, 5.485)]: add((*X, 0), P(*X), 1.0)
NP = 6.399
add((-NP, 11.885, 0), (335, 520)); add((NP, 11.885, 0), (1494, 531))
add((-NP, 11.885, 1.07), (335, 423)); add((NP, 11.885, 1.07), (1495, 438))
add((0, 11.885, 0.914), (922, 442))
pts3 = np.array(pts3, float); pts2 = np.array(pts2, float); wts = np.array(wts)
far_row = float(sys.argv[4]) if len(sys.argv) > 4 else 505.0
FBW = float(sys.argv[5]) if len(sys.argv) > 5 else 1.0
def model(q):
    f, rv, tv = q[0], q[1:4], q[4:7]
    K = np.array([[f, 0, 960], [0, f, 540], [0, 0, 1.0]]); R, _ = cv2.Rodrigues(rv)
    return K, R, tv
def proj(q, X):
    K, R, tv = model(q); p = (K @ (R @ np.asarray(X).T + tv[:, None])).T; return p[:, :2] / p[:, 2:]
def res(q):
    r = ((proj(q, pts3) - pts2) * wts[:, None]).ravel()
    # far baseline: its row at the court centre
    fb = proj(q, np.array([[0, 23.77, 0]]))[0, 1] - far_row
    return np.concatenate([r, [fb * FBW]])
rv0, _ = cv2.Rodrigues(cam["R"])
q0 = np.concatenate([[cam["K"][0, 0]], rv0.ravel(), cam["t"]])
sol = least_squares(res, q0)
K, R, tv = model(sol.x); C = -R.T @ tv
r = proj(sol.x, pts3) - pts2
print("focal %.0f  camera %s m" % (sol.x[0], np.round(C, 2)))
print("residuals px:", np.round(np.linalg.norm(r, axis=1), 1))
for name, y in [("near service", 5.485), ("net", 11.885), ("far service", 18.285), ("far baseline", 23.77)]:
    print(name, "row %.1f" % proj(sol.x, [[0, y, 0]])[0, 1])
Hn = K @ np.stack([R[:, 0], R[:, 1], tv], 1); Hn /= Hn[2, 2]
np.savez(out, K=K, R=R, t=tv, C=C, H=Hn)
