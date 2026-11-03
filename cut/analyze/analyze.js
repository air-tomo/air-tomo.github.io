/* テニス試合解析の画面。動画の読み込み・音での区切り・コートの位置合わせ・ボール検出・結果の再生をすべてブラウザで行う。 */
const ort = window.ort;
const $ = (id) => document.getElementById(id);
const fmt = Detect.fmt;
// 試験用: ?model=URL（モデルを URL から）、?clips=N（最初の N 区間だけ）、?dur=秒（各区間の長さを制限）
const params = new URLSearchParams(location.search);
const state = window.__state = { file: null, reader: null, info: null, prep: null, clips: [], allHits: [], loudHits: [], modelBytes: null,
                cam: null, taps: [], points: [], results: [], playQueue: null, stopAt: null };

const show = (id) => ['stepSelect', 'stepProgress', 'stepCalib', 'stepResult'].forEach((s) => { $(s).hidden = s !== id; });
function progress(title, ratio, text) {
  $('progTitle').textContent = title; $('progBar').style.width = Math.round(ratio * 100) + '%'; $('progText').textContent = text || '';
}
function fail(e) {
  console.error(e); show('stepProgress'); $('error').hidden = false; $('error').textContent = (e && e.message) || String(e);
}
let wake = null;
async function keepAwake() { try { wake = await navigator.wakeLock.request('screen'); } catch (e) { /* 使えない端末もある */ } }

// ---- 1. モデル（端末の保存領域に保存しておく） ----

async function modelStore() { const root = await navigator.storage.getDirectory(); return root.getDirectoryHandle('models', { create: true }); }
async function loadSavedModel() {
  try { const f = await (await (await modelStore()).getFileHandle('ball.onnx')).getFile(); return new Uint8Array(await f.arrayBuffer()); } catch (e) { return null; }
}
async function saveModel(bytes) {
  try { const h = await (await modelStore()).getFileHandle('ball.onnx', { create: true }); const w = await h.createWritable(); await w.write(bytes); await w.close(); } catch (e) { /* 保存できなくても今回は使える */ }
}
(async () => {
  if (params.get('model')) { state.modelBytes = new Uint8Array(await (await fetch(params.get('model'))).arrayBuffer()); $('modelName').textContent = 'URL のモデルを使います'; updateStart(); return; }
  const saved = await loadSavedModel();
  if (saved) { state.modelBytes = saved; $('modelName').textContent = '保存済みのモデルを使います（' + (saved.length / 1e6).toFixed(0) + 'MB）'; }
  updateStart();
})();
function updateStart() { $('startBtn').disabled = !(state.file && state.modelBytes); }
$('video').addEventListener('change', () => { state.file = $('video').files[0] || null; $('videoName').textContent = state.file ? state.file.name + '（' + (state.file.size / 1e9).toFixed(2) + 'GB）' : ''; updateStart(); });
$('model').addEventListener('change', async () => {
  const f = $('model').files[0]; if (!f) return;
  state.modelBytes = new Uint8Array(await f.arrayBuffer()); $('modelName').textContent = f.name + '（保存しました）';
  saveModel(state.modelBytes); updateStart();
});

// ---- 音声から打球音とラリーの区間 ----

let audioCtx = null;
function decodeAudio(bytes) {
  audioCtx = audioCtx || new OfflineAudioContext(1, 1, Detect.SR);
  const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  return new Promise((resolve, reject) => {
    const p = audioCtx.decodeAudioData(buf, (audio) => {
      const out = new Float32Array(audio.getChannelData(0));
      for (let c = 1; c < audio.numberOfChannels; c++) { const ch = audio.getChannelData(c); for (let i = 0; i < out.length; i++) out[i] += ch[i]; }
      if (audio.numberOfChannels > 1) for (let i = 0; i < out.length; i++) out[i] /= audio.numberOfChannels;
      resolve(out);
    }, reject);
    if (p && p.catch) p.catch(reject);
  });
}
async function extractAudio(onProgress) {
  const a = state.info.audio, acc = Detect.createAccumulator(state.info.duration), cfg = MP4.aacConfig(a);
  const per = Math.max(1, Math.round(30 / a.frameSec)), overlap = 8;
  for (let i0 = 0; i0 < a.count; i0 += per) {
    const i1 = Math.min(a.count, i0 + per), p0 = Math.max(0, i0 - overlap);
    const pcm = await decodeAudio(await MP4.adts(state.reader, a, cfg, p0, i1));
    Detect.accumulate(acc, pcm, Math.round(a.times[p0] * Detect.SR), Math.round((a.times[i0] - a.times[p0]) * Detect.SR));
    onProgress(i1 / a.count);
  }
  return acc;
}

