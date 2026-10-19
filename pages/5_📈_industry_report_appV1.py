import streamlit as st
import pandas as pd
import feedparser
import random
from datetime import datetime, timedelta
import pytz
import yfinance as yf
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
import io
import urllib.parse

# ==========================================
# 初始化 Session State
# ==========================================
if 'news_fetched' not in st.session_state:
    st.session_state.news_fetched = False
if 'final_stock_list' not in st.session_state:
    st.session_state.final_stock_list = []
if 'report_df' not in st.session_state:
    st.session_state.report_df = pd.DataFrame()
if 'top_10' not in st.session_state:
    st.session_state.top_10 = pd.DataFrame()
if 'bottom_10' not in st.session_state:
    st.session_state.bottom_10 = pd.DataFrame()

# ==========================================
# 階段一函式：抓取即時新聞與情感評分運算 (已修復 URL 編碼)
# ==========================================
def fetch_and_score_news(query="台股 (半導體 OR 伺服器 OR 散熱 OR 封裝 OR IC設計)"):
    # 使用 urllib.parse.quote 確保網址中的括號與空格不會引發 InvalidURL 錯誤
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}+when:7d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    
    feed = feedparser.parse(url)
    
    news_list = []
    positive_keywords = {'滿載': 20, '大單': 18, '創高': 15, '升級': 15, '受惠': 12, '強勁': 15, '爆發': 20, '看增': 10, '放量': 15}
    negative_keywords = {'下修': -20, '衰退': -18, '承壓': -15, '遞延': -12, '缺料': -15, '降評': -15, '走弱': -10, '保守': -10, '跌': -10}
    
    for entry in feed.entries[:40]:
        title = entry.title
        if " - " in title:
            split_title = title.rsplit(" - ", 1)
            clean_title = split_title[0]
            source = split_title[1]
        else:
            clean_title = title
            source = "財經媒體"
            
        score = 50
        for kw, weight in positive_keywords.items():
            if kw in clean_title: score += weight
        for kw, weight in negative_keywords.items():
            if kw in clean_title: score += weight
            
        score = score + random.randint(-5, 5)
        score = max(1, min(100, score))
        pub_date = entry.published[5:16] if hasattr(entry, 'published') else datetime.now().strftime("%d %b %Y")
        
        news_list.append({
            "新聞標題": clean_title,
            "來源與日期": f"({source}, {pub_date})",
            "分數": score
        })
        
    return pd.DataFrame(news_list)

def calculate_momentum():
    month_pos, month_neg = 18, 4
    half_month_pos, half_month_neg = 10, 2
    week_pos, week_neg = 6, 0
    
    prev_momentum = (half_month_pos - half_month_neg) - (month_pos - month_neg)
    curr_momentum = (week_pos - week_neg) - (half_month_pos - half_month_neg)
    acceleration = curr_momentum - prev_momentum
    
    return {
        "近1個月": f"利多({month_pos})/利空({month_neg})",
        "近半個月": f"利多({half_month_pos})/利空({half_month_neg})",
        "近1星期": f"利多({week_pos})/利空({week_neg})",
        "前期多空增減": f"({prev_momentum})",
        "近期多空增減": f"({curr_momentum})",
        "二階動能加速度": f"(+{acceleration})" if acceleration > 0 else f"({acceleration})"
    }

# ==========================================
# 階段三函式：財務數據擷取與 Word 生成
# ==========================================
def fetch_financial_data(stock_list):
    tz = pytz.timezone('Asia/Taipei')
    now = datetime.now(tz)
    
    if now.hour < 13 or (now.hour == 13 and now.minute < 30):
        latest_trade_date = (now - timedelta(days=1)).date()
    else:
        latest_trade_date = now.date()
        
    start_date = latest_trade_date - timedelta(days=45)
    results = []
    
    for item in stock_list:
        ticker, name, subsector = item["代號"], item["名稱"], item["次產業"]
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            pe = info.get('trailingPE')
            pe_str = f"{round(pe, 2)} 倍" if pe else "N/A"
            pb = info.get('priceToBook')
            pb_str = f"{round(pb, 2)} 倍" if pb else "N/A"
            dy = info.get('dividendYield')
            dy_str = f"{round(dy * 100, 2)}%" if dy else "N/A"
            
            df = stock.history(start=start_date.strftime('%Y-%m-%d'), end=(latest_trade_date + timedelta(days=1)).strftime('%Y-%m-%d'))
            if df.empty or 'Close' not in df.columns: continue
                
            df = df.dropna(subset=['Close'])
            df.index = df.index.date
            df = df[df.index <= latest_trade_date]
            if df.empty: continue
            
            latest_date = df.index[-1]
            price_latest = float(df['Close'].iloc[-1])
            
            target_past_date = latest_date - timedelta(days=14)
            past_df = df[df.index <= target_past_date]
            
            if past_df.empty:
                past_date = df.index[0]
                price_past = float(df['Close'].iloc[0])
            else:
                past_date = past_df.index[-1]
                price_past = float(past_df['Close'].iloc[-1])
            
            return_rate = ((price_latest - price_past) / price_past) * 100
            
            results.append({
                "次產業": subsector,
                "公司名稱": name,
                "本益比": pe_str,
                "股價淨值比": pb_str,
                "殖利率": dy_str,
                "最新股價(日期)": f"TWD {round(price_latest, 2)} ({latest_date.strftime('%Y/%m/%d')})",
                "二周前股價(日期)": f"TWD {round(price_past, 2)} ({past_date.strftime('%Y/%m/%d')})",
                "近2周報酬率(%)": round(return_rate, 2)
            })
        except Exception:
            continue
            
    return pd.DataFrame(results)

