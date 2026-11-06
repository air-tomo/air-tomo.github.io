// cut/（公開中のページ）を www/ にコピーして、アプリ用の native.js を読み込ませる。
// アプリの中身はページと同じなので、cut/ を直せばアプリも同じように直る。
const fs = require('fs'), path = require('path');
const SRC = path.join(__dirname, '..', '..', 'cut'), OUT = path.join(__dirname, 'www');
const FILES = ['index.html', 'app.js', 'detect.js', 'mp4.js', 'worker.js'];
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
for (const f of FILES) fs.copyFileSync(path.join(SRC, f), path.join(OUT, f));
// Capacitor のプラグイン（保存・共有）をまとめた小さなファイル
fs.copyFileSync(path.join(__dirname, 'native.js'), path.join(OUT, 'native.js'));
let html = fs.readFileSync(path.join(OUT, 'index.html'), 'utf8');
if (!html.includes('native.js')) html = html.replace('<script src="mp4.js"></script>', '<script src="native.js"></script>\n<script src="mp4.js"></script>');
fs.writeFileSync(path.join(OUT, 'index.html'), html);
console.log('www/ ready:', fs.readdirSync(OUT).join(', '));
