"""Export TrackNet to ONNX for phones/browsers (ONNX Runtime Web + WebGPU, Android, iOS). Output: class map (uint8, 0-255) per pixel.
Usage: export_onnx.py model_best.pt out.onnx"""
import sys, torch, tnet
class Wrap(torch.nn.Module):
    def __init__(self, m): super().__init__(); self.m = m
    def forward(self, x):  # x: (1, 9, H, W) float 0-1, three frames newest first (BGR)
        return self.m(x).argmax(1).to(torch.uint8)
m = Wrap(tnet.load(sys.argv[1])).eval()
x = torch.rand(1, 9, 256, 640)
torch.onnx.export(m, x, sys.argv[2], input_names=["frames"], output_names=["heat"], opset_version=17,
                  dynamic_axes={"frames": {2: "h", 3: "w"}, "heat": {1: "h", 2: "w"}}, dynamo=False)
print("ok")
