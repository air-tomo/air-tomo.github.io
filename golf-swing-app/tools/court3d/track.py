"""Ball candidates (several per frame: every ball in view) -> smooth tracks. No court calibration needed.

  python3 track.py CAND.json OUT.json

CAND.json: [[t, [[score, x, y], ...]], ...] from small_run.py. OUT.json: [[[t, x, y], ...], ...] tracks of moving balls,
longest first. Constant-velocity prediction, greedy nearest assignment inside a gate that grows with the speed; a track
ends after 4 frames without a candidate. Kept: >= 6 points that move (> 40 px end to end).
"""
import json, sys
import numpy as np


def tracks(cand, gate=40.0, miss=4, min_len=6, min_move=40.0):
    live, done = [], []
    for fi, (t, cs) in enumerate(cand):
        used = set(); pred = []
        for tr in live:
            (i1, t1, x1, y1) = tr["pts"][-1]
            if len(tr["pts"]) >= 2:
                (i0, t0, x0, y0) = tr["pts"][-2]; k = (fi - i1) / max(1, i1 - i0)
                px, py = x1 + (x1 - x0) * k, y1 + (y1 - y0) * k; sp = np.hypot(x1 - x0, y1 - y0) / max(1, i1 - i0)
            else:
                px, py, sp = x1, y1, 0.0
            pred.append((px, py, gate + 1.5 * sp * (fi - i1)))
        pairs = sorted((np.hypot(c[1] - p[0], c[2] - p[1]), j, n) for j, p in enumerate(pred) for n, c in enumerate(cs))
        got = set()
        for d, j, n in pairs:
            if d > pred[j][2] or j in got or n in used: continue
            live[j]["pts"].append((fi, t, cs[n][1], cs[n][2])); got.add(j); used.add(n)
        for n, c in enumerate(cs):
            if n not in used: live.append({"pts": [(fi, t, c[1], c[2])]})
        keep = []
        for tr in live:
            (keep if fi - tr["pts"][-1][0] <= miss else done).append(tr)
        live = keep
    done += live
    out = []
    for tr in done:
        p = tr["pts"]
        if len(p) >= min_len and np.hypot(p[-1][2] - p[0][2], p[-1][3] - p[0][3]) > min_move:
            out.append([[round(q[1], 4), float(q[2]), float(q[3])] for q in p])
    return sorted(out, key=len, reverse=True)


if __name__ == "__main__":
    T = tracks(json.load(open(sys.argv[1])))
    json.dump(T, open(sys.argv[2], "w"))
    print(len(T), "tracks; longest", [len(t) for t in T[:8]])