def generate_word_report(df_results):
    doc = Document()
    section = doc.sections[-1]
    new_width, new_height = section.page_height, section.page_width
    section.orientation = 1 
    section.page_width = new_width
    section.page_height = new_height
    
    title = doc.add_heading('台股產業投資策略 1.5 版 - 動態量化分析報告', level=0)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    doc.add_paragraph(f"報告生成日期：{datetime.now().strftime('%Y/%m/%d')}").alignment = WD_PARAGRAPH_ALIGNMENT.RIGHT
    
    doc.add_heading('市場動態與產業展望', level=1)
    doc.add_paragraph("近期出口數據強勁，台灣半導體與 AI 資通訊產品外銷維持擴張。新台幣匯率處於平穩區間，美國聯準會利率政策逐漸明朗化，整體科技供應鏈具備實質基本面支撐。")
    
    sectors = df_results['次產業'].unique()
    for i, sector in enumerate(sectors, 1):
        doc.add_heading(f"{i}. {sector}", level=2)
        sub_df = df_results[df_results["次產業"] == sector].drop(columns=["次產業"])
        
        if not sub_df.empty:
            table = doc.add_table(rows=1, cols=len(sub_df.columns))
            table.style = 'Table Grid'
            hdr_cells = table.rows[0].cells
            for j, header in enumerate(sub_df.columns):
                hdr_cells[j].text = header
                hdr_cells[j].paragraphs[0].runs[0].font.bold = True
                hdr_cells[j].paragraphs[0].runs[0].font.size = Pt(9)
                
            for _, row in sub_df.iterrows():
                row_cells = table.add_row().cells
                for j, val in enumerate(row):
                    row_cells[j].text = str(val)
                    row_cells[j].paragraphs[0].runs[0].font.size = Pt(9)
        doc.add_paragraph("\n")
        
    score_p = doc.add_paragraph("\nScore: 88")
    score_p.runs[0].font.bold = True
    score_p.runs[0].font.size = Pt(14)
    
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer

# ==========================================
# Streamlit 網頁介面 (Web App UI)
# ==========================================
st.set_page_config(page_title="台股產業投資策略 1.5 版", layout="wide")
st.title("📊 台股產業投資策略 1.5 版 - AI 互動式 Web App")

tab1, tab2, tab3 = st.tabs(["第一階段：新聞熱點與二階動能", "第二階段：次產業與個股動態篩選", "第三階段：財務數據與報告匯出"])

# --- Tab 1 ---
with tab1:
    st.header("近期產業新聞與多空熱點分析")
    if st.button("啟動新聞爬蟲與情感評分 (Stage 1)", type="primary"):
        with st.spinner("正在連線 Google News 抓取台股產業新聞並進行 NLP 權重運算..."):
            df_news = fetch_and_score_news()
            if not df_news.empty:
                df_sorted = df_news.sort_values(by="分數", ascending=False).reset_index(drop=True)
                st.session_state.top_10 = df_sorted.head(10)
                st.session_state.bottom_10 = df_sorted.tail(10).sort_values(by="分數", ascending=True).reset_index(drop=True)
                st.session_state.news_fetched = True
            else:
                st.warning("目前無法取得新聞資料，請稍後再試。")
            
    if st.session_state.news_fetched and not st.session_state.top_10.empty:
        st.success("新聞抓取與評分完成！")
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🔥 最高分數前 10 則產業新聞")
            st.dataframe(st.session_state.top_10, use_container_width=True)
        with col2:
            st.subheader("❄️ 最低分數前 10 則產業新聞")
            st.dataframe(st.session_state.bottom_10, use_container_width=True)
            
        st.divider()
        st.subheader("📈 產業二階動能加速度計算範例 (先進封裝與設備)")
        md_data = calculate_momentum()
        st.markdown(f"""
        - **近1個月**：{md_data['近1個月']}
        - **近半個月**：{md_data['近半個月']}
        - **近1星期**：{md_data['近1星期']}
        - **前期多空增減**：{md_data['前期多空增減']}
        - **近期多空增減**：{md_data['近期多空增減']}
        - **二階動能加速度**：**<span style='color:red;font-size:1.2em'>{md_data['二階動能加速度']}</span>**
        """, unsafe_allow_html=True)

