import pandas as pd
import yfinance as yf
import os
import time

# 保存先フォルダ
DATA_DIR = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)

def fetch_all_data():
    # 1. 銘柄リストを読み込む
    try:
        df = pd.read_csv("jreit_list.csv")
    except FileNotFoundError:
        print("❌ 'jreit_list.csv' が見つかりません。さっき作った場所にいますか？")
        return

    # 2. 上場廃止（名前に"（廃）"が入っているもの）を除外
    # ※ Wikipediaの表記に依存する処理です
    df = df[~df['Name'].str.contains("（廃）")]
    
    total = len(df)
    print(f"🚀 全 {total} 銘柄のデータ取得を開始します...")

    success_count = 0
    error_count = 0

    for i, row in df.iterrows():
        code = row['Yahoo_Code'] # 8951.T など
        name = row['Name']
        save_path = os.path.join(DATA_DIR, f"{code}.csv")

        # 進捗表示 (例: [1/60] 8951.T 日本ビルファンド...)
        print(f"[{i+1}/{total}] {code} {name} ... ", end="", flush=True)

        # すでに今日ダウンロード済みならスキップする（時短用）
        # ※ もし再取得したいなら、このif文をコメントアウトしてください
        if os.path.exists(save_path):
            # ファイルの更新日時をチェックして、例えば24時間以内ならスキップとかもできるが
            # 今回は単純にファイルがあればスキップせず上書きする仕様にします（最新データが欲しいので）
            pass 

        try:
            # yfinanceでデータ取得 (過去5年分)
            ticker = yf.Ticker(code)
            hist = ticker.history(period="5y")

            if hist.empty:
                print("⚠️ データなし (Skip)")
                error_count += 1
                continue

            # 必要なデータだけ整理して保存
            # index(Date)が保存されるようにする
            hist.to_csv(save_path)
            
            print("✅ OK")
            success_count += 1
            
            # サーバーに負荷をかけないよう少し待つ
            time.sleep(1)

        except Exception as e:
            print(f"❌ Error: {e}")
            error_count += 1

    print("\n" + "="*40)
    print(f"🎉 完了！")
    print(f"成功: {success_count} 件")
    print(f"失敗/除外: {error_count} 件")
    print(f"保存場所: {DATA_DIR}")
    print("="*40)

if __name__ == "__main__":
    fetch_all_data()