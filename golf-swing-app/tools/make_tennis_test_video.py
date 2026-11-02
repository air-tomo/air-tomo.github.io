"""テニスのポイント間カットを確かめるためのテスト動画を作る。

実際の試合に近づけるため、次の音を入れる。
- ラリー: 手前の選手（大きい音）と奥の選手（小さい音）が 1〜2 秒間隔で交互に打つ。ロブは長め
- 打球の間のボールのバウンド、サーブ前のボールつき（打球より小さく低い音）
- フォルト→セカンドサーブ、サービスエース（打球1回だけのポイント）
- ポイント間 18〜28 秒: 話し声、足音、風、隣のコートの小さな打球音

使い方: python tools/make_tennis_test_video.py test_videos/tennis.mp4
正解のポイント区間は <出力>.json に保存する。
"""

import json
import os
import subprocess
import sys
import wave

import numpy as np

SR = 48000
rng = np.random.default_rng(int(os.environ.get("SEED", 42)))


def burst(length_sec, decay_sec, amp, lowpass=None):
    n = int(length_sec * SR)
    x = rng.standard_normal(n) * np.exp(-np.arange(n) / (decay_sec * SR))
    if lowpass:
        x = np.convolve(x, np.ones(lowpass) / lowpass, mode="same")
        x /= np.max(np.abs(x)) + 1e-9
    return amp * x


def racket_hit(amp):
    x = burst(0.06, 0.006, amp)
    n = x.size
    ping = np.sin(2 * np.pi * 1100 * np.arange(n) / SR) * np.exp(-np.arange(n) / (0.01 * SR))
    return x + 0.3 * amp * ping


def ball_bounce(amp):
    return burst(0.05, 0.008, amp, lowpass=4)


def add(audio, t, sound):
    i = int(t * SR)
    j = min(audio.size, i + sound.size)
    if 0 <= i < audio.size:
        audio[i:j] += sound[: j - i]


def build_match():
    """(音声に入れるイベント, 正解のポイント) を作る。
    ポイントはサーブから始まる。フォルト→セカンドサーブは同じポイント。"""
    events = []  # (time, sound)
    points = []  # {"serves": [サーブの時刻], "end": 最後の打球, "hits": 打球数}
    t = 8.0
    plans = (["rally"] * 8 + ["ace", "fault_then_rally", "fault_then_rally", "long_rally", "short_rally"])
    rng.shuffle(plans)
    for p, plan in enumerate(plans):
        near_serves = (p // 4) % 2 == 0  # サービスゲームごとにサーバーが交代
        serve_amp = 0.8 if near_serves else 0.3

        def serve_prep(at):
            # サーブ前のボールつき 3 回 → トス → サーブ。手前のボールつきはカメラに近く、かなり大きく聞こえる
            for k in range(3):
                events.append((at - 3.6 + k * 0.7, ball_bounce(0.3 if near_serves else 0.1)))
            events.append((at, racket_hit(serve_amp)))

        serves = []
        if plan == "fault_then_rally":
            serve_prep(t)
            events.append((t + 0.7, ball_bounce(0.06)))
            serves.append(t)
            t += rng.uniform(7, 11)

        serve_prep(t)
        serves.append(t)
        if plan == "ace":
            events.append((t + 0.6, ball_bounce(0.06)))
            points.append({"serves": serves, "end": t, "hits": len(serves)})
        else:
            shots = {"long_rally": 18, "short_rally": 2}.get(plan, rng.integers(3, 8))
            near = near_serves
            for k in range(1, int(shots)):
                gap = 3.3 if rng.random() < 0.08 else rng.uniform(1.0, 2.0)  # たまにロブ
                events.append((t + gap * 0.55, ball_bounce(0.05 if near else 0.08)))
                t += gap
                near = not near
                events.append((t, racket_hit(rng.uniform(0.6, 0.9) if near else rng.uniform(0.2, 0.35))))
            points.append({"serves": serves, "end": t, "hits": int(shots) + len(serves) - 1})
        t += rng.uniform(18, 28)  # ポイント間
    return events, points, t + 5


def make_audio(path, events, duration):
    n = int(SR * duration)
    tt = np.arange(n) / SR
    audio = 0.006 * rng.standard_normal(n)  # 環境ノイズ
    wind = np.convolve(rng.standard_normal(n), np.ones(400) / 400, mode="same")
    audio += 0.6 * wind * (0.5 + 0.5 * np.sin(2 * np.pi * 0.05 * tt))
    # 隣のコートの小さな打球音（不規則）
    other = 3.0
    while other < duration:
        add(audio, other, racket_hit(0.03))
        other += rng.uniform(0.9, 6.0)
    # 話し声（倍音を含む低めの音がゆっくり立ち上がる）
    for _ in range(int(duration / 15)):
        at = rng.uniform(0, duration - 2)
        ln = rng.uniform(0.5, 1.8)
        k = np.arange(int(ln * SR))
        env = np.sin(np.pi * k / k.size) ** 2
        f0 = rng.uniform(110, 220)
        voice = sum(np.sin(2 * np.pi * f0 * h * k / SR) / h for h in range(1, 6))
        add(audio, at, 0.08 * env * voice)
    # 足音
    for _ in range(int(duration / 4)):
        add(audio, rng.uniform(0, duration), ball_bounce(0.03))
    for at, sound in events:
        add(audio, at, sound)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "tennis.mp4"
    events, points, duration = build_match()
    wav = out + ".wav"
    make_audio(wav, events, duration)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={duration:.2f}",
        "-i", wav, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", out,
    ], check=True)
    os.remove(wav)
    with open(out + ".json", "w") as f:
        json.dump({"duration": duration, "points": points}, f, indent=1)
    play = sum(p["end"] - p["serves"][0] for p in points)
    print(f"{out} を作成しました（{duration:.0f} 秒・{len(points)} ポイント・打球区間の合計 {play:.0f} 秒）")


if __name__ == "__main__":
    main()
