import cv2, numpy as np, sys
bg = cv2.imread(sys.argv[1]); out = sys.argv[2]
g = cv2.GaussianBlur(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
sat = cv2.cvtColor(bg, cv2.COLOR_BGR2HSV)[..., 1]
def shift(a, dy, dx):
    return np.roll(np.roll(a, dy, 0), dx, 1)
# thin bright ridge: brighter than both sides at distance d, in any of 4 directions
resp = np.zeros_like(g)
for dy, dx in [(1, 0), (0, 1), (1, 1), (1, -1)]:
    for d in (3, 5):
        r = np.minimum(g - shift(g, dy * d, dx * d), g - shift(g, -dy * d, -dx * d))
        resp = np.maximum(resp, r)
mask = ((resp > 22) & (sat < 110)).astype(np.uint8) * 255
mask[:495] = 0
cv2.imwrite(out + "_mask.png", mask[440:720])
L = np.asarray(cv2.HoughLinesP(mask, 1, np.pi / 1440, 120, minLineLength=150, maxLineGap=20)).reshape(-1, 4)
vis = bg.copy()
for x1, y1, x2, y2 in L:
    cv2.line(vis, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 1)
cv2.imwrite(out + "_lines.png", vis[440:720])
np.save(out + "_segs.npy", L)
print(len(L))
