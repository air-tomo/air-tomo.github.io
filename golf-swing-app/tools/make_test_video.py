"""動作確認用のテスト動画を作る。

打球音に似た鋭い衝撃音を指定時刻に入れ、背景に雑音・話し声に似た音・
ゆっくり立ち上がる大きな音（誤検出しないべき音）を混ぜる。
iPhone と同じ HEVC + AAC の .mov で、縦向き（回転メタデータ付き）にする。

使い方: python tools/make_test_video.py test_videos/test.mov
"""

import os
import subprocess
import sys
import wave

import numpy as np

SR = 48000
DURATION = 60.0
IMPACTS = [7.5, 19.2, 31.0, 44.6, 55.3]
DISTRACTORS = [12.0, 26.0, 38.0]  # 立ち上がりが遅い音（声や車の音のつもり）


def make_audio(path):
    rng = np.random.default_rng(0)
    n = int(SR * DURATION)
    t = np.arange(n) / SR
    audio = 0.01 * rng.standard_normal(n)  # 環境ノイズ
    audio += 0.02 * np.sin(2 * np.pi * 120 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.3 * t))
    for at in IMPACTS:
        i = int(at * SR)
        length = int(0.08 * SR)
        decay = np.exp(-np.arange(length) / (0.008 * SR))
        audio[i:i + length] += 0.8 * rng.standard_normal(length) * decay
    for at in DISTRACTORS:
        i = int(at * SR)
        length = int(1.0 * SR)
        env = np.sin(np.pi * np.arange(length) / length) ** 2
        audio[i:i + length] += 0.3 * np.sin(2 * np.pi * 300 * t[:length]) * env
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "test.mov"
    wav = out + ".wav"
    tmp = out + ".tmp.mov"
    make_audio(wav)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={DURATION}",
        "-i", wav,
        "-c:v", "libx265", "-preset", "ultrafast", "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        tmp,
    ], check=True)
    # iPhone の縦向き動画と同じく回転メタデータを付ける
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", tmp,
        "-c", "copy", out,
    ], check=True)
    os.remove(tmp)
    os.remove(wav)
    print(f"{out} を作成しました。打球位置: {IMPACTS}")


if __name__ == "__main__":
    main()
