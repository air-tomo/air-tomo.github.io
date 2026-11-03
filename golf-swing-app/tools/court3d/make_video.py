"""Whole pipeline for one match video (run on a Mac: TrackNet uses the Apple GPU via MPS).

  python3 make_video.py VIDEO CAM.npz MODEL.pt OUTDIR [--start SEC]

1. cut the points from the audio (same detector as cut/ and golf-swing-app/)
2. TrackNet ball positions for every point (30 fps)
3. bounces, serve speed
4. render the annotated video OUTDIR/final.mp4
"""
import argparse, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, APP)
import numpy as np, swing, tennis
ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("cam"); ap.add_argument("model"); ap.add_argument("outdir")
ap.add_argument("--start", type=float, default=0.0, help="ignore points before this time (warm-up)")
a = ap.parse_args(); os.makedirs(a.outdir, exist_ok=True)
info = swing.probe(a.video)
env_path = os.path.join(a.outdir, "env.npy")
if not os.path.exists(env_path): np.save(env_path, swing.audio_envelope(a.video, info["duration"]))
env = np.load(env_path)
r = tennis.analyze(env, info["duration"], tennis.TennisSettings())
segs = [(i, s, e) for i, (s, e) in enumerate(r["segments"]) if s >= a.start]
match = {"segments": [[s, e] for _, s, e in segs], "labels": [r["labels"][i] for i, _, _ in segs],
         "clips": [{"clip": i, "start": s, "end": e, "ball": [], "bounces": []} for i, s, e in segs]}
mj = os.path.join(a.outdir, "match.json"); json.dump(match, open(mj, "w"))
args = []
for k, c in enumerate(match["clips"]):
    out = os.path.join(a.outdir, "tn_%03d.json" % k)
    if not os.path.exists(out):
        subprocess.run([sys.executable, os.path.join(HERE, "tn_run.py"), a.video, str(c["start"]), str(c["end"] - c["start"]), out, a.model], check=True)
    args.append("%d:%s" % (k, out))
post = os.path.join(a.outdir, "tn_post.json")
subprocess.run([sys.executable, os.path.join(HERE, "tn_post.py"), mj, a.cam, env_path, post] + args, check=True)
subprocess.run([sys.executable, os.path.join(HERE, "render.py"), a.video, mj, os.path.join(a.outdir, "clips"), os.path.join(a.outdir, "final.mp4")],
               env=dict(os.environ, TN=post), check=True)
