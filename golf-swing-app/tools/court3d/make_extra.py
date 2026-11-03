"""Training data from our own videos: frames (the same crop the detector sees) + ball positions found by TrackNet (pseudo labels).

  python3 make_extra.py VIDEO LABEL_DIR OUT_DIR
LABEL_DIR holds tn_*.json from tn_run.py / tn_roi.py ([[t, x, y], ...] in source pixels, 30 per second).
OUT_DIR/<clip>/frames/%06d.jpg (640x360) and labels.json ([x, y] in 640x360 or null), as train_small.py expects.
"""
import glob, json, os, subprocess, sys
VIDEO, LAB, OUT = sys.argv[1:4]
X0, Y0, CW, CH = 320, 120, 1280, 720           # the crop of a 1920x1080 frame, scaled to 640x360
for path in sorted(glob.glob(os.path.join(LAB, "tn_*.json"))):
    pts = json.load(open(path))
    if len(pts) < 10: continue
    name = os.path.basename(path)[:-5]; d = os.path.join(OUT, name); os.makedirs(os.path.join(d, "frames"), exist_ok=True)
    t0 = pts[0][0] - 2 / 30                     # tn files start at the third frame
    n = len(pts) + 2
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", "%.3f" % t0, "-i", VIDEO, "-frames:v", str(n),
                    "-vf", "fps=30,crop=%d:%d:%d:%d,scale=640:360" % (CW, CH, X0, Y0), "-q:v", "3",
                    os.path.join(d, "frames", "%06d.jpg")], check=True)
    got = len(glob.glob(os.path.join(d, "frames", "*.jpg")))
    labels = [None, None] + [([round((x - X0) / 2, 1), round((y - Y0) / 2, 1)] if x is not None else None) for _, x, y in pts]
    labels = labels[:got]
    # ffmpeg numbers frames from 1; train_small.py reads %06d from 0
    for i in range(got):
        os.rename(os.path.join(d, "frames", "%06d.jpg" % (i + 1)), os.path.join(d, "frames", "%06d.jpg" % i))
    json.dump(labels, open(os.path.join(d, "labels.json"), "w"))
    print(name, got, "frames", sum(l is not None for l in labels), "with the ball")
