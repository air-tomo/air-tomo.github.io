"""Train a small ball detector (about 1/16 of TrackNet's computation) for phones.

Same idea as TrackNet: 3 consecutive frames (9 channels) in, a heat map of the ball out. Differences:
- channels 16-32-64-128 instead of 64-128-256-512, and a 1-channel heat map (sigmoid) instead of 256 classes
- trained on the TrackNet tennis dataset (Label.csv per clip) and, optionally, our own videos with pseudo labels
  (tn_*.json from tn_run.py / tn_roi.py)

Usage (Colab GPU):
  python3 train_small.py --tracknet /content/Dataset --extra /content/extra --epochs 30 --out /content/ball_small
Writes ball_small.pt and ball_small.onnx (input "frames" (1, 9, H, W) float 0-1 BGR newest first; output "heat" (1, H, W) float 0-1).
"""
import argparse, csv, glob, json, os, random, subprocess
import numpy as np, cv2, torch, torch.nn as nn, torch.nn.functional as F

W, H = 640, 360


def block(i, o):
    return nn.Sequential(nn.Conv2d(i, o, 3, padding=1, bias=False), nn.BatchNorm2d(o), nn.ReLU(inplace=True))


class SmallNet(nn.Module):
    def __init__(self, c=(16, 32, 64, 128)):
        super().__init__()
        self.e1 = nn.Sequential(block(9, c[0]), block(c[0], c[0]))
        self.e2 = nn.Sequential(block(c[0], c[1]), block(c[1], c[1]))
        self.e3 = nn.Sequential(block(c[1], c[2]), block(c[2], c[2]))
        self.b = nn.Sequential(block(c[2], c[3]), block(c[3], c[3]))
        self.d3 = nn.Sequential(block(c[3] + c[2], c[2]), block(c[2], c[2]))
        self.d2 = nn.Sequential(block(c[2] + c[1], c[1]), block(c[1], c[1]))
        self.d1 = nn.Sequential(block(c[1] + c[0], c[0]), block(c[0], c[0]))
        self.head = nn.Conv2d(c[0], 1, 1)

    def forward(self, x):
        e1 = self.e1(x); e2 = self.e2(F.max_pool2d(e1, 2)); e3 = self.e3(F.max_pool2d(e2, 2)); b = self.b(F.max_pool2d(e3, 2))
        up = lambda t, ref: F.interpolate(t, size=ref.shape[2:], mode="nearest")
        d3 = self.d3(torch.cat([up(b, e3), e3], 1)); d2 = self.d2(torch.cat([up(d3, e2), e2], 1)); d1 = self.d1(torch.cat([up(d2, e1), e1], 1))
        return self.head(d1)[:, 0]          # logits (N, H, W)


# ---------------- data ----------------

def tracknet_samples(root):
    """[(frame paths [i-2, i-1, i], (x, y) in 1280x720 or None)]"""
    out = []
    for clip in sorted(glob.glob(os.path.join(root, "game*", "Clip*"))):
        rows = list(csv.reader(open(os.path.join(clip, "Label.csv"))))[1:]
        names = [r[0] for r in rows]
        for i in range(2, len(rows)):
            v = int(rows[i][1] or 0)
            xy = (float(rows[i][2]), float(rows[i][3])) if v in (1, 2) and rows[i][2] else None
            out.append(([os.path.join(clip, names[j]) for j in (i - 2, i - 1, i)], xy, (1280, 720), os.path.basename(os.path.dirname(clip))))
    return out


def extra_samples(root):
    """our own videos: root/<name>/frames/%06d.jpg (640x360 crops as in tn_run.py) + labels.json [[x, y] or null]"""
    out = []
    for d in sorted(glob.glob(os.path.join(root, "*"))):
        lab = os.path.join(d, "labels.json")
        if not os.path.exists(lab): continue
        L = json.load(open(lab))
        for i in range(2, len(L)):
            fr = [os.path.join(d, "frames", "%06d.jpg" % j) for j in (i - 2, i - 1, i)]
            out.append((fr, tuple(L[i]) if L[i] else None, (W, H), "extra:" + os.path.basename(d)))
    return out