$('startBtn').addEventListener('click', async () => {
  show('stepProgress'); $('error').hidden = true; keepAwake();
  try {
    progress('動画を読み込んでいます', 0);
    state.reader = MP4.fileReader(state.file);
    state.info = await MP4.parse(state.reader);
    if (!state.info.video || !state.info.audio) throw new Error('映像と音声のある動画を選んでください');
    Ball.decoderConfig(state.info.video);   // 対応していない形式ならここでわかる
    const acc = await extractAudio((r) => progress('音声から打球音を探しています', r * 0.9));
    state.prep = Detect.prepare(Detect.envelope(acc));
    const r = Detect.tennisClips(state.prep, Detect.TENNIS);
    state.clips = r.clips.map((c) => ({ start: c.start, end: c.end }));
    if (params.get('clips')) state.clips = state.clips.slice(0, +params.get('clips'));
    if (params.get('dur')) state.clips.forEach((c) => { c.end = Math.min(c.end, c.start + +params.get('dur')); });
    const hits = Detect.detectHits(state.prep, 6, 0.3);
    state.allHits = hits.times;
    const levels = hits.times.map((t) => state.prep.env[Math.min(state.prep.env.length - 1, Math.round(t / Detect.HOP_SEC - 0.5))]);
    state.loudHits = Rally.loudHitsOf(hits.times, levels);
    await startCalibration();
  } catch (e) { fail(e); }
});

// ---- 2. コートの位置合わせ ----

