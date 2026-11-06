# テニス切り出しアプリ（iOS / Android）

公開中のページ `cut/` を [Capacitor](https://capacitorjs.com/) でアプリにしたもの。中身はページと同じで、
`npm run build` が `cut/` を `www/` にコピーし、アプリ用の `native.js`（端末に保存して共有画面を開く）を足す。

- Android テスト版（apk）を作る: `npm ci && npm run android && (cd android && ./gradlew assembleDebug)`
  （JDK 21・Android SDK 36）→ `android/app/build/outputs/apk/debug/app-debug.apk`
- iOS: Apple Developer Program に登録後、Mac で `npm run ios` → Xcode で署名して TestFlight へ。
- ストア公開: Google Play Console（登録 25 ドル）と Apple Developer Program（年 99 ドル）が必要。

撮り方のおすすめ: コートの後ろ（フェンス・三脚）に固定、高さ 1.5〜2m、横向き。手持ちは不可。
