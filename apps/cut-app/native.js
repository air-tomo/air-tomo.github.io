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

  // iPhone: 写真ライブラリから変換なしで受け取る（VideoPicker は ios/App/App/SceneDelegate.swift）。
  // ブラウザの選択画面は動画をブラウザ用に変換するので、長い試合だと数分待たされる
  if (C.getPlatform && C.getPlatform() === 'ios') {
    const Picker = C.registerPlugin('VideoPicker');
    window.NativePick = async function () {
      const label = document.getElementById('fileLabel');
      const before = label.textContent;
      label.textContent = '写真ライブラリを開いています…';
      const sub = await Picker.addListener('loading', function () { label.textContent = '動画を読み込んでいます…'; });
      try {
        const r = await Picker.pick();
        if (r.cancelled) { label.textContent = before; return; }
        window.CutApp.setFile(window.MP4.urlFile(C.convertFileSrc(r.path), r.size, r.name, /\.mp4$/i.test(r.name) ? 'video/mp4' : 'video/quicktime'));
      } catch (err) {
        label.textContent = before;
        alert(err.message || String(err));
      } finally { sub.remove(); }
    };
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
