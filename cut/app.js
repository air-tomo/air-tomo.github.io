/* 画面の操作。動画の解析と書き出しはすべて iPhone（ブラウザ）の中で行う。 */
(function () {
  'use strict';
  const $ = function (id) { return document.getElementById(id); };
  const fmt = Detect.fmt;

  const TEXT = {
    golf: { title: '⛳ ゴルフスイング自動切り抜き', lead: '練習動画から打球音でスイングを見つけ、1本の動画にまとめます。' },
    tennis: { title: '🎾 テニス ポイント間カット', lead: '試合動画から打球音でラリーを見つけ、ポイント間の待ち時間を取り除きます。' },
  };
  const SETTINGS = {
    golf: [
      { key: 'preSec', label: '打球の前', note: 'スイング開始を含める秒数', type: 'number', min: 0, max: 15, step: 0.5, unit: '秒' },
      { key: 'postSec', label: '打球の後', note: 'フォロースルーを含める秒数', type: 'number', min: 0, max: 15, step: 0.5, unit: '秒' },
      { key: 'sensitivity', label: '検出感度', note: '見逃す→上げる／誤検出→下げる', type: 'range', min: 1, max: 10, step: 1 },
      { key: 'minIntervalSec', label: '最短の打球間隔', note: 'これより近い音は同じスイング扱い', type: 'number', min: 1, max: 60, step: 1, unit: '秒' },
    ],
    tennis: [
      { key: 'preSec', label: 'ポイントの前', note: 'サーブのトス・動作を含める秒数', type: 'number', min: 0, max: 15, step: 0.5, unit: '秒' },
      { key: 'postSec', label: 'ポイントの後', note: '最後の打球のあとに残す秒数', type: 'number', min: 0, max: 15, step: 0.5, unit: '秒' },
      { key: 'sensitivity', label: '検出感度', note: 'ラリーが途切れる→上げる／余計な場面が入る→下げる', type: 'range', min: 1, max: 10, step: 1 },
      { key: 'maxShotGapSec', label: 'ラリーの打球間隔', note: 'これより長く打球がなければポイント終了', type: 'number', min: 1, max: 15, step: 0.5, unit: '秒' },
      { key: 'minHits', label: '最少打球数', note: 'これ未満の打球数は物音とみなして除外', type: 'number', min: 1, max: 10, step: 1, unit: '回' },
      { key: 'keepServeOnly', label: 'サーブだけのポイントも残す', note: 'エース・フォルトなど（大きな打球音のみ）', type: 'checkbox' },
    ],
  };
  const NUDGE = 0.5;

  const state = {
    mode: 'golf', file: null, reader: null, info: null, prep: null, keyframeSec: 0,
    settings: { golf: Object.assign({}, Detect.GOLF), tennis: Object.assign({}, Detect.TENNIS) },
    clips: [], count: 0, unit: '', srcUrl: null, outUrl: null, outFile: null, wake: null, queue: null, stopAt: null,
  };

  // ---- 端末に覚えておく設定（使えなくても動くように） ----
  function load(key) { try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; } }
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* 保存できなくても続ける */ } }
  ['golf', 'tennis'].forEach(function (m) { Object.assign(state.settings[m], load('cut.settings.' + m) || {}); });

  function show(id) {
    ['stepSelect', 'stepProgress', 'stepReview', 'stepDone'].forEach(function (s) { $(s).hidden = s !== id; });
    window.scrollTo(0, 0);
  }

  function setProgress(ratio, step) {
    $('bar').value = ratio * 100;
    $('pct').textContent = Math.floor(ratio * 100) + '%';
    if (step) $('step').textContent = step;
  }

  function showError(message) {
    show('stepProgress');
    $('error').textContent = message;
    $('error').hidden = false;
    $('retryBtn').hidden = false;
    $('progressHint').hidden = true;
    releaseWake();
  }

  async function keepAwake() {
    try { if (navigator.wakeLock) state.wake = await navigator.wakeLock.request('screen'); } catch (e) { /* 対応していない端末 */ }
  }
  function releaseWake() {
    if (state.wake) state.wake.release().catch(function () {});
    state.wake = null;
  }

  // ---- モード ----

  function setMode(mode) {
    state.mode = mode;
    save('cut.mode', mode);
    $('title').textContent = TEXT[mode].title;
    $('lead').textContent = TEXT[mode].lead;
    document.title = mode === 'tennis' ? 'テニス ポイント間カット' : 'ゴルフスイング自動切り抜き';
    document.querySelectorAll('.modes button').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-mode') === mode);
    });
    renderSettings();
    if (state.prep) redetect();
  }
  document.querySelectorAll('.modes button').forEach(function (b) {
    b.addEventListener('click', function () { setMode(b.getAttribute('data-mode')); });
  });

  // ---- 1. 動画を選んで解析 ----

  $('file').addEventListener('change', function () {
    const f = $('file').files[0];
    state.file = f || null;
    $('fileLabel').textContent = f ? f.name + '（' + formatSize(f.size) + '）' : 'タップして動画を選択';
    $('fileLabel').classList.toggle('selected', !!f);
    $('loadBtn').disabled = !f;
  });

  function formatSize(bytes) {
    return bytes >= 1024 * 1024 * 1024 ? (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB' : (bytes / 1024 / 1024).toFixed(1) + ' MB';
  }

  let audioCtx = null;
  function decodeAudio(bytes) {
    // 解析用に 22.05kHz に変換してデコードする
    audioCtx = audioCtx || new OfflineAudioContext(1, 1, Detect.SR);
    const buf = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    return new Promise(function (resolve, reject) {
      const done = function (audio) {
        const out = new Float32Array(audio.getChannelData(0));
        for (let c = 1; c < audio.numberOfChannels; c++) {
          const ch = audio.getChannelData(c);
          for (let i = 0; i < out.length; i++) out[i] += ch[i];
        }
        if (audio.numberOfChannels > 1) for (let i = 0; i < out.length; i++) out[i] /= audio.numberOfChannels;
        resolve(out);
      };
      const p = audioCtx.decodeAudioData(buf, done, reject);
      if (p && p.catch) p.catch(reject);
    });
  }

  async function extractAudio(onProgress) {
    const a = state.info.audio;
    const acc = Detect.createAccumulator(state.info.duration);
    const cfg = a.codec === 'mp4a' ? MP4.aacConfig(a) : null;
    if (cfg) {
      // 約30秒ずつ取り出してデコードする（長い動画でもメモリを使いすぎない）
      const per = Math.max(1, Math.round(30 / a.frameSec)), overlap = 8;
      for (let i0 = 0; i0 < a.count; i0 += per) {
        const i1 = Math.min(a.count, i0 + per), p0 = Math.max(0, i0 - overlap);
        const pcm = await decodeAudio(await MP4.adts(state.reader, a, cfg, p0, i1));
        Detect.accumulate(acc, pcm, Math.round(a.times[p0] * Detect.SR), Math.round((a.times[i0] - a.times[p0]) * Detect.SR));
        onProgress(i1 / a.count);
      }
    } else {
      if (state.file.size > 800 * 1024 * 1024) {
        throw new Error('この動画の音声形式（' + a.codec + '）は大きなファイルに対応していません。');
      }
      Detect.accumulate(acc, await decodeAudio(new Uint8Array(await state.file.arrayBuffer())), 0, 0);
      onProgress(1);
    }
    return acc;
  }

  function measureKeyframes(v) {
    if (!v.sync) return v.frameSec;
    const gaps = [];
    let last = -1;
    for (let i = 0; i < v.count && gaps.length < 200; i++) {
      if (!v.sync[i]) continue;
      if (last >= 0) gaps.push(v.times[i] - v.times[last]);
      last = i;
    }
    if (!gaps.length) return v.duration;
    gaps.sort(function (x, y) { return x - y; });
    return gaps[gaps.length >> 1];
  }

  $('loadBtn').addEventListener('click', async function () {
    show('stepProgress');
    $('error').hidden = true;
    $('retryBtn').hidden = true;
    $('progressHint').hidden = false;
    setProgress(0, '動画を読み込んでいます');
    keepAwake();
    try {
      state.reader = MP4.fileReader(state.file);
      try {
        state.info = await MP4.parse(state.reader);
      } catch (e) {
        throw new Error('この動画は読み込めません（iPhone の MOV / MP4 に対応）。' + (e.message || ''));
      }
      if (!state.info.video) throw new Error('映像が見つかりません。');
      if (!state.info.audio) throw new Error('音声がありません。打球音で検出するため、音声付きの動画を選んでください。');
      state.keyframeSec = measureKeyframes(state.info.video);

      const acc = await extractAudio(function (r) { setProgress(r * 0.95, '音声から打球音を探しています'); });
      setProgress(0.97, '検出しています');
      await new Promise(function (r) { setTimeout(r, 0); });
      state.prep = Detect.prepare(Detect.envelope(acc));

      if (state.srcUrl) URL.revokeObjectURL(state.srcUrl);
      state.srcUrl = URL.createObjectURL(state.file);
      $('srcVideo').src = state.srcUrl;
      releaseWake();
      redetect();
      show('stepReview');
    } catch (e) {
      console.error(e);
      showError(e.message || String(e));
    }
  });

  $('retryBtn').addEventListener('click', function () { show(state.prep ? 'stepReview' : 'stepSelect'); });

  // ---- 2. 確認・調整 ----

  function renderSettings() {
    const box = $('settings');
    box.innerHTML = '';
    const st = state.settings[state.mode];
    SETTINGS[state.mode].forEach(function (def) {
      const row = document.createElement('div');
      row.className = 'row';
      const label = document.createElement('label');
      label.htmlFor = 'set_' + def.key;
      label.textContent = def.label + ' ';
      const val = document.createElement('b');
      if (def.type === 'range') { val.textContent = st[def.key]; label.appendChild(val); }
      const small = document.createElement('small');
      small.textContent = def.note;
      label.appendChild(small);
      const input = document.createElement('input');
      input.type = def.type;
      input.id = 'set_' + def.key;
      if (def.type === 'checkbox') input.checked = !!st[def.key];
      else {
        input.min = def.min; input.max = def.max; input.step = def.step; input.value = st[def.key];
        if (def.type === 'number') input.inputMode = 'decimal';
      }
      input.addEventListener(def.type === 'number' ? 'change' : 'input', function () {
        let v;
        if (def.type === 'checkbox') v = input.checked;
        else {
          v = parseFloat(input.value);
          if (isNaN(v)) v = Detect[state.mode.toUpperCase()][def.key];
          v = Math.min(def.max, Math.max(def.min, v));
          if (def.type === 'range') val.textContent = v;
        }
        st[def.key] = v;
        save('cut.settings.' + state.mode, st);
        scheduleRedetect();
      });
      row.appendChild(label);
      if (def.type === 'number') {
        const wrap = document.createElement('span');
        wrap.appendChild(input);
        wrap.appendChild(document.createTextNode(' ' + def.unit));
        row.appendChild(wrap);
      } else row.appendChild(input);
      box.appendChild(row);
    });
  }

  let redetectTimer = null;
  function scheduleRedetect() {
    clearTimeout(redetectTimer);
    redetectTimer = setTimeout(redetect, 150);
  }

  function redetect() {
    if (!state.prep) return;
    stopPreview();
    const st = state.settings[state.mode];
    const r = state.mode === 'tennis' ? Detect.tennisClips(state.prep, st) : Detect.golfClips(state.prep, st);
    state.clips = r.clips.map(function (c) { c.on = true; return c; });
    state.count = r.count;
    state.unit = r.unit;
    renderClips();
  }

  function renderSummary() {
    const on = state.clips.filter(function (c) { return c.on; });
    const total = on.reduce(function (s, c) { return s + c.end - c.start; }, 0);
    const dur = state.info.duration;
    const el = $('summary');
    el.innerHTML = '';
    const line1 = document.createElement('div');
    line1.innerHTML = '<b></b> を検出 ／ 元の長さ <span></span>';
    line1.querySelector('b').textContent = state.count + ' ' + state.unit;
    line1.querySelector('span').textContent = fmt(dur);
    const line2 = document.createElement('div');
    line2.textContent = on.length + ' 本を書き出し・合計 ' + fmt(total) + '（' + Math.round(total / dur * 100) + '%）';
    el.appendChild(line1);
    el.appendChild(line2);
    if (state.keyframeSec > 0.6) {
      const note = document.createElement('div');
      note.className = 'hint';
      note.textContent = '画質を保つため再エンコードしません。各区間の始まりと終わりは、動画のキーフレーム（約 ' +
        state.keyframeSec.toFixed(1) + ' 秒ごと）に合わせて少し広がります。';
      el.appendChild(note);
    }
    $('exportBtn').textContent = '書き出す（' + on.length + ' 本・' + fmt(total) + '）';
    $('exportBtn').disabled = !on.length;
  }

  function renderClips() {
    const list = $('clips');
    list.innerHTML = '';
    state.clips.forEach(function (c, i) { list.appendChild(clipItem(c, i)); });
    if (!state.clips.length) {
      const li = document.createElement('li');
      li.className = 'card hint';
      li.textContent = state.mode === 'tennis'
        ? 'ラリーが見つかりませんでした。「検出の設定」で感度を上げるか、最少打球数を下げてください。'
        : '打球音が見つかりませんでした。「検出の設定」で感度を上げてください。';
      list.appendChild(li);
      $('settingsBox').open = true;
    }
    renderSummary();
  }

  function clipItem(c, i) {
    const li = document.createElement('li');
    li.className = 'clip' + (c.on ? '' : ' off');
    li.id = 'clip' + i;

    const top = document.createElement('div');
    top.className = 'top';
    const chk = document.createElement('input');
    chk.type = 'checkbox';
    chk.checked = c.on;
    chk.setAttribute('aria-label', c.label + ' を書き出す');
    chk.addEventListener('change', function () { c.on = chk.checked; li.classList.toggle('off', !c.on); renderSummary(); });
    const info = document.createElement('div');
    info.className = 'info';
    const name = document.createElement('b');
    name.textContent = c.label;
    const detail = document.createElement('span');
    const updateDetail = function () {
      detail.textContent = '元 ' + fmt(c.start) + '〜' + fmt(c.end) + '・' + (c.end - c.start).toFixed(1) + ' 秒・' + c.detail;
    };
    updateDetail();
    info.appendChild(name);
    info.appendChild(detail);
    const play = document.createElement('button');
    play.type = 'button';
    play.className = 'play';
    play.textContent = '▶';
    play.setAttribute('aria-label', c.label + ' を再生');
    play.addEventListener('click', function () { startPreview([i]); });
    top.appendChild(chk);
    top.appendChild(info);
    top.appendChild(play);

    const trim = document.createElement('div');
    trim.className = 'trim';
    const nudge = function (label, edge, delta) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.addEventListener('click', function () {
        const dur = state.info.duration;
        if (edge === 'start') c.start = Math.min(c.end - NUDGE, Math.max(0, c.start + delta));
        else c.end = Math.max(c.start + NUDGE, Math.min(dur, c.end + delta));
        updateDetail();
        renderSummary();
        startPreview([i], edge === 'end' ? Math.max(c.start, c.end - 2) : null);
      });
      return b;
    };
    trim.appendChild(document.createTextNode('開始'));
    trim.appendChild(nudge('−' + NUDGE, 'start', -NUDGE));
    trim.appendChild(nudge('+' + NUDGE, 'start', NUDGE));
    const gap = document.createElement('span');
    gap.className = 'gap';
    trim.appendChild(gap);
    trim.appendChild(document.createTextNode('終了'));
    trim.appendChild(nudge('−' + NUDGE, 'end', -NUDGE));
    trim.appendChild(nudge('+' + NUDGE, 'end', NUDGE));

    li.appendChild(top);
    li.appendChild(trim);
    return li;
  }

  // 元の動画で区間を再生する。複数渡すと続けて再生する（書き出し後のイメージ確認）
  function startPreview(indices, from) {
    stopPreview();
    if (!indices.length) return;
    state.queue = indices.slice();
    playNext(from);
  }
  function playNext(from) {
    const video = $('srcVideo');
    document.querySelectorAll('.clip.playing').forEach(function (el) { el.classList.remove('playing'); });
    if (!state.queue || !state.queue.length) { stopPreview(); return; }
    const i = state.queue.shift();
    const c = state.clips[i];
    state.stopAt = c.end;
    const el = $('clip' + i);
    if (el) el.classList.add('playing');
    $('nowPlaying').textContent = '再生中: ' + c.label + '（' + fmt(c.start) + '〜' + fmt(c.end) + '）';
    video.currentTime = from != null ? from : c.start;
    const p = video.play();
    if (p && p.catch) p.catch(function () {});
  }
  function stopPreview() {
    state.queue = null;
    state.stopAt = null;
    $('nowPlaying').textContent = '';
    document.querySelectorAll('.clip.playing').forEach(function (el) { el.classList.remove('playing'); });
  }
  $('srcVideo').addEventListener('timeupdate', function () {
    const video = $('srcVideo');
    if (state.stopAt == null || video.currentTime < state.stopAt) return;
    if (state.queue && state.queue.length) playNext();
    else { video.pause(); stopPreview(); }
  });

  $('playAllBtn').addEventListener('click', function () {
    const on = [];
    state.clips.forEach(function (c, i) { if (c.on) on.push(i); });
    startPreview(on);
  });
  $('allOnBtn').addEventListener('click', function () { state.clips.forEach(function (c) { c.on = true; }); renderClips(); });
  $('allOffBtn').addEventListener('click', function () { state.clips.forEach(function (c) { c.on = false; }); renderClips(); });

  // ---- 3. 書き出し ----

  function outputName() {
    const base = state.file.name.replace(/\.[^.]+$/, '');
    const ftyp = state.info.ftyp;
    const quicktime = !ftyp || String.fromCharCode(ftyp[8], ftyp[9], ftyp[10], ftyp[11]) === 'qt  ';
    return { name: base + '_cut' + (quicktime ? '.mov' : '.mp4'), type: quicktime ? 'video/quicktime' : 'video/mp4' };
  }

  function exportInWorker(segments, name) {
    return new Promise(function (resolve, reject) {
      const worker = new Worker('worker.js');
      worker.onmessage = function (e) {
        const m = e.data;
        if (m.type === 'progress') setProgress(m.progress, '書き出しています');
        else if (m.type === 'done') { worker.terminate(); resolve(m); }
        else if (m.type === 'no-storage') { worker.terminate(); resolve(null); }
        else if (m.type === 'error') { worker.terminate(); reject(new Error(m.message)); }
      };
      worker.onerror = function (e) { worker.terminate(); reject(new Error(e.message || '書き出しに失敗しました')); };
      worker.postMessage({ file: state.file, segments: segments, name: name });
    });
  }

  // 保存領域が使えない場合（プライベートブラウズなど）はメモリ上で作る。短い動画向け
  async function exportInMemory(segments) {
    const parts = [], starts = [];
    let pos = 0;
    const writer = {
      get position() { return pos; },
      write: async function (u8) { parts.push(u8); starts.push(pos); pos += u8.byteLength; },
      patch: async function (at, u8) {
        for (let k = 0; k < parts.length; k++) {
          if (at >= starts[k] && at + u8.length <= starts[k] + parts[k].length) { parts[k].set(u8, at - starts[k]); return; }
        }
      },
    };
    const stats = await MP4.mux(state.reader, state.info, segments, writer, function (p) { setProgress(p, '書き出しています'); });
    return { parts: parts, stats: stats };
  }

  $('exportBtn').addEventListener('click', async function () {
    stopPreview();
    $('srcVideo').pause();
    const segments = state.clips.filter(function (c) { return c.on; }).map(function (c) { return [c.start, c.end]; });
    if (!segments.length) return;
    show('stepProgress');
    $('error').hidden = true;
    $('retryBtn').hidden = true;
    $('progressHint').hidden = false;
    setProgress(0, '書き出しています');
    keepAwake();
    const out = outputName();
    try {
      let result = await exportInWorker(segments, out.name);
      let file;
      if (result) file = new File([result.file], out.name, { type: out.type });
      else {
        result = await exportInMemory(segments);
        file = new File(result.parts, out.name, { type: out.type });
      }
      releaseWake();
      showDone(file, result.stats);
    } catch (e) {
      console.error(e);
      showError('書き出しに失敗しました: ' + (e.message || e));
    }
  });

  function showDone(file, stats) {
    state.outFile = file;
    if (state.outUrl) URL.revokeObjectURL(state.outUrl);
    state.outUrl = URL.createObjectURL(file);
    $('outVideo').src = state.outUrl;
    $('downloadLink').href = state.outUrl;
    $('downloadLink').download = file.name;
    $('doneSummary').textContent = '完成: ' + fmt(stats.duration) + '・' + formatSize(file.size) + '（元 ' + fmt(state.info.duration) + '）';
    show('stepDone');
  }

  $('shareBtn').addEventListener('click', async function () {
    const f = state.outFile;
    if (navigator.canShare && navigator.canShare({ files: [f] })) {
      try { await navigator.share({ files: [f] }); } catch (e) { /* キャンセル */ }
    } else {
      $('downloadLink').click();
    }
  });
  $('backBtn').addEventListener('click', function () { show('stepReview'); });
  $('newBtn').addEventListener('click', function () {
    state.prep = null;
    state.clips = [];
    $('file').value = '';
    $('file').dispatchEvent(new Event('change'));
    show('stepSelect');
  });

  setMode(load('cut.mode') === 'tennis' ? 'tennis' : 'golf');
})();
