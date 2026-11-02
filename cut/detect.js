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

  // ポイント間は通常 20〜25 秒あり、ラリー中の打球間隔（1〜3 秒）よりずっと長い
  const TENNIS = {
    preSec: 2.5, postSec: 2, sensitivity: 6, maxShotGapSec: 4, minHits: 2, keepServeOnly: true, mergeGapSec: 3,
  };
  const HIT_MIN_GAP_SEC = 0.3;
  const QUIET_HIT_DB = 12;

  function groupRallies(times, strengths, st) {
    const groups = [];
    times.forEach(function (t, i) {
      const g = groups[groups.length - 1];
      if (g && t - g[g.length - 1].t <= st.maxShotGapSec) g.push({ t: t, s: strengths[i] });
      else groups.push([{ t: t, s: strengths[i] }]);
    });
    // サーブ前のボールつきなど、ラリーの前後にある小さな音を取り除く
    const trimmed = groups.map(function (g) {
      const ref = percentile(g.map(function (h) { return h.s; }), 75);
      while (g.length > 1 && g[0].s < ref - QUIET_HIT_DB) g = g.slice(1);
      while (g.length > 1 && g[g.length - 1].s < ref - QUIET_HIT_DB) g = g.slice(0, -1);
      return g;
    });
    const multi = trimmed.filter(function (g) { return g.length >= Math.max(2, st.minHits); });
    // サーブだけのポイントは、他のラリーの1打目（サーブ）並みに大きい音だけ残す（奥側のサーブも拾えるよう小さめを基準に）
    const serveLevel = multi.length ? percentile(multi.map(function (g) { return g[0].s; }), 20) - 3 : null;
    const rallies = [];
    trimmed.forEach(function (g) {
      let keep = g.length >= st.minHits;
      if (!keep && g.length === 1 && st.keepServeOnly && serveLevel !== null) keep = g[0].s >= serveLevel;
      if (keep) rallies.push({ start: g[0].t, end: g[g.length - 1].t, hits: g.length });
    });
    return rallies;
  }

  function tennisClips(prep, st) {
    const hits = detectHits(prep, st.sensitivity, HIT_MIN_GAP_SEC);
    const rallies = groupRallies(hits.times, hits.strengths, st);
    const clips = [];
    rallies.forEach(function (r, i) {
      const start = Math.max(0, r.start - st.preSec), end = Math.min(prep.duration, r.end + st.postSec);
      const last = clips[clips.length - 1];
      if (last && start - last.end < st.mergeGapSec) {
        last.end = Math.max(last.end, end); last.last = i; last.hits += r.hits;
      } else {
        clips.push({ start: start, end: end, first: i, last: i, hits: r.hits, at: r.start });
      }
    });
    clips.forEach(function (c) {
      c.label = c.first === c.last ? 'ポイント ' + (c.first + 1) : 'ポイント ' + (c.first + 1) + '〜' + (c.last + 1);
      c.detail = c.hits + ' 打' + (c.hits === 1 ? '（サーブのみ）' : '');
    });
    return { clips: clips, count: rallies.length, unit: 'ポイント' };
  }

  const Detect = {
    SR: SR, HOP_SEC: HOP_SEC, GOLF: GOLF, TENNIS: TENNIS,
    createAccumulator: createAccumulator, accumulate: accumulate, envelope: envelope, prepare: prepare,
    detectHits: detectHits, golfClips: golfClips, tennisClips: tennisClips, fmt: fmt,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = Detect;
  else root.Detect = Detect;
})(typeof self !== 'undefined' ? self : this);
