"""App 1 (cut only, any camera angle): hit sounds + small ball model + tracks -> points -> one video of the points.

  python3 cut_video.py VIDEO MODEL.pt OUTDIR [--y0 300 --h 704]

1. hit sounds (golf-swing-app/tennis.py, the same detector as cut/detect.js) and its candidate segments (padded 2 s)
2. small_run.py on those segments (ball candidates), track.py, cutfree.py
3. OUTDIR/points.json and OUTDIR/points.mp4 (the points one after another, with a small label)
Resumable: finished segments are not run again.
"""
import argparse, json, os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, APP); sys.path.insert(0, HERE)
import numpy as np, swing, tennis, track, cutfree

ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("model"); ap.add_argument("outdir")
ap.add_argument("--y0", type=int, default=300); ap.add_argument("--h", type=int, default=704)
a = ap.parse_args(); os.makedirs(a.outdir, exist_ok=True)
info = swing.probe(a.video); env_p = os.path.join(a.outdir, "env.npy")
if not os.path.exists(env_p): np.save(env_p, swing.audio_envelope(a.video, info["duration"]))
env = np.load(env_p); r = tennis.analyze(env, info["duration"], tennis.TennisSettings())
hits = r["hits"]; json.dump(hits, open(os.path.join(a.outdir, "hits.json"), "w"))
segs = [(max(0, s - 2), min(info["duration"], e + 2)) for s, e in r["segments"]]
allp = []
for k, (s, e) in enumerate(segs):
    out = os.path.join(a.outdir, "small_%03d.json" % k)
    if not os.path.exists(out.replace(".json", "_cand.json")):
        subprocess.run([sys.executable, os.path.join(HERE, "small_run.py"), a.video, "%.3f" % s, "%.3f" % (e - s), out, a.model,
                        "--y0", str(a.y0), "--h", str(a.h)], check=True)
    T = track.tracks(json.load(open(out.replace(".json", "_cand.json"))))
    json.dump(T, open(os.path.join(a.outdir, "tracks_%03d.json" % k), "w"))
    P = cutfree.points(cutfree.shots(T, [h for h in hits if s <= h <= e], 1920), 1920, tracks=T)
    allp += [[max(s, p[0]), min(e, p[1]), p[2]] for p in P]
    print("segment", k, "%.1f-%.1f" % (s, e), "points", P, flush=True)
json.dump(allp, open(os.path.join(a.outdir, "points.json"), "w"))
# the video of the points
parts = []
for n, (s, e, ns) in enumerate(allp, 1):
    f = os.path.join(a.outdir, "p%03d.mp4" % n)
    label = "ポイント %d (元 %d:%02d)" % (n, int(s) // 60, int(s) % 60)
    font = next((p for p in ["/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"] if os.path.exists(p)), None)
    vf = "scale=1280:-2" + (",drawtext=fontfile=%s:text='%s':x=16:y=16:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.5" % (font, label) if font else "")
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", "%.3f" % s, "-i", a.video, "-t", "%.3f" % (e - s), "-vf", vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-c:a", "aac", "-b:a", "96k", f], check=True)
    parts.append(f)
lst = os.path.join(a.outdir, "list.txt"); open(lst, "w").write("".join("file '%s'\n" % p for p in parts))
subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart",
                os.path.join(a.outdir, "points.mp4")], check=True)
print("done", len(allp), "points ->", os.path.join(a.outdir, "points.mp4"))
