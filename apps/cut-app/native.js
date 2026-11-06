/*
 * アプリ（iOS / Android）のときだけ使う部分。ページ版では何もしない。
 * ブラウザの「ダウンロード」や「共有」はアプリの中では動かないので、
 * 書き出した動画をいったん端末のキャッシュに保存して、OS の共有画面（写真に保存・LINE など）を開く。
 */
(function () {
  'use strict';
  const C = window.Capacitor;
  const native = !!(C && C.isNativePlatform && C.isNativePlatform());
  document.documentElement.classList.toggle('native-app', native);
  if (!native) return;
  const Filesystem = C.registerPlugin('Filesystem'), Share = C.registerPlugin('Share');

  function toBase64(bytes) {
    let s = '';
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }

  // 大きな動画でもメモリを使いすぎないよう、4MB ずつ書く
  window.NativeSave = async function (file, onProgress) {
    const path = 'export/' + file.name, CH = 4 * 1024 * 1024;
    try { await Filesystem.deleteFile({ path: path, directory: 'CACHE' }); } catch (e) { /* なければよい */ }
    for (let off = 0; off < file.size; off += CH) {
      const bytes = new Uint8Array(await file.slice(off, off + CH).arrayBuffer());
      const data = toBase64(bytes);
      if (off === 0) await Filesystem.writeFile({ path: path, data: data, directory: 'CACHE', recursive: true });
      else await Filesystem.appendFile({ path: path, data: data, directory: 'CACHE' });
      if (onProgress) onProgress(Math.min(1, (off + CH) / file.size));
    }
    const uri = (await Filesystem.getUri({ path: path, directory: 'CACHE' })).uri;
    try { await Share.share({ title: file.name, files: [uri] }); } catch (e) { /* キャンセル */ }
    return true;
  };
})();
