/*
 * 打球音の検出と、ゴルフのスイング／テニスのラリーの切り出し区間の計算。
 * golf-swing-app/swing.py・tennis.py と同じ方法・同じ初期値。
 */
(function (root) {
  'use strict';

  const SR = 22050; // 解析用のサンプルレート
  const HOP = 110; // 包絡線のフレーム間隔 (約5ms)
  const HOP_SEC = HOP / SR;

  // ---- 音量の包絡線 ----

  function createAccumulator(durationSec) {
    const n = Math.ceil(durationSec / HOP_SEC) + 2;
    return { sum: new Float64Array(n), count: new Uint16Array(n) };
  }

  /* 動画の startSample サンプル目から始まる PCM を加える。打球音は鋭い広帯域の衝撃音なので、
     差分をとって風切り音や話し声などの低域成分を抑える。先頭 skip サンプルは捨てる。 */
  function accumulate(acc, pcm, startSample, skip) {
    const n = acc.sum.length;
    for (let k = Math.max(1, skip || 0); k < pcm.length; k++) {
      const f = Math.floor((startSample + k) / HOP);
      if (f < 0) continue;
      if (f >= n) break;
      const d = pcm[k] - pcm[k - 1];
      acc.sum[f] += d * d;
      acc.count[f]++;
    }
  }

  function envelope(acc) {
    const n = acc.sum.length;
    const env = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      env[i] = 10 * Math.log10((acc.count[i] ? acc.sum[i] / acc.count[i] : 0) + 1e-10);
    }
    return env;
  }

  // numpy.percentile と同じ線形補間（sorted は昇順）
  function percentileSorted(sorted, q) {
    if (!sorted.length) return 0;
    const pos = (sorted.length - 1) * q / 100;
    const lo = Math.floor(pos), hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }
  function percentile(values, q) {
    return percentileSorted(Float64Array.from(values).sort(), q);
  }

  /* 長い動画でも速いよう、間引いた点で中央値を求めて線形補間する */
  function movingMedian(x, size) {
    const step = Math.max(1, size >> 3), half = size >> 1, n = x.length;
    const centers = [], values = [];
    for (let c = 0; c < n; c += step) {
      centers.push(c);
      values.push(percentileSorted(x.slice(Math.max(0, c - half), Math.min(n, c + half + 1)).sort(), 50));
    }
    const out = new Float32Array(n);
    let k = 0;
    for (let i = 0; i < n; i++) {
      while (k + 1 < centers.length && centers[k + 1] <= i) k++;
      if (k + 1 >= centers.length) { out[i] = values[k]; continue; }
      const r = (i - centers[k]) / (centers[k + 1] - centers[k]);
      out[i] = values[k] + (values[k + 1] - values[k]) * r;
    }
    return out;
  }

  /* 設定に関係なく使える値を先に計算しておく（設定を変えたときの再計算を一瞬にするため） */
  function prepare(env) {
    const n = env.length;
    const background = movingMedian(env, Math.floor(2.0 / HOP_SEC));
    const prominence = new Float32Array(n), attack = new Float32Array(n);
    const af = Math.max(1, Math.floor(0.015 / HOP_SEC));
    for (let i = 0; i < n; i++) {
      prominence[i] = env[i] - background[i];
      attack[i] = env[i] - env[i >= af ? i - af : 0];
    }
    const top = percentileSorted(Float32Array.from(env).sort(), 99.9);
    return { env: env, prominence: prominence, attack: attack, top: top, duration: n * HOP_SEC };
  }

  function frameToSec(i) { return Math.round((i + 0.5) * HOP_SEC * 1000) / 1000; }

  /* 打球音の時刻 (秒) と強さ (背景からの dB) */
  function detectHits(prep, sensitivity, minGapSec) {
    const s = Math.min(10, Math.max(1, Math.round(sensitivity)));
    const promDb = 30 - 1.8 * s; // 感度 5 で 21dB
    const attackDb = 18 - 1.0 * s; // 感度 5 で 13dB
    // 遠くのコートや打席の小さな音は無視する
    const floor = prep.top - (40 - 2 * s);
    const minGap = Math.max(1, Math.floor(minGapSec / HOP_SEC));
    const env = prep.env, prom = prep.prominence, att = prep.attack;

    const times = [], strengths = [];
    let groupLast = -Infinity, peak = -1;
    const flush = function () {
      if (peak >= 0) { times.push(frameToSec(peak)); strengths.push(Math.round(prom[peak] * 10) / 10); }
    };
    for (let i = 0; i < env.length; i++) {
      if (!(prom[i] > promDb && att[i] > attackDb && env[i] > floor)) continue;
      if (i - groupLast >= minGap) { flush(); peak = i; } else if (env[i] > env[peak]) peak = i;
      groupLast = i;
    }
    flush();
    return { times: times, strengths: strengths };
  }

  function fmt(sec) {
    sec = Math.max(0, Math.floor(sec));
    const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    const p = function (x) { return (x < 10 ? '0' : '') + x; };
    return h ? h + ':' + p(m) + ':' + p(s) : m + ':' + p(s);
  }

  // ---- ゴルフ ----

  const GOLF = { preSec: 3, postSec: 2, sensitivity: 5, minIntervalSec: 5 };

  function golfClips(prep, st) {
    const impacts = detectHits(prep, st.sensitivity, st.minIntervalSec).times;
    const clips = [];
    impacts.forEach(function (t, i) {
      const start = Math.max(0, t - st.preSec), end = Math.min(prep.duration, t + st.postSec);
      if (end - start < 0.2) return;
      const last = clips[clips.length - 1];
      if (last && start <= last.end) { last.end = Math.max(last.end, end); last.last = i; }
      else clips.push({ start: start, end: end, first: i, last: i, at: t });
    });
    clips.forEach(function (c) {
      c.label = c.first === c.last ? 'スイング ' + (c.first + 1) : 'スイング ' + (c.first + 1) + '〜' + (c.last + 1);
      c.detail = '打球 ' + fmt(c.at);
    });
    return { clips: clips, count: impacts.length, unit: 'スイング' };
  }

  // ---- テニス ----
  // ポイントはサーブから始まる（golf-swing-app/tennis.py と同じ組み立て方）
  // 1. 間隔の短い打球を1つのラリーにまとめる（ラリー中は 1〜3 秒、ポイント間は 20〜25 秒）
  // 2. サーブ前のボールつき（1 秒未満のリズムで続く、サーブより小さい音）を除き、最初の打球をサーブとする
  // 3. フォルト（サーブ1打だけ）のすぐ後にセカンドサーブが続けば同じポイント。1st と 2nd は同じ選手が同じ場所から
  //    打つので音の大きさがほぼ同じ。セカンドサーブより明らかに小さい音（ボールつき・隣のコートの打球）はフォルトではない
  // 4. 各サーブのトス（約 1.5 秒前）から最後の打球の少し後までを残す

  const TENNIS = {
    preSec: 1.5, postSec: 2, sensitivity: 6, maxShotGapSec: 5, minHits: 2, keepServeOnly: true,
    secondServeGapSec: 12, mergeGapSec: 3,
  };
  const HIT_MIN_GAP_SEC = 0.3; // これより近い打球音は同じ1打（反響など）
  const QUIET_HIT_DB = 12; // ラリーの典型的な打球より、これ以上小さい音は前後から除く
  const BOUNCE_GAP_SEC = 1.0; // サーブ前のボールつきの間隔
  const BOUNCE_QUIETER_DB = 3; // ボールつきはサーブよりこれ以上小さい
  const MAX_FAULT_HITS = 2; // フォルトの打球数（サーブ＋相手が打ち返した1打まで）
  // フォルトとみなす音の大きさの差（1st − 2nd、元の音量 dB）。実際の試合動画で、本当のフォルトは -1.0〜+2.3dB、
  // サーブ直前のボールつきや隣のコートの打球を誤ってフォルトとした例は -3.3〜-9.4dB だった
  const FAULT_QUIETER_DB = 3.0; // 1st がこれより小さければフォルトではない（ボールつき・隣のコートの音）
  const FAULT_LOUDER_DB = 4.0; // 1st がこれより大きければ、後ろはセカンドサーブではない（物音など）

  function trimQuiet(g) {
    const ref = percentile(g.map(function (h) { return h.s; }), 75);
    while (g.length > 1 && g[0].s < ref - QUIET_HIT_DB) g = g.slice(1);
    while (g.length > 1 && g[g.length - 1].s < ref - QUIET_HIT_DB) g = g.slice(0, -1);
    return g;
  }

  /* サーブ前のボールつきを除き、そこで区切る（フォルト後にボールをついてセカンドサーブを打つ場合も分かれる） */
  function splitAtServes(g) {
    const out = [];
    let start = 0, i = 0;
    while (i < g.length) {
      let j = i;
      while (j + 1 < g.length && g[j + 1].t - g[j].t < BOUNCE_GAP_SEC) j++;
      let loudest = -Infinity;
      for (let k = i; k <= j; k++) loudest = Math.max(loudest, g[k].s);
      if (j > i && j + 1 < g.length && loudest < g[j + 1].s - BOUNCE_QUIETER_DB) {
        if (i > start) out.push(g.slice(start, i));
        start = i = j + 1;
      } else {
        i = j + 1;
      }
    }
    out.push(g.slice(start));
    return out.filter(function (x) { return x.length; });
  }

  /* levels は各打球の元の音量 (dB)、strengths は背景からの差 */
  function findPoints(times, strengths, st, levels) {
    levels = levels || strengths;
    let groups = [];
    times.forEach(function (t, i) {
      const g = groups[groups.length - 1], h = { t: t, s: strengths[i], lv: levels[i] };
      if (g && t - g[g.length - 1].t <= st.maxShotGapSec) g.push(h);
      else groups.push([h]);
    });
    groups = [].concat.apply([], groups.map(splitAtServes)).map(trimQuiet);

    const multi = groups.filter(function (g) { return g.length >= Math.max(2, st.minHits); });
    // サーブだけの打球は、他のラリーの1打目（サーブ）並みに大きい音かで物音と区別する（奥側のサーブも拾えるよう小さめを基準に）
    const serveLevel = multi.length ? percentile(multi.map(function (g) { return g[0].s; }), 20) - 3 : null;
    const isServe = function (g) { return serveLevel !== null && g[0].s >= serveLevel; };
    const part = function (g, fault) { return { start: g[0].t, end: g[g.length - 1].t, hits: g.length, fault: fault }; };

    const points = [];
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i], next = groups[i + 1];
      const beforeServe = next && g.length <= MAX_FAULT_HITS && next[0].t - g[g.length - 1].t <= st.secondServeGapSec;
      const diff = beforeServe ? g[0].lv - next[0].lv : 0;
      // サーブの直前の小さな音（ボールつき・隣のコートの打球）。ポイントには入れない
      if (beforeServe && diff < -FAULT_QUIETER_DB) continue;
      if (beforeServe && isServe(g) && diff <= FAULT_LOUDER_DB) {
        const first = part(g, true), second = part(next, false);
        points.push({ start: first.start, end: second.end, hits: first.hits + second.hits, parts: [first, second] });
        i++;
        continue;
      }
      if (g.length >= st.minHits || (g.length === 1 && st.keepServeOnly && isServe(g))) {
        const p = part(g, false);
        points.push({ start: p.start, end: p.end, hits: p.hits, parts: [p] });
      }
    }
    return points;
  }

  function tennisClips(prep, st) {
    const hits = detectHits(prep, st.sensitivity, HIT_MIN_GAP_SEC);
    // 打球の時刻から包絡線のフレームに戻して、元の音量を読む
    const levels = hits.times.map(function (t) {
      return prep.env[Math.min(prep.env.length - 1, Math.round(t / HOP_SEC - 0.5))];
    });
    const points = findPoints(hits.times, hits.strengths, st, levels);
    const clips = [];
    points.forEach(function (p, pi) {
      p.parts.forEach(function (part, ki) {
        const start = Math.max(0, part.start - st.preSec), end = Math.min(prep.duration, part.end + st.postSec);
        const last = clips[clips.length - 1];
        if (last && start - last.end < st.mergeGapSec) {
          last.end = Math.max(last.end, end);
          last.members.push([pi, ki]);
        } else {
          clips.push({ start: start, end: end, at: part.start, members: [[pi, ki]] });
        }
      });
    });
    clips.forEach(function (c) {
      const first = c.members[0], last = c.members[c.members.length - 1];
      const p = points[first[0]];
      if (first[0] !== last[0]) {
        c.label = 'ポイント ' + (first[0] + 1) + '〜' + (last[0] + 1);
        c.detail = 'ポイントの間が短いためつなげています';
      } else if (c.members.length === 1 && p.parts.length > 1) {
        const part = p.parts[first[1]];
        c.label = 'ポイント ' + (first[0] + 1) + (part.fault ? '・1st サーブ（フォルト）' : '・セカンドサーブ');
        c.detail = part.hits + ' 打';
      } else {
        c.label = 'ポイント ' + (first[0] + 1);
        c.detail = p.hits + ' 打' + (p.parts.length > 1 ? '（フォルト→セカンドサーブ）' : p.hits === 1 ? '（サーブのみ）' : '');
      }
    });
    return { clips: clips, count: points.length, unit: 'ポイント', points: points };
  }

  const Detect = {
    SR: SR, HOP_SEC: HOP_SEC, GOLF: GOLF, TENNIS: TENNIS,
    createAccumulator: createAccumulator, accumulate: accumulate, envelope: envelope, prepare: prepare,
    detectHits: detectHits, golfClips: golfClips, tennisClips: tennisClips, fmt: fmt,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = Detect;
  else root.Detect = Detect;
})(typeof self !== 'undefined' ? self : this);
