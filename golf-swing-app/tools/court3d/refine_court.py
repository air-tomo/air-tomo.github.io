"""Court homography from 4 rough corners (doubles: near-left, near-right, far-right, far-left), refined by fitting every court line.
World: x lateral (m, centre 0), y depth from the near baseline (m)."""
import numpy as np, cv2
DX, SX = 5.485, 4.115
LINES = {  # name: (p, q) endpoints in world coords
    "base_n": ((-DX, 0), (DX, 0)), "base_f": ((-DX, 23.77), (DX, 23.77)),
    "dside_l": ((-DX, 0), (-DX, 23.77)), "dside_r": ((DX, 0), (DX, 23.77)),
    "sside_l": ((-SX, 0), (-SX, 23.77)), "sside_r": ((SX, 0), (SX, 23.77)),
    "serv_n": ((-SX, 5.485), (SX, 5.485)), "serv_f": ((-SX, 18.285), (SX, 18.285)),
    "centre": ((0, 5.485), (0, 18.285)),
}
def ridge(img):
    g = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    r = np.zeros_like(g)
    for dy, dx in [(1, 0), (0, 1), (1, 1), (1, -1)]:
        for d in (2, 4):
            a = np.roll(np.roll(g, dy * d, 0), dx * d, 1); b = np.roll(np.roll(g, -dy * d, 0), -dx * d, 1)
            r = np.maximum(r, np.minimum(g - a, g - b))
    return r
def P(H, pts): return cv2.perspectiveTransform(np.asarray(pts, float).reshape(-1, 1, 2), H).reshape(-1, 2)
def fit(img, corners, iters=3, band=6, thr=12):
    W = np.array([(-DX, 0), (DX, 0), (DX, 23.77), (-DX, 23.77)], float)
    H, _ = cv2.findHomography(W, np.asarray(corners, float))
    R = ridge(img); h, w = R.shape
    ys, xs = np.nonzero(R > thr); pts = np.stack([xs, ys], 1).astype(float); val = R[ys, xs]
    for _ in range(iters):
        fitted = {}
        for name, (p, q) in LINES.items():
            a, b = P(H, [p, q]); d = b - a; L = np.linalg.norm(d); d /= L; n = np.array([-d[1], d[0]])
            rel = pts - a; along = rel @ d; off = rel @ n
            m = (np.abs(off) < band) & (along > 0.03 * L) & (along < 0.97 * L)
            if m.sum() < 30: continue
            sel = pts[m]; wts = val[m]
            c = np.average(sel, axis=0, weights=wts); cov = np.cov((sel - c).T, aweights=wts)
            ev, evec = np.linalg.eigh(cov); dirv = evec[:, 1]
            fitted[name] = (c, dirv)
        # intersections of fitted lines that meet in the model
        wpts, ipts = [], []
        for n1, (p1, q1) in LINES.items():
            for n2, (p2, q2) in LINES.items():
                if n1 >= n2 or n1 not in fitted or n2 not in fitted: continue
                A = np.array([[q1[0] - p1[0], -(q2[0] - p2[0])], [q1[1] - p1[1], -(q2[1] - p2[1])]], float)
                if abs(np.linalg.det(A)) < 1e-9: continue
                s = np.linalg.solve(A, np.array([p2[0] - p1[0], p2[1] - p1[1]], float))
                if not (-0.01 <= s[0] <= 1.01): continue
                X = np.array(p1) + s[0] * (np.array(q1) - np.array(p1))
                (c1, d1), (c2, d2) = fitted[n1], fitted[n2]
                M = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]])
                if abs(np.linalg.det(M)) < 1e-6: continue
                t = np.linalg.solve(M, c2 - c1); wpts.append(X); ipts.append(c1 + t[0] * d1)
        if len(wpts) >= 4: H, _ = cv2.findHomography(np.array(wpts), np.array(ipts))
    err = np.linalg.norm(P(H, wpts) - np.array(ipts), axis=1) if len(wpts) else []
    return H, len(wpts), float(np.max(err)) if len(err) else None
def camera_from_H(H, w, h):
    cx, cy = w / 2, h / 2
    def B(f): return np.linalg.inv(np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])) @ H
    def cost(f):
        b = B(f); return (b[:, 0] @ b[:, 1]) ** 2 / (np.linalg.norm(b[:, 0]) * np.linalg.norm(b[:, 1])) ** 2 + (np.linalg.norm(b[:, 0]) / np.linalg.norm(b[:, 1]) - 1) ** 2
    fs = np.arange(300, 6000, 2.0); f = fs[np.argmin([cost(x) for x in fs])]
    b = B(f); s = 1 / np.linalg.norm(b[:, 0]); r1, r2, t = b[:, 0] * s, b[:, 1] * s, b[:, 2] * s
    if t[2] < 0: r1, r2, t = -r1, -r2, -t
    R = np.stack([r1, r2, np.cross(r1, r2)], 1); U, _, Vt = np.linalg.svd(R); R = U @ Vt
    K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])
    return K, R, t, -R.T @ t
