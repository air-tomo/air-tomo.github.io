import json, numpy as np, cv2, sys
d = json.load(open(sys.argv[1])); bg = cv2.imread(sys.argv[2]); out = sys.argv[3]; y0, y1 = 250, 760
vis = (bg * 0.6).astype(np.uint8)
rng = np.random.default_rng(1)
for i, t in enumerate(d["tracks"]):
    if len(t["frames"]) < int(sys.argv[4]) if len(sys.argv) > 4 else 8: continue
    c = tuple(int(v) for v in rng.integers(80, 255, 3))
    p = np.array(t["pts"]).astype(int)
    for a, b in zip(p, p[1:]): cv2.line(vis, tuple(a), tuple(b), c, 2)
    cv2.putText(vis, str(i), tuple(p[0]), cv2.FONT_HERSHEY_SIMPLEX, 0.6, c, 2)
cv2.imwrite(out, vis[y0:y1])
