/*
 * cut/rally.js（ブラウザ版）が tn_post.py（Python 版）と同じ結果を出すかの確認。
 *   node golf-swing-app/tools/court3d/test_rally.js
 * data/IMG_0131_tn/*.json（ボール位置）と data/IMG_0131_expected.json（Python 版の結果）を使う。
 */
const fs = require('fs'), path = require('path');
const R = require(path.join(__dirname, '..', '..', '..', 'cut', 'rally.js'));
const E = JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'IMG_0131_expected.json')));
const cam = R.camera(E.cam), loud = R.loudHitsOf(E.times, E.levels), near = R.loudHitsOf(E.times, E.levels, 10);
const clips = E.clips.map((c) => ({ start: c.start, end: c.end, pts: JSON.parse(fs.readFileSync(path.join(__dirname, 'data', 'IMG_0131_tn', 'tn_' + String(c.k).padStart(3, '0') + '.json'))) }));
const m = R.analyzeMatch(clips, E.times, loud, cam, near);
let bad = 0;
E.clips.forEach((c, i) => {
  const a = m.clips[i], e = E.expected[c.k];
  const okR = JSON.stringify(a.ranges) === JSON.stringify(e.ranges);
  const okB = JSON.stringify(a.bounces.map((b) => [b.t, b.court, b.call])) === JSON.stringify(e.bounces);
  const okS = (a.serve ? a.serve.kmh : null) === null || e.serve_kmh === null ? (a.serve ? a.serve.kmh : null) === e.serve_kmh : Math.abs(a.serve.kmh - e.serve_kmh) <= 3;
  if (!(okR && okB && okS)) { bad++; console.log('clip', c.k, 'ranges', okR, 'bounces', okB, 'serve', okS); }
});
const pts = m.points.map((p) => p.serves.map((s) => [s.t, s.side, s.fault]));
const okP = JSON.stringify(pts) === JSON.stringify(E.points);
console.log((bad || !okP ? 'NG' : 'OK') + ': clips ' + (E.clips.length - bad) + '/' + E.clips.length + ', points ' + m.points.length + '/' + E.points.length + (okP ? '' : ' (point list differs)'));
process.exit(bad || !okP ? 1 : 0);
