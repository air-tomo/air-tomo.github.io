"""コマンドラインで動画を処理する。Mac にある長い試合動画を、ブラウザを使わず直接処理できる。

例:
  python cli.py tennis match.mp4                 # match_cut.mp4 を作る
  python cli.py tennis match.mp4 --list          # 検出したポイントを表示するだけ（書き出さない）
  python cli.py tennis match.mp4 --sensitivity 7 --pre 3
  python cli.py golf practice.mov
"""

import argparse
import os
import sys
import tempfile
import time

import swing
import tennis


def progress_bar(label):
    def show(r):
        filled = int(r * 30)
        sys.stdout.write(f"\r{label} [{'#' * filled}{'.' * (30 - filled)}] {int(r * 100):3d}%")
        sys.stdout.flush()
        if r >= 1:
            sys.stdout.write("\n")
    return show


def main():
    parser = argparse.ArgumentParser(description="打球音でゴルフのスイング／テニスのラリーを切り出して1本の MP4 にする")
    parser.add_argument("mode", choices=["tennis", "golf"])
    parser.add_argument("input", help="入力動画")
    parser.add_argument("-o", "--output", help="出力 MP4（省略時は <入力名>_cut.mp4）")
    parser.add_argument("--list", action="store_true", help="検出結果を表示するだけで書き出さない")
    parser.add_argument("--pre", type=float, help="打球（ポイント）の前に残す秒数")
    parser.add_argument("--post", type=float, help="打球（ポイント）の後に残す秒数")
    parser.add_argument("--sensitivity", type=int, help="検出感度 1〜10")
    parser.add_argument("--max-gap", type=float, help="[テニス] 同じラリーとみなす打球間隔（秒）")
    parser.add_argument("--min-hits", type=int, help="[テニス] ラリーとみなす最少打球数")
    parser.add_argument("--no-serve-only", action="store_true", help="[テニス] サーブだけのポイントを残さない")
    parser.add_argument("--min-interval", type=float, help="[ゴルフ] スイングの最短間隔（秒）")
    args = parser.parse_args()

    if args.mode == "tennis":
        settings = tennis.TennisSettings()
        if args.max_gap is not None:
            settings.max_shot_gap_sec = args.max_gap
        if args.min_hits is not None:
            settings.min_hits = args.min_hits
        if args.no_serve_only:
            settings.keep_serve_only = False
    else:
        settings = swing.DetectSettings()
        if args.min_interval is not None:
            settings.min_interval_sec = args.min_interval
    if args.pre is not None:
        settings.pre_sec = args.pre
    if args.post is not None:
        settings.post_sec = args.post
    if args.sensitivity is not None:
        settings.sensitivity = args.sensitivity

    try:
        info = swing.probe(args.input)
        env = swing.audio_envelope(args.input, info["duration"], on_progress=progress_bar("音声を解析"))
        if args.mode == "tennis":
            result = tennis.analyze(env, info["duration"], settings)
            segments, labels = result["segments"], result["labels"]
            for i, r in enumerate(result["rallies"]):
                note = "（サーブのみ）" if r["hits"] == 1 else ""
                print(f"  ポイント {i + 1:3d}: 元 {swing.format_time(r['start'])}  {r['hits']} 打{note}")
            print(f"{len(result['rallies'])} ポイントを検出しました")
        else:
            impacts = swing.detect_impacts(env, settings)
            segments = swing.build_segments(impacts, info["duration"], settings.pre_sec, settings.post_sec)
            labels = [f"スイング {i + 1}（元 {swing.format_time(t)}）" for i, t in enumerate(impacts)]
            labels = labels if len(labels) == len(segments) else None
            print(f"{len(impacts)} 回のスイングを検出しました: "
                  + "、".join(swing.format_time(t) for t in impacts))
        if not segments:
            raise swing.ProcessingError("打球音が検出できませんでした。--sensitivity を上げてお試しください。")

        kept = sum(b - a for a, b in segments)
        print(f"長さ: {swing.format_time(info['duration'])} → {swing.format_time(kept)}"
              f"（{kept / info['duration'] * 100:.0f}%）")
        if args.list:
            return

        output = args.output or os.path.splitext(args.input)[0] + "_cut.mp4"
        started = time.time()
        with tempfile.TemporaryDirectory() as work_dir:
            swing.cut_and_concat(args.input, segments, output, work_dir, hdr=info["hdr"],
                                 src_bitrate=info["video_bitrate"], labels=labels,
                                 on_progress=progress_bar("書き出し  "))
        print(f"保存しました: {output}（{time.time() - started:.0f} 秒）")
    except swing.ProcessingError as e:
        sys.exit(f"エラー: {e}")


if __name__ == "__main__":
    main()
