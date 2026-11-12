/*
 * アプリ（apps/cut-app の www = cut/ の中身）を本物のブラウザで最初から最後まで動かすテスト。
 * 合成した動画（打球音のクリックで 3 つのラリー）を ffmpeg で作り、テニスを選ぶ → 撮り方ガイド → 動画を選ぶ →
 * 検出 → 書き出し まで。iOS アプリと同じ「URL で少しずつ読む」経路（MP4.urlFile）も通す。
 *   node tests/test_app.js       （ffmpeg と playwright が必要）
 */
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs'), path = require('path'), os = require('os'), http = require('http');

const ROOT = path.resolve(__dirname, '..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'cutapp-'));
const fails = [];
function check(name, ok, msg) { console.log((ok ? '  ok   ' : '  FAIL ') + name + ': ' + msg); if (!ok) fails.push(name); }

// 60 秒の合成動画: ラリー 3 本（各 5〜6 打、2 秒間隔）を間隔をあけて置く
const rallies = [[5, 6], [24, 5], [44, 6]];
const hits = [];
rallies.forEach(([t0, n]) => { for (let i = 0; i < n; i++) hits.push(t0 + i * 2 + (i % 2 ? 0.1 : 0)); });
const click = hits.map((h) => `between(t,${h},${(h + 0.012).toFixed(3)})*sin(2*PI*1800*t)*0.9`).join('+');
const video = path.join(tmp, 'match.mp4');
execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=0x2f6e9e:s=640x360:r=30:d=60',
  '-f', 'lavfi', '-i', `aevalsrc='${click}+0.004*(random(0)-0.5)':s=44100:d=60`, '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
  '-c:a', 'aac', '-b:a', '128k', '-shortest', video]);

execFileSync('node', [path.join(ROOT, 'apps', 'cut-app', 'build.js')]);
const www = path.join(ROOT, 'apps', 'cut-app', 'www');
fs.copyFileSync(video, path.join(www, '_test_match.mp4'));

// 部分読み出し（Range）に応じる小さなサーバー（iOS アプリの読み方と同じ）
const server = http.createServer((req, res) => {
  const p = path.join(www, decodeURIComponent(req.url.split('?')[0]));
  if (!fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); return res.end(); }
  const size = fs.statSync(p).size, type = p.endsWith('.html') ? 'text/html' : p.endsWith('.js') ? 'text/javascript' : 'video/mp4';
  const m = /bytes=(\d+)-(\d*)/.exec(req.headers.range || '');
  if (m) { const a = +m[1], b = m[2] ? +m[2] : size - 1; res.writeHead(206, { 'Content-Type': type, 'Content-Range': `bytes ${a}-${b}/${size}`, 'Content-Length': b - a + 1 }); fs.createReadStream(p, { start: a, end: b }).pipe(res); }
  else { res.writeHead(200, { 'Content-Type': type, 'Content-Length': size }); fs.createReadStream(p).pipe(res); }
}).listen(0);

async function run(page, pick) {
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(`http://localhost:${server.address().port}/index.html`);
  await page.evaluate(() => { try { localStorage.clear(); } catch (e) {} });
  await page.reload();
  await page.click('button[data-mode="tennis"] >> nth=0');
  await pick(page);
  await page.click('#loadBtn');
  await page.waitForSelector('#stepReview:not([hidden]), #error:not([hidden])', { timeout: 120000 });
  const review = await page.isVisible('#stepReview');
  const label = review ? await page.textContent('#exportBtn') : await page.textContent('#error');
  let done = '-';
  if (review) {
    await page.click('#exportBtn');
    await page.waitForSelector('#stepDone:not([hidden]), #error:not([hidden])', { timeout: 120000 });
    done = (await page.isVisible('#stepDone')) ? await page.textContent('#doneSummary') : 'error: ' + await page.textContent('#error');
  }
  return { review, label, done, errors };
}

(async () => {
  const b = await chromium.launch();
  try {
    // 1) ふつうのファイル選択（ガイドが出る → 「動画を選ぶ」）
    let page = await b.newPage({ viewport: { width: 390, height: 844 } });
    let guideShown = false;
    let r = await run(page, async (p) => {
      await p.click('#fileLabel');
      guideShown = await p.isVisible('#guide');
      const [chooser] = await Promise.all([p.waitForEvent('filechooser'), p.click('#guidePick')]);
      await chooser.setFiles(video);
    });
    check('guide before picking', guideShown, guideShown ? '撮り方ガイドが出た' : '出なかった');
    const n = r.review ? +(/（(\d+) 本/.exec(r.label) || [0, 0])[1] : 0;
    check('detect (file input)', n === 3, r.label.trim());
    check('export (file input)', /^完成/.test(r.done), r.done.trim());
    check('no page errors', !r.errors.length, r.errors.join(' | ') || 'なし');
    await page.close();

    // 2) iOS アプリと同じ URL 読み（MP4.urlFile、Range で少しずつ）
    page = await b.newPage({ viewport: { width: 390, height: 844 } });
    const size = fs.statSync(video).size;
    r = await run(page, async (p) => {
      await p.evaluate((size) => window.CutApp.setFile(MP4.urlFile('/_test_match.mp4', size, 'match.mp4', 'video/mp4')), size);
    });
    const n2 = r.review ? +(/（(\d+) 本/.exec(r.label) || [0, 0])[1] : 0;
    check('detect (URL / Range, iOS path)', n2 === 3, r.label.trim());
    check('export (URL / Range, iOS path)', /^完成/.test(r.done), r.done.trim());
  } finally {
    await b.close(); server.close();
    fs.rmSync(path.join(www, '_test_match.mp4'), { force: true }); fs.rmSync(tmp, { recursive: true, force: true });
  }
  console.log('test_app:', fails.length ? 'NG ' + fails.join(', ') : 'OK');
  process.exit(fails.length ? 1 : 0);
})();