const GUIDES = Court.TAP_LABELS.concat(['左のネットポストの一番上', '右のネットポストの一番上']);
let calibTime = 0, frameRGBA = null;
async function grabFrame(t) {
  const v = document.createElement('video'); v.muted = true; v.playsInline = true; v.preload = 'auto';
  v.src = URL.createObjectURL(state.file);
  await new Promise((res, rej) => { v.onloadeddata = res; v.onerror = () => rej(new Error('動画を表示できません')); });
  v.currentTime = t;
  await new Promise((res) => { v.onseeked = res; });
  const c = $('calibCanvas'); c.width = v.videoWidth; c.height = v.videoHeight;
  const ctx = c.getContext('2d', { willReadFrequently: true }); ctx.drawImage(v, 0, 0);
  frameRGBA = ctx.getImageData(0, 0, c.width, c.height).data;
  URL.revokeObjectURL(v.src);
}
async function startCalibration() {
  // 最初のラリーの少し前（選手が少ない場面）
  const gaps = state.clips.map((c, i) => (i ? c.start - state.clips[i - 1].end : c.start));
  const k = gaps.indexOf(Math.max.apply(null, gaps));
  calibTime = Math.max(0, (k ? state.clips[k - 1].end : 0) + gaps[k] / 2);
  await grabFrame(calibTime);
  state.taps = []; show('stepCalib'); drawCalib();
}
function drawCalib() {
  const c = $('calibCanvas'), o = $('calibOverlay'); o.width = c.width; o.height = c.height;
  const g = o.getContext('2d'); g.clearRect(0, 0, o.width, o.height);
  const s = c.width / 960;
  state.taps.forEach((p, i) => {
    g.strokeStyle = i < 4 ? '#ffde00' : '#4fd1ff'; g.lineWidth = 3 * s;
    g.beginPath(); g.arc(p[0], p[1], 10 * s, 0, Math.PI * 2); g.stroke();
    g.beginPath(); g.moveTo(p[0] - 16 * s, p[1]); g.lineTo(p[0] + 16 * s, p[1]); g.moveTo(p[0], p[1] - 16 * s); g.lineTo(p[0], p[1] + 16 * s); g.stroke();
  });
  const n = state.taps.length;
  $('calibGuide').textContent = n < GUIDES.length ? '画面をタップ（押したまま動かすと拡大）: ' + GUIDES[n] : '';
  $('skipPostBtn').hidden = !(n >= 4 && n < 6);
  if (state.cam) drawCourt(g, state.cam, s);
}
function drawCourt(g, cam, s) {
  const P = Rally.camera(cam);
  const L = Object.values(Court.LINES);
  g.strokeStyle = 'rgba(255,40,40,.9)'; g.lineWidth = 2 * s;
  L.forEach((pq) => { const a = P.proj([pq[0][0], pq[0][1], 0]), b = P.proj([pq[1][0], pq[1][1], 0]); g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke(); });
  g.strokeStyle = 'rgba(80,220,255,.9)';
  const n0 = P.proj([-6.399, 11.885, 1.07]), n1 = P.proj([0, 11.885, 0.914]), n2 = P.proj([6.399, 11.885, 1.07]);
  g.beginPath(); g.moveTo(n0[0], n0[1]); g.lineTo(n1[0], n1[1]); g.lineTo(n2[0], n2[1]); g.stroke();
}
// タップ（押したまま動かすと拡大鏡で細かく合わせられる）
(function () {
  const stage = $('calibStage'), loupe = $('loupe');
  const lc = document.createElement('canvas'); lc.width = lc.height = 120; loupe.appendChild(lc);
  let pos = null;
  const toImg = (e) => {
    const r = $('calibCanvas').getBoundingClientRect(), c = $('calibCanvas');
    const t = e.touches ? e.touches[0] : e;
    return [(t.clientX - r.left) / r.width * c.width, (t.clientY - r.top) / r.height * c.height, t.clientX - r.left, t.clientY - r.top];
  };
  const showLoupe = (p) => {
    loupe.style.display = 'block'; loupe.style.left = (p[2] - 60) + 'px'; loupe.style.top = (p[3] - 150) + 'px';
    const g = lc.getContext('2d'); g.imageSmoothingEnabled = false;
    g.drawImage($('calibCanvas'), p[0] - 20, p[1] - 20, 40, 40, 0, 0, 120, 120);
    g.strokeStyle = '#ff0'; g.beginPath(); g.moveTo(60, 50); g.lineTo(60, 70); g.moveTo(50, 60); g.lineTo(70, 60); g.stroke();
  };
  const down = (e) => { if (state.taps.length >= GUIDES.length || !$('calibResult').hidden) return; e.preventDefault(); pos = toImg(e); showLoupe(pos); };
  const move = (e) => { if (!pos) return; e.preventDefault(); pos = toImg(e); showLoupe(pos); };
  const up = (e) => { if (!pos) return; e.preventDefault(); loupe.style.display = 'none'; state.taps.push([pos[0], pos[1]]); pos = null; afterTap(); };
  stage.addEventListener('touchstart', down, { passive: false }); stage.addEventListener('touchmove', move, { passive: false }); stage.addEventListener('touchend', up, { passive: false });
  stage.addEventListener('mousedown', down); stage.addEventListener('mousemove', move); stage.addEventListener('mouseup', up);
})();
window.__tap = (x, y) => { state.taps.push([x, y]); afterTap(); };
function afterTap() {
  drawCalib();
  if (state.taps.length === GUIDES.length) solveCalibration();
}
$('undoBtn').addEventListener('click', () => { state.taps.pop(); state.cam = null; $('calibResult').hidden = true; drawCalib(); });
$('skipPostBtn').addEventListener('click', () => solveCalibration());
$('otherFrameBtn').addEventListener('click', async () => {
  calibTime = (calibTime + 37) % Math.max(1, state.info.duration - 1); await grabFrame(calibTime);
  state.taps = []; state.cam = null; $('calibResult').hidden = true; drawCalib();
});
function solveCalibration() {
  const c = $('calibCanvas'), w = c.width, h = c.height, taps = state.taps.slice(0, 4);
  const fit = Court.fit(frameRGBA, w, h, taps);
  const cam0 = Court.cameraFromH(fit.H, w, h);
  const extra = Court.TAP_POINTS.map((p, i) => [[p[0], p[1], 0], taps[i], 4]);
  if (state.taps.length >= 6) extra.push([[-6.399, 11.885, 1.07], state.taps[4], 8], [[6.399, 11.885, 1.07], state.taps[5], 8]);
  state.cam = Court.refineCamera(cam0, fit.ground, extra, w, h, Rally.leastSquares);
  $('calibResult').hidden = false;
  $('calibInfo').textContent = '赤い線がコートの白線、水色の線がネットの上端に重なっていれば OK です。カメラの高さ ' + state.cam.C[2].toFixed(1) + 'm・ベースラインから ' + (-state.cam.C[1]).toFixed(1) + 'm 後ろ。ずれていたら「1 つ戻す」でやり直してください。';
  drawCalib();
}

