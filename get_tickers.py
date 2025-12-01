import pandas as pd
import requests
import io

def get_jreit_tickers():
    url = "https://ja.wikipedia.org/wiki/J-REIT"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        # 取得したHTMLからすべての表を抜き出す
        dfs = pd.read_html(io.StringIO(response.text))
        print(f"🔍 ページ内に {len(dfs)} 個のテーブルが見つかりました。解析します...")

        target_df = None
        
        # 全テーブルをループして、「8951」(日本ビルファンド) が含まれている表を探す
        for i, df in enumerate(dfs):
            # データフレーム全体を文字列にして検索
            df_str = df.astype(str)
            if df_str.apply(lambda x: x.str.contains('8951', na=False)).any().any():
                print(f"🎯 目的のテーブルを発見しました！ (Table #{i})")
                target_df = df
                break
        
        if target_df is not None:
            # どの列がコードで、どの列が名前か特定する
            # 通常、数字4桁の列がコード、"投資法人"を含む列が名前
            code_col = None
            name_col = None
            
            for col in target_df.columns:
                # 文字列型に変換してチェック
                col_data = target_df[col].astype(str)
                
                # 最初の行の値で判断（8951など）
                first_val = col_data.iloc[0] if len(col_data) > 0 else ""
                
                # 数字4桁を含んでいればコード列とみなす
                if "8951" in first_val or (first_val.isdigit() and len(first_val) == 4):
                    code_col = col
                # "投資法人"という文字が含まれていれば名前列とみなす
                elif "投資法人" in first_val or "投資法人" in str(col):
                    name_col = col
            
            if code_col is not None:
                print(f"📝 データ抽出中... (コード列: {code_col}, 名前列: {name_col})")
                
                # 必要な列だけ抜き出す
                # 名前列が見つからない場合は、コード列の次の列を名前と仮定する
                if name_col is None:
                    col_idx = target_df.columns.get_loc(code_col)
                    name_col = target_df.columns[col_idx + 1]

                result_df = pd.DataFrame()
                result_df["Code"] = target_df[code_col]
                result_df["Name"] = target_df[name_col]
                
                # クリーニング（数字以外の文字が入っている場合への対処など）
                # 文字列型にしてから .T をつける
                result_df["Yahoo_Code"] = result_df["Code"].astype(str).str.replace(r'\D', '', regex=True) + ".T"
                
                # ゴミデータ（コードが空のものなど）を除去
                result_df = result_df[result_df["Yahoo_Code"] != ".T"]
                
                # CSV保存
                result_df.to_csv("jreit_list.csv", index=False, encoding="utf-8-sig")
                print("✅ 成功！ 'jreit_list.csv' を作成しました。")
                print("--- 取得データ例 (最初の3件) ---")
                print(result_df.head(3))
            else:
                print("❌ テーブルは見つかりましたが、コード列の特定に失敗しました。")
                print(target_df.head()) # デバッグ用に表示
        else:
            print("❌ J-REITのリストが含まれるテーブルが見つかりませんでした。")
            
    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")

if __name__ == "__main__":
    get_jreit_tickers()