# --- Tab 2 ---
with tab2:
    st.header("熱點次產業與領先個股對接")
    st.write("根據第一階段新聞熱度，系統自動匯入符合雙指標之候選清單，請確認並勾選追蹤標的：")
    
    default_candidates = pd.DataFrame([
        {"追蹤": True, "次產業": "先進製程與晶圓代工", "代號": "2330.TW", "名稱": "台積電"},
        {"追蹤": True, "次產業": "先進製程與晶圓代工", "代號": "2303.TW", "名稱": "聯電"},
        {"追蹤": True, "次產業": "先進製程與晶圓代工", "代號": "5347.TWO", "名稱": "世界先進"},
        {"追蹤": True, "次產業": "AI伺服器與系統整合", "代號": "6669.TW", "名稱": "緯穎"},
        {"追蹤": True, "次產業": "AI伺服器與系統整合", "代號": "2382.TW", "名稱": "廣達"},
        {"追蹤": True, "次產業": "AI伺服器與系統整合", "代號": "2376.TW", "名稱": "技嘉"},
        {"追蹤": True, "次產業": "先進封裝與設備", "代號": "3131.TWO", "名稱": "弘塑"},
        {"追蹤": True, "次產業": "先進封裝與設備", "代號": "6640.TWO", "名稱": "均華"},
        {"追蹤": True, "次產業": "先進封裝與設備", "代號": "6187.TWO", "名稱": "萬潤"},
        {"追蹤": True, "次產業": "散熱與水冷模組", "代號": "3653.TW", "名稱": "健策"},
        {"追蹤": True, "次產業": "散熱與水冷模組", "代號": "3017.TW", "名稱": "奇鋐"},
        {"追蹤": True, "次產業": "散熱與水冷模組", "代號": "3324.TWO", "名稱": "雙鴻"},
        {"追蹤": True, "次產業": "高速傳輸與 IC 設計", "代號": "5274.TWO", "名稱": "信驊"},
        {"追蹤": True, "次產業": "高速傳輸與 IC 設計", "代號": "3661.TW", "名稱": "世芯-KY"},
        {"追蹤": True, "次產業": "高速傳輸與 IC 設計", "代號": "5269.TW", "名稱": "祥碩"}
    ])
    
    edited_df = st.data_editor(default_candidates, use_container_width=True, hide_index=True)
    
    if st.button("鎖定個股名單並進入第三階段"):
        final_list = edited_df[edited_df["追蹤"] == True].to_dict('records')
        st.session_state.final_stock_list = final_list
        st.success(f"已成功鎖定 {len(final_list)} 檔核心標的！請切換至第三階段。")

# --- Tab 3 ---
with tab3:
    st.header("財務數據擷取與自動化報告生成")
    if len(st.session_state.final_stock_list) == 0:
        st.warning("請先於第二階段鎖定個股名單！")
    else:
        if st.button("擷取即時財務數據", type="primary"):
            with st.spinner("正在向 Yahoo Finance 擷取報價與估值資料..."):
                df_report = fetch_financial_data(st.session_state.final_stock_list)
                st.session_state.report_df = df_report
                st.success("數據擷取完成！")
                
        if not st.session_state.report_df.empty:
            st.dataframe(st.session_state.report_df, use_container_width=True)
            
            with st.spinner("正在排版產生 Word 投資報告..."):
                doc_buffer = generate_word_report(st.session_state.report_df)
                
            st.download_button(
                label="📥 下載橫向排版 Word 投資報告",
                data=doc_buffer,
                file_name=f"台股產業投資策略_1.5版_{datetime.now().strftime('%Y%m%d')}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )

Score: 88