// ---- 3. ボール検出とポイントの解析 ----

$('calibOkBtn').addEventListener('click', async () => {
  show('stepProgress'); keepAwake();
  try {
    progress('ボール検出を準備しています', 0);
    const det = await Ball.createDetector(ort, state.modelBytes);
    const cam = Rally.camera(state.cam);
    const total = state.clips.reduce((s, c) => s + c.end - c.start, 0);
    let done = 0; const t0 = performance.now();
    const clipData = [];
    for (let k = 0; k < state.clips.length; k++) {
      const c = state.clips[k];
      const pts = await Ball.track(state.reader, state.info, c.start, c.end, det, (r) => {
        const d = done + r * (c.end - c.start), el = (performance.now() - t0) / 1000, eta = d > 0 ? el / d * (total - d) : 0;
        progress('ボールを追っています（' + (det.ep === 'webgpu' ? 'GPU' : 'CPU') + '）', d / total,
          'ラリー ' + (k + 1) + ' / ' + state.clips.length + '・残り約 ' + (eta < 90 ? Math.round(eta) + ' 秒' : Math.round(eta / 60) + ' 分'));
      });
      clipData.push({ start: c.start, end: c.end, pts: pts });
      done += c.end - c.start;
    }
    progress('ポイントを組み立てています', 1);
    state.match = Rally.analyzeMatch(clipData, state.allHits, state.loudHits, cam);
    buildPoints(); show('stepResult');
  } catch (e) { fail(e); }
});

function buildPoints() {
  // 1 ポイント = （1st サーブのフォルト +）サーブとラリー。サーブの間の待ち時間は飛ばして続けて再生する
  const m = state.match;
  state.points = m.points.map((pt) => {
    const parts = pt.ranges.map((rg, j) => {
      const cl = m.clips[rg[0]], sv = pt.serves[j];
      const speed = sv.speed && sv.speed.rms_px < 3 && sv.speed.kmh > 60 && sv.speed.kmh < 190 ? sv.speed : null;
      return { start: rg[1], end: rg[2], ball: cl.ball, bounces: cl.bounces.filter((b) => b.t >= rg[1] && b.t <= rg[2]), serve: speed,
               tag: sv.fault ? (j === 0 && pt.ranges.length > 1 ? '1st フォルト' : 'ダブルフォルト') : j === 1 ? 'セカンド' : '' };
    });
    return { parts: parts, start: parts[0].start, end: parts[parts.length - 1].end };
  });
  const total = state.points.reduce((s, p) => s + p.parts.reduce((a, q) => a + q.end - q.start, 0), 0);
  $('summary').textContent = state.points.length + ' ポイント・合計 ' + fmt(total) + '（元の動画 ' + fmt(state.info.duration) + '）。▶ で再生すると、ボールの軌跡・バウンド・イン/アウトが重なって表示されます。';
  const ul = $('points'); ul.innerHTML = '';
  state.points.forEach((p, i) => {
    const li = document.createElement('li'); li.id = 'pt' + i;
    const info = document.createElement('div'); info.className = 'info';
    const sp = p.parts.map((q) => q.serve).find((x) => x);
    const tags = p.parts.map((q) => q.tag).filter((x) => x).join('→');
    const b = document.createElement('b'); b.textContent = 'ポイント ' + (i + 1) + (tags ? '（' + tags + '）' : '') + (sp ? '・サーブ ' + sp.kmh + 'km/h' : '');
    const s = document.createElement('span'); s.textContent = '元 ' + fmt(p.start) + '〜' + fmt(p.end);
    const calls = document.createElement('span');
    p.parts.forEach((q) => q.bounces.forEach((x) => { const c = document.createElement('i'); c.className = 'chip ' + x.call; c.textContent = x.call === 'CLOSE' ? '微妙' : x.call; calls.appendChild(c); }));
    info.append(b, s, calls);
    const play = document.createElement('button'); play.className = 'play'; play.textContent = '▶'; play.addEventListener('click', () => startPlay([i]));
    li.append(info, play); ul.appendChild(li);
  });
  $('player').src = URL.createObjectURL(state.file);
}

