"""Points without court calibration (works from any camera angle): hit sounds + ball tracks.

  python3 cutfree.py TRACKS.json HITS.json OUT.json [--width 1920]

TRACKS.json: tracks from track.py ([[[t, x, y], ...], ...]); HITS.json: hit sound times [t, ...].
A shot = a hit sound where a fast ball track starts (within -0.1 .. +0.3 s) or turns (velocity changes direction).
A point = shots no more than 3.6 s apart; it starts 1.5 s before the first shot (the toss) and ends 2 s after the last.
Single shots are kept only if the ball flew far (a serve that was not returned, a winner), else dropped (a knock-up,
the server bouncing the ball).
Prints and writes [[start, end, number of shots], ...].
"""
import argparse, json
import numpy as np


def fast(tr, W):
    st = [np.hypot(b[1] - a[1], b[2] - a[2]) / max(1e-3, b[0] - a[0]) for a, b in zip(tr, tr[1:])]
    return np.median(st) > 0.09 * W           # px per second (1920 wide: > 170 px/s, about 6 px a frame)


def turns(tr, h, win=0.12):
    """the track's direction before and after h differs by > 60 degrees"""
    pre = [q for q in tr if h - 0.25 <= q[0] <= h - 0.02]; post = [q for q in tr if h + 0.02 <= q[0] <= h + 0.25]
    if len(pre) < 3 or len(post) < 3: return False
    v1 = np.subtract(pre[-1][1:], pre[0][1:]); v2 = np.subtract(post[-1][1:], post[0][1:])
    n1, n2 = np.hypot(*v1), np.hypot(*v2)
    if n1 < 5 or n2 < 5: return False
    return float(np.dot(v1, v2) / (n1 * n2)) < 0.5


def shots(tracks, hits, W):
    F = [tr for tr in tracks if fast(tr, W)]
    out = []
    for h in hits:
        for tr in F:
            starts = -0.1 <= tr[0][0] - h <= 0.3
            if starts or (tr[0][0] < h < tr[-1][0] and turns(tr, h)):
                after = [q for q in tr if 0 <= q[0] - h <= 0.8]
                reach = np.hypot(after[-1][1] - after[0][1], after[-1][2] - after[0][2]) if len(after) >= 2 else 0
                out.append((h, float(reach))); break
    return out


def points(sh, W, gap=3.6, pre=1.5, post=2.0):
    ch, cur = [], []
    for s in sh:
        if cur and s[0] - cur[-1][0] > gap: ch.append(cur); cur = []
        cur.append(s)
    if cur: ch.append(cur)
    return [[round(c[0][0] - pre, 2), round(c[-1][0] + post, 2), len(c)] for c in ch if len(c) >= 2 or c[0][1] > 0.25 * W]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("tracks"); ap.add_argument("hits"); ap.add_argument("out"); ap.add_argument("--width", type=int, default=1920)
    a = ap.parse_args()
    P = points(shots(json.load(open(a.tracks)), json.load(open(a.hits)), a.width), a.width)
    json.dump(P, open(a.out, "w")); print(P)
