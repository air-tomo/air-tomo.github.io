import pandas as pd
import matplotlib.pyplot as plt
import io
import os
import glob
from jinja2 import Environment, FileSystemLoader
import matplotlib.ticker as mticker

# --- 設定 ---
DATA_DIR = "data/raw"
OUTPUT_DIR = "output"
TEMPLATE_DIR = "templates"

# 日本語フォント設定（文字化け対策：英語表記にする逃げ手）
plt.rcParams['font.family'] = 'sans-serif'

# Jinja2の準備
env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
template = env.get_template("layout.html")

def calculate_yield(df):
    """
    株価データから「実績利回り」を計算する
    ロジック: 過去約1年(250営業日)の配当金合計 ÷ その日の株価
    """
    # 配当金(Dividends)列がない場合は0で埋める
    if 'Dividends' not in df.columns:
        df['Dividends'] = 0
    
    # 過去1年分(約250行)の配当金合計を計算
    df['Annual_Div'] = df['Dividends'].rolling(window=250, min_periods=1).sum()
    
    # 利回り(%) = 年間配当 / 株価 * 100
    df['Yield'] = (df['Annual_Div'] / df['Close']) * 100
    
    return df

def create_dual_axis_chart(df, title):
    """株価(左軸)と利回り(右軸)の2軸チャートを作る"""
    if df.empty:
        return ""
    
    fig, ax1 = plt.figure(figsize=(10, 6)), plt.gca()
    
    # 日付データをdatetime型に
    dates = pd.to_datetime(df['Date'])
    
    # --- 左軸：株価（青色） ---
    ax1.plot(dates, df['Close'], color="#007bff", label="株価", linewidth=1.5)
    ax1.set_ylabel("株価 (円)", color="#007bff", fontsize=12)
    ax1.tick_params(axis='y', labelcolor="#007bff")
    ax1.grid(True, linestyle='--', alpha=0.3)

    # --- 右軸：利回り（オレンジ色） ---
    ax2 = ax1.twinx()  # 右軸を作成
    ax2.plot(dates, df['Yield'], color="#ff9900", label="利回り(%)", linewidth=1.5, linestyle='-')
    ax2.set_ylabel("利回り (%)", color="#ff9900", fontsize=12)
    ax2.tick_params(axis='y', labelcolor="#ff9900")
    
    # タイトル
    plt.title(f"{title} - Price & Yield Chart", fontsize=14)
    
    # SVGとして出力
    buf = io.StringIO()
    plt.savefig(buf, format="svg", bbox_inches='tight')
    plt.close()
    
    return buf.getvalue()

def build():
    csv_files = glob.glob(os.path.join(DATA_DIR, "*.csv"))
    tickers = []
    
    print("🚀 利回りチャート生成モードで開始します...")

    for path in csv_files:
        filename = os.path.basename(path)
        code = filename.replace(".csv", "")
        
        # データ読み込み
        df = pd.read_csv(path)
        
        # 利回り計算
        df = calculate_yield(df)
        
        # 最新データの取得
        if not df.empty:
            current_price = df['Close'].iloc[-1]
            current_yield = df['Yield'].iloc[-1]
        else:
            current_price = 0
            current_yield = 0
            
        current_price_str = f"{int(current_price):,}円"
        current_yield_str = f"{current_yield:.2f}%"
        
        # 2軸チャート生成
        chart_svg = create_dual_axis_chart(df, code)
        
        # --- 💰 ここがマネーポイント（広告枠） ---
        affiliate_area = """
        <div style="background:#f9f9f9; padding:1.5rem; border-radius:8px; margin-top:2rem; text-align:center;">
            <h3>📊 この銘柄をお得に買うなら</h3>
            <p>J-REITの積立投資なら、手数料無料の<strong>SBI証券</strong>がおすすめです。</p>
            <a href="#" role="button" style="background-color:#d32f2f; border-color:#d32f2f;">
                SBI証券の公式サイトへ（アフィリエイトリンク予定地）
            </a>
        </div>
        """
        
        page_content = f"""
        <article>
            <hgroup>
                <h2>{code}</h2>
                <h3>現在値: {current_price_str} <span style="color:#ff9900; margin-left:1rem;">利回り: {current_yield_str}</span></h3>
            </hgroup>
            
            <div class="chart-container">
                {chart_svg}
            </div>
            
            <details>
                <summary>💡 チャートの見方</summary>
                <p><strong>青線：</strong>株価です。下がると買い時かもしれません。<br>
                <strong>オレンジ線：</strong>実績利回りです。これが高いとき（山になっているとき）がお得なタイミングです。</p>
            </details>
            
            {affiliate_area}
            
            <p style="margin-top:2rem;"><a href="https://finance.yahoo.co.jp/quote/{code}" target="_blank">Yahoo!ファイナンスで詳細を見る</a></p>
        </article>
        <p><a href="index.html">← 一覧に戻る</a></p>
        """
        
        html = template.render(title=f"{code} 利回りチャート", content=page_content)
        
        output_path = os.path.join(OUTPUT_DIR, f"{code}.html")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
            
        tickers.append({
            "code": code,
            "price": current_price_str,
            "yield": current_yield_str,
            "link": f"{code}.html"
        })
        print(f"📄 Updated: {code}.html (Yield: {current_yield_str})")

    # 一覧ページも更新
    index_list_html = '<div class="grid-list">'
    # 利回りが高い順に並び替え（これ大事！）
    tickers_sorted = sorted(tickers, key=lambda x: float(x['yield'].replace('%','')), reverse=True)
    
    for t in tickers_sorted:
        index_list_html += f"""
        <article>
            <header><strong>{t['code']}</strong></header>
            <p style="font-size:1.5rem; font-weight:bold; color:#ff9900;">{t['yield']}</p>
            <p style="color:#666;">{t['price']}</p>
            <a href="{t['link']}" role="button" class="outline">チャートを見る</a>
        </article>
        """
    index_list_html += "</div>"
    
    index_html = template.render(title="J-REIT 利回りランキング", content=index_list_html)
    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)
        
    print(f"\n🎉 完了！利回りランキング順になりました！")

if __name__ == "__main__":
    build()