# テニス切り出しアプリ（iOS / Android）

公開中のページ `cut/` を [Capacitor](https://capacitorjs.com/) でアプリにしたもの。中身はページと同じで、
`npm run build` が `cut/` を `www/` にコピーし、アプリ用の `native.js`（端末に保存して共有画面を開く）を足す。

- Android テスト版（apk）を作る: `npm ci && npm run android && (cd android && ./gradlew assembleDebug)`
  （JDK 21・Android SDK 36）→ `android/app/build/outputs/apk/debug/app-debug.apk`
- iOS（自分の iPhone で試す。無料の Apple ID で可、7 日ごとに入れ直し）: Mac に Xcode を入れて
  ```
  git clone https://github.com/air-tomo/air-tomo.github.io.git && cd air-tomo.github.io/apps/cut-app
  npm ci && npm run ios && npx cap open ios
  ```
  Xcode で App → Signing & Capabilities → Team に自分の Apple ID、Bundle Identifier を自分用に変える
  （例 `com.<名前>.tenniscut`）→ iPhone をつないで ▶。iPhone の「設定 → 一般 → VPN とデバイス管理」で開発元を信頼。
  配布（TestFlight・App Store）は Apple Developer Program（年 99 ドル）に登録してから。
- iOS では写真ライブラリから動画を変換せずに受け取る（`ios/App/App/SceneDelegate.swift` の VideoPicker）。
  ブラウザの選択画面は動画をブラウザ用に変換するので長い試合だと数分かかるが、アプリならほぼ待たない。
- ストア公開: Google Play Console（登録 25 ドル）と Apple Developer Program（年 99 ドル）が必要。

撮り方のおすすめ: コートの後ろ（フェンス・三脚）に固定、高さ 1.5〜2m、横向き。手持ちは不可。
