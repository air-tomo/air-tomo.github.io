"""テニスの試合動画から、ポイント間（ラリーをしていない時間）を取り除く。

ポイントはサーブから始まる。打球音を検出し、次の順で1ポイントを組み立てる。
1. 間隔の短い打球を1つのラリーとしてまとめる（ラリー中の打球間隔は 1〜3 秒、ポイント間は 20〜25 秒）
2. サーブ前のボールつき（1 秒未満のリズムで続く、サーブより小さい音）を除き、最初の打球をサーブとする
3. フォルト（サーブ1打だけ）のすぐ後にセカンドサーブが続く場合は、同じポイントとしてまとめる。
   1st と 2nd は同じ選手が同じ場所から打つので音の大きさがほぼ同じ（実測で -1〜+2dB）。
   セカンドサーブより明らかに小さい音（サーブ前のボールつき、隣のコートの打球、実測で 3〜9dB 小さい）は
   フォルトではないので取り除く
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
# フォルトとみなす音の大きさの差（1st − 2nd、元の音量 dB）。実際の試合動画で、本当のフォルトは -1.0〜+2.3dB、
# サーブ直前のボールつきや隣のコートの打球を誤ってフォルトとした例は -3.3〜-9.4dB だった
FAULT_QUIETER_DB = 3.0  # 1st がこれより小さければフォルトではない（ボールつき・隣のコートの音）
FAULT_LOUDER_DB = 4.0  # 1st がこれより大きければ、後ろはセカンドサーブではない（物音など）
# ポイントの合間にカメラの近くでボールを拾う音・足音・ボールつきが 1〜2 秒おきに続くと、前のラリーと次のサーブが
# つながってしまう（実際の試合動画で、ポイント間が 12 秒しかない場面）。大きな打球（手前の選手の打球）が
# これだけの間なく、小さな音だけが続いたあとの大きな打球は、新しいポイントのサーブ（またはリターン）とする
LOUD_BELOW_TOP_DB = 6.0  # 大きな打球: 動画内の打球の上位 10% の音量から、これ以内
SERVE_PAUSE_SEC = 9.0  # ラリー中、手前の選手は 2〜4 秒ごとに打つ（弱い打球が続いても 8 秒未満だった）
PRE_SERVE_SEC = 1.6  # 新しいポイントの大きな打球の直前、これ以内の小さな音は奥の選手のサーブとして残す
RALLY_TAIL_SEC = 2.5  # 最後の大きな打球のあと、これ以内の小さな音は前のポイントの続き（奥の選手の最後の打球）


@dataclass
class TennisSettings:
    pre_sec: float = 1.5  # サーブの何秒前から残すか（トスからサーブを打つまで）
    post_sec: float = 2.0  # 最後の打球の何秒後まで残すか（ボールの行方・リアクション）
    sensitivity: int = 6  # 1(鈍い)〜10(敏感)。遠い側の選手の打球も拾うため、ゴルフより少し高め
    max_shot_gap_sec: float = 5.0  # 打球の間隔がこれ以下なら同じラリー（ロブや、奥の選手の小さな打球を拾えなかった場合を考えて長め）
    min_hits: int = 2  # ラリーとみなす最少の打球数（サーブ＋リターン）
    keep_serve_only: bool = True  # サーブだけで終わったポイント（エースなど）も残す
    second_serve_gap_sec: float = 12.0  # フォルトからセカンドサーブまでの最大間隔（ポイント間より短い）
    merge_gap_sec: float = 3.0  # 切り出した区間の間がこれより短ければつなげる（細切れ防止）


def _trim_quiet(g):
    """ラリーの前後にある小さな音を取り除く。g は (時刻, 強さ, 音量) のリスト。"""
    ref = np.percentile([h[1] for h in g], 75)
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
        if j > i and j + 1 < len(g) and max(h[1] for h in g[i:j + 1]) < g[j + 1][1] - BOUNCE_QUIETER_DB:
            if i > start:
                out.append(g[start:i])
            start = i = j + 1
        else:
            i = j + 1
    out.append(g[start:])
    return [part for part in out if part]


def _split_at_pauses(g, loud):
    """小さな音だけが SERVE_PAUSE_SEC 以上続いたあとの大きな打球で区切る。間の小さな音（ボール拾い・足音）は除く。"""
    out, cur, last_loud = [], [], None
    for h in g:
        if h[2] >= loud:
            since = h[0] - (last_loud if last_loud is not None else cur[0][0] if cur else h[0])
            if cur and since >= SERVE_PAUSE_SEC:
                tail = [x for x in cur if last_loud is not None and x[0] - last_loud <= RALLY_TAIL_SEC]
                if tail:
                    out.append(tail)
                cur = [x for x in cur if h[0] - x[0] <= PRE_SERVE_SEC]
            last_loud = h[0]
        cur.append(h)
    out.append(cur)
    return [part for part in out if part]


def find_points(times, strengths, settings, levels=None):
    """打球からポイントを組み立てる。levels は各打球の元の音量 (dB)。強さ (strengths) は背景からの差。

    各ポイントは {start, end, hits, parts}。parts はサーブごとの区間
    （フォルトがあれば [1stサーブ, セカンドサーブ〜]）で、各要素は {start, end, hits, fault}。
    """
    groups = []
    if levels is None:
        levels = strengths
    for t, st, lv in zip(times, strengths, levels):
        if groups and t - groups[-1][-1][0] <= settings.max_shot_gap_sec:
            groups[-1].append((t, st, lv))
        else:
            groups.append([(t, st, lv)])
    loud = np.percentile(levels, 90) - LOUD_BELOW_TOP_DB if len(levels) else 0.0
    groups = [_trim_quiet(part) for g in groups for p in _split_at_pauses(g, loud) for part in _split_at_serves(p)]

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
        before_serve = (nxt is not None and len(g) <= MAX_FAULT_HITS
                        and nxt[0][0] - g[-1][0] <= settings.second_serve_gap_sec)
        diff = g[0][2] - nxt[0][2] if before_serve else 0.0
        loudest = max(h[2] for h in g)
        if before_serve and loudest < loud and loudest - nxt[0][2] < -FAULT_QUIETER_DB:
            # サーブの直前の小さな音（ボールつき・隣のコートの打球）。ポイントには入れない
            i += 1
            continue
        if before_serve and is_serve(g) and diff <= FAULT_LOUDER_DB:
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
    # 打球の時刻から包絡線のフレームに戻して、元の音量を読む
    levels = [float(env[min(len(env) - 1, int(round(t / swing.HOP_SEC - 0.5)))]) for t in times]
    points = find_points(times, strengths, settings, levels)
    segments, labels = build_segments(points, duration, settings)
    return {"hits": times, "rallies": points, "segments": segments, "labels": labels}
