"""打球音の検出と、FFmpeg による切り抜き・結合処理。"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 22050
HOP = 110  # 包絡線のフレーム間隔 (約5ms)
WIN = 220  # 包絡線の窓幅 (約10ms)
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
    return {"duration": duration, "hdr": hdr}


def load_audio(path):
    """動画からモノラル音声を float32 配列として取り出す。"""
    ffmpeg, _ = find_ffmpeg()
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise ProcessingError("音声の取り出しに失敗しました。")
    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size < SAMPLE_RATE:
        raise ProcessingError("音声が短すぎます。")
    return samples


def _envelope_db(samples):
    """高域を強調した短時間エネルギー (dB) を返す。打球音は鋭い広帯域の衝撃音なので、
    差分をとって風切り音や話し声などの低域成分を抑える。"""
    emphasized = np.diff(samples, prepend=samples[0])
    hop, win = HOP, WIN
    n_frames = 1 + (len(emphasized) - win) // hop
    if n_frames <= 0:
        return np.zeros(0)
    power = np.convolve(emphasized ** 2, np.ones(win) / win, mode="valid")[::hop][:n_frames]
    return 10.0 * np.log10(power + 1e-10)


def _moving_percentile(x, size, q):
    """長い動画でも速いよう、間引いた点で百分位を求めて線形補間する。"""
    step = max(1, size // 8)
    centers = np.arange(0, len(x), step)
    half = size // 2
    values = np.array([np.percentile(x[max(0, c - half):c + half + 1], q) for c in centers])
    return np.interp(np.arange(len(x)), centers, values)


def detect_impacts(samples, settings, on_progress=None):
    """打球の瞬間 (秒) のリストを返す。"""
    env = _envelope_db(samples)
    if env.size == 0:
        return []
    if on_progress:
        on_progress(0.3)

    # 周囲 2 秒の背景レベルとの差が大きく、立ち上がりが鋭い点を打球候補とする
    background = _moving_percentile(env, int(2.0 / HOP_SEC), 50)
    if on_progress:
        on_progress(0.7)
    prominence = env - background
    attack_frames = max(1, int(0.015 / HOP_SEC))
    attack = env - np.concatenate([np.full(attack_frames, env[0]), env[:-attack_frames]])

    s = min(10, max(1, int(settings.sensitivity)))
    prominence_db = 30.0 - 1.8 * s  # 感度 5 で 21dB, 10 で 12dB
    attack_db = 18.0 - 1.0 * s  # 感度 5 で 13dB
    # 動画全体で最も大きい音から極端に小さい音は無視する（遠くの打席の音など）
    loud_floor = np.max(env) - (40.0 - 2.0 * s)

    candidates = np.where((prominence > prominence_db) & (attack > attack_db) & (env > loud_floor))[0]
    if candidates.size == 0:
        return []

    # 近接する候補をまとめ、最大値のフレームを打球時刻とする
    min_gap = int(settings.min_interval_sec / HOP_SEC)
    groups = []
    for idx in candidates:
        if groups and idx - groups[-1][-1] < min_gap:
            groups[-1].append(idx)
        else:
            groups.append([idx])
    impacts = []
    for g in groups:
        peak = g[int(np.argmax(env[g]))]
        impacts.append(round(float((peak * HOP + WIN / 2) / SAMPLE_RATE), 3))
    if on_progress:
        on_progress(1.0)
    return impacts


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


def _has_filter(ffmpeg, name):
    out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


def _video_filter(ffmpeg, hdr):
    # iPhone の HDR (HLG/Dolby Vision) 動画はそのまま H.264 にすると白っぽくなるので SDR に変換する
    if hdr and _has_filter(ffmpeg, "zscale") and _has_filter(ffmpeg, "tonemap"):
        return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
    return "format=yuv420p"


def cut_and_concat(src, segments, out_path, work_dir, hdr=False, on_progress=None):
    """各区間を H.264/AAC で切り出し、1本の MP4 に結合する。"""
    ffmpeg, _ = find_ffmpeg()
    vf = _video_filter(ffmpeg, hdr)
    total = sum(b - a for a, b in segments)
    done = 0.0
    parts = []
    for i, (start, end) in enumerate(segments):
        part = os.path.join(work_dir, f"part_{i:04d}.mp4")
        cmd = [
            ffmpeg, "-v", "error", "-y",
            "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
            "-map", "0:v:0", "-map", "0:a:0",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            # 結合時にずれないよう、全パーツのタイムベースを揃える（フレームレートは元動画のまま）
            "-video_track_timescale", "90000",
            "-movflags", "+faststart",
            part,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ProcessingError(f"{i + 1} 本目のスイングの切り出しに失敗しました: {result.stderr.strip()[-300:]}")
        parts.append(part)
        done += end - start
        if on_progress:
            on_progress(done / total if total else 1.0)

    list_path = os.path.join(work_dir, "parts.txt")
    with open(list_path, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.basename(p)}'\n")
    result = subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c", "copy", "-movflags", "+faststart", out_path],
        capture_output=True,
        text=True,
        cwd=work_dir,
    )
    if result.returncode != 0:
        raise ProcessingError(f"動画の結合に失敗しました: {result.stderr.strip()[-300:]}")
    for p in parts:
        os.remove(p)
    os.remove(list_path)
