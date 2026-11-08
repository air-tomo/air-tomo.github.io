# AGENTS.md

このリポジトリで作業するエージェント（Codex など）向けの約束。

## Claude Code との受け渡し（handoff/）

クラウドの Claude Code は GitHub にしか届かないので、このリポジトリの `handoff/` を受け渡し場所にする。

- `handoff/to-codex/<ID>.md` : Claude → Codex の依頼（1 依頼 = 1 ファイル）
- `handoff/to-claude/<ID>.md` : Codex → Claude の報告（依頼と同じ ID）

Codex の動き:
1. `git pull` して、`handoff/to-codex/` の `status: OPEN` のファイルを古い順に読む。
2. 着手したら依頼ファイルの `status:` を `IN_PROGRESS` にして commit・push する（二重に着手しないため）。
3. 終わったら `handoff/to-claude/<ID>.md` に報告を書き、依頼ファイルを `status: DONE`（できなければ `FAILED` と理由）にして commit・push する。
4. `OPEN` がなければ何もしない。
5. 依頼に書かれていない変更（コード・公開・設定）はしない。依頼の「完了条件」に沿って報告する。

報告ファイルの書式:
```
status: DONE | FAILED
request_id: <ID>
reported_at: <JST の日時>

## 結果
（依頼の完了条件に沿って）
```

## リポジトリの中身

- `cut/` : 公開ページ（ゴルフ・テニスの動画自動カット）。GitHub Pages で https://air-tomo.github.io/cut/
- `apps/cut-app/` : `cut/` を Capacitor で包んだ iOS / Android アプリ（テニス切り出し）
- `golf-swing-app/` : Mac 版と解析ツール（`tools/court3d/` にボール追跡・落下点・サーブ速度）
- ブラウザ版（`cut/detect.js`・`cut/rally.js`）と Python 版（`golf-swing-app/tennis.py`・`tools/court3d/tn_post.py`）は同じ結果を出すこと
  （`node golf-swing-app/tools/court3d/test_rally.js` で確認）