// ---- 再生と重ね描き ----

function startPlay(list) {
  state.playQueue = [];
  list.forEach((i) => state.points[i].parts.forEach((q) => state.playQueue.push([i, q])));
  next();
}
function next() {
  document.querySelectorAll('.points li.playing').forEach((el) => el.classList.remove('playing'));
  if (!state.playQueue || !state.playQueue.length) { $('player').pause(); state.current = null; return; }
  const [i, q] = state.playQueue.shift(); state.current = q;
  const el = $('pt' + i); if (el) el.classList.add('playing');
  $('player').currentTime = q.start; $('player').play().catch(() => {});
}
$('playAllBtn').addEventListener('click', () => startPlay(state.points.map((_, i) => i)));
$('stopBtn').addEventListener('click', () => { state.playQueue = []; next(); });
const COL = { IN: '#22c55e', OUT: '#ef4444', CLOSE: '#f59e0b' };
const TXT = { IN: 'IN', OUT: 'OUT', CLOSE: '微妙' };
function drawOverlay() {
  requestAnimationFrame(drawOverlay);
  const v = $('player'), o = $('playOverlay'), p = state.current;
  if (!v.videoWidth) return;
  const W = o.clientWidth, H = o.clientHeight;
  if (o.width !== W * devicePixelRatio) { o.width = W * devicePixelRatio; o.height = H * devicePixelRatio; }
  const g = o.getContext('2d'); g.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0); g.clearRect(0, 0, W, H);
  if (!p) return;
  const t = v.currentTime;
  if (t >= p.end) { next(); return; }
  const sx = W / v.videoWidth, sy = H / v.videoHeight;
  // ボールの軌跡（直近 0.3 秒）
  p.ball.forEach((q) => {
    if (q[0] > t || q[0] < t - 0.3) return;
    const a = 1 - (t - q[0]) / 0.3; g.fillStyle = 'rgba(255,230,0,' + (0.3 + 0.7 * a) + ')';
    g.beginPath(); g.arc(q[1] * sx, q[2] * sy, 2 + 3 * a, 0, Math.PI * 2); g.fill();
  });
  // バウンド
  p.bounces.forEach((b) => {
    const dt = t - b.t; if (dt < 0 || dt > 1.5) return;
    const x = b.uv[0] * sx, y = b.uv[1] * sy, r = 8 + 16 * dt;
    g.globalAlpha = 1 - dt / 1.5; g.strokeStyle = COL[b.call]; g.lineWidth = 3;
    g.beginPath(); g.ellipse(x, y, r, r * 0.45, 0, 0, Math.PI * 2); g.stroke();
    g.font = 'bold 18px sans-serif'; g.fillStyle = COL[b.call]; g.strokeStyle = '#000'; g.lineWidth = 3;
    g.strokeText(TXT[b.call], x + 12, y - 14); g.fillText(TXT[b.call], x + 12, y - 14); g.globalAlpha = 1;
  });
  // コートの図（右上）
  const m = Math.min(W, H) / 230, mx = W - 12 * m * 1.2 - 8, my = 8;
  const mp = (x, y) => [mx + (x + 6) * m, my + (23.77 - y + 1) * m * 0.55];
  g.fillStyle = 'rgba(0,0,0,.55)'; g.fillRect(mx - 4, my - 4, 12 * m + 8, 25.77 * m * 0.55 + 8);
  g.strokeStyle = 'rgba(255,255,255,.85)'; g.lineWidth = 1;
  Object.values(Court.LINES).forEach((pq) => { const a = mp(pq[0][0], pq[0][1]), b = mp(pq[1][0], pq[1][1]); g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke(); });
  g.lineWidth = 2; const n0 = mp(-6.4, 11.885), n1 = mp(6.4, 11.885); g.beginPath(); g.moveTo(n0[0], n0[1]); g.lineTo(n1[0], n1[1]); g.stroke();
  p.bounces.forEach((b) => {
    if (b.t > t) return; const q = mp(b.court[0], b.court[1]);
    g.fillStyle = COL[b.call]; g.beginPath(); g.arc(q[0], q[1], t - b.t < 1.5 ? 4 : 2.5, 0, Math.PI * 2); g.fill();
  });
  if (p.tag) {
    g.font = 'bold 16px sans-serif'; const w = g.measureText(p.tag).width + 16;
    g.fillStyle = 'rgba(0,0,0,.6)'; g.fillRect(8, H - 38, w, 28); g.fillStyle = '#fff'; g.fillText(p.tag, 16, H - 18);
  }
  if (p.serve && t >= p.serve.t) {
    g.fillStyle = 'rgba(200,30,30,.85)'; g.fillRect(8, 8, 150, 30); g.fillStyle = '#fff'; g.font = 'bold 17px sans-serif';
    g.fillText('サーブ ' + p.serve.kmh + ' km/h', 16, 29);
  }
}
requestAnimationFrame(drawOverlay);

