import streamlit as st
import yfinance as yf
from google import genai
import pandas as pd

# 子頁面設定
st.set_page_config(
    page_title="台股量化估值與 AI 投資報告",
    page_icon="📈",
    layout="wide"
)

st.title("📈 台股量化估值與 AI 投資報告生成器")
st.caption("內建「投資報告專業 1.3 版」邏輯：自動判斷 P/E 與 P/B 河流圖切換，整合多空情緒熱點量化分析")

# 側邊欄設定
with st.sidebar:
    st.header("⚙️ 模組參數設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password", key="stock_report_api_key")
    selected_model = st.selectbox(
        "選擇 Gemini 模型",
        ["gemini-3.8-flash", "gemini-3.8-pro","gemini-1.5-flash", "gemini-1.5-pro"],
        key="stock_report_model"
    )
    st.markdown("---")
    st.info("💡 **1.3 版機制**：累計 EPS > 0 採用 **P/E 模型**；累計 EPS ≤ 0 自動切換為 **P/B 淨值比河流圖模型**。")

# 輸入控制區
col1, col2 = st.columns([1, 2])

with col1:
    stock_id = st.text_input("請輸入台股代號（例：2330, 6217, 6209）", value="2330").strip()
    macro_news = st.text_area(
        "總體經濟新聞/補充背景資訊（選填）",
        value="美國聯準會維持利率政策方向，台灣半導體與電子零組件出口維持溫和復甦，新台幣匯率平穩。",
        height=120
    )
    submit_btn = st.button("🚀 生成專業報告", type="primary", use_container_width=True)

# 數據抓取函式 (加入防錯機制、名稱抓取與時間紀錄)
@st.cache_data(ttl=300)
def fetch_stock_data(ticker_symbol: str):
    ticker_symbol = ticker_symbol.strip()
    
    # 依序嘗試 上市 (.TW) 與 上櫃 (.TWO)
    for suffix in [".TW", ".TWO"]:
        try:
            ticker = yf.Ticker(f"{ticker_symbol}{suffix}")
            hist = ticker.history(period="5d")
            
            if not hist.empty:
                latest_price = hist['Close'].iloc[-1]
                latest_date = hist.index[-1].strftime("%Y 年 %m 月 %d 日")
                
                # 嘗試取得公司名稱
                company_name = ""
                try:
                    info = ticker.info
                    company_name = info.get('longName', '') or info.get('shortName', '')
                except:
                    pass
                
                # 紀錄當下抓取時間 (設定為台北時區)
                current_time = pd.Timestamp.now(tz='Asia/Taipei').strftime("%Y-%m-%d %H:%M:%S")
                
                return {
                    "price": float(latest_price),
                    "date": latest_date,
                    "time": current_time,
                    "name": company_name,
                    "symbol": ticker_symbol
                }
        except Exception as e:
            continue
            
    return None

# 執行分析
if submit_btn:
    if not gemini_api_key:
        st.error("請先在左側邊欄輸入 Gemini API Key！")
    elif not stock_id:
        st.error("請輸入有效的台股代號！")
    else:
        with st.spinner("正在抓取市場即時數據..."):
            stock_info = fetch_stock_data(stock_id)
            
        if not stock_info:
            st.error(f"⚠️ 無法取得代號 {stock_id} 的市場數據。可能是 Yahoo Finance 暫時限制存取，或請確認代號是否正確。")
        else:
            price = stock_info["price"]
            price_date = stock_info["date"]
            fetch_time = stock_info["time"]
            company_name = stock_info["name"]
            
            # 判斷名稱是否抓取成功，若失敗則顯示交由 AI 識別
            display_name = company_name if company_name else "交由 AI 識別"
            
            # 在右側 UI 介面同時顯示代碼、名稱與時間
            with col2:
                st.subheader("📊 即時市場數據")
                st.markdown(f"**🎯 標的：** {display_name} ({stock_id})")
                st.metric("當前市場股價", f"{price:.2f} TWD", delta=f"報價日期: {price_date}")
                st.caption(f"🕒 資料更新時間：{fetch_time}")
                
            prompt_template = f"""你是一位擁有台股推薦經驗的金融專家。
以第三人稱陳述。不要提及你的專業資歷。

作為背景參考的總體經濟數據：
{macro_news}

根據近期的財務數據與新聞標題，請給出一個分數（1 到 100 分），以反映台灣股市中目標公司（股票代號 {stock_id}）在下個月的潛在投資價值。

所有金額請以新台幣（TWD）表示。不要推薦替代標的。不要使用『提供的』這個詞，改用『近期』或『最新』。不要直接對投資人喊話，也不要建議任何操作。

請嚴格依據以下結構進行撰寫：

以『投資報告：[請依據股票代碼 {stock_id} 填入對應的台灣中文公司名稱] ({stock_id}) 投資價值分析』為標題開頭。務必使用你的知識庫自動識別並寫出正確的中文企業名稱。

撰寫一份關於該公司狀況的簡短投資報告，內容須包含以下章節：

1. 近期新聞
2. 財務狀況與次產業成長率
3. 估值分析
4. 總體經濟展望
5. 短期市場情緒與多空熱點分析（權重量化版）
6. 量化估值分析（動態多場景推算）
* 【強制帶入數據】：
  - 當前市場股價標註格式必須嚴格為：當前市場股價：{price:.2f} TWD (報價日期：{price_date}，資料擷取時間：{fetch_time})。
* 【判定機制】：
  - 若「過去 4 季累計 EPS > 0」：採用【本益比 (P/E) 評價模型】。
  - 若「過去 4 季累計 EPS ≤ 0」：自動切換採用【股價淨值比 (P/B) 河流圖評價模型】。
* 【P/E 模型執行標準】：
  - 列出過去 4 季 EPS 與加總。
  - 算式：理論合理價格 = 合理 P/E * 過去 4 季累計 EPS。
  - 呈現 4 種 P/E 場景表格（悲觀/中性/樂觀/極度樂觀）。
* 【P/B 模型執行標準】：
  - 標明「近一季每股淨值 (BPS)」與當前股價。
  - 算式：理論合理價格 = 合理 P/B * 近一季每股淨值 (BPS)。
  - 參考 P/B 河流圖倍數區間（如 0.5倍、1.0倍、1.5倍、2.0倍、2.5倍、3.0倍等），建立多場景理論價格估值表格。
  - 說明當前股價落在河流圖的哪個 P/B 階梯與溢價/折價狀況。

最後，另起一行，輸出 Score: X。
"""

            st.markdown("---")
            st.subheader("📑 AI 生成投資分析報告")
            report_placeholder = st.empty()
            
            try:
                client = genai.Client(api_key=gemini_api_key)
                response = client.models.generate_content_stream(
                    model=selected_model,
                    contents=prompt_template
                )
                
                full_text = ""
                for chunk in response:
                    full_text += chunk.text
                    report_placeholder.markdown(full_text + "▌")
                    
                report_placeholder.markdown(full_text)
                
                st.download_button(
                    label="📥 下載報告 (Markdown 格式)",
                    data=full_text,
                    file_name=f"{stock_id}_投資報告專業1.3版.md",
                    mime="text/markdown"
                )
            except Exception as e:
                st.error(f"報告生成失敗：{str(e)}")
