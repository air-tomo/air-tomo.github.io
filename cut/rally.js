/*
 * テニスのボール位置（ボール検出モデルの出力）から、バウンド・ポイントの区切り・イン/アウト・サーブ速度を出す。
 * golf-swing-app/tools/court3d/tn_post.py と同じ方法（同じ入力なら同じ結果になるよう合わせてある）。
 * ブラウザ（iPhone / Android）と Node（テスト用）の両方で動く。
 *
 * 座標: 画像は元動画の画素 (u, v)。コートは x = 横 (m, 中央 0)、y = 手前のベースラインからの奥行き (m)、z = 高さ。
 */
(function (root) {
  'use strict';

  const NET_Y = 11.885, SERVICE_N = 5.485, SERVICE_F = 18.285, BASE_F = 23.77, SINGLES = 4.115;

  // ---- 行列とカメラ ----

  function applyH(H, u, v) {
    const w = H[2][0] * u + H[2][1] * v + H[2][2];
    return [(H[0][0] * u + H[0][1] * v + H[0][2]) / w, (H[1][0] * u + H[1][1] * v + H[1][2]) / w];
  }
  function inv3(m) {
    const [a, b, c] = m[0], [d, e, f] = m[1], [g, h, i] = m[2];
    const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
    const det = a * A + b * B + c * C;
    return [[A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
            [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
            [C / det, -(a * h - b * g) / det, (a * e - b * d) / det]];
  }
  /* cam = {K, R, t, H}（tn_post.py の cam npz と同じ中身） */
  function camera(cam) {
    const Hinv = inv3(cam.H);
    return {
      ground: function (u, v) { return applyH(Hinv, u, v); },
      /* 画素 (u, v) を通る視線が奥行き y = yPlane の面と交わる点の高さ (m) */
      rayHeight: function (u, v, yPlane) {
        const R = cam.R, K = cam.K, t = cam.t;
        const C = [0, 1, 2].map(function (j) { return -(R[0][j] * t[0] + R[1][j] * t[1] + R[2][j] * t[2]); });
        const kx = (u - K[0][2]) / K[0][0], ky = (v - K[1][2]) / K[1][1];
        const d = [0, 1, 2].map(function (j) { return R[0][j] * kx + R[1][j] * ky + R[2][j]; });
        if (Math.abs(d[1]) < 1e-9) return null;
        const s = (yPlane - C[1]) / d[1];
        return s > 0 ? C[2] + s * d[2] : null;
      },
      proj: function (X) {
        const R = cam.R, K = cam.K, t = cam.t;
        const c = [0, 1, 2].map(function (r) { return R[r][0] * X[0] + R[r][1] * X[1] + R[r][2] * X[2] + t[r]; });
        const p = [0, 1, 2].map(function (r) { return K[r][0] * c[0] + K[r][1] * c[1] + K[r][2] * c[2]; });
        return [p[0] / p[2], p[1] / p[2]];
      },
    };
  }

  // ---- 小さな数値計算 ----

  function polyfit1(ts, ys) {  // y = a t + b（最小二乗）
    const n = ts.length; let st = 0, sy = 0, stt = 0, sty = 0;
    for (let i = 0; i < n; i++) { st += ts[i]; sy += ys[i]; stt += ts[i] * ts[i]; sty += ts[i] * ys[i]; }
    const a = (n * sty - st * sy) / (n * stt - st * st);
    return [a, (sy - a * st) / n];
  }
  function percentile(values, q) {
    const s = Float64Array.from(values).sort(); if (!s.length) return 0;
    const pos = (s.length - 1) * q / 100, lo = Math.floor(pos), hi = Math.ceil(pos);
    return s[lo] + (s[hi] - s[lo]) * (pos - lo);
  }
  function median(a) { return percentile(a, 50); }
  const r3 = function (x) { return Math.round(x * 1000) / 1000; };
  const r2 = function (x) { return Math.round(x * 100) / 100; };
  const r1 = function (x) { return Math.round(x * 10) / 10; };

  /* 重力と空気抵抗のある 3D の軌道。ts（昇順）での位置を返す */
  function simulate(p0, v0, ts, k, g, dt) {
    k = k == null ? 0.0204 : k; g = g == null ? 9.81 : g; dt = dt || 1 / 600;
    const out = []; let p = p0.slice(), v = v0.slice(), tc = 0;
    for (const T of ts) {
      while (tc + dt <= T + 1e-12) {
        const sp = Math.hypot(v[0], v[1], v[2]);
        const a = [-k * sp * v[0], -k * sp * v[1], -k * sp * v[2] - g];
        v = [v[0] + a[0] * dt, v[1] + a[1] * dt, v[2] + a[2] * dt];
        p = [p[0] + v[0] * dt, p[1] + v[1] * dt, p[2] + v[2] * dt]; tc += dt;
      }
      out.push(p.slice());
    }
    return out;
  }
  /* 頑健な最小二乗（soft L1, Levenberg-Marquardt, 数値微分）。fn(q) は残差の配列 */
  function leastSquares(fn, q0, fScale, iters) {
    fScale = fScale || 1; iters = iters || 60;
    const rho = function (r) { const z = (r / fScale) * (r / fScale); return 2 * (Math.sqrt(1 + z) - 1) * fScale * fScale; };
    const cost = function (r) { let s = 0; for (const x of r) s += rho(x); return s / 2; };
    let q = q0.slice(), r = fn(q), c = cost(r), lam = 1e-3;
    for (let it = 0; it < iters; it++) {
      const n = q.length, m = r.length;
      const w = r.map(function (x) { return 1 / Math.sqrt(Math.sqrt(1 + (x / fScale) * (x / fScale))); });   // IRLS weights
      const J = [];
      for (let j = 0; j < n; j++) {
        const h = 1e-6 * Math.max(1, Math.abs(q[j])); const qq = q.slice(); qq[j] += h;
        const rr = fn(qq); J.push(rr.map(function (x, i) { return (x - r[i]) / h; }));
      }
      const A = [], b = [];
      for (let a = 0; a < n; a++) {
        A.push([]); let s = 0;
        for (let i = 0; i < m; i++) s += J[a][i] * w[i] * w[i] * r[i];
        b.push(-s);
        for (let bb = 0; bb < n; bb++) { let t = 0; for (let i = 0; i < m; i++) t += J[a][i] * w[i] * w[i] * J[bb][i]; A[a].push(t); }
      }
      let improved = false;
      for (let tries = 0; tries < 8; tries++) {
        const M = A.map(function (row, a) { return row.map(function (x, bb) { return a === bb ? x * (1 + lam) + 1e-12 : x; }); });
        const step = solve(M, b); if (!step) { lam *= 10; continue; }
        const qn = q.map(function (x, j) { return x + step[j]; }); const rn = fn(qn), cn = cost(rn);
        if (cn < c) { q = qn; r = rn; const dc = c - cn; c = cn; lam = Math.max(lam / 3, 1e-9); improved = true; if (dc < 1e-9 * (1 + c)) it = iters; break; }
        lam *= 10;
      }
      if (!improved) break;
    }
    return { x: q, fun: r, cost: c };
  }
  function solve(A, b) {  // Gauss elimination with partial pivoting
    const n = b.length, M = A.map(function (row, i) { return row.concat([b[i]]); });
    for (let c = 0; c < n; c++) {
      let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
      if (Math.abs(M[p][c]) < 1e-15) return null;
      const tmp = M[c]; M[c] = M[p]; M[p] = tmp;
      for (let r = 0; r < n; r++) if (r !== c) { const f = M[r][c] / M[c][c]; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; }
    }
    return M.map(function (row, i) { return row[n] / row[i]; });
  }

  // ---- ボールの点列 ----

  /* [[t, u|null, v|null], ...] → 外れ値（前後の点から 80 画素以上離れた点）を除いた [[t, u, v], ...] */
  function clean(pts) {
    const P = pts.filter(function (q) { return q[1] != null; });
    return P.filter(function (q, i) {
      const nb = [P[i - 1], P[i + 1]].filter(function (a) { return a && Math.abs(a[0] - q[0]) < 0.1; });
      return !(nb.length && Math.min.apply(null, nb.map(function (a) { return Math.hypot(q[1] - a[1], q[2] - a[2]); })) > 80);
    });
  }

  /* バウンド: 画像上でボールが下がってから上がる点（V 字）。手前の選手の打球（大きな打球音）の瞬間は除く */
  function findBounces(P, loudHits, cam) {
    const out = [];
    for (let i = 2; i < P.length - 2; i++) {
      const A = P[i - 2], Q = P[i], B = P[i + 2];
      if (B[0] - A[0] > 0.3) continue;
      if (!(Q[2] >= P[i - 1][2] && Q[2] >= P[i + 1][2] && Q[2] - A[2] > 3 && Q[2] - B[2] > 3)) continue;
      if (loudHits.some(function (h) { return Math.abs(Q[0] - h) < 0.07; })) continue;
      let t1 = Q[0], x1 = Q[1], y1 = Q[2];
      // 一番低い点を取り逃すことがあるので、落ちる線と上がる線の交点を使う
      const pre = P.slice(Math.max(0, i - 3), i + 1).filter(function (q) { return t1 - q[0] < 0.25; });
      const post = P.slice(i, i + 4).filter(function (q) { return q[0] - t1 < 0.25; });
      if (pre.length >= 2 && post.length >= 2) {
        const tp = pre.map(function (q) { return q[0]; }), tq = post.map(function (q) { return q[0]; });
        const fx = polyfit1(tp, pre.map(function (q) { return q[1]; })), fy = polyfit1(tp, pre.map(function (q) { return q[2]; }));
        const gx = polyfit1(tq, post.map(function (q) { return q[1]; })), gy = polyfit1(tq, post.map(function (q) { return q[2]; }));
        if (Math.abs(fy[0] - gy[0]) > 1e-6) {
          const tc = (gy[1] - fy[1]) / (fy[0] - gy[0]);
          if (tc >= pre[0][0] - 0.05 && tc <= post[post.length - 1][0] + 0.05) { t1 = tc; x1 = fx[0] * tc + fx[1]; y1 = fy[0] * tc + fy[1]; }
        }
      }
      const g = cam.ground(x1, y1);
      if (!(g[0] > -7 && g[0] < 7 && g[1] > -2 && g[1] < 26)) continue;
      if (out.length && t1 - out[out.length - 1].t < 0.4) continue;
      out.push({ t: r3(t1), uv: [r1(x1), r1(y1)], court: [r2(g[0]), r2(g[1])] });
    }
    return out;
  }

  /* サーブの速度: 打球音の時刻（打点は手前ベースラインの少し後ろ・高さ約 2.6m）から、見えたバウンド地点までの 3D 軌道 */
  function serveSpeed(P, bounce, hit, cam) {
    const obs = P.filter(function (q) { return hit + 0.02 < q[0] && q[0] < bounce.t - 0.01; });
    if (obs.length < 5) return null;
    const ts = obs.map(function (q) { return q[0] - hit; }), tb = bounce.t - hit, gb = [bounce.court[0], bounce.court[1], 0];
    const fn = function (q) {
      const Pp = simulate(q.slice(0, 3), q.slice(3), ts.concat([tb]));
      const r = [];
      for (let i = 0; i < obs.length; i++) { const uv = cam.proj(Pp[i]); r.push(uv[0] - obs[i][1], uv[1] - obs[i][2]); }
      const L = Pp[Pp.length - 1];
      r.push((L[0] - gb[0]) * 40, (L[1] - gb[1]) * 40, (L[2] - gb[2]) * 40, (q[1] + 0.5) / 0.6 * 3, (q[2] - 2.6) / 0.3 * 3);
      return r;
    };
    const s = leastSquares(fn, [0, -0.5, 2.6, gb[0] / tb, (gb[1] + 0.5) / tb, -1.0], 2.0);
    let ss = 0; for (let i = 0; i < 2 * obs.length; i++) ss += s.fun[i] * s.fun[i];
    return { kmh: Math.round(Math.hypot(s.x[3], s.x[4], s.x[5]) * 3.6), rms_px: r2(Math.sqrt(ss / (2 * obs.length))), contact: s.x.slice(0, 3).map(r2) };
  }

  // ---- イン・アウトとポイント ----

  function call(x, y, rect) {
    const inside = Math.min(x - rect[0], rect[1] - x, y - rect[2], rect[3] - y);
    const margin = y < NET_Y ? 0.15 : 0.8;   // 低いカメラでは奥側は ±0.8m 程度しか分からない
    return inside > margin ? 'IN' : inside < -margin ? 'OUT' : 'CLOSE';
  }
  const farBox = function (x, y) { return y >= 11.6 && y <= 19.3 && Math.abs(x) <= 4.7; };
  const nearBox = function (x, y) { return y >= 4.9 && y <= 12.2 && Math.abs(x) <= 4.7; };
  function ballGap(P, t0, t1, gap) {
    gap = gap || 2.0;
    const ts = [t0].concat(P.filter(function (q) { return q[0] >= t0 && q[0] <= t1; }).map(function (q) { return q[0]; }), [t1]);
    for (let i = 0; i + 1 < ts.length; i++) if (ts[i + 1] - ts[i] > gap) return ts[i];
    return null;
  }
  function launch(P, bb, hits) {
    const hs = hits.filter(function (h) { return bb.t - h >= 0.2 && bb.t - h <= 1.3; });
    if (hs.length) return hs[hs.length - 1];
    let t0 = bb.t;
    const seq = P.filter(function (q) { return q[0] <= bb.t; }).map(function (q) { return q[0]; });
    for (let i = seq.length - 1; i >= 0; i--) { if (t0 - seq[i] > 0.2) break; t0 = seq[i]; }
    return bb.t - t0 >= 0.2 ? t0 : null;
  }
  /* サーブ: サービスボックス付近に、平均 60km/h 以上で落ちた球 */
  function serveAt(P, bb, hits) {
    const x = bb.court[0], y = bb.court[1], far = farBox(x, y), near = nearBox(x, y);
    if (!far && !near) return null;
    const tl = launch(P, bb, hits);
    if (tl == null) return null;
    const sy = far ? -0.5 : 24.3, sx = -Math.sign(x) * 1.0;
    const v = Math.hypot(x - sx, y - sy) / Math.max(1e-3, bb.t - tl) * 3.6;
    return v >= 60 ? [tl, far ? 'far' : 'near', v] : null;
  }

  /*
   * 1 つの区間（音で切り出したラリー）を解析する。
   * pts: [[t, u, v], ...]（ボール検出の出力、時刻は秒）、allHits / loudHits: 打球音の時刻、clip: {start, end}
   * 返り値: {ball, bounces, serve, ranges, points}（tn_post.py と同じ形）
   */
  function analyzeClip(pts, allHits, loudHits, clip, cam) {
    const P = clean(pts);
    const bs = findBounces(P, loudHits, cam);
    const hits = allHits.filter(function (h) { return h >= clip.start - 0.5 && h <= clip.end && !bs.some(function (b) { return Math.abs(h - b.t) < 0.08; }); });
    const loud = loudHits.filter(function (h) { return h >= clip.start - 0.5 && h <= clip.end; });
    // 手前の選手のサーブ: 大きな打球音のあと、ボールが画面の高い所から奥のコートへ速く下りる
    const nearServes = loud.filter(function (h) {
      const seg = P.filter(function (q) { return q[0] - h > 0.05 && q[0] - h < 0.5; });
      if (seg.length < 3 || seg[0][2] > 260 || seg[seg.length - 1][2] < 400) return false;
      const d = [];
      for (let i = 1; i < seg.length; i++) d.push(Math.hypot(seg[i][1] - seg[i - 1][1], seg[i][2] - seg[i - 1][2]) / (seg[i][0] - seg[i - 1][0]) / 30);
      return median(d) > 15;
    });
    const events = nearServes.map(function (h) { return { t: h, kind: 'serve_hit' }; })
      .concat(bs.map(function (b) { return { t: b.t, kind: 'bounce', b: b }; }))
      .sort(function (a, b) { return a.t - b.t; });
    const points = []; let cur = null;
    const close = function (c, tEnd) { c.end = r3(tEnd); points.push(c); };
    const start = function (tl, side) { return { serve_t: r3(tl), serve_side: side, bounces: [], end: null, serve: null }; };
    for (const ev of events) {
      if (ev.kind === 'serve_hit') {
        if (cur) {
          const lastT = cur.bounces.length ? cur.bounces[cur.bounces.length - 1].t : cur.serve_t;
          const g = ballGap(P, lastT, ev.t);
          if (g == null) continue;               // ボールが続いている: スマッシュなどでサーブではない
          close(cur, g + 1.0); cur = null;
        }
        cur = start(ev.t, 'far');
        continue;
      }
      const bb = ev.b, x = bb.court[0], y = bb.court[1];
      if (cur && cur.bounces.length) {
        const last = cur.bounces[cur.bounces.length - 1];
        const gapEnd = ballGap(P, last.t, bb.t);
        if (gapEnd != null) { close(cur, gapEnd + 1.0); cur = null; }
      }
      if (cur && !cur.bounces.length && bb.t - cur.serve_t > 1.2) cur.bounces.push({ t: cur.serve_t, court: [0, -1], missing: true });
      if (!cur || !cur.bounces.length) {
        const sv = serveAt(P, bb, hits);
        if (!cur && !sv) continue;               // サーブ前のボールつき、ポイント間の返球
        if (!cur) cur = start(sv[0], sv[1]);
        const side = cur.serve_side;
        const box = x < 0 ? [-SINGLES, 0] : [0, SINGLES];
        const rect = side === 'far' ? [box[0], box[1], NET_Y, SERVICE_F] : [box[0], box[1], SERVICE_N, NET_Y];
        bb.kind = 'serve';
        bb.call = side === 'far' && !farBox(x, y) && y < NET_Y ? 'OUT' : call(x, y, rect);
        cur.bounces.push(bb);
        if (side === 'far' && loud.some(function (h) { return Math.abs(cur.serve_t - h) < 0.05; })) {
          const sp = serveSpeed(P, bb, cur.serve_t, cam);
          if (sp) { sp.t = cur.serve_t; sp.bounce = bb.court; cur.serve = sp; }
        }
        if (bb.call === 'OUT') { close(cur, bb.t + 1.5); cur = null; }   // フォルト
        continue;
      }
      bb.kind = 'rally'; bb.call = call(x, y, [-SINGLES, SINGLES, 0, BASE_F]);
      cur.bounces.push(bb);
      if (bb.call === 'OUT') { close(cur, bb.t + 1.5); cur = null; }
    }
    if (cur) {
      const lastT = cur.bounces.length ? cur.bounces[cur.bounces.length - 1].t : cur.serve_t;
      const g = ballGap(P, lastT, clip.end);
      let e;
      if (g != null) e = g + 1.0;
      else if (cur.bounces.length) {
        const tEnd = cur.bounces[cur.bounces.length - 1].t;
        const after = hits.filter(function (h) { return h - tEnd > 0 && h - tEnd < 1.5; });
        e = (after.length ? after[after.length - 1] : tEnd) + 1.5;
      } else e = lastT + 3.0;
      close(cur, Math.min(clip.end, e));
    }
    const ranges = points.map(function (p) { return [r3(Math.max(clip.start, p.serve_t - 1.2)), r3(Math.min(clip.end, p.end))]; });
    const keep = [];
    points.forEach(function (p) { p.bounces.forEach(function (b) { if (!b.missing) keep.push(b); }); });
    const serve = (points.find(function (p) { return p.serve; }) || {}).serve || null;
    return {
      ball: P, bounces: keep, serve: serve, ranges: ranges,
      points: points.map(function (p) { return { serve_t: p.serve_t, serve_side: p.serve_side, end: p.end, serve: p.serve }; }),
    };
  }

  // ---- 試合全体: サーブ・フォルト・ポイント（tn_post.py と同じ） ----

  function rallyUntil(P, bs, i0, tFrom, hits, clipEnd) {
    const out = []; let lastT = tFrom;
    for (const bb of bs.slice(i0)) {
      const g = ballGap(P, lastT, bb.t);
      if (g != null) return [out, g + 1.0];
      bb.kind = 'rally'; bb.call = call(bb.court[0], bb.court[1], [-SINGLES, SINGLES, 0, BASE_F]);
      out.push(bb); lastT = bb.t;
      if (bb.call === 'OUT') return [out, bb.t + 1.5];
    }
    const g = ballGap(P, lastT, clipEnd);
    if (g != null) return [out, g + 1.0];
    const after = hits.filter(function (h) { return h - lastT > 0 && h - lastT < 1.5; });
    return [out, Math.min(clipEnd, (after.length ? after[after.length - 1] : lastT) + 1.5)];
  }
  /* ラリーでは打球が続く: サーブのバウンドから 4.5 秒以内に打球音が 2 回以上。フォルトのあとは多くても 1 回 */
  function returned(tBounce, hits) { return hits.filter(function (h) { return h - tBounce > 0.1 && h - tBounce < 4.5; }).length >= 2; }

  /*
   * 試合全体を解析する。clips: [{start, end, pts}]（pts はボール検出の出力）、allHits / loudHits: 打球音の時刻。
   * 返り値: {clips: [{ball, bounces, serve, ranges}], points: [{ranges: [[clipIndex, a, b], ...], serves: [{t, side, fault, speed}]}]}
   */
  function analyzeMatch(clips, allHits, loudHits, cam) {
    const C = clips.map(function (c) {
      const P = clean(c.pts), bs = findBounces(P, loudHits, cam);
      const hits = allHits.filter(function (h) { return h >= c.start - 0.5 && h <= c.end && !bs.some(function (b) { return Math.abs(h - b.t) < 0.08; }); });
      const loud = loudHits.filter(function (h) { return h >= c.start - 0.5 && h <= c.end; });
      return { P: P, bs: bs, hits: hits, loud: loud, c: c, res: { ball: P, bounces: [], serve: null, ranges: [] } };
    });
    const attempts = [];
    C.forEach(function (cd, ci) {
      const P = cd.P, bs = cd.bs, hits = cd.hits, loud = cd.loud, list = [];
      loud.forEach(function (h) {     // 手前の選手のサーブ
        const seg = P.filter(function (q) { return q[0] - h > 0.05 && q[0] - h < 0.5; });
        if (seg.length < 3 || seg[0][2] > 260 || seg[seg.length - 1][2] < 400) return;
        const d = []; for (let i = 1; i < seg.length; i++) d.push(Math.hypot(seg[i][1] - seg[i - 1][1], seg[i][2] - seg[i - 1][2]) / (seg[i][0] - seg[i - 1][0]) / 30);
        if (median(d) > 15) list.push({ t: h, side: 'far' });
      });
      bs.forEach(function (bb) {      // 奥の選手のサーブ: 手前のサービスボックス付近へ速く、ベースラインで高い打点（1.8m 以上）
        if (!nearBox(bb.court[0], bb.court[1])) return;
        const sv = serveAt(P, bb, hits); if (!sv) return;
        const first = P.filter(function (q) { return q[0] - sv[0] >= 0 && q[0] - sv[0] < 0.25; });
        const z = first.length ? cam.rayHeight(first[0][1], first[0][2], 23.8) : null;
        if (z == null || z < 1.8) return;
        if (list.some(function (a) { return Math.abs(a.t - sv[0]) < 1.0; })) return;
        list.push({ t: sv[0], side: 'near' });
      });
      // 奥の選手のサーブで音も軌道も取れなかったもの: 手前のサービスボックスに落ちて、手前の選手が 0.2〜1.0 秒後に打ち返す
      bs.forEach(function (bb) {
        if (!nearBox(bb.court[0], bb.court[1]) || list.some(function (a) { return Math.abs(a.t - (bb.t - 0.6)) < 1.2; })) return;
        if (loud.some(function (h) { return h - bb.t > 0.2 && h - bb.t < 1.0; })) list.push({ t: bb.t - 0.6, side: 'near' });
      });
      list.sort(function (a, b) { return a.t - b.t; });
      const dead = function (h) {     // サーブの前 4 秒は、手前の選手の打球もコート内のバウンドもない
        if (loud.some(function (x) { return h - x > 0.4 && h - x < 4.0; })) return false;
        return !bs.some(function (b) { return h - b.t > 0.3 && h - b.t < 4.0 && b.court[1] > 1.5 && b.court[1] < 22.3; });
      };
      list.filter(function (a) { return dead(a.t); }).forEach(function (a) {
        const sb = bs.find(function (b) { return b.t - a.t > 0.25 && b.t - a.t < 1.3; }) || null;
        a.bounce = sb; a.clip = ci;
        if (sb) {
          const x = sb.court[0], y = sb.court[1], box = x < 0 ? [-SINGLES, 0] : [0, SINGLES];
          const rect = a.side === 'far' ? [box[0], box[1], NET_Y, SERVICE_F] : [box[0], box[1], SERVICE_N, NET_Y];
          sb.kind = 'serve';
          sb.call = (a.side === 'far' && y < NET_Y) || (a.side === 'near' && y > NET_Y) ? 'OUT' : call(x, y, rect);
        }
        a.returned = returned(sb ? sb.t : a.t + 0.6, hits);
        attempts.push(a);
      });
    });
    attempts.sort(function (a, b) { return a.t - b.t; });
    const all = [];
    attempts.forEach(function (a) {   // 返球されたサーブの 6 秒以内の「サーブ」はラリー中のスマッシュなど
      const last = all[all.length - 1];
      if (last && last.returned && a.t - last.t < (a.side !== last.side ? 15 : 6)) return;
      all.push(a);
    });
    const groups = [];
    for (let i = 0; i < all.length; i++) {
      const a = all[i], parts = [a], nx = all[i + 1] || null, gapNext = nx ? nx.t - a.t : 1e9;
      // サーバーが立っていた側: サーブはクロスに打つので、入ったボックスの反対側
      const half = function (s) { return s.bounce && Math.abs(s.bounce.court[0]) > 0.5 ? -Math.sign(s.bounce.court[0]) : 0; };
      let fault;
      const same = nx && nx.side === a.side;
      if (same && gapNext < 30 && half(a) && half(nx)) fault = half(a) === half(nx);   // セカンドサーブは同じ側から、次のポイントは反対側から
      else if (same) fault = (!a.returned && gapNext < 25) || gapNext < 15;   // 返球されずに、またサーブを打った
      else fault = false;                                                       // 相手のサーブ: セカンドサーブではない
      if (fault) {     // フォルト
        a.fault = true; if (a.bounce) a.bounce.call = 'OUT';
        parts.push(all[i + 1]); i++;
      }
      const last = parts[parts.length - 1];
      if (parts.length === 2 && !last.returned && last.bounce && last.bounce.call !== 'IN') { last.fault = true; last.bounce.call = 'OUT'; }   // ダブルフォルト
      groups.push(parts);
    }
    const points = groups.map(function (parts) {
      const pr = [];
      parts.forEach(function (a) {
        const cd = C[a.clip], c = cd.c, r = cd.res, sb = a.bounce;
        if (sb) r.bounces.push(sb);
        let end;
        if (a.fault || !a.returned) end = sb ? sb.t + 1.5 : a.t + 2.5;
        else {
          let i0 = sb ? cd.bs.indexOf(sb) + 1 : cd.bs.findIndex(function (b) { return b.t > a.t + 0.3; });
          if (i0 < 0) i0 = cd.bs.length;
          const nxt = all.find(function (b) { return b.t > a.t; });
          const nxtT = nxt ? nxt.t : c.end;
          const rr = rallyUntil(cd.P, cd.bs.filter(function (b) { return b.t < nxtT; }), i0, sb ? sb.t : a.t, cd.hits, c.end);
          rr[0].forEach(function (b) { r.bounces.push(b); }); end = rr[1];
        }
        const rg = [r3(Math.max(c.start, a.t - 1.2)), r3(Math.min(c.end, end))];
        pr.push([a.clip].concat(rg)); r.ranges.push(rg);
        if (a.side === 'far' && sb && !a.fault) {
          const sp = serveSpeed(cd.P, sb, a.t, cam);
          if (sp) { sp.t = r3(a.t); sp.bounce = sb.court; a.speed = sp; r.serve = r.serve || sp; }
        }
      });
      return { ranges: pr, serves: parts.map(function (a) { return { t: r3(a.t), side: a.side, fault: !!a.fault, speed: a.speed || null }; }) };
    });
    C.forEach(function (cd) { cd.res.bounces.sort(function (a, b) { return a.t - b.t; }); });
    return { clips: C.map(function (cd) { return cd.res; }), points: points };
  }

  /* 打球音のうち「大きい音」（手前の選手）: 上位 10% の音量から 6dB 以内 */
  function loudHitsOf(times, levels) {
    const th = percentile(levels, 90) - 6;
    return times.filter(function (t, i) { return levels[i] >= th; });
  }

  const Rally = {
    camera: camera, clean: clean, findBounces: findBounces, serveSpeed: serveSpeed, analyzeClip: analyzeClip, analyzeMatch: analyzeMatch,
    loudHitsOf: loudHitsOf, simulate: simulate, leastSquares: leastSquares,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = Rally;
  else root.Rally = Rally;
})(typeof self !== 'undefined' ? self : this);
