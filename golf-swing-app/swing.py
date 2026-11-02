"""打球音の検出と、FFmpeg による切り抜き・結合処理。"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 22050
HOP = 110  # 包絡線のフレーム間隔 (約5ms)
HOP_SEC = HOP / SAMPLE_RATE


class ProcessingError(Exception):
    """ユーザーに表示するエラー。メッセージは日本語。"""


@dataclass
class DetectSettings:
    pre_sec: float = 3.0  # 打球の何秒前から切り出すか
    post_sec: float = 2.0  # 打球の何秒後まで切り出すか
    sensitivity: int = 5  # 1(鈍い)〜10(敏感)
    min_interval_sec: float = 5.0  # 連続した打球とみなさない最小間隔


def find_ffmpeg():
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ProcessingError(
            "FFmpeg が見つかりません。Mac では「brew install ffmpeg」でインストールしてください。"
        )
    return ffmpeg, ffprobe


def probe(path):
    """動画の長さ・音声の有無・HDR かどうかを調べる。"""
    _, ffprobe = find_ffmpeg()
    result = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ProcessingError("動画ファイルを読み込めませんでした。対応していない形式の可能性があります。")
    info = json.loads(result.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ProcessingError("動画トラックが見つかりません。")
    if audio is None:
        raise ProcessingError("音声トラックがありません。打球音で検出するため、音声付きの動画をアップロードしてください。")
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ProcessingError("動画の長さを取得できませんでした。")
    hdr = video.get("color_transfer") in ("arib-std-b67", "smpte2084")
    bitrate = int(video.get("bit_rate") or info.get("format", {}).get("bit_rate") or 0)
    return {"duration": duration, "hdr": hdr, "video_bitrate": bitrate}


def audio_envelope(path, duration, on_progress=None):
    """動画の音声から、高域を強調した約5msごとの音量 (dB) を返す。

    打球音は鋭い広帯域の衝撃音なので、差分をとって風切り音や話し声などの低域成分を抑える。
    2時間の試合動画でもメモリを使いすぎないよう、FFmpeg の出力を少しずつ読んで処理する。
    """
    ffmpeg, _ = find_ffmpeg()
    proc = subprocess.Popen(
        [ffmpeg, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    chunk_bytes = HOP * 2000 * 2  # 約10秒ずつ
    pending = b""
    prev = 0.0
    frames = []
    read_samples = 0
    while True:
        data = proc.stdout.read(chunk_bytes)
        if not data:
            break
        buf = pending + data
        usable = len(buf) // (2 * HOP) * (2 * HOP)
        buf, pending = buf[:usable], buf[usable:]
        if not buf:
            continue
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
        d = np.diff(x, prepend=np.float32(prev))
        prev = x[-1]
        frames.append((d.astype(np.float64) ** 2).reshape(-1, HOP).mean(axis=1))
        read_samples += x.size
        if on_progress:
            on_progress(min(1.0, read_samples / SAMPLE_RATE / duration))
    proc.wait()
    if proc.returncode != 0:
        raise ProcessingError("音声の取り出しに失敗しました。")
    power = np.concatenate(frames) if frames else np.zeros(0)
    if power.size * HOP_SEC < 1.0:
        raise ProcessingError("音声が短すぎます。")
    return 10.0 * np.log10(power + 1e-10)


def frame_to_sec(idx):
    return round(float((idx + 0.5) * HOP_SEC), 3)


def _moving_percentile(x, size, q):
    """長い動画でも速いよう、間引いた点で百分位を求めて線形補間する。"""
    step = max(1, size // 8)
    centers = np.arange(0, len(x), step)
    half = size // 2
    values = np.array([np.percentile(x[max(0, c - half):c + half + 1], q) for c in centers])
    return np.interp(np.arange(len(x)), centers, values)


def detect_hits(env, sensitivity, min_gap_sec):
    """打球音の時刻 (秒) と、その強さ (背景からの dB) を返す。"""
    if env.size == 0:
        return [], []
    # 周囲 2 秒の背景レベルとの差が大きく、立ち上がりが鋭い点を打球候補とする
    background = _moving_percentile(env, int(2.0 / HOP_SEC), 50)
    prominence = env - background
    attack_frames = max(1, int(0.015 / HOP_SEC))
    attack = env - np.concatenate([np.full(attack_frames, env[0]), env[:-attack_frames]])

    s = min(10, max(1, int(sensitivity)))
    prominence_db = 30.0 - 1.8 * s  # 感度 5 で 21dB, 10 で 12dB
    attack_db = 18.0 - 1.0 * s  # 感度 5 で 13dB
    # 動画内の大きな音から極端に小さい音は無視する（遠くのコートや打席の音など）。
    # 1回だけの物音に引きずられないよう、最大値ではなく上位 0.1% の音量を基準にする
    loud_floor = np.percentile(env, 99.9) - (40.0 - 2.0 * s)

    candidates = np.where((prominence > prominence_db) & (attack > attack_db) & (env > loud_floor))[0]
    if candidates.size == 0:
        return [], []

    # 近接する候補をまとめ、最大値のフレームを打球時刻とする
    min_gap = max(1, int(min_gap_sec / HOP_SEC))
    groups = []
    for idx in candidates:
        if groups and idx - groups[-1][-1] < min_gap:
            groups[-1].append(idx)
        else:
            groups.append([idx])
    times, strengths = [], []
    for g in groups:
        peak = g[int(np.argmax(env[g]))]
        times.append(frame_to_sec(peak))
        strengths.append(round(float(prominence[peak]), 1))
    return times, strengths


def detect_impacts(env, settings):
    """ゴルフの打球の瞬間 (秒) のリストを返す。"""
    return detect_hits(env, settings.sensitivity, settings.min_interval_sec)[0]


def build_segments(impacts, duration, pre_sec, post_sec):
    """打球時刻から切り出し区間を作る。重なる区間は1つにまとめる。"""
    segments = []
    for t in impacts:
        start = max(0.0, t - pre_sec)
        end = min(duration, t + post_sec)
        if end - start < 0.2:
            continue
        if segments and start <= segments[-1][1]:
            segments[-1][1] = max(segments[-1][1], end)
        else:
            segments.append([start, end])
    return [(round(a, 3), round(b, 3)) for a, b in segments]


def _ffmpeg_list(ffmpeg, kind):
    out = subprocess.run([ffmpeg, "-hide_banner", kind], capture_output=True, text=True).stdout
    return {line.split()[1] for line in out.splitlines() if len(line.split()) > 1}


def _video_filter(ffmpeg, hdr):
    # iPhone の HDR (HLG/Dolby Vision) 動画はそのまま H.264 にすると白っぽくなるので SDR に変換する
    filters = _ffmpeg_list(ffmpeg, "-filters")
    if hdr and "zscale" in filters and "tonemap" in filters:
        return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    return "format=yuv420p"


SOFTWARE_ENCODER = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def _encoders(ffmpeg, src_bitrate):
    """試す順に映像エンコーダの引数を返す。Mac では GPU (VideoToolbox) を使うと
    長い試合動画でも数倍速く書き出せる。使えなければ libx264 に切り替える。"""
    candidates = []
    if "h264_videotoolbox" in _ffmpeg_list(ffmpeg, "-encoders"):
        # HEVC から H.264 にすると同じ画質でもビットレートが必要なので元の 1.5 倍を目安にする
        bitrate = min(60_000_000, max(6_000_000, int(src_bitrate * 1.5)))
        candidates.append(["-c:v", "h264_videotoolbox", "-b:v", str(bitrate), "-allow_sw", "1"])
    candidates.append(SOFTWARE_ENCODER)
    return candidates


def _duration(ffprobe, path):
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def cut_and_concat(src, segments, out_path, work_dir, hdr=False, src_bitrate=0,
                   labels=None, on_progress=None):
    """各区間を H.264/AAC で切り出し、1本の MP4 に結合する。
    labels を渡すと、各区間の先頭にチャプター（QuickTime 等で頭出しできる目次）を付ける。"""
    ffmpeg, ffprobe = find_ffmpeg()
    out_path = os.path.abspath(out_path)  # 結合は work_dir で実行するため
    vf = _video_filter(ffmpeg, hdr)
    encoders = _encoders(ffmpeg, src_bitrate)
    total = sum(b - a for a, b in segments)
    done = 0.0
    parts = []
    for i, (start, end) in enumerate(segments):
        part = os.path.join(work_dir, f"part_{i:04d}.mp4")
        while True:
            cmd = [
                ffmpeg, "-v", "error", "-y",
                "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
                "-map", "0:v:0", "-map", "0:a:0",
                "-vf", vf,
                *encoders[0],
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                # 結合時にずれないよう、全パーツのタイムベースを揃える（フレームレートは元動画のまま）
                "-video_track_timescale", "90000",
                "-movflags", "+faststart",
                part,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                break
            if len(encoders) > 1 and i == 0:
                # GPU エンコーダが使えない環境では、以降すべてソフトウェアで処理する
                encoders = encoders[1:]
                continue
            raise ProcessingError(f"{i + 1} 本目の切り出しに失敗しました: {result.stderr.strip()[-300:]}")
        parts.append(part)
        done += end - start
        if on_progress:
            on_progress(done / total if total else 1.0)

    list_path = os.path.join(work_dir, "parts.txt")
    with open(list_path, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.basename(p)}'\n")
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", list_path]
    meta_path = os.path.join(work_dir, "chapters.txt")
    if labels:
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(";FFMETADATA1\n")
            pos = 0.0
            for p, label in zip(parts, labels):
                length = _duration(ffprobe, p)
                title = label.replace("=", "\\=").replace(";", "\\;").replace("#", "\\#")
                f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(pos * 1000)}\n"
                        f"END={int((pos + length) * 1000)}\ntitle={title}\n")
                pos += length
        cmd += ["-i", meta_path, "-map", "0", "-map_metadata", "1", "-map_chapters", "1"]
    cmd += ["-c", "copy", "-movflags", "+faststart", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=work_dir)
    if result.returncode != 0:
        raise ProcessingError(f"動画の結合に失敗しました: {result.stderr.strip()[-300:]}")
    for p in parts:
        os.remove(p)
    os.remove(list_path)
    if labels:
        os.remove(meta_path)


def format_time(sec):
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
