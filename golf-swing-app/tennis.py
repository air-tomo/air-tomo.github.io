"""テニスの試合動画から、ポイント間（ラリーをしていない時間）を取り除く。

ポイントはサーブから始まる。打球音を検出し、次の順で1ポイントを組み立てる。
1. 間隔の短い打球を1つのラリーとしてまとめる（ラリー中の打球間隔は 1〜3 秒、ポイント間は 20〜25 秒）
2. サーブ前のボールつき（1 秒未満のリズムで続く、サーブより小さい音）を除き、最初の打球をサーブとする
3. フォルト（サーブ1打だけ）のすぐ後にセカンドサーブが続く場合は、同じポイントとしてまとめる
4. 各サーブのトス（約 1.5 秒前）から、最後の打球の少し後までを残す。フォルトとセカンドサーブの間の待ち時間は除く
"""

from dataclasses import dataclass

import numpy as np

import swing

HIT_MIN_GAP_SEC = 0.3  # これより近い打球音は同じ1打（反響など）とみなす
QUIET_HIT_DB = 12.0  # ラリーの典型的な打球より、これ以上小さい音は前後から取り除く
BOUNCE_GAP_SEC = 1.0  # サーブ前のボールつきの間隔（ラリーの打球間隔より短い）
BOUNCE_QUIETER_DB = 3.0  # ボールつきはサーブよりこれ以上小さい
MAX_FAULT_HITS = 2  # フォルトの打球数（サーブ＋相手が打ち返した1打まで）


@dataclass
class TennisSettings:
    pre_sec: float = 1.5  # サーブの何秒前から残すか（トスからサーブを打つまで）
    post_sec: float = 2.0  # 最後の打球の何秒後まで残すか（ボールの行方・リアクション）
    sensitivity: int = 6  # 1(鈍い)〜10(敏感)。遠い側の選手の打球も拾うため、ゴルフより少し高め
    max_shot_gap_sec: float = 4.0  # 打球の間隔がこれ以下なら同じラリー（ロブを考えて長め）
    min_hits: int = 2  # ラリーとみなす最少の打球数（サーブ＋リターン）
    keep_serve_only: bool = True  # サーブだけで終わったポイント（エースなど）も残す
    second_serve_gap_sec: float = 12.0  # フォルトからセカンドサーブまでの最大間隔（ポイント間より短い）
    merge_gap_sec: float = 3.0  # 切り出した区間の間がこれより短ければつなげる（細切れ防止）


def _trim_quiet(g):
    """ラリーの前後にある小さな音を取り除く。g は (時刻, 強さ) のリスト。"""
    ref = np.percentile([st for _, st in g], 75)
    while len(g) > 1 and g[0][1] < ref - QUIET_HIT_DB:
        g = g[1:]
    while len(g) > 1 and g[-1][1] < ref - QUIET_HIT_DB:
        g = g[:-1]
    return g


def _split_at_serves(g):
    """サーブ前のボールつきを取り除き、そこで区切る。

    ボールつきは 1 秒未満の間隔で 2 回以上続き、トスの間を空けて、それより大きなサーブの音が来る。
    フォルトの後にボールをついてセカンドサーブを打つ場合も、ここでフォルトと分かれる。
    """
    out, start, i = [], 0, 0
    while i < len(g):
        j = i
        while j + 1 < len(g) and g[j + 1][0] - g[j][0] < BOUNCE_GAP_SEC:
            j += 1
        if j > i and j + 1 < len(g) and max(st for _, st in g[i:j + 1]) < g[j + 1][1] - BOUNCE_QUIETER_DB:
            if i > start:
                out.append(g[start:i])
            start = i = j + 1
        else:
            i = j + 1
    out.append(g[start:])
    return [part for part in out if part]


def find_points(times, strengths, settings):
    """打球からポイントを組み立てる。

    各ポイントは {start, end, hits, parts}。parts はサーブごとの区間
    （フォルトがあれば [1stサーブ, セカンドサーブ〜]）で、各要素は {start, end, hits, fault}。
    """
    groups = []
    for t, st in zip(times, strengths):
        if groups and t - groups[-1][-1][0] <= settings.max_shot_gap_sec:
            groups[-1].append((t, st))
        else:
            groups.append([(t, st)])
    groups = [_trim_quiet(part) for g in groups for part in _split_at_serves(g)]

    multi = [g for g in groups if len(g) >= max(2, settings.min_hits)]
    # サーブだけの打球を物音と区別するため、他のラリーの1打目（サーブ）並みに大きい音かを見る。
    # 奥側の選手のサーブは小さく聞こえるので、小さめのサーブを基準にする
    serve_level = np.percentile([g[0][1] for g in multi], 20) - 3.0 if multi else None

    def is_serve(g):
        return serve_level is not None and g[0][1] >= serve_level

    points = []
    i = 0
    while i < len(groups):
        g = groups[i]
        part = {"start": g[0][0], "end": g[-1][0], "hits": len(g), "fault": False}
        nxt = groups[i + 1] if i + 1 < len(groups) else None
        if (nxt is not None and len(g) <= MAX_FAULT_HITS and is_serve(g)
                and nxt[0][0] - g[-1][0] <= settings.second_serve_gap_sec):
            # フォルト → セカンドサーブ（同じポイント）
            part["fault"] = True
            second = {"start": nxt[0][0], "end": nxt[-1][0], "hits": len(nxt), "fault": False}
            points.append({"start": part["start"], "end": second["end"],
                           "hits": part["hits"] + second["hits"], "parts": [part, second]})
            i += 2
            continue
        keep = len(g) >= settings.min_hits or (len(g) == 1 and settings.keep_serve_only and is_serve(g))
        if keep:
            points.append({"start": part["start"], "end": part["end"], "hits": part["hits"], "parts": [part]})
        i += 1
    return points


def _part_name(point, part_index):
    if len(point["parts"]) == 1:
        return ""
    return "（1st・フォルト）" if part_index == 0 else "（セカンドサーブ）"


def build_segments(points, duration, settings):
    """各サーブのトスから最後の打球の少し後までを切り出し区間にする。
    返り値は (区間のリスト, 区間ごとのラベル)。"""
    segments, members = [], []
    for pi, p in enumerate(points):
        for ki, part in enumerate(p["parts"]):
            start = max(0.0, part["start"] - settings.pre_sec)
            end = min(duration, part["end"] + settings.post_sec)
            if segments and start - segments[-1][1] < settings.merge_gap_sec:
                segments[-1][1] = max(segments[-1][1], end)
                members[-1].append((pi, ki))
            else:
                segments.append([start, end])
                members.append([(pi, ki)])
    labels = []
    for m in members:
        (p0, k0), (p1, k1) = m[0], m[-1]
        if p0 != p1:
            name = f"ポイント {p0 + 1}〜{p1 + 1}"
        elif len(m) == 1:
            name = f"ポイント {p0 + 1}{_part_name(points[p0], k0)}"
        else:
            name = f"ポイント {p0 + 1}"
        labels.append(f"{name}（元 {swing.format_time(points[p0]['parts'][k0]['start'])}）")
    return [(round(a, 3), round(b, 3)) for a, b in segments], labels


def analyze(env, duration, settings):
    """音量の包絡線から、ポイントと切り出し区間を求める。"""
    times, strengths = swing.detect_hits(env, settings.sensitivity, HIT_MIN_GAP_SEC)
    points = find_points(times, strengths, settings)
    segments, labels = build_segments(points, duration, settings)
    return {"hits": times, "rallies": points, "segments": segments, "labels": labels}
