status: OPEN
request_id: 20261124-ios-testflight
from: Claude Code
created_at: 2026-11-24 JST
type: BUILD（Mac で iOS アプリをビルドして TestFlight に上げる。必要最小限のコード修正のみ）

# Tennis Replay の iOS 版を TestFlight に上げる

オーナーが Apple Developer Program に登録済み。Mac で `apps/cut-app` の iOS 版をビルドし、App Store Connect に上げて、
TestFlight でテスター（身近な 5〜10 人）に配れる状態にしてください。

## 前提（オーナーに頼むこと）
- Xcode に Apple ID でサインイン（Xcode → Settings → Accounts）。2 要素認証の確認が出たらオーナーが承認する。
- App Store Connect でのアプリ作成ができない場合（権限・名前の重複など）はオーナーに聞く。勝手に別名で作らない。

## 手順
1. `git pull`。Mac に Xcode（最新）・Node・CocoaPods が無ければ入れる。
2. `cd apps/cut-app && npm install && npm run build`（`build.js` が `cut/` を `www/` に入れる）→ `npx cap sync ios`
3. App Store Connect（appstoreconnect.apple.com）で新規 App:
   - プラットフォーム iOS / 名前 `Tennis Replay`（重複なら `Tennis Replay - Point Cut`、それも駄目ならオーナーに聞く）
   - 主言語 日本語 / バンドル ID `io.github.airtomo.tennisreplay`（Certificates, Identifiers & Profiles で登録）/ SKU `tennisreplay`
4. `ios/App/App.xcworkspace`（無ければ `App.xcodeproj`）を開き、Signing & Capabilities で Team を選び Automatically manage signing。
   Version `0.2.0` / Build `1`（上げ直すたびに Build を +1）。
5. ビルドが通らないときは最小限だけ直す。特に `ios/App/App/SceneDelegate.swift` の `VideoPickerPlugin`（写真から変換なしで動画を選ぶ。
   PHPicker `.current`、tmp へコピー、ページに URL を返す）は**まだ一度もコンパイルしていない**。直した内容は報告に書く。
6. 実機（iPhone）で動作確認:
   - テニスの試合動画（10 分以上）を選ぶ → 待たずに解析が始まる（「変換中」で止まらない）
   - ポイント一覧が出る → 書き出し → 共有シートで「ビデオを保存」→ 写真に入る（クラッシュしない）
7. Product → Archive → Distribute App → App Store Connect → Upload。
8. App Store Connect の TestFlight:
   - テスト情報: フィードバック用メール（オーナーに聞く）、プライバシーポリシー URL `https://air-tomo.github.io/tennis/privacy.html`
   - 輸出コンプライアンス: 独自の暗号化なし（`ITSAppUsesNonExemptEncryption` = NO を Info.plist に入れてよい）
   - 外部テストのグループ「friends」を作り、ビルドを追加してベータ版審査に提出。**公開リンクを有効にし、リンクはオーナーにだけ伝える**（リポジトリに書かない）。

## やらないこと
- App Store 本審査への提出（ベータまで）。価格・課金の設定。
- 試合動画・証明書・API キー・パスワードを commit しない。
- 依頼外のコード変更。

## 完了条件（handoff/to-claude/20261124-ios-testflight.md に報告）
- アップロードしたビルド番号と、ベータ版審査の状態（提出済み / 承認済み）
- 実機確認の結果（10 分以上の動画で読み込みにかかった秒数、書き出し・保存の可否、使った iPhone と iOS）
- 直したコードの差分の要約（commit して push）
- 詰まった所とオーナーの操作が必要な所
