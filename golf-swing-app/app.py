"""ゴルフスイング自動切り抜き・テニスのポイント間カット Web アプリ。

ゴルフ: 打球音からスイングを検出し、すべてのスイングを1本の MP4 に結合する。
テニス: 打球音からラリーを検出し、ポイント間を取り除いた1本の MP4 にする。
"""

import argparse
import os
import shutil
import socket
import threading
import time
import traceback
import uuid

from flask import Flask, abort, jsonify, render_template, request, send_file

import swing
import tennis

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR = os.path.join(BASE_DIR, "jobs")
JOB_TTL_SEC = 24 * 60 * 60  # 処理結果は24時間で削除

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 ** 3  # 32GB まで（2時間の試合動画を想定）

jobs = {}
jobs_lock = threading.Lock()


def update_job(job_id, **fields):
    with jobs_lock:
        jobs[job_id].update(fields)


def cleanup_old_jobs():
    if not os.path.isdir(JOBS_DIR):
        return
    now = time.time()
    for name in os.listdir(JOBS_DIR):
        path = os.path.join(JOBS_DIR, name)
        if os.path.isdir(path) and now - os.path.getmtime(path) > JOB_TTL_SEC:
            shutil.rmtree(path, ignore_errors=True)
            with jobs_lock:
                jobs.pop(name, None)


def _analyze(mode, env, info, settings):
    """モードごとに切り出し区間を求め、(区間, チャプター名, ジョブに保存する情報) を返す。"""
    if mode == "tennis":
        result = tennis.analyze(env, info["duration"], settings)
        if not result["segments"]:
            raise swing.ProcessingError(
                "ラリーが検出できませんでした。「検出感度」を上げるか「最少打球数」を下げてお試しください。"
            )
        found = f"{len(result['rallies'])} ポイントを検出しました。切り出しています"
        return result["segments"], result["labels"], found, {
            "rallies": result["rallies"], "labels": result["labels"]}

    impacts = swing.detect_impacts(env, settings)
    segments = swing.build_segments(impacts, info["duration"], settings.pre_sec, settings.post_sec)
    if not segments:
        raise swing.ProcessingError("打球音が検出できませんでした。「検出感度」を上げてもう一度お試しください。")
    labels = [f"スイング {i + 1}（元 {swing.format_time(t)}）" for i, t in enumerate(impacts)]
    # 重なってまとめられた区間はチャプターを付けない（数が合わないため）
    labels = labels if len(labels) == len(segments) else None
    found = f"{len(impacts)} 回のスイングを検出しました。切り出しています"
    return segments, labels, found, {"impacts": impacts}


def process_job(job_id, src, mode, settings):
    work_dir = os.path.dirname(src)
    try:
        update_job(job_id, status="running", step="動画を解析しています", progress=1)
        info = swing.probe(src)

        update_job(job_id, step="音声から打球音を探しています", progress=2)
        env = swing.audio_envelope(
            src, info["duration"],
            on_progress=lambda r: update_job(job_id, progress=2 + int(r * 15)),
        )
        segments, labels, found, extra = _analyze(mode, env, info, settings)
        update_job(job_id, segments=segments, step=found, progress=18, **extra)

        out_path = os.path.join(work_dir, "output.mp4")
        swing.cut_and_concat(
            src, segments, out_path, work_dir, hdr=info["hdr"], src_bitrate=info["video_bitrate"],
            labels=labels,
            on_progress=lambda r: update_job(
                job_id, progress=18 + int(r * 80),
                step=f"切り出し・結合中 ({int(r * 100)}%)",
            ),
        )
        os.remove(src)
        update_job(job_id, status="done", step="完了しました", progress=100,
                   output_size=os.path.getsize(out_path),
                   input_duration=round(info["duration"], 1),
                   output_duration=round(sum(b - a for a, b in segments), 1))
    except swing.ProcessingError as e:
        update_job(job_id, status="error", step="エラー", error=str(e))
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception:
        traceback.print_exc()
        update_job(job_id, status="error", step="エラー",
                   error="予期しないエラーが発生しました。サーバーのログを確認してください。")
        shutil.rmtree(work_dir, ignore_errors=True)


