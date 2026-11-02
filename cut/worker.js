/*
 * 書き出し用の Worker。
 * iPhone のメモリに収まらない大きな動画でも書き出せるよう、
 * ブラウザ内のストレージ（OPFS）に少しずつ書き込み、最後にファイルとして返す。
 */
importScripts('mp4.js');

const OUTPUT_DIR = 'outputs';

self.onmessage = async function (e) {
  const { file, segments, name } = e.data;
  let handle = null, fileHandle = null;
  try {
    try {
      const root = await navigator.storage.getDirectory();
      // 前回の書き出し結果は消してから使う（容量の節約）
      await root.removeEntry(OUTPUT_DIR, { recursive: true }).catch(function () {});
      const dir = await root.getDirectoryHandle(OUTPUT_DIR, { create: true });
      fileHandle = await dir.getFileHandle(name, { create: true });
      handle = await fileHandle.createSyncAccessHandle();
    } catch (err) {
      // プライベートブラウズなど OPFS が使えない環境
      self.postMessage({ type: 'no-storage' });
      return;
    }

    // 細かい書き込みは遅いので、4MB ずつまとめて書き込む
    const BUFFER = 4 * 1024 * 1024;
    const buf = new Uint8Array(BUFFER);
    let pos = 0, flushed = 0, used = 0;
    const flush = async function () {
      if (used) await handle.write(buf.subarray(0, used), { at: flushed });
      flushed += used;
      used = 0;
    };
    const writer = {
      get position() { return pos; },
      write: async function (u8) {
        if (used + u8.length > BUFFER) await flush();
        if (u8.length > BUFFER) {
          await handle.write(u8, { at: flushed });
          flushed += u8.length;
        } else {
          buf.set(u8, used);
          used += u8.length;
        }
        pos += u8.length;
      },
      patch: async function (at, u8) {
        if (at >= flushed) buf.set(u8, at - flushed);
        else await handle.write(u8, { at: at });
      },
    };
    const reader = MP4.fileReader(file);
    const info = await MP4.parse(reader);
    const stats = await MP4.mux(reader, info, segments, writer, function (p) {
      self.postMessage({ type: 'progress', progress: p });
    });
    await flush();
    await handle.truncate(pos);
    await handle.flush();
    await handle.close();
    handle = null;
    self.postMessage({ type: 'done', file: await fileHandle.getFile(), stats: stats });
  } catch (err) {
    if (handle) try { await handle.close(); } catch (_) {}
    self.postMessage({ type: 'error', message: (err && err.message) || String(err) });
  }
};