// ---- ポイントだけの動画を書き出す（再エンコードしない。../worker.js と同じ仕組み） ----

$('exportBtn').addEventListener('click', async () => {
  const segments = [];
  state.points.forEach((p) => p.parts.forEach((q) => segments.push([q.start, q.end])));
  if (!segments.length) return;
  $('exportBox').hidden = false; $('exportBtn').disabled = true; keepAwake();
  const base = state.file.name.replace(/\.[^.]+$/, ''), ftyp = state.info.ftyp;
  const qt = !ftyp || String.fromCharCode(ftyp[8], ftyp[9], ftyp[10], ftyp[11]) === 'qt  ';
  const name = base + '_points' + (qt ? '.mov' : '.mp4'), type = qt ? 'video/quicktime' : 'video/mp4';
  try {
    const result = await new Promise((resolve, reject) => {
      const w = new Worker('../worker.js');
      w.onmessage = (e) => {
        const m = e.data;
        if (m.type === 'progress') { $('exportBar').style.width = Math.round(m.progress * 100) + '%'; $('exportText').textContent = '書き出しています'; }
        else if (m.type === 'done') { w.terminate(); resolve(m); }
        else if (m.type === 'no-storage') { w.terminate(); reject(new Error('この画面（プライベートブラウズなど）では書き出せません')); }
        else if (m.type === 'error') { w.terminate(); reject(new Error(m.message)); }
      };
      w.onerror = (e) => { w.terminate(); reject(new Error(e.message || '書き出しに失敗しました')); };
      w.postMessage({ file: state.file, segments: segments, name: name });
    });
    const file = new File([result.file], name, { type: type });
    const url = URL.createObjectURL(file);
    $('exportText').textContent = '完成: ' + fmt(result.stats.duration) + '・' + (file.size / 1e6).toFixed(0) + 'MB';
    $('downloadLink').href = url; $('downloadLink').download = name; $('downloadLink').hidden = false;
    $('shareBtn').hidden = !(navigator.canShare && navigator.canShare({ files: [file] }));
    $('shareBtn').onclick = async () => { try { await navigator.share({ files: [file] }); } catch (e) { /* キャンセル */ } };
  } catch (e) { $('exportText').textContent = e.message || String(e); }
  $('exportBtn').disabled = false;
});