class DS(torch.utils.data.Dataset):
    def __init__(self, samples, aug=False):
        self.s, self.aug = samples, aug
        yy, xx = np.mgrid[0:H, 0:W]; self.yy, self.xx = yy.astype(np.float32), xx.astype(np.float32)

    def __len__(self): return len(self.s)

    def __getitem__(self, i):
        paths, xy, (sw, sh), _ = self.s[i]
        ims = [cv2.resize(cv2.imread(p), (W, H)) for p in paths]
        x = np.concatenate([ims[2], ims[1], ims[0]], 2).astype(np.float32) / 255     # newest first, BGR
        t = np.zeros((H, W), np.float32)
        if xy is not None:
            cx, cy = xy[0] * W / sw, xy[1] * H / sh
            t = np.exp(-((self.xx - cx) ** 2 + (self.yy - cy) ** 2) / (2 * 2.5 ** 2))
        if self.aug:
            if random.random() < 0.5: x = x[:, ::-1].copy(); t = t[:, ::-1].copy()
            x = np.clip(x * random.uniform(0.7, 1.3) + random.uniform(-0.1, 0.1), 0, 1)
        return torch.from_numpy(x.transpose(2, 0, 1).copy()), torch.from_numpy(t)


def loss_fn(logit, t):
    # focal-style weighted BCE: the ball is a few pixels in a big frame
    p = torch.sigmoid(logit)
    w = torch.where(t > 0.1, 50.0, 1.0)
    return (F.binary_cross_entropy_with_logits(logit, t, reduction="none") * w * (1 - torch.exp(-torch.abs(p - t)))).mean()


def peak(prob, thr=0.5):
    m = (prob > thr).astype(np.uint8)
    n, lab, st, cen = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1: return None
    k = 1 + int(np.argmax(st[1:, 4])); return cen[k]


def evaluate(model, loader, dev, tol=4.0):
    model.eval(); tp = fp = fn = tn = 0
    with torch.no_grad():
        for x, t in loader:
            pr = torch.sigmoid(model(x.to(dev))).cpu().numpy(); t = t.numpy()
            for a, b in zip(pr, t):
                g = np.unravel_index(np.argmax(b), b.shape) if b.max() > 0.5 else None
                q = peak(a)
                if g is None: fp += q is not None; tn += q is None
                elif q is None: fn += 1
                elif np.hypot(q[0] - g[1], q[1] - g[0]) <= tol: tp += 1
                else: fp += 1; fn += 1
    model.train()
    prec = tp / max(1, tp + fp); rec = tp / max(1, tp + fn)
    return {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(2 * prec * rec / max(1e-9, prec + rec), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracknet", required=True); ap.add_argument("--extra", default="")
    ap.add_argument("--epochs", type=int, default=30); ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--out", default="ball_small"); ap.add_argument("--limit", type=int, default=0, help="use only N samples (quick test)")
    a = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    S = tracknet_samples(a.tracknet) + (extra_samples(a.extra) if a.extra else [])
    val_games = {"game9", "game10"}                    # held out
    tr = [s for s in S if s[3] not in val_games]; va = [s for s in S if s[3] in val_games]
    random.seed(0); random.shuffle(tr); random.shuffle(va)
    if a.limit: tr, va = tr[:a.limit], va[:max(8, a.limit // 4)]
    print("train", len(tr), "val", len(va), "device", dev)
    dl = torch.utils.data.DataLoader(DS(tr, aug=True), batch_size=a.batch, shuffle=True, num_workers=2, drop_last=True)
    vl = torch.utils.data.DataLoader(DS(va), batch_size=a.batch, num_workers=2)
    model = SmallNet().to(dev)
    opt = torch.optim.AdamW(model.parameters(), 1e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, 2e-3, total_steps=a.epochs * len(dl))
    best = -1
    for ep in range(a.epochs):
        tot = 0
        for x, t in dl:
            x, t = x.to(dev), t.to(dev)
            l = loss_fn(model(x), t); opt.zero_grad(); l.backward(); opt.step(); sched.step(); tot += l.item()
        m = evaluate(model, vl, dev)
        print("epoch", ep + 1, "loss %.4f" % (tot / max(1, len(dl))), m, flush=True)
        if m["f1"] > best:
            best = m["f1"]; torch.save(model.state_dict(), a.out + ".pt")
    # export for phones: heat map in 0-1
    model.load_state_dict(torch.load(a.out + ".pt", map_location=dev, weights_only=True)); model.eval().cpu()
    class Export(nn.Module):
        def __init__(s, m): super().__init__(); s.m = m
        def forward(s, x): return torch.sigmoid(s.m(x))
    torch.onnx.export(Export(model), torch.rand(1, 9, 256, 640), a.out + ".onnx", input_names=["frames"], output_names=["heat"],
                      opset_version=17, dynamic_axes={"frames": {2: "h", 3: "w"}, "heat": {1: "h", 2: "w"}}, dynamo=False)
    print("best f1", best, "->", a.out + ".onnx")


if __name__ == "__main__":
    main()
