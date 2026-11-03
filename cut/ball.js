/*
 * ブラウザでのボール検出（iPhone / Android）。
 * 動画のデータを WebCodecs でデコードし、ボール検出モデル（TrackNet と同じ構造の ONNX）を
 * ONNX Runtime Web（WebGPU、なければ WebAssembly）で動かして、各フレームのボール位置を出す。
 * golf-swing-app/tools/court3d/tn_roi.py と同じ手順（ボールが見つかったら次のフレームは周辺だけを見る）。
 *
 * 使い方:
 *   const det = await Ball.createDetector(ort, modelUrlOrBuffer);
 *   const pts = await Ball.track(reader, info, t0, t1, det, onProgress);   // [[t, u, v|null], ...]（元動画の画素）
 */
(function (root) {
  'use strict';

  // 解析する範囲（1920x1080 の動画の中央 1280x512 = コートとその上の空中）を 640x256 にして入れる
  const CROP = { x: 320, y: 120, w: 1280, h: 512 };
  const IW = 640, IH = 256, RW = 320, RH = 128, FPS = 30;

  // ---- デコーダの設定（MP4 / MOV のサンプル記述から） ----

  function u32(b, o) { return ((b[o] << 24) >>> 0) + (b[o + 1] << 16) + (b[o + 2] << 8) + b[o + 3]; }
  function findBox(b, start, end, type) {
    for (let o = start; o + 8 <= end;) {
      const size = u32(b, o), t = String.fromCharCode(b[o + 4], b[o + 5], b[o + 6], b[o + 7]);
      if (size < 8) break;
      if (t === type) return { start: o, end: o + size, body: o + 8 };
      o += size;
    }
    return null;
  }
  function hex2(n) { return (n < 16 ? '0' : '') + n.toString(16).toUpperCase(); }
  /* track.stsdBox（mp4.js の parse 結果）から VideoDecoder の設定を作る */
  function decoderConfig(track) {
    const b = track.stsdBox;               // stsd: header(8) + version/flags(4) + count(4) + sample entry
    const entry = 16, entrySize = u32(b, entry), fmt = String.fromCharCode(b[entry + 4], b[entry + 5], b[entry + 6], b[entry + 7]);
    const width = (b[entry + 32] << 8) | b[entry + 33], height = (b[entry + 34] << 8) | b[entry + 35];
    const kids = entry + 8 + 78;
    if (fmt === 'avc1' || fmt === 'avc3') {
      const c = findBox(b, kids, entry + entrySize, 'avcC');
      const d = b.slice(c.body, c.end);
      return { codec: 'avc1.' + hex2(d[1]) + hex2(d[2]) + hex2(d[3]), description: d, codedWidth: width, codedHeight: height };
    }
    if (fmt === 'hvc1' || fmt === 'hev1') {
      const c = findBox(b, kids, entry + entrySize, 'hvcC');
      const d = b.slice(c.body, c.end);
      const space = ['', 'A', 'B', 'C'][d[1] >> 6], tier = (d[1] >> 5) & 1, profile = d[1] & 31;
      let compat = u32(d, 2), rev = 0;
      for (let i = 0; i < 32; i++) { rev = (rev << 1) | (compat & 1); compat >>>= 1; }
      const cons = []; for (let i = 6; i < 12; i++) cons.push(d[i]);
      while (cons.length && cons[cons.length - 1] === 0) cons.pop();
      const codec = fmt + '.' + space + profile + '.' + (rev >>> 0).toString(16).toUpperCase() + '.' + (tier ? 'H' : 'L') + d[12] +
        (cons.length ? '.' + cons.map(function (x) { return x.toString(16).toUpperCase(); }).join('.') : '');
      return { codec: codec, description: d, codedWidth: width, codedHeight: height };
    }
    throw new Error('この動画の形式（' + fmt + '）はまだ解析できません');
  }

  // ---- フレームを 30fps で取り出す ----

  /* t0〜t1 秒のフレームを 30fps 相当で取り出し、onFrame(t, rgba Uint8ClampedArray IW*IH*4) を呼ぶ */
  async function frames(reader, info, t0, t1, onFrame) {
    const v = info.video, cfg = decoderConfig(v);
    const canvas = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(IW, IH) : Object.assign(document.createElement('canvas'), { width: IW, height: IH });
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const sx = CROP.x * cfg.codedWidth / 1920, sy = CROP.y * cfg.codedHeight / 1080;
    const sw = CROP.w * cfg.codedWidth / 1920, sh = CROP.h * cfg.codedHeight / 1080;
    // 開始時刻より前の最後のキーフレームからデコードする
    let i0 = 0;
    for (let i = 0; i < v.count; i++) { if (v.times[i] > t0) break; if (!v.sync || v.sync[i]) i0 = i; }
    let i1 = v.count; for (let i = i0; i < v.count; i++) if (v.times[i] > t1 + 0.5) { i1 = i; break; }
    const pending = [];
    let error = null;
    const dec = new VideoDecoder({ output: function (f) { pending.push(f); }, error: function (e) { error = e; } });
    dec.configure(cfg);
    let nextGrid = t0, last = null;
    const emit = async function (item, tg) {
      ctx.drawImage(item.frame, sx, sy, sw, sh, 0, 0, IW, IH);
      await onFrame(tg, ctx.getImageData(0, 0, IW, IH).data);
    };
    // 30fps の格子の各時刻に、一番近いフレームを使う（前のフレームと今のフレームの中点より前の格子は前のフレーム）
    const handle = async function (f) {
      const t = f.timestamp / 1e6;
      if (last) {
        const mid = (last.t + t) / 2;
        while (nextGrid <= t1 + 1e-9 && nextGrid <= mid) {
          if (Math.abs(last.t - nextGrid) < 1 / FPS) await emit(last, nextGrid);
          nextGrid += 1 / FPS;
        }
        last.frame.close();
      }
      last = { t: t, frame: f };
    };
    for (let i = i0; i < i1; i++) {
      if (error) throw error;
      const data = await reader.read(v.offsets[i], v.sizes[i]);
      const pts = v.times[i] + (v.cts ? v.cts[i] / v.timescale : 0);
      dec.decode(new EncodedVideoChunk({ type: (!v.sync || v.sync[i]) ? 'key' : 'delta', timestamp: Math.round(pts * 1e6), data: data }));
      while (dec.decodeQueueSize > 8) await new Promise(function (r) { setTimeout(r, 1); });
      while (pending.length) { pending.sort(function (a, b) { return a.timestamp - b.timestamp; }); await handle(pending.shift()); }
    }
    await dec.flush();
    pending.sort(function (a, b) { return a.timestamp - b.timestamp; });
    while (pending.length) await handle(pending.shift());
    while (last && nextGrid <= t1 && Math.abs(last.t - nextGrid) < 1 / FPS) { await emit(last, nextGrid); nextGrid += 1 / FPS; }
    if (last) last.frame.close();
    dec.close();
  }

  // ---- ボール検出モデル ----

  async function createDetector(ort, model) {
    let session = null, ep = '';
    for (const e of ['webgpu', 'wasm']) {
      try { session = await ort.InferenceSession.create(model, { executionProviders: [e], graphOptimizationLevel: 'all' }); ep = e; break; } catch (err) { /* 次を試す */ }
    }
    if (!session) throw new Error('この端末ではボール検出を動かせません');
    return { ort: ort, session: session, ep: ep };
  }
  /* 3 フレーム（新しい順、BGR、0-1）を (x0, y0, w, h) で切り出してモデルに入れ、ボールの中心（入力画素）を返す */
  async function detect(det, f0, f1, f2, x0, y0, w, h) {
    const inp = new Float32Array(9 * w * h), plane = w * h;
    [f0, f1, f2].forEach(function (f, k) {
      for (let y = 0; y < h; y++) {
        let o = ((y0 + y) * IW + x0) * 4, p = y * w;
        for (let x = 0; x < w; x++, o += 4, p++) {
          inp[(k * 3) * plane + p] = f[o + 2] / 255;      // B
          inp[(k * 3 + 1) * plane + p] = f[o + 1] / 255;  // G
          inp[(k * 3 + 2) * plane + p] = f[o] / 255;      // R
        }
      }
    });
    const out = await det.session.run({ frames: new det.ort.Tensor('float32', inp, [1, 9, h, w]) });
    // TrackNet: 0-255 のクラス番号（128 以上がボール）。軽量モデル（train_small.py）: 0-1 の確率（0.5 以上）
    const heat = out.heat.data, thr = heat instanceof Float32Array ? 0.5 : 128;
    // しきい値以上の画素の一番大きな塊の重心
    const seen = new Uint8Array(w * h); let best = null;
    for (let i = 0; i < w * h; i++) {
      if (heat[i] < thr || seen[i]) continue;
      const stack = [i]; seen[i] = 1; let n = 0, sx = 0, sy = 0;
      while (stack.length) {
        const j = stack.pop(), x = j % w, y = (j - x) / w; n++; sx += x; sy += y;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const xx = x + dx, yy = y + dy; if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
          const k = yy * w + xx; if (!seen[k] && heat[k] >= thr) { seen[k] = 1; stack.push(k); }
        }
      }
      if (!best || n > best.n) best = { n: n, x: sx / n, y: sy / n };
    }
    return best ? [best.x + x0, best.y + y0] : null;
  }

  /* t0〜t1 秒のボール位置。ボールが見つかったら、次は予測位置の周辺（画面の 1/4）だけを見る */
  async function track(reader, info, t0, t1, det, onProgress) {
    const buf = [], hist = [], res = [];
    let n = 0;
    const total = Math.max(1, Math.round((t1 - t0) * FPS));
    await frames(reader, info, t0, t1, async function (t, rgba) {
      buf.push(rgba); if (buf.length > 3) buf.shift();
      if (buf.length < 3) { n++; return; }
      const i = n++;
      let pos = null;
      const recent = hist.filter(function (h) { return i - h[0] <= 2; });
      if (recent.length) {
        const a = recent.length >= 2 ? recent[recent.length - 2] : null, b = recent[recent.length - 1];
        const k = i - b[0];
        const vx = a ? (b[1] - a[1]) / Math.max(1, b[0] - a[0]) : 0, vy = a ? (b[2] - a[2]) / Math.max(1, b[0] - a[0]) : 0;
        const px = b[1] + vx * k, py = b[2] + vy * k;
        const x0 = Math.round(Math.min(Math.max(px - RW / 2, 0), IW - RW)), y0 = Math.round(Math.min(Math.max(py - RH / 2, 0), IH - RH));
        pos = await detect(det, buf[2], buf[1], buf[0], x0, y0, RW, RH);
      }
      if (!pos) pos = await detect(det, buf[2], buf[1], buf[0], 0, 0, IW, IH);
      if (pos) hist.push([i, pos[0], pos[1]]);
      res.push([Math.round(t * 1e4) / 1e4].concat(pos ? [Math.round((CROP.x + pos[0] * 2) * 10) / 10, Math.round((CROP.y + pos[1] * 2) * 10) / 10] : [null, null]));
      if (onProgress) onProgress(Math.min(1, n / total));
    });
    return res;
  }

  const Ball = { decoderConfig: decoderConfig, frames: frames, createDetector: createDetector, detect: detect, track: track, CROP: CROP };
  if (typeof module !== 'undefined' && module.exports) module.exports = Ball;
  else root.Ball = Ball;
})(typeof self !== 'undefined' ? self : this);
