"""TrackNet ball detector (architecture as published by yastrebksv/TrackNet), weights loaded with weights_only=True."""
import torch, torch.nn as nn, numpy as np, cv2
def block(i, o): return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.ReLU(), nn.BatchNorm2d(o))
class Block(nn.Module):
    def __init__(self, i, o): super().__init__(); self.block = block(i, o)
    def forward(self, x): return self.block(x)
class BallTrackerNet(nn.Module):
    def __init__(self):
        super().__init__()
        c = [(9, 64), (64, 64), (64, 128), (128, 128), (128, 256), (256, 256), (256, 256), (256, 512), (512, 512), (512, 512),
             (512, 256), (256, 256), (256, 256), (256, 128), (128, 128), (128, 64), (64, 64), (64, 256)]
        for k, (i, o) in enumerate(c, 1): setattr(self, f"conv{k}", Block(i, o))
        self.pool = nn.MaxPool2d(2, 2); self.up = nn.Upsample(scale_factor=2)
    def forward(self, x):
        x = self.pool(self.conv2(self.conv1(x)))
        x = self.pool(self.conv4(self.conv3(x)))
        x = self.pool(self.conv7(self.conv6(self.conv5(x))))
        x = self.up(self.conv10(self.conv9(self.conv8(x))))
        x = self.up(self.conv13(self.conv12(self.conv11(x))))
        x = self.up(self.conv15(self.conv14(x)))
        return self.conv18(self.conv17(self.conv16(x)))
def load(path):
    m = BallTrackerNet()
    sd = torch.load(path, map_location="cpu", weights_only=True)
    m.load_state_dict(sd); m.eval(); return m
def heat_to_xy(hm):
    """hm: (256, 360, 640) logits or the (360, 640) argmax class map -> argmax class map -> centre of the >=128 blob nearest the peak (or None)."""
    cls = hm if hm.ndim == 2 else hm.argmax(0)
    cls = cls.astype(np.uint8)
    _, b = cv2.threshold(cls, 127, 255, cv2.THRESH_BINARY)
    n, lab, st, cen = cv2.connectedComponentsWithStats(b, 8)
    if n <= 1: return None
    i = 1 + int(np.argmax(st[1:, 4]))
    return float(cen[i][0]), float(cen[i][1]), int(st[i][4])
