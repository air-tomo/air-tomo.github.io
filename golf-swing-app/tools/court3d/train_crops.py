"""Train the small ball detector (train_small.SmallNet) on full-resolution crops from make_crops.py. Runs on a CPU too.

  python3 train_crops.py --train a.npz b.npz --val c.npz --hours 5 --out ball_small

Random 160 x 256 windows of the 192 x 288 crops (the ball kept inside), brightness/contrast/flip augmentation, the same
weighted loss as train_small.py. Saves the best model on the validation crops (F1 with a 4 px tolerance) as OUT.pt and
OUT.onnx (input "frames" (1, 9, H, W) float 0-1 BGR newest first, any H, W multiple of 8; output "heat" (1, H, W) 0-1).
"""
import argparse, time
import numpy as np, torch, torch.nn as nn
from train_small import SmallNet, loss_fn, peak

ap = argparse.ArgumentParser()
ap.add_argument("--train", nargs="+", required=True); ap.add_argument("--val", nargs="+", required=True)
ap.add_argument("--hours", type=float, default=5); ap.add_argument("--batch", type=int, default=16)
ap.add_argument("--out", default="ball_small"); ap.add_argument("--init", default="")
a = ap.parse_args()
torch.set_num_threads(4); torch.manual_seed(0); rng = np.random.default_rng(0)
WH, WW, SIG = 160, 256, 2.5


def load(paths):
    X, Y = [], []
    for p in paths:
        d = np.load(p); X.append(d["X"]); Y.append(d["Y"])
    return np.concatenate(X), np.concatenate(Y)


Xt, Yt = load(a.train); Xv, Yv = load(a.val)
print("train", len(Xt), "(ball %d)" % (Yt[:, 0] >= 0).sum(), "val", len(Xv), "(ball %d)" % (Yv[:, 0] >= 0).sum(), flush=True)
yy, xx = np.mgrid[0:WH, 0:WW].astype(np.float32)


def window(x, y, train):
    """a WH x WW window of one crop with the ball inside; returns (input 9xHxW float, target HxW)"""
    H, W = x.shape[:2]
    if y[0] >= 0:
        lo_x, hi_x = int(max(0, y[0] - WW + 8)), int(min(W - WW, y[0] - 8)); lo_y, hi_y = int(max(0, y[1] - WH + 8)), int(min(H - WH, y[1] - 8))
        ox = rng.integers(lo_x, hi_x + 1) if train and hi_x >= lo_x else int(np.clip(y[0] - WW / 2, 0, W - WW))
        oy = rng.integers(lo_y, hi_y + 1) if train and hi_y >= lo_y else int(np.clip(y[1] - WH / 2, 0, H - WH))
    else:
        ox = rng.integers(0, W - WW + 1) if train else (W - WW) // 2; oy = rng.integers(0, H - WH + 1) if train else (H - WH) // 2
    w = x[oy:oy + WH, ox:ox + WW].astype(np.float32) / 255
    t = np.zeros((WH, WW), np.float32)
    if y[0] >= 0: t = np.exp(-((xx - (y[0] - ox)) ** 2 + (yy - (y[1] - oy)) ** 2) / (2 * SIG ** 2))
    if train:
        if rng.random() < 0.5: w = w[:, ::-1]; t = t[:, ::-1]
        w = np.clip(w * rng.uniform(0.6, 1.4) + rng.uniform(-0.15, 0.15), 0, 1)
    return np.ascontiguousarray(w.transpose(2, 0, 1)), np.ascontiguousarray(t)


V = [window(Xv[i], Yv[i], False) for i in range(len(Xv))]
Vx = torch.from_numpy(np.stack([v[0] for v in V])); Vt = np.stack([v[1] for v in V])


def evaluate(m, tol=4.0):
    m.eval(); tp = fp = fn = 0
    with torch.no_grad():
        pr = np.concatenate([torch.sigmoid(m(Vx[i:i + 32])).numpy() for i in range(0, len(Vx), 32)])
    for p, t in zip(pr, Vt):
        g = np.unravel_index(np.argmax(t), t.shape) if t.max() > 0.5 else None; q = peak(p)
        if g is None: fp += q is not None
        elif q is None: fn += 1
        elif np.hypot(q[0] - g[1], q[1] - g[0]) <= tol: tp += 1
        else: fp += 1; fn += 1
    m.train(); P = tp / max(1, tp + fp); R = tp / max(1, tp + fn)
    return {"precision": round(P, 3), "recall": round(R, 3), "f1": round(2 * P * R / max(1e-9, P + R), 3)}


m = SmallNet()
if a.init: m.load_state_dict(torch.load(a.init, map_location="cpu", weights_only=True))
opt = torch.optim.AdamW(m.parameters(), 2e-3, weight_decay=1e-4)
end = time.time() + a.hours * 3600; step = 0; best = -1; t_last = time.time(); run = 0
print("start", evaluate(m), flush=True)
while time.time() < end:
    idx = rng.integers(0, len(Xt), a.batch)
    B = [window(Xt[i], Yt[i], True) for i in idx]
    x = torch.from_numpy(np.stack([b[0] for b in B])); t = torch.from_numpy(np.stack([b[1] for b in B]))
    lr = 2e-3 * max(0.05, 1 - (time.time() - (end - a.hours * 3600)) / (a.hours * 3600))
    for g in opt.param_groups: g["lr"] = lr
    l = loss_fn(m(x), t); opt.zero_grad(); l.backward(); opt.step(); step += 1; run = 0.98 * run + 0.02 * l.item()
    if time.time() - t_last > 900 or time.time() >= end:
        r = evaluate(m); t_last = time.time()
        print("step", step, "loss %.4f" % run, "lr %.5f" % lr, r, flush=True)
        if r["f1"] > best: best = r["f1"]; torch.save(m.state_dict(), a.out + ".pt")
m.load_state_dict(torch.load(a.out + ".pt", map_location="cpu", weights_only=True)); m.eval()


class Export(nn.Module):
    def __init__(s, m): super().__init__(); s.m = m
    def forward(s, x): return torch.sigmoid(s.m(x))          # SmallNet gives (N, H, W) logits


torch.onnx.export(Export(m), torch.rand(1, 9, 256, 640), a.out + ".onnx", input_names=["frames"], output_names=["heat"],
                  opset_version=17, dynamic_axes={"frames": {2: "h", 3: "w"}, "heat": {1: "h", 2: "w"}}, dynamo=False)
print("best f1", best, "->", a.out + ".onnx", flush=True)
