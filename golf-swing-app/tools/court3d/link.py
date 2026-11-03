"""Link ball candidates: seed with triplets of nearly constant velocity, extend greedily with prediction."""
import numpy as np, json, sys
def link(cands, vmin=3.0, vmax=110.0, amax=7.0, gate=10.0, maxmiss=3):
    P = [np.array([(c[0], c[1]) for c in f]) if f else np.zeros((0, 2)) for f in cands]
    used = [np.zeros(len(p), bool) for p in P]
    tracks = []
    n = len(P)
    for k in range(1, n - 1):
        A, B, C = P[k - 1], P[k], P[k + 1]
        if not (len(A) and len(B) and len(C)): continue
        for j, b in enumerate(B):
            if used[k][j]: continue
            da = np.linalg.norm(A - b, axis=1); ia = np.where((da > vmin) & (da < vmax) & ~used[k - 1])[0]
            if not len(ia): continue
            best = None
            for i in ia:
                v = b - A[i]; pred = b + v
                dc = np.linalg.norm(C - pred, axis=1); m = int(np.argmin(dc))
                if dc[m] < amax and not used[k + 1][m] and (best is None or dc[m] < best[0]): best = (dc[m], i, m)
            if best is None: continue
            _, i, m = best
            tr = {(k - 1): A[i], k: b, (k + 1): C[m]}
            used[k - 1][i] = used[k][j] = used[k + 1][m] = True
            # extend forward / backward with constant-acceleration prediction
            for direction in (1, -1):
                ks = sorted(tr); cur = ks[-1] if direction == 1 else ks[0]; miss = 0
                while 0 <= cur + direction < n and miss <= maxmiss:
                    ks = sorted(tr)
                    seq = ks[-3:] if direction == 1 else ks[:3][::-1]
                    p0, p1, p2 = (tr[s] for s in seq)
                    step = (cur + direction - seq[-1]) * direction
                    v = (p2 - p1) / ((seq[-1] - seq[-2]) * direction)
                    a = ((p2 - p1) / ((seq[-1] - seq[-2]) * direction) - (p1 - p0) / ((seq[-2] - seq[-3]) * direction)) / max(1, abs(seq[-1] - seq[-2]))
                    pred = p2 + v * step + 0.5 * a * step * step
                    Q = P[cur + direction]
                    g = gate + 0.15 * np.linalg.norm(v) * step
                    if len(Q):
                        d = np.linalg.norm(Q - pred, axis=1); d[used[cur + direction]] = 1e9; m = int(np.argmin(d))
                        if d[m] < g:
                            tr[cur + direction] = Q[m]; used[cur + direction][m] = True; miss = 0
                        else: miss += 1
                    else: miss += 1
                    cur += direction
            if len(tr) >= 6:
                ks = sorted(tr); tracks.append({"frames": ks, "pts": [tr[q].tolist() for q in ks]})
    return tracks
if __name__ == "__main__":
    d = json.load(open(sys.argv[1]))
    tr = link(d["cands"])
    tr.sort(key=lambda t: -len(t["frames"]))
    for t in tr[:15]:
        f = t["frames"]; p = np.array(t["pts"])
        sp = np.median(np.linalg.norm(np.diff(p, axis=0), axis=1) / np.diff(f))
        print(f"frames {f[0]+1:3d}-{f[-1]+1:3d} n={len(f):3d} speed {sp:5.1f}px/f  from {p[0].round()} to {p[-1].round()}")
    json.dump(dict(d, tracks=tr), open(sys.argv[2], "w"))
