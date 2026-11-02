"""テニスの試合動画から、ポイント間（ラリーをしていない時間）を取り除く。

打球音を検出し、間隔の短い打球を1つのラリー（ポイント）としてまとめる。
ポイント間は通常 20〜25 秒あり、ラリー中の打球間隔（1〜3 秒）よりずっと長いので、
打球の間隔だけでラリーの区切りが分かる。
"""

from dataclasses import dataclass

import numpy as np

import swing

HIT_MIN_GAP_SEC = 0.3  # これより近い打球音は同じ1打（反響など）とみなす
QUIET_HIT_DB = 12.0  # ラリーの典型的な打球より、これ以上小さい音は前後から取り除く


@dataclass
class TennisSettings:
    pre_sec: float = 2.5  # サーブの何秒前から残すか（トスとサーブ動作が入る長さ）
    post_sec: float = 2.0  # 最後の打球の何秒後まで残すか（ボールの行方・リアクション）
    sensitivity: int = 6  # 1(鈍い)〜10(敏感)。遠い側の選手の打球も拾うため、ゴルフより少し高め
    max_shot_gap_sec: float = 4.0  # 打球の間隔がこれ以下なら同じラリー（ロブを考えて長め）
    min_hits: int = 2  # ラリーとみなす最少の打球数（サーブ＋リターン）
    keep_serve_only: bool = True  # サーブだけで終わったポイント（エース・フォルト）も残す
    merge_gap_sec: float = 3.0  # 切り出した区間の間がこれより短ければつなげる（細切れ防止）


def group_rallies(times, strengths, settings):
    """打球をラリーにまとめる。各ラリーは {start, end, hits} の辞書。"""
    groups = []
    for t, st in zip(times, strengths):
        if groups and t - groups[-1][-1][0] <= settings.max_shot_gap_sec:
            groups[-1].append((t, st))
        else:
            groups.append([(t, st)])

    trimmed = []
    for g in groups:
        # サーブ前のボールつきなど、ラリーの前後にある小さな音を取り除く
        ref = np.percentile([st for _, st in g], 75)
        while len(g) > 1 and g[0][1] < ref - QUIET_HIT_DB:
            g = g[1:]
        while len(g) > 1 and g[-1][1] < ref - QUIET_HIT_DB:
            g = g[:-1]
        trimmed.append(g)

    multi = [g for g in trimmed if len(g) >= max(2, settings.min_hits)]
    # サーブだけのポイントは打球音が1つしかない。物音と区別するため、
    # 他のラリーの1打目（サーブ）と同じくらい大きい音だけを残す。
    # 奥側の選手のサーブは小さく聞こえるので、小さめのサーブを基準にする
    serve_level = np.percentile([g[0][1] for g in multi], 20) - 3.0 if multi else None

    rallies = []
    for g in trimmed:
        n = len(g)
        keep = n >= settings.min_hits
        if not keep and n == 1 and settings.keep_serve_only and serve_level is not None:
            keep = g[0][1] >= serve_level
        if keep:
            rallies.append({"start": g[0][0], "end": g[-1][0], "hits": n})
    return rallies


def build_segments(rallies, duration, settings):
    """ラリーの前後に余白を付けて切り出し区間を作る。
    返り値は (区間のリスト, 区間ごとのラベル)。"""
    segments, members = [], []
    for i, r in enumerate(rallies):
        start = max(0.0, r["start"] - settings.pre_sec)
        end = min(duration, r["end"] + settings.post_sec)
        if segments and start - segments[-1][1] < settings.merge_gap_sec:
            segments[-1][1] = max(segments[-1][1], end)
            members[-1].append(i)
        else:
            segments.append([start, end])
            members.append([i])
    labels = []
    for m in members:
        name = f"ポイント {m[0] + 1}" if len(m) == 1 else f"ポイント {m[0] + 1}〜{m[-1] + 1}"
        labels.append(f"{name}（元 {swing.format_time(rallies[m[0]]['start'])}）")
    return [(round(a, 3), round(b, 3)) for a, b in segments], labels


def analyze(env, duration, settings):
    """音量の包絡線から、ラリーと切り出し区間を求める。"""
    times, strengths = swing.detect_hits(env, settings.sensitivity, HIT_MIN_GAP_SEC)
    rallies = group_rallies(times, strengths, settings)
    segments, labels = build_segments(rallies, duration, settings)
    return {"hits": times, "rallies": rallies, "segments": segments, "labels": labels}
