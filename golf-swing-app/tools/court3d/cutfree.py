"""Points without court calibration (works from any camera angle): hit sounds + ball tracks.

  python3 cutfree.py TRACKS.json HITS.json OUT.json [--width 1920]

TRACKS.json: tracks from track.py ([[[t, x, y], ...], ...]); HITS.json: hit sound times [t, ...].
A shot = a hit sound where a fast ball track starts (within -0.1 .. +0.3 s) or turns (velocity changes direction).
A point = shots no more than 3.6 s apart, grown over fast ball flights just before / after (sound not heard, ball flying away);
it starts 2 s before (the toss) and ends 2.5 s after.
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


def points(sh, W, gap=3.6, pre=2.0, post=2.5, tracks=None, link=2.5):
    """chains of shots; with tracks, a chain grows over the fast ball flights right before / after it (a serve whose sound
    was not heard, the last shot flying away), so the point is not cut short"""
    ch, cur = [], []
    for s in sh:
        if cur and s[0] - cur[-1][0] > gap: ch.append(cur); cur = []
        cur.append(s)
    if cur: ch.append(cur)
    F = sorted([(t[0][0], t[-1][0]) for t in (tracks or []) if fast(t, W)])
    out = []
    for c in ch:
        if not (len(c) >= 2 or c[0][1] > 0.25 * W): continue
        a, b = c[0][0], c[-1][0]
        grown = True
        while grown:
            grown = False
            for s0, s1 in F:
                if s1 >= a - link and s0 < a: a = s0; grown = True
                if s0 <= b + link and s1 > b: b = s1; grown = True
        out.append([round(a - pre, 2), round(b + post, 2), len(c)])
    merged = []
    for p in out:
        if merged and p[0] <= merged[-1][1]: merged[-1] = [merged[-1][0], max(merged[-1][1], p[1]), merged[-1][2] + p[2]]
        else: merged.append(p)
    return merged


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("tracks"); ap.add_argument("hits"); ap.add_argument("out"); ap.add_argument("--width", type=int, default=1920)
    a = ap.parse_args()
    T = json.load(open(a.tracks))
    P = points(shots(T, json.load(open(a.hits)), a.width), a.width, tracks=T)
    json.dump(P, open(a.out, "w")); print(P)
