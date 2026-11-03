/*
 * コートの位置合わせ（ブラウザ用）。golf-swing-app/tools/court3d/refine_court.py と同じ方法。
 * 画面で 4 点（手前のサービスラインの左右の端・奥のベースラインの左右の端）を指定すると、
 * 白線をすべて当てはめて地面の射影変換 H と、カメラ（焦点距離・向き・位置）を求める。
 * カメラが低い（ネットより低い）と奥のコートが数画素に潰れて白線だけでは決まらないので、
 * ネットポストの頂上 2 点と、指定した 4 点そのものも使って 3D で当てはめ直す（refineCamera）。
 * IMG_0131（地面から 0.7m）で、ネットポストの誤差 2〜8 画素、手で合わせた結果とカメラ位置 0.3m 以内。
 * 放送映像（TrackNet データ）では白線だけで Python 版と 0.4 画素以内。
 *
 * 座標: 画像は元動画の画素。コートは x = 横 (m, 中央 0)、y = 手前のベースラインからの奥行き (m)。
 */
(function (root) {
  'use strict';

  const DX = 5.485, SX = 4.115;
  const LINES = {
    base_n: [[-DX, 0], [DX, 0]], base_f: [[-DX, 23.77], [DX, 23.77]],
    dside_l: [[-DX, 0], [-DX, 23.77]], dside_r: [[DX, 0], [DX, 23.77]],
    sside_l: [[-SX, 0], [-SX, 23.77]], sside_r: [[SX, 0], [SX, 23.77]],
    serv_n: [[-SX, 5.485], [SX, 5.485]], serv_f: [[-SX, 18.285], [SX, 18.285]],
    centre: [[0, 5.485], [0, 18.285]],
  };
  // 画面で指定する 4 点（この順）
  const TAP_POINTS = [[-SX, 5.485], [SX, 5.485], [SX, 23.77], [-SX, 23.77]];
  const TAP_LABELS = ['手前のサービスラインの左端', '手前のサービスラインの右端', '奥のベースラインの右端（シングルス）', '奥のベースラインの左端（シングルス）'];

  function applyH(H, x, y) {
    const w = H[6] * x + H[7] * y + H[8];
    return [(H[0] * x + H[1] * y + H[2]) / w, (H[3] * x + H[4] * y + H[5]) / w];
  }
  function solve(A, b) {
    const n = b.length, M = A.map(function (r, i) { return r.concat([b[i]]); });
    for (let c = 0; c < n; c++) {
      let p = c; for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
      if (Math.abs(M[p][c]) < 1e-14) return null;
      const t = M[c]; M[c] = M[p]; M[p] = t;
      for (let r = 0; r < n; r++) if (r !== c) { const f = M[r][c] / M[c][c]; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; }
    }
    return M.map(function (r, i) { return r[n] / r[i]; });
  }
  /* 対応点（world → image）から射影変換（h33 = 1、最小二乗。座標は正規化して解く） */
  function homography(W, I) {
    const norm = function (P) {
      let mx = 0, my = 0; P.forEach(function (p) { mx += p[0]; my += p[1]; }); mx /= P.length; my /= P.length;
      let d = 0; P.forEach(function (p) { d += Math.hypot(p[0] - mx, p[1] - my); }); const s = Math.SQRT2 / (d / P.length || 1);
      return { P: P.map(function (p) { return [(p[0] - mx) * s, (p[1] - my) * s]; }), T: [s, 0, -mx * s, 0, s, -my * s, 0, 0, 1] };
    };
    const a = norm(W), b = norm(I);
    const AtA = []; const Atb = new Array(8).fill(0);
    for (let i = 0; i < 8; i++) AtA.push(new Array(8).fill(0));
    a.P.forEach(function (p, i) {
      const q = b.P[i], x = p[0], y = p[1], u = q[0], v = q[1];
      const rows = [[x, y, 1, 0, 0, 0, -u * x, -u * y, u], [0, 0, 0, x, y, 1, -v * x, -v * y, v]];
      rows.forEach(function (r) { for (let j = 0; j < 8; j++) { Atb[j] += r[j] * r[8]; for (let k = 0; k < 8; k++) AtA[j][k] += r[j] * r[k]; } });
    });
    const h = solve(AtA, Atb); if (!h) return null;
    const Hn = h.concat([1]);
    // H = Tb^-1 * Hn * Ta
    const Tbi = [1 / b.T[0], 0, -b.T[2] / b.T[0], 0, 1 / b.T[4], -b.T[5] / b.T[4], 0, 0, 1];
    const H = mul(Tbi, mul(Hn, a.T));
    return H.map(function (x) { return x / H[8]; });
  }
  function mul(A, B) {
    const C = new Array(9).fill(0);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) for (let k = 0; k < 3; k++) C[i * 3 + j] += A[i * 3 + k] * B[k * 3 + j];
    return C;
  }

  /* 細く明るい線（白線）の強さ: 両側（4 方向・2 と 4 画素先）より明るい分 */
  function ridge(gray, w, h) {
    const r = new Float32Array(w * h);
    const dirs = [[1, 0], [0, 1], [1, 1], [1, -1]];
    for (let y = 4; y < h - 4; y++) for (let x = 4; x < w - 4; x++) {
      const i = y * w + x, g = gray[i]; let best = 0;
      for (const d of dirs) for (const s of [2, 4]) {
        const a = gray[i + d[1] * s * w + d[0] * s], b = gray[i - d[1] * s * w - d[0] * s];
        const m = Math.min(g - a, g - b); if (m > best) best = m;
      }
      r[i] = best;
    }
    return r;
  }
  function grayOf(rgba, w, h) {
    // OpenCV と同じ係数（BGR2GRAY）。3x3 のガウスぼかし相当を軽くかける
    const g = new Float32Array(w * h);
    for (let i = 0; i < w * h; i++) g[i] = 0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2];
    const out = new Float32Array(w * h);
    const k = [1, 2, 1];
    for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
      let s = 0; for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) s += k[dy + 1] * k[dx + 1] * g[(y + dy) * w + x + dx];
      out[y * w + x] = s / 16;
    }
    return out;
  }

  /*
   * rgba: 背景（選手が少ない）フレームの画素、w × h。taps: TAP_POINTS に対応する画像上の 4 点。
   * 返り値: {H（world→image, 9 要素）, points（当てはめに使った交点数）, maxErr（画素）}
   */
  function fit(rgba, w, h, taps, opts) {
    opts = opts || {}; const band = opts.band || 6, thr = opts.thr || 12, iters = opts.iters || 4;
    let H = homography(TAP_POINTS, taps);
    const R = ridge(grayOf(rgba, w, h), w, h);
    const px = [], py = [], pv = [];
    for (let i = 0; i < w * h; i++) if (R[i] > thr) { px.push(i % w); py.push((i - i % w) / w); pv.push(R[i]); }
    let wpts = [], ipts = [];
    for (let it = 0; it < iters; it++) {
      const fitted = {};
      // 遠くの線は画面上で数画素しか離れていないので、隣の線と混ざらないよう探す幅を狭くする
      const mids = {};
      for (const name in LINES) { const pq = LINES[name]; mids[name] = applyH(H, (pq[0][0] + pq[1][0]) / 2, (pq[0][1] + pq[1][1]) / 2); }
      for (const name in LINES) {
        const pq = LINES[name], a = applyH(H, pq[0][0], pq[0][1]), b = applyH(H, pq[1][0], pq[1][1]);
        let dx = b[0] - a[0], dy = b[1] - a[1]; const L = Math.hypot(dx, dy); dx /= L; dy /= L;
        let near = Infinity;
        for (const other in LINES) {
          if (other === name) continue;
          const o = LINES[other], par = (o[0][1] === o[1][1]) === (pq[0][1] === pq[1][1]);
          if (!par) continue;
          const m = mids[other], dist = Math.abs(-(m[0] - a[0]) * dy + (m[1] - a[1]) * dx);
          near = Math.min(near, dist);
        }
        const bandIt = it === 0 ? 3 * band : it === 1 ? 2 * band : band;   // 最初は広く探して、だんだん狭める
        const bandHere = Math.max(1.5, Math.min(bandIt, 0.4 * near));
        let sw = 0, cx = 0, cy = 0; const sel = [];
        for (let i = 0; i < px.length; i++) {
          const rx = px[i] - a[0], ry = py[i] - a[1], along = rx * dx + ry * dy, off = -rx * dy + ry * dx;
          if (Math.abs(off) < bandHere && along > 0.03 * L && along < 0.97 * L) { sel.push(i); sw += pv[i]; cx += pv[i] * px[i]; cy += pv[i] * py[i]; }
        }
        if (sel.length < 30) continue;
        cx /= sw; cy /= sw;
        let sxx = 0, sxy = 0, syy = 0;
        for (const i of sel) { const ex = px[i] - cx, ey = py[i] - cy; sxx += pv[i] * ex * ex; sxy += pv[i] * ex * ey; syy += pv[i] * ey * ey; }
        const ang = 0.5 * Math.atan2(2 * sxy, sxx - syy);
        fitted[name] = { c: [cx, cy], d: [Math.cos(ang), Math.sin(ang)] };
      }
      wpts = []; ipts = [];
      const names = Object.keys(LINES);
      for (let i = 0; i < names.length; i++) for (let j = i + 1; j < names.length; j++) {
        const n1 = names[i], n2 = names[j]; if (!fitted[n1] || !fitted[n2]) continue;
        const p1 = LINES[n1][0], q1 = LINES[n1][1], p2 = LINES[n2][0], q2 = LINES[n2][1];
        const s = solve([[q1[0] - p1[0], -(q2[0] - p2[0])], [q1[1] - p1[1], -(q2[1] - p2[1])]], [p2[0] - p1[0], p2[1] - p1[1]]);
        if (!s || s[0] < -0.01 || s[0] > 1.01 || s[1] < -0.01 || s[1] > 1.01) continue;
        const X = [p1[0] + s[0] * (q1[0] - p1[0]), p1[1] + s[0] * (q1[1] - p1[1])];
        const f1 = fitted[n1], f2 = fitted[n2];
        const t = solve([[f1.d[0], -f2.d[0]], [f1.d[1], -f2.d[1]]], [f2.c[0] - f1.c[0], f2.c[1] - f1.c[1]]);
        if (!t) continue;
        wpts.push(X); ipts.push([f1.c[0] + t[0] * f1.d[0], f1.c[1] + t[0] * f1.d[1]]);
      }
      // 合わない交点（隣のコートの線などを拾った線）を外しながら当てはめ直す
      for (let k = 0; k < 6 && wpts.length >= 5; k++) {
        const Hn = homography(wpts, ipts); if (!Hn) break; H = Hn;
        const err = wpts.map(function (p, i) { const q = applyH(H, p[0], p[1]); return Math.hypot(q[0] - ipts[i][0], q[1] - ipts[i][1]); });
        let worst = 0; err.forEach(function (e, i) { if (e > err[worst]) worst = i; });
        if (err[worst] < 4) break;
        wpts.splice(worst, 1); ipts.splice(worst, 1);
      }
      if (wpts.length === 4) { const Hn = homography(wpts, ipts); if (Hn) H = Hn; }
    }
    let maxErr = 0;
    wpts.forEach(function (p, i) { const q = applyH(H, p[0], p[1]); maxErr = Math.max(maxErr, Math.hypot(q[0] - ipts[i][0], q[1] - ipts[i][1])); });
    return { H: H, points: wpts.length, maxErr: maxErr, ground: wpts.map(function (p, i) { return [p, ipts[i]]; }) };
  }

  /* 射影変換からカメラ（主点は画像の中心、正方画素）: {K, R, t, H(3x3)} */
  function cameraFromH(H9, w, h) {
    const cx = w / 2, cy = h / 2;
    const Hm = [[H9[0], H9[1], H9[2]], [H9[3], H9[4], H9[5]], [H9[6], H9[7], H9[8]]];
    const B = function (f) { return [0, 1, 2].map(function (c) { return [(Hm[0][c] - cx * Hm[2][c]) / f, (Hm[1][c] - cy * Hm[2][c]) / f, Hm[2][c]]; }); };
    const cost = function (f) {
      const b = B(f), n0 = Math.hypot.apply(null, b[0]), n1 = Math.hypot.apply(null, b[1]);
      const dot = b[0][0] * b[1][0] + b[0][1] * b[1][1] + b[0][2] * b[1][2];
      return (dot / (n0 * n1)) * (dot / (n0 * n1)) + (n0 / n1 - 1) * (n0 / n1 - 1);
    };
    let f = 300, best = Infinity;
    for (let x = 300; x < 6000; x += 2) { const c = cost(x); if (c < best) { best = c; f = x; } }
    const b = B(f), s = 1 / Math.hypot.apply(null, b[0]);
    let r1 = b[0].map(function (x) { return x * s; }), r2 = b[1].map(function (x) { return x * s; }), t = b[2].map(function (x) { return x * s; });
    if (t[2] < 0) { r1 = r1.map(function (x) { return -x; }); r2 = r2.map(function (x) { return -x; }); t = t.map(function (x) { return -x; }); }
    // r2 を r1 に直交させて正規化（SVD の代わり）
    const d = r1[0] * r2[0] + r1[1] * r2[1] + r1[2] * r2[2];
    r2 = r2.map(function (x, i) { return x - d * r1[i]; }); const n2 = Math.hypot.apply(null, r2); r2 = r2.map(function (x) { return x / n2; });
    const r3 = [r1[1] * r2[2] - r1[2] * r2[1], r1[2] * r2[0] - r1[0] * r2[2], r1[0] * r2[1] - r1[1] * r2[0]];
    const R = [[r1[0], r2[0], r3[0]], [r1[1], r2[1], r3[1]], [r1[2], r2[2], r3[2]]];
    const K = [[f, 0, cx], [0, f, cy], [0, 0, 1]];
    const C = [0, 1, 2].map(function (j) { return -(R[0][j] * t[0] + R[1][j] * t[1] + R[2][j] * t[2]); });
    return { K: K, R: R, t: t, C: C, H: Hm };
  }

  // ---- ネットポストも使った 3D のカメラ当てはめ（カメラが低いとき用） ----

  function rodrigues(r) {
    const th = Math.hypot(r[0], r[1], r[2]);
    if (th < 1e-12) return [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    const k = r.map(function (x) { return x / th; }), c = Math.cos(th), s = Math.sin(th), v = 1 - c;
    return [[c + k[0] * k[0] * v, k[0] * k[1] * v - k[2] * s, k[0] * k[2] * v + k[1] * s],
            [k[1] * k[0] * v + k[2] * s, c + k[1] * k[1] * v, k[1] * k[2] * v - k[0] * s],
            [k[2] * k[0] * v - k[1] * s, k[2] * k[1] * v + k[0] * s, c + k[2] * k[2] * v]];
  }
  function toRodrigues(R) {
    const tr = R[0][0] + R[1][1] + R[2][2], th = Math.acos(Math.max(-1, Math.min(1, (tr - 1) / 2)));
    if (th < 1e-9) return [0, 0, 0];
    const f = th / (2 * Math.sin(th));
    return [(R[2][1] - R[1][2]) * f, (R[0][2] - R[2][0]) * f, (R[1][0] - R[0][1]) * f];
  }
  function project(q, X, w, h) {
    const R = rodrigues(q.slice(1, 4)), t = q.slice(4, 7);
    const c = [0, 1, 2].map(function (r) { return R[r][0] * X[0] + R[r][1] * X[1] + R[r][2] * X[2] + t[r]; });
    return [q[0] * c[0] / c[2] + w / 2, q[0] * c[1] / c[2] + h / 2];
  }
  /*
   * カメラ（焦点距離・向き・位置）を 3D の点で当てはめ直す。
   * ground: [[X(world 2D), uv], ...]（白線の交点）、extra: [[X(world 3D), uv, weight], ...]（ネットポストの頂上など）
   * 例: ネットポストの頂上は (±6.399, 11.885, 1.07)、ネット中央の上端は (0, 11.885, 0.914)
   */
  function refineCamera(cam, ground, extra, w, h, LS) {
    const q0 = [cam.K[0][0]].concat(toRodrigues(cam.R), cam.t);
    const fn = function (q) {
      const r = [];
      ground.forEach(function (g) { const p = project(q, [g[0][0], g[0][1], 0], w, h); r.push(p[0] - g[1][0], p[1] - g[1][1]); });
      extra.forEach(function (e) { const p = project(q, e[0], w, h), wt = e[2] || 1; r.push((p[0] - e[1][0]) * wt, (p[1] - e[1][1]) * wt); });
      return r;
    };
    const s = LS(fn, q0, 3.0, 100);
    const R = rodrigues(s.x.slice(1, 4)), t = s.x.slice(4, 7), f = s.x[0];
    const K = [[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]];
    const H = [0, 1, 2].map(function (i) { return [0, 1, 3].map(function (j) {
      const col = j === 3 ? t : [R[0][j], R[1][j], R[2][j]];
      return K[i][0] * col[0] + K[i][1] * col[1] + K[i][2] * col[2];
    }); });
    const n = H[2][2]; for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) H[i][j] /= n;
    const C = [0, 1, 2].map(function (j) { return -(R[0][j] * t[0] + R[1][j] * t[1] + R[2][j] * t[2]); });
    return { K: K, R: R, t: t, C: C, H: H, residual: Math.sqrt(s.fun.reduce(function (a, x) { return a + x * x; }, 0) / s.fun.length) };
  }

  const Court = { LINES: LINES, TAP_POINTS: TAP_POINTS, TAP_LABELS: TAP_LABELS, applyH: applyH, homography: homography, fit: fit, cameraFromH: cameraFromH, refineCamera: refineCamera };
  if (typeof module !== 'undefined' && module.exports) module.exports = Court;
  else root.Court = Court;
})(typeof self !== 'undefined' ? self : this);
