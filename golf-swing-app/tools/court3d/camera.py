"""Recover K, R, t from the ground homography (principal point at image centre, square pixels)."""
import numpy as np, cv2, sys
Hm = np.load(sys.argv[1]); w, h = 1920, 1080
cx, cy = w / 2, h / 2
def decompose(f):
    K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])
    B = np.linalg.inv(K) @ Hm
    s = 1 / np.linalg.norm(B[:, 0])
    r1, r2, t = B[:, 0] * s, B[:, 1] * s, B[:, 2] * s
    return K, r1, r2, t
# focal from orthogonality + equal norm of r1, r2
def cost(f):
    K, r1, r2, t = decompose(f)
    B = np.linalg.inv(K) @ Hm
    return (B[:, 0] @ B[:, 1]) ** 2 / (np.linalg.norm(B[:, 0]) * np.linalg.norm(B[:, 1])) ** 2 + (np.linalg.norm(B[:, 0]) / np.linalg.norm(B[:, 1]) - 1) ** 2
fs = np.arange(600, 4000, 2.0); f = fs[np.argmin([cost(x) for x in fs])]
K, r1, r2, t = decompose(f)
r3 = np.cross(r1, r2)
if t[2] < 0: r1, r2, t = -r1, -r2, -t; r3 = np.cross(r1, r2)
R = np.stack([r1, r2, r3], 1); U, _, Vt = np.linalg.svd(R); R = U @ Vt
C = -R.T @ t
print("focal px %.0f (hFOV %.1f deg)" % (f, 2 * np.degrees(np.arctan(cx / f))))
print("camera position (x lateral, y from near baseline, z) m:", np.round(C, 2))
def proj(X):
    p = K @ (R @ np.asarray(X, float) + t); return p[:2] / p[2]
# z axis sign: world z must point up (camera above ground => C[2] > 0)
print("net top centre (0,11.885,0.914) ->", np.round(proj([0, 11.885, 0.914]), 1), " net post top (±6.4,11.885,1.07) ->", np.round(proj([-6.4, 11.885, 1.07]), 1), np.round(proj([6.4, 11.885, 1.07]), 1))
np.savez(sys.argv[2], K=K, R=R, t=t, C=C)
