import json, numpy as np, cv2, sys
L = json.load(open(sys.argv[1])); bg = cv2.imread(sys.argv[2]); out = sys.argv[3]
def y_at(l, x): return l[1] + (x - l[0]) * l[3] / l[2]
def inter(a, b):
    A = np.array([[a[2], -b[2]], [a[3], -b[3]]]); r = np.array([b[0] - a[0], b[1] - a[1]])
    t = np.linalg.solve(A, r); return np.array([a[0] + t[0] * a[2], a[1] + t[0] * a[3]])
H = [l for l in L["h"] if 495 < y_at(l, 960) < 700]
H.sort(key=lambda l: -y_at(l, 960))
base, serv = H[0], H[1]
D = L["d"]
left = sorted([l for l in D if l[3] / l[2] < 0], key=lambda l: -abs(l[3] / l[2]))   # steeper = inner (singles)
right = sorted([l for l in D if l[3] / l[2] > 0], key=lambda l: -abs(l[3] / l[2]))
sL, dL, sR, dR = left[0], left[1], right[0], right[1]
img = np.array([inter(base, dL), inter(base, sL), inter(base, sR), inter(base, dR), inter(serv, sL), inter(serv, sR)])
W = np.array([[-5.485, 0], [-4.115, 0], [4.115, 0], [5.485, 0], [-4.115, 5.485], [4.115, 5.485]])
Hm, _ = cv2.findHomography(W, img)
res = cv2.perspectiveTransform(W[None], Hm)[0] - img
print("reprojection err px", np.round(np.abs(res).max(1), 2))
def P(x, y): return cv2.perspectiveTransform(np.array([[[x, y]]], float), Hm)[0, 0]
for name, y in [("net", 11.885), ("far service", 18.285), ("far baseline", 23.77)]:
    print(name, "row at centre %.1f" % P(0, y)[1], " singles x %.0f..%.0f" % (P(-4.115, y)[0], P(4.115, y)[0]))
print("horizontal lines found (y@960):", [round(y_at(l, 960), 1) for l in H])
vis = bg.copy()
def seg(a, b, c=(0, 255, 255)):
    p, q = P(*a), P(*b); cv2.line(vis, tuple(int(v) for v in p), tuple(int(v) for v in q), c, 2)
for x in (-5.485, -4.115, 4.115, 5.485): seg((x, 0), (x, 23.77))
for y in (0, 23.77): seg((-5.485, y), (5.485, y))
for y in (5.485, 18.285): seg((-4.115, y), (4.115, y))
seg((0, 5.485), (0, 18.285)); seg((-6.4, 11.885), (6.4, 11.885), (0, 0, 255))
cv2.imwrite(out, vis[440:720]); np.save(out + ".H.npy", Hm)
