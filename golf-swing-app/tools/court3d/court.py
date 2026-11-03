"""Court calibration from a background frame: cluster Hough segments into lines, label them, fit homography."""
import cv2, numpy as np, sys, json
segs = np.load(sys.argv[1]); bg = cv2.imread(sys.argv[2]); out = sys.argv[3]
# line as (theta, rho) normal form
def norm(x1, y1, x2, y2):
    th = np.arctan2(x2 - x1, -(y2 - y1))  # normal angle
    if th < 0: th += np.pi
    if th >= np.pi: th -= np.pi
    rho = x1 * np.cos(th) + y1 * np.sin(th)
    return th, rho
items = []
for s in segs:
    th, rho = norm(*s); ln = np.hypot(s[2] - s[0], s[3] - s[1]); items.append((th, rho, ln, s))
items.sort(key=lambda x: -x[2])
clusters = []
for th, rho, ln, s in items:
    for c in clusters:
        if abs(c["th"] - th) < 0.02 and abs(c["rho"] - rho) < 8:
            c["segs"].append(s); c["len"] += ln; break
    else:
        clusters.append({"th": th, "rho": rho, "segs": [s], "len": ln})
def fit(c):
    pts = np.concatenate([[s[:2], s[2:]] for s in c["segs"]]).astype(float)
    vx, vy, x0, y0 = cv2.fitLine(pts, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    xs = pts[:, 0]
    return dict(p=(x0, y0), d=(vx, vy), xmin=xs.min(), xmax=xs.max(), len=c["len"])
lines = [fit(c) for c in clusters if c["len"] > 250]
def y_at(l, x): return l["p"][1] + (x - l["p"][0]) * l["d"][1] / l["d"][0]
def x_at(l, y): return l["p"][0] + (y - l["p"][1]) * l["d"][0] / l["d"][1]
horiz = [l for l in lines if abs(l["d"][1] / l["d"][0]) < 0.06]
diag = [l for l in lines if 0.08 < abs(l["d"][1] / l["d"][0]) < 3]
vert = [l for l in lines if abs(l["d"][0]) < 0.2]
horiz.sort(key=lambda l: -y_at(l, 960))
for l in horiz: print("H y@960 %.1f len %d x %d-%d" % (y_at(l, 960), l["len"], l["xmin"], l["xmax"]))
for l in diag: print("D slope %.3f x@600 %.1f len %d" % (l["d"][1] / l["d"][0], x_at(l, 600), l["len"]))
for l in vert: print("V x@540 %.1f len %d" % (x_at(l, 540), l["len"]))
json.dump({"h": [[float(v) for v in (*l["p"], *l["d"])] for l in horiz], "d": [[float(v) for v in (*l["p"], *l["d"])] for l in diag],
           "v": [[float(v) for v in (*l["p"], *l["d"])] for l in vert]}, open(out, "w"))
