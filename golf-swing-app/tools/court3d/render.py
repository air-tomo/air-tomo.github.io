"""Render the annotated match video: ball trail, bounce markers, top-down court map. Usage: render.py video match.json outdir final.mp4"""
import sys, json, os, subprocess, numpy as np, cv2
from multiprocessing import Pool
from PIL import Image, ImageDraw, ImageFont
VIDEO, MATCH, OUTDIR, FINAL = sys.argv[1:5]
TN = json.load(open(os.environ["TN"])) if os.environ.get("TN") else {}
W, H, FPS = 1280, 720, 30
CX, CY, CWID = 160, 0, 1600          # crop of the 1920x1080 source (drops the near-camera ground), scaled to W x H
SC = W / CWID
FONT = next(f for f in ["/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
                         "/System/Library/Fonts/Hiragino Sans GB.ttc", "/Library/Fonts/Arial Unicode.ttf"] if os.path.exists(f))
f_big, f_small = ImageFont.truetype(FONT, 26), ImageFont.truetype(FONT, 16)
M = json.load(open(MATCH)); N = len(M["clips"])
for k, v in TN.items():
    c = M["clips"][int(k)]; c["ball"] = v["ball"]; c["bounces"] = v["bounces"]; c["serve"] = v.get("serve")
    if "ranges" in v: c["ranges"] = v["ranges"]       # from the serve (toss) to the end of the point, decided from the ball
# top-down map
MX, MY, MS = W - 170, 60, 12.0   # left, top, px per metre
def mp(x, y): return (MX + 75 + x * MS, MY + 300 - y * MS * 0.5 * 2 * 0.5 * 2 * 0.5 * 2 / 2)  # placeholder, replaced below
def mp(x, y):  # x lateral, y depth (0 = near baseline at bottom)
    return (MX + 75 + x * 12, MY + 24 + (23.77 - y) * 12)
COURT = [((-5.485, 0), (5.485, 0)), ((-5.485, 23.77), (5.485, 23.77)), ((-5.485, 0), (-5.485, 23.77)), ((5.485, 0), (5.485, 23.77)),
         ((-4.115, 0), (-4.115, 23.77)), ((4.115, 0), (4.115, 23.77)), ((-4.115, 5.485), (4.115, 5.485)),
         ((-4.115, 18.285), (4.115, 18.285)), ((0, 5.485), (0, 18.285))]
CALL_COL = {"IN": (60, 220, 90), "OUT": (255, 60, 60), "CLOSE": (255, 200, 0)}
CALL_TXT = {"IN": "IN", "OUT": "OUT", "CLOSE": "微妙"}
def fmt(t): return "%d:%02d" % (int(t) // 60, int(t) % 60)
def draw_frame(img, t, clip, k, title):
    pil = Image.fromarray(img); d = ImageDraw.Draw(pil, "RGBA")
    # ball trail (last 0.3 s)
    tr = [(bt, (x - CX) * SC, (y - CY) * SC) for bt, x, y in clip["ball"] if t - 0.3 < bt <= t + 1e-6]
    for i, (bt, x, y) in enumerate(tr):
        a = (bt - (t - 0.3)) / 0.3; r = 2 + 4 * a
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 230, 0, int(80 + 175 * a)))
    # bounces
    for b in clip["bounces"]:
        dt = t - b["t"]
        if 0 <= dt < 1.5:
            x, y = (b["uv"][0] - CX) * SC, (b["uv"][1] - CY) * SC; r = 10 + 20 * dt
            col = CALL_COL.get(b.get("call"), (255, 60, 60))
            al = int(255 * (1 - dt / 1.5))
            d.ellipse([x - r, y - r * 0.45, x + r, y + r * 0.45], outline=col + (al,), width=3)
            if b.get("call"):
                d.text((x + 14, y - 30), CALL_TXT[b["call"]], font=f_big, fill=col + (al,), stroke_width=2, stroke_fill=(0, 0, 0, al))
    # map
    d.rounded_rectangle([MX - 5, MY - 5, W - 10, MY + 330], 10, fill=(0, 0, 0, 140))
    d.rectangle([mp(-5.485, 23.77), mp(5.485, 0)], fill=(60, 80, 170, 200))
    for (a, b) in COURT: d.line([mp(*a), mp(*b)], fill=(255, 255, 255, 230), width=1)
    d.line([mp(-6.4, 11.885), mp(6.4, 11.885)], fill=(230, 230, 230, 255), width=2)
    for b in clip["bounces"]:
        if b["t"] > t: continue
        x, y = mp(*b["court"]); recent = t - b["t"] < 1.5
        r = 6 if recent else 4
        col = CALL_COL.get(b.get("call"), (255, 60, 60))
        d.ellipse([x - r, y - r, x + r, y + r], fill=col + (255 if recent else 170,), outline=(0, 0, 0, 255))
    d.text((MX, MY + 310), "奥側は ±1m 程度", font=f_small, fill=(220, 220, 220, 255))
    sv = clip.get("serve")
    if sv and t >= sv["t"] and sv["rms_px"] < 3 and 60 < sv["kmh"] < 190:
        d.rounded_rectangle([12, 60, 300, 100], 8, fill=(200, 30, 30, 200))
        d.text((24, 65), "サーブ %d km/h" % sv["kmh"], font=f_big, fill=(255, 255, 255, 255))
    # header
    d.rounded_rectangle([12, 12, 430, 52], 8, fill=(0, 0, 0, 150))
    d.text((24, 17), title, font=f_big, fill=(255, 255, 255, 255))
    return np.asarray(pil)
def render(job):
    n, k, a, b = job
    clip = M["clips"][k]; title = "ポイント %d  (元 %s)" % (n, fmt(a))
    out = os.path.join(OUTDIR, "c%03d.mp4" % n)
    dec = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-ss", "%.3f" % a, "-i", VIDEO, "-t", "%.3f" % (b - a),
                            "-vf", "fps=%d,crop=%d:%d:%d:%d,scale=%d:%d,format=rgb24" % (FPS, CWID, CWID * 9 // 16, CX, CY, W, H), "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-",
                            "-ss", "%.3f" % a, "-t", "%.3f" % (b - a), "-i", VIDEO, "-map", "0:v", "-map", "1:a:0",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest", out],
                           stdin=subprocess.PIPE)
    i = 0; sz = W * H * 3
    while True:
        buf = dec.stdout.read(sz)
        if len(buf) < sz: break
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        enc.stdin.write(draw_frame(img, a + i / FPS, clip, k, title).tobytes()); i += 1
    enc.stdin.close(); enc.wait(); dec.wait()
    return out
if __name__ == "__main__":
    os.makedirs(OUTDIR, exist_ok=True)
    ks = list(range(N)) if len(sys.argv) < 6 else [int(x) for x in sys.argv[5].split(",")]
    jobs = []
    for k in ks:
        c = M["clips"][k]
        for a, b in c.get("ranges", [[c["start"], c["end"]]]):
            if b - a > 1.0: jobs.append((len(jobs) + 1, k, a, b))
    with Pool(4) as p: files = p.map(render, jobs)
    lst = os.path.join(OUTDIR, "list.txt")
    open(lst, "w").write("".join("file '%s'\n" % f for f in files))
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-movflags", "+faststart", FINAL], check=True)
    print("done", FINAL, os.path.getsize(FINAL) // 1e6, "MB")
