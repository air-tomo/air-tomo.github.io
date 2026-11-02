/*
 * MP4 / MOV（iPhone の動画）の読み込みと、選んだ区間だけを再エンコードせずに書き出す処理。
 *
 * 映像・音声のデータはそのままコピーするので、画質が落ちず、iPhone でも速い。
 * 大きなファイルでもメモリに載せないよう、必要な部分だけを File.slice で読む。
 * ブラウザ（メイン・Worker）と Node（テスト用）の両方で動く。
 */
(function (root) {
  'use strict';

  // ---- バイト列の読み書き ----

  function fourcc(b, o) {
    return String.fromCharCode(b[o], b[o + 1], b[o + 2], b[o + 3]);
  }
  function u16(b, o) { return (b[o] << 8) | b[o + 1]; }
  function u32(b, o) { return ((b[o] << 24) >>> 0) + (b[o + 1] << 16) + (b[o + 2] << 8) + b[o + 3]; }
  function i32(b, o) { return (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3]; }
  function u64(b, o) { return u32(b, o) * 4294967296 + u32(b, o + 4); }

  function concat(parts) {
    let n = 0;
    for (const p of parts) n += p.length;
    const out = new Uint8Array(n);
    let o = 0;
    for (const p of parts) { out.set(p, o); o += p.length; }
    return out;
  }
  function be(bytesPer, values) {
    const out = new Uint8Array(bytesPer * values.length);
    const dv = new DataView(out.buffer);
    values.forEach(function (v, i) {
      if (bytesPer === 1) dv.setUint8(i, v);
      else if (bytesPer === 2) dv.setUint16(i * 2, v);
      else if (bytesPer === 4) dv.setUint32(i * 4, v >>> 0);
      else { dv.setUint32(i * 8, Math.floor(v / 4294967296)); dv.setUint32(i * 8 + 4, v >>> 0); }
    });
    return out;
  }
  const U8 = function () { return be(1, Array.from(arguments)); };
  const U16 = function () { return be(2, Array.from(arguments)); };
  const U32 = function () { return be(4, Array.from(arguments)); };
  const U64 = function () { return be(8, Array.from(arguments)); };
  function str4(s) { return new Uint8Array([s.charCodeAt(0), s.charCodeAt(1), s.charCodeAt(2), s.charCodeAt(3)]); }

  function box(type, parts) {
    const body = concat(parts);
    const out = new Uint8Array(8 + body.length);
    new DataView(out.buffer).setUint32(0, out.length);
    out.set(str4(type), 4);
    out.set(body, 8);
    return out;
  }
  function fullbox(type, version, flags, parts) {
    return box(type, [U8(version, (flags >> 16) & 255, (flags >> 8) & 255, flags & 255)].concat(parts));
  }

  // ---- 読み込み ----

  function fileReader(file) {
    return {
      size: file.size,
      read: async function (offset, length) {
        return new Uint8Array(await file.slice(offset, offset + length).arrayBuffer());
      },
    };
  }

  function children(b, start, end) {
    const out = [];
    let o = start;
    while (o + 8 <= end) {
      let size = u32(b, o);
      const type = fourcc(b, o + 4);
      let hdr = 8;
      if (size === 1) { size = u64(b, o + 8); hdr = 16; } else if (size === 0) size = end - o;
      if (size < hdr || o + size > end) break;
      out.push({ type: type, start: o, end: o + size, body: o + hdr });
      o += size;
    }
    return out;
  }
  function find(b, parent, type) {
    if (!parent) return null;
    return children(b, parent.body, parent.end).find(function (c) { return c.type === type; }) || null;
  }
  function bytesOf(b, bx) { return bx ? b.slice(bx.start, bx.end) : null; }

  async function topLevelBoxes(reader) {
    const boxes = [];
    let o = 0;
    while (o + 8 <= reader.size) {
      const h = await reader.read(o, 16);
      let size = u32(h, 0);
      const type = fourcc(h, 4);
      if (size === 1) size = u64(h, 8); else if (size === 0) size = reader.size - o;
      if (size < 8) break;
      boxes.push({ type: type, start: o, size: size });
      o += size;
    }
    return boxes;
  }

  function parseTrack(b, trak, movieTimescale) {
    const tkhd = find(b, trak, 'tkhd');
    const mdia = find(b, trak, 'mdia');
    const mdhd = find(b, mdia, 'mdhd');
    const hdlr = find(b, mdia, 'hdlr');
    const minf = find(b, mdia, 'minf');
    const stbl = find(b, minf, 'stbl');
    if (!tkhd || !mdhd || !hdlr || !stbl) return null;

    const tv = b[tkhd.body];
    const m = tkhd.body + (tv === 1 ? 52 : 40);
    const mv = b[mdhd.body];
    const timescale = u32(b, mdhd.body + (mv === 1 ? 20 : 12));
    const track = {
      id: u32(b, tkhd.body + (tv === 1 ? 20 : 12)),
      handler: fourcc(b, hdlr.body + 8),
      timescale: timescale,
      language: u16(b, mdhd.body + (mv === 1 ? 32 : 20)),
      matrix: b.slice(m, m + 36),
      widthRaw: u32(b, m + 36),
      heightRaw: u32(b, m + 40),
      hdlrBox: bytesOf(b, hdlr),
      mhdBox: bytesOf(b, find(b, minf, 'vmhd') || find(b, minf, 'smhd')),
      dinfBox: bytesOf(b, find(b, minf, 'dinf')),
      stsdBox: bytesOf(b, find(b, stbl, 'stsd')),
    };
    const stsd = find(b, stbl, 'stsd');
    track.codec = fourcc(b, stsd.body + 12);

    // 編集リスト: 先頭の空白（遅延）と、メディアの開始位置（AAC のプライミングなど）
    let delay = 0, mediaTime = 0;
    const elst = find(b, find(b, trak, 'edts'), 'elst');
    if (elst) {
      const ev = b[elst.body];
      const count = u32(b, elst.body + 4);
      let o = elst.body + 8;
      for (let i = 0; i < count; i++) {
        const segDur = ev === 1 ? u64(b, o) : u32(b, o);
        const mt = ev === 1 ? i32(b, o + 12) : i32(b, o + 4);
        o += ev === 1 ? 20 : 12;
        if (mt === -1) { delay += segDur / movieTimescale; continue; }
        mediaTime = mt;
        break;
      }
    }
    track.offsetSec = delay - mediaTime / timescale;

    // サンプル表
    const stsz = find(b, stbl, 'stsz');
    if (!stsz) throw new Error('stz2 などの形式には対応していません');
    const constSize = u32(b, stsz.body + 4);
    const n = u32(b, stsz.body + 8);
    if (n > 8000000) throw new Error('サンプル数が多すぎます（非圧縮音声など未対応の形式）');
    const sizes = new Uint32Array(n);
    for (let i = 0; i < n; i++) sizes[i] = constSize || u32(b, stsz.body + 12 + i * 4);

    const durs = new Uint32Array(n);
    const dts = new Float64Array(n);
    const stts = find(b, stbl, 'stts');
    let s = 0, t = 0;
    for (let e = 0, cnt = u32(b, stts.body + 4); e < cnt; e++) {
      const c = u32(b, stts.body + 8 + e * 8), d = u32(b, stts.body + 12 + e * 8);
      for (let k = 0; k < c && s < n; k++, s++) { durs[s] = d; dts[s] = t; t += d; }
    }

    let cts = null;
    const ctts = find(b, stbl, 'ctts');
    if (ctts) {
      cts = new Int32Array(n);
      s = 0;
      for (let e = 0, cnt = u32(b, ctts.body + 4); e < cnt; e++) {
        const c = u32(b, ctts.body + 8 + e * 8), off = i32(b, ctts.body + 12 + e * 8);
        for (let k = 0; k < c && s < n; k++, s++) cts[s] = off;
      }
    }

    let sync = null;
    const stss = find(b, stbl, 'stss');
    if (stss) {
      sync = new Uint8Array(n);
      for (let e = 0, cnt = u32(b, stss.body + 4); e < cnt; e++) {
        const idx = u32(b, stss.body + 8 + e * 4) - 1;
        if (idx >= 0 && idx < n) sync[idx] = 1;
      }
    }

    const co = find(b, stbl, 'co64') || find(b, stbl, 'stco');
    const is64 = co.type === 'co64';
    const chunkCount = u32(b, co.body + 4);
    const chunkOffset = function (c) { return is64 ? u64(b, co.body + 8 + c * 8) : u32(b, co.body + 8 + c * 4); };
    const offsets = new Float64Array(n);
    const stsc = find(b, stbl, 'stsc');
    const entries = u32(b, stsc.body + 4);
    s = 0;
    for (let e = 0; e < entries; e++) {
      const first = u32(b, stsc.body + 8 + e * 12);
      const spc = u32(b, stsc.body + 12 + e * 12);
      const next = e + 1 < entries ? u32(b, stsc.body + 8 + (e + 1) * 12) : chunkCount + 1;
      for (let c = first; c < next && s < n; c++) {
        let off = chunkOffset(c - 1);
        for (let k = 0; k < spc && s < n; k++, s++) { offsets[s] = off; off += sizes[s]; }
      }
    }

    const times = new Float64Array(n);
    for (let i = 0; i < n; i++) times[i] = dts[i] / timescale + track.offsetSec;
    Object.assign(track, {
      count: n, sizes: sizes, offsets: offsets, durs: durs, cts: cts, sync: sync, times: times,
      duration: t / timescale,
      frameSec: n ? durs[0] / timescale : 0,
    });
    return track;
  }

  async function parse(reader) {
    const boxes = await topLevelBoxes(reader);
    const moovInfo = boxes.find(function (x) { return x.type === 'moov'; });
    if (!moovInfo) throw new Error('MP4 / MOV ファイルではありません');
    const ftypInfo = boxes.find(function (x) { return x.type === 'ftyp'; });
    const b = await reader.read(moovInfo.start, moovInfo.size);
    const moov = { type: 'moov', start: 0, end: b.length, body: 8 };
    const mvhd = find(b, moov, 'mvhd');
    const movieTimescale = u32(b, mvhd.body + (b[mvhd.body] === 1 ? 20 : 12));
    const tracks = [];
    for (const c of children(b, moov.body, moov.end)) {
      if (c.type !== 'trak') continue;
      const t = parseTrack(b, c, movieTimescale);
      if (t && t.count) tracks.push(t);
    }
    const video = tracks.find(function (t) { return t.handler === 'vide'; }) || null;
    const sounds = tracks.filter(function (t) { return t.handler === 'soun'; });
    const audio = sounds.find(function (t) { return t.codec === 'mp4a'; }) || sounds[0] || null;
    return {
      ftyp: ftypInfo ? await reader.read(ftypInfo.start, ftypInfo.size) : null,
      video: video,
      audio: audio,
      duration: video ? video.duration + Math.max(0, video.offsetSec) : 0,
    };
  }

  // ---- 音声（AAC）を ADTS 形式で取り出す。ブラウザの decodeAudioData でデコードできる ----

  function aacConfig(track) {
    const b = track.stsdBox;
    for (let o = 0; o + 4 < b.length; o++) {
      if (b[o] !== 0x65 || fourcc(b, o) !== 'esds') continue;
      let p = o + 8; // 'esds' + version/flags
      const readLen = function () {
        let len = 0;
        for (let k = 0; k < 4; k++) { const x = b[p++]; len = (len << 7) | (x & 0x7f); if (!(x & 0x80)) break; }
        return len;
      };
      if (b[p++] !== 0x03) return null;
      readLen();
      p += 2;
      const flags = b[p++];
      if (flags & 0x80) p += 2;
      if (flags & 0x40) p += 1 + b[p];
      if (flags & 0x20) p += 2;
      if (b[p++] !== 0x04) return null;
      readLen();
      p += 13;
      if (b[p++] !== 0x05) return null;
      readLen();
      let aot = b[p] >> 3;
      const freqIndex = ((b[p] & 7) << 1) | (b[p + 1] >> 7);
      const channels = (b[p + 1] >> 3) & 15;
      if (aot === 5 || aot === 29) aot = 2; // HE-AAC はベースの AAC-LC として扱う
      if (aot < 1 || aot > 4 || freqIndex > 12 || channels < 1 || channels > 7) return null;
      return { profile: aot - 1, freqIndex: freqIndex, channels: channels };
    }
    return null;
  }

  /* 位置順に並んだ [offset, length] の一覧をまとめて読む。ブラウザのファイル読み込みは1回ごとの
     待ち時間が大きいので、近くにあるものは間の不要な部分ごと1回で読み、複数を同時に読む。 */
  const MERGE_GAP = 256 * 1024, MERGE_SPAN = 8 * 1024 * 1024, PARALLEL = 6;
  async function readMany(reader, items) {
    const groups = [];
    items.forEach(function (it, k) {
      const g = groups[groups.length - 1];
      if (g && it[0] >= g.end && it[0] - g.end <= MERGE_GAP && it[0] + it[1] - g.start <= MERGE_SPAN) {
        g.end = Math.max(g.end, it[0] + it[1]);
        g.items.push(k);
      } else groups.push({ start: it[0], end: it[0] + it[1], items: [k] });
    });
    const out = new Array(items.length);
    let next = 0;
    async function work() {
      while (next < groups.length) {
        const g = groups[next++];
        const bytes = await reader.read(g.start, g.end - g.start);
        for (const k of g.items) out[k] = bytes.subarray(items[k][0] - g.start, items[k][0] - g.start + items[k][1]);
      }
    }
    const workers = [];
    for (let w = 0; w < PARALLEL; w++) workers.push(work());
    await Promise.all(workers);
    return out;
  }

  function readSamples(reader, track, i0, i1) {
    const items = [];
    for (let i = i0; i < i1; i++) items.push([track.offsets[i], track.sizes[i]]);
    return readMany(reader, items);
  }

  /* 前から順に読む用途向けに、まとめて先読みして使い回す */
  function windowReader(reader, size) {
    let cache = null, cacheStart = 0;
    return {
      size: reader.size,
      read: async function (offset, length) {
        if (length >= size) return reader.read(offset, length);
        if (!cache || offset < cacheStart || offset + length > cacheStart + cache.length) {
          cacheStart = offset;
          cache = await reader.read(offset, Math.min(size, reader.size - offset));
        }
        return cache.slice(offset - cacheStart, offset - cacheStart + length);
      },
    };
  }

  async function adts(reader, track, cfg, i0, i1) {
    const frames = await readSamples(reader, track, i0, i1);
    const parts = [];
    for (const f of frames) {
      const len = f.length + 7;
      parts.push(new Uint8Array([
        0xff, 0xf1,
        (cfg.profile << 6) | (cfg.freqIndex << 2) | (cfg.channels >> 2),
        ((cfg.channels & 3) << 6) | (len >> 11),
        (len >> 3) & 0xff,
        ((len & 7) << 5) | 0x1f,
        0xfc,
      ]), f);
    }
    return concat(parts);
  }

  // ---- 書き出し ----

  function firstGE(arr, t) {
    let lo = 0, hi = arr.length;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] < t) lo = mid + 1; else hi = mid; }
    return lo;
  }

  // HEVC の NAL ユニット種別（IDR・CRA などのキーフレームの種類を見分けるため）
  const HEVC_CODECS = ['hvc1', 'hev1', 'dvh1', 'dvhe'];
  const HEVC_CRA = 21, HEVC_BLA_W_LP = 16, HEVC_RASL_N = 8, HEVC_RASL_R = 9;

  function nalLengthSize(track) {
    const b = track.stsdBox;
    for (let o = 0; o + 30 < b.length; o++) {
      if (fourcc(b, o) === 'hvcC') return (b[o + 4 + 21] & 3) + 1;
    }
    return 4;
  }

  async function hevcPictureType(reader, track, i, lengthSize) {
    const b = await reader.read(track.offsets[i], Math.min(track.sizes[i], 4096));
    let o = 0;
    while (o + lengthSize < b.length) {
      let len = 0;
      for (let k = 0; k < lengthSize; k++) len = len * 256 + b[o + k];
      const type = (b[o + lengthSize] >> 1) & 0x3f;
      if (type < 32) return type; // 最初の映像 NAL（VPS/SPS/PPS/SEI は飛ばす）
      o += lengthSize + len;
    }
    return -1;
  }

  /* つなぎ目の CRA を BLA（つなぎ目用のキーフレーム）に書き換える。
     デコーダが前の区間とのフレーム順序を混同しないようにするため（NAL ヘッダ1バイトの変更）。 */
  function craToBla(b, lengthSize) {
    let o = 0;
    while (o + lengthSize < b.length) {
      let len = 0;
      for (let k = 0; k < lengthSize; k++) len = len * 256 + b[o + k];
      const h = o + lengthSize;
      if (((b[h] >> 1) & 0x3f) === HEVC_CRA) b[h] = (b[h] & 0x81) | (HEVC_BLA_W_LP << 1);
      o += lengthSize + len;
    }
  }

  function newOut(src) {
    return { src: src, sizes: [], durs: [], cts: [], sync: [], chunkOffsets: [], chunkCounts: [], time: 0 };
  }

  /*
   * segments: [[開始秒, 終了秒], ...] を1本の動画にして writer に書き出す。
   * 再エンコードしないため、各区間の開始は直前のキーフレームまで少し早まる。
   * writer: { position, write(Uint8Array), patch(位置, Uint8Array) }
   */
  async function mux(reader, info, segments, writer, onProgress) {
    const v = info.video, a = info.audio;
    reader = windowReader(reader, 4 * 1024 * 1024);
    // B フレームのある動画でも壊れないよう、区間はキーフレームからキーフレームの手前まで（GOP 単位）にする
    const ranges = [];
    const sorted = segments.slice().sort(function (x, y) { return x[0] - y[0]; });
    for (const seg of sorted) {
      let i0 = Math.max(0, firstGE(v.times, seg[0] + 1e-6) - 1);
      if (v.sync) while (i0 > 0 && !v.sync[i0]) i0--;
      let i1 = Math.min(v.count, firstGE(v.times, seg[1]));
      if (v.sync) while (i1 < v.count && !v.sync[i1]) i1++;
      if (i1 <= i0) continue;
      const last = ranges[ranges.length - 1];
      if (last && i0 <= last[1]) last[1] = Math.max(last[1], i1);
      else ranges.push([i0, i1]);
    }
    if (!ranges.length) throw new Error('書き出す区間がありません');

    // HEVC の CRA から始まる区間では、直後の RASL フレーム（前の GOP を参照するので表示できない）を除く
    // 除いたフレームの時間はキーフレームに足して、後続フレームの表示時刻がずれないようにする
    const drops = new Set(), splices = new Set(), extraDur = new Map();
    const lengthSize = nalLengthSize(v);
    if (HEVC_CODECS.indexOf(v.codec) >= 0) {
      const ls = lengthSize;
      for (const r of ranges) {
        if (await hevcPictureType(reader, v, r[0], ls) !== HEVC_CRA) continue;
        splices.add(r[0]);
        for (let j = r[0] + 1; j < Math.min(r[1], r[0] + 32); j++) {
          const type = await hevcPictureType(reader, v, j, ls);
          if (type === HEVC_RASL_N || type === HEVC_RASL_R) {
            drops.add(j);
            extraDur.set(r[0], (extraDur.get(r[0]) || 0) + v.durs[j]);
          }
          else if (type < 6 || type > 9) break; // 先行フレーム（RADL/RASL）が終わった
        }
      }
    }

    let totalBytes = 0, doneBytes = 0;
    for (const r of ranges) for (let i = r[0]; i < r[1]; i++) totalBytes += v.sizes[i];
    if (a) totalBytes *= 1.03;

    const vo = newOut(v), ao = a ? newOut(a) : null;
    const ftyp = info.ftyp || box('ftyp', [str4('qt  '), U32(0x200), str4('qt  ')]);
    await writer.write(ftyp);
    const mdatPos = writer.position;
    await writer.write(concat([U32(1), str4('mdat'), U64(0)]));

    async function writeBlock(out, idx) {
      if (!idx.length) return;
      out.chunkOffsets.push(writer.position);
      out.chunkCounts.push(idx.length);
      const src = out.src;
      let k = 0;
      while (k < idx.length) {
        const start = src.offsets[idx[k]];
        let len = src.sizes[idx[k]], m = k + 1;
        while (m < idx.length && idx[m] === idx[m - 1] + 1 && src.offsets[idx[m]] === start + len && len < 16e6) {
          len += src.sizes[idx[m]];
          m++;
        }
        const bytes = await reader.read(start, len);
        if (src === v && splices.has(idx[k])) craToBla(bytes.subarray(0, v.sizes[idx[k]]), lengthSize);
        await writer.write(bytes);
        doneBytes += len;
        k = m;
      }
      for (const i of idx) {
        const dur = src.durs[i] + (src === v ? extraDur.get(i) || 0 : 0);
        out.sizes.push(src.sizes[i]);
        out.durs.push(dur);
        if (src.cts) out.cts.push(src.cts[i]);
        out.sync.push(src.sync ? src.sync[i] : 1);
        out.time += dur;
      }
    }

    for (const r of ranges) {
      const vIdx = [];
      let vDur = 0;
      for (let i = r[0]; i < r[1]; i++) {
        vDur += v.durs[i];
        if (!drops.has(i)) vIdx.push(i);
      }

      // 音声は映像と出力上の長さがずれないよう、映像の区間に合わせてサンプル数を決める
      const aIdx = [];
      if (a) {
        const vStart = vo.time / v.timescale, aStart = ao.time / a.timescale;
        const vEnd = (vo.time + vDur) / v.timescale;
        let j = firstGE(a.times, v.times[r[0]] + (aStart - vStart) - a.frameSec / 2);
        let aOut = aStart;
        while (j < a.count && aOut < vEnd - a.frameSec / 2) { aIdx.push(j); aOut += a.durs[j] / a.timescale; j++; }
      }

      // 約1秒ごとに映像と音声を交互に並べる（再生時の読み込みを効率よくするため）
      let pv = 0, pa = 0;
      while (pv < vIdx.length || pa < aIdx.length) {
        const tBlock = (pv < vIdx.length ? v.times[vIdx[pv]] : a.times[aIdx[pa]]) + 1.0;
        const vb = [], ab = [];
        while (pv < vIdx.length && v.times[vIdx[pv]] < tBlock) vb.push(vIdx[pv++]);
        while (pa < aIdx.length && (a.times[aIdx[pa]] < tBlock || pv >= vIdx.length)) ab.push(aIdx[pa++]);
        await writeBlock(vo, vb);
        await writeBlock(ao || newOut(v), ab);
        if (onProgress) onProgress(Math.min(1, doneBytes / totalBytes));
      }
    }

    const mdatSize = writer.position - mdatPos;
    await writer.patch(mdatPos + 8, U64(mdatSize));
    const moov = buildMoov([vo].concat(ao ? [ao] : []));
    await writer.write(moov);
    if (onProgress) onProgress(1);
    return { duration: vo.time / v.timescale, bytes: writer.position, ranges: ranges.length };
  }

  const MOVIE_TIMESCALE = 1000;
  const IDENTITY = U32(0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000);

  function table(entryBytes, rows, prefix) {
    const out = new Uint8Array(4 + rows.length * entryBytes);
    const dv = new DataView(out.buffer);
    dv.setUint32(0, rows.length);
    rows.forEach(function (row, i) {
      row.forEach(function (val, k) {
        const o = 4 + i * entryBytes + k * (entryBytes / row.length);
        if (entryBytes / row.length === 8) { dv.setUint32(o, Math.floor(val / 4294967296)); dv.setUint32(o + 4, val >>> 0); }
        else dv.setUint32(o, val >>> 0);
      });
    });
    return prefix ? concat([prefix, out]) : out;
  }

  function runs(values) {
    const out = [];
    for (const val of values) {
      const last = out[out.length - 1];
      if (last && last[1] === val) last[0]++;
      else out.push([1, val]);
    }
    return out;
  }

  function buildTrak(out, trackId) {
    const src = out.src;
    const mediaDur = out.time;
    const movieDur = Math.round(mediaDur / src.timescale * MOVIE_TIMESCALE);
    const isAudio = src.handler === 'soun';

    const stbl = [src.stsdBox, fullbox('stts', 0, 0, [table(8, runs(out.durs))])];
    if (src.cts) {
      const neg = out.cts.some(function (x) { return x < 0; });
      stbl.push(fullbox('ctts', neg ? 1 : 0, 0, [table(8, runs(out.cts))]));
    }
    if (src.sync) {
      const keys = [];
      out.sync.forEach(function (s, i) { if (s) keys.push([i + 1]); });
      stbl.push(fullbox('stss', 0, 0, [table(4, keys)]));
    }
    const stsc = [];
    out.chunkCounts.forEach(function (c, i) {
      const last = stsc[stsc.length - 1];
      if (!last || last[1] !== c) stsc.push([i + 1, c, 1]);
    });
    stbl.push(fullbox('stsc', 0, 0, [table(12, stsc)]));
    const stsz = new Uint8Array(8 + out.sizes.length * 4);
    const dv = new DataView(stsz.buffer);
    dv.setUint32(4, out.sizes.length);
    out.sizes.forEach(function (s, i) { dv.setUint32(8 + i * 4, s); });
    stbl.push(fullbox('stsz', 0, 0, [stsz]));
    stbl.push(fullbox('co64', 0, 0, [table(8, out.chunkOffsets.map(function (o) { return [o]; }))]));

    const minf = [
      src.mhdBox || (isAudio ? fullbox('smhd', 0, 0, [U32(0)]) : fullbox('vmhd', 0, 1, [U16(0, 0, 0, 0)])),
      src.dinfBox || box('dinf', [fullbox('dref', 0, 0, [U32(1), fullbox('url ', 0, 1, [])])]),
      box('stbl', stbl),
    ];
    const big = mediaDur > 0xffffffff;
    const mdhd = big
      ? fullbox('mdhd', 1, 0, [U64(0, 0), U32(src.timescale), U64(mediaDur), U16(src.language, 0)])
      : fullbox('mdhd', 0, 0, [U32(0, 0, src.timescale, mediaDur), U16(src.language, 0)]);
    const trak = [
      fullbox('tkhd', 0, 3, [
        U32(0, 0, trackId, 0, movieDur), U32(0, 0),
        U16(0, 0, isAudio ? 0x0100 : 0, 0), src.matrix, U32(src.widthRaw, src.heightRaw),
      ]),
    ];
    // B フレームがある映像は、最初の表示時刻が 0 になるよう編集リストで補正する
    if (src.cts && out.cts[0]) {
      trak.push(box('edts', [fullbox('elst', 0, 0, [U32(1, movieDur, out.cts[0], 0x10000)])]));
    }
    trak.push(box('mdia', [mdhd, src.hdlrBox, box('minf', minf)]));
    return { box: box('trak', trak), movieDur: movieDur };
  }

  function buildMoov(outs) {
    const traks = outs.map(function (o, i) { return buildTrak(o, i + 1); });
    const dur = Math.max.apply(null, traks.map(function (t) { return t.movieDur; }));
    const mvhd = fullbox('mvhd', 0, 0, [
      U32(0, 0, MOVIE_TIMESCALE, dur, 0x10000), U16(0x0100), new Uint8Array(10), IDENTITY,
      new Uint8Array(24), U32(outs.length + 1),
    ]);
    return box('moov', [mvhd].concat(traks.map(function (t) { return t.box; })));
  }

  const MP4 = {
    fileReader: fileReader, parse: parse, aacConfig: aacConfig, adts: adts, mux: mux, firstGE: firstGE,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = MP4;
  else root.MP4 = MP4;
})(typeof self !== 'undefined' ? self : this);