def _float_arg(name, default, lo, hi):
    try:
        value = float(request.form.get(name, default))
    except ValueError:
        value = default
    return min(hi, max(lo, value))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    file = request.files.get("video")
    if file is None or not file.filename:
        return jsonify(error="動画ファイルを選択してください。"), 400

    mode = "tennis" if request.form.get("mode") == "tennis" else "golf"
    if mode == "tennis":
        settings = tennis.TennisSettings(
            pre_sec=_float_arg("pre_sec", 1.5, 0, 15),
            post_sec=_float_arg("post_sec", 2.0, 0, 15),
            sensitivity=int(_float_arg("sensitivity", 6, 1, 10)),
            max_shot_gap_sec=_float_arg("max_shot_gap_sec", 5.0, 1, 15),
            min_hits=int(_float_arg("min_hits", 2, 1, 10)),
            keep_serve_only=request.form.get("keep_serve_only") == "1",
            second_serve_gap_sec=_float_arg("second_serve_gap_sec", 12.0, 0, 60),
        )
    else:
        settings = swing.DetectSettings(
            pre_sec=_float_arg("pre_sec", 3.0, 0, 15),
            post_sec=_float_arg("post_sec", 2.0, 0, 15),
            sensitivity=int(_float_arg("sensitivity", 5, 1, 10)),
            min_interval_sec=_float_arg("min_interval_sec", 5.0, 1, 60),
        )

    cleanup_old_jobs()
    job_id = uuid.uuid4().hex
    work_dir = os.path.join(JOBS_DIR, job_id)
    os.makedirs(work_dir)
    ext = os.path.splitext(file.filename)[1].lower() or ".mov"
    src = os.path.join(work_dir, "input" + ext)
    file.save(src)

    with jobs_lock:
        jobs[job_id] = {
            "status": "queued", "step": "処理待ち", "progress": 0,
            "filename": file.filename, "mode": mode, "settings": vars(settings),
        }
    threading.Thread(target=process_job, args=(job_id, src, mode, settings), daemon=True).start()
    return jsonify(job_id=job_id)


@app.route("/api/jobs/<job_id>")
def job_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify(error="処理が見つかりません。サーバーが再起動された可能性があります。"), 404
        return jsonify(job)


def _output_path(job_id):
    if not job_id.isalnum():
        abort(404)
    path = os.path.join(JOBS_DIR, job_id, "output.mp4")
    if not os.path.isfile(path):
        abort(404)
    return path


@app.route("/video/<job_id>.mp4")
def preview(job_id):
    # Safari の動画再生は Range リクエストが必要。send_file(conditional=True) が対応する
    return send_file(_output_path(job_id), mimetype="video/mp4", conditional=True)


@app.route("/download/<job_id>")
def download(job_id):
    with jobs_lock:
        mode = jobs.get(job_id, {}).get("mode", "golf")
    prefix = "tennis_points_" if mode == "tennis" else "golf_swings_"
    name = prefix + time.strftime("%Y%m%d_%H%M") + ".mp4"
    return send_file(_output_path(job_id), mimetype="video/mp4", as_attachment=True, download_name=name)


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="ファイルが大きすぎます（上限 32GB）。Mac 上の動画なら cli.py で直接処理できます。"), 413


def lan_addresses():
    """iPhone からアクセスするための LAN 内 IP アドレスを推定する。"""
    addrs = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # 実際には送信しない
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def main():
    parser = argparse.ArgumentParser(description="ゴルフスイング自動切り抜き Web アプリ")
    parser.add_argument("--host", default="0.0.0.0",
                        help="待ち受けアドレス。0.0.0.0 で同じ Wi-Fi の端末から接続可能、127.0.0.1 でこの Mac のみ")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    swing.find_ffmpeg()
    os.makedirs(JOBS_DIR, exist_ok=True)
    cleanup_old_jobs()

    print("=" * 60)
    print(" スイング・ラリー自動カット を起動しました")
    print(f"  この Mac から : http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        for addr in lan_addresses():
            print(f"  iPhone から   : http://{addr}:{args.port}")
        host = socket.gethostname().split(".")[0]
        print(f"  (または)      : http://{host}.local:{args.port}")
    else:
        print("  ※ 127.0.0.1 で起動したため iPhone からは接続できません")
    print(" 終了するには Ctrl+C を押してください")
    print("=" * 60, flush=True)

    try:
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=8,
              max_request_body_size=app.config["MAX_CONTENT_LENGTH"], channel_timeout=600)
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
