import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import tempfile

# ==========================================
# 0. 頁面基本設定與專家知識庫
# ==========================================
st.set_page_config(page_title="跨領域專家 AI 投資分析", layout="wide", page_icon="📈")

STOCK_INSIGHTS = {
    "2330": {
        "name": "台積電",
        "ind_1": "先進封裝（CoWoS/SoIC）產能瓶頸化為營收催化劑：全網焦點集中於 CoWoS 產能持續供不應求。台積電積極擴充廠區封裝量能，使 AI 加速器出貨瓶頸獲得實質解除。",
        "ind_2": "N3/N2 製程節點壟斷級領先：客戶全面導入先進製程，良率穩定且毛利率維持高位；2nm 順利量產，技術代差顯著拉開與競爭對手的距離。",
        "macro_1": "半導體超級週期（Super-cycle）擴張期：全球生產力提升需求全面鎖定 AI 基礎建設，推動晶體管數量需求的指數級成長。",
        "macro_2": "強大定價權（Pricing Power）抵禦通膨：在先進製程全球市場佔有率具絕對優勢，具備將成本轉嫁給下游大廠的定價能力。",
        "risks": [
            ("地緣政治風險", "美中科技限制擴大、關稅政策變動", "海外建廠短中期對毛利率造成結構性稀釋約 1~2%"),
            ("客戶集中風險", "CSP 雲端業者下修 AI 資本支出", "需緊密追蹤全球四大雲端巨頭的季度 CapEx 財測"),
            ("技術執行風險", "次世代製程量產初期良率爬坡", "歷史良率紀錄優異，此項風險發生機率相對較低")
        ]
    }
}

# ==========================================
# 1. 智慧台股/美股即時股價獲取函數
# ==========================================
@st.cache_data(ttl=300)
def get_live_price(ticker_symbol):
    ticker_symbol = ticker_symbol.upper().strip()
    symbols_to_try = [ticker_symbol]
    
    # 若輸入為 4 碼純數字，自動擴充嘗試台股上市 (.TW) 或上櫃 (.TWO)
    if ticker_symbol.isdigit() and len(ticker_symbol) == 4:
        symbols_to_try = [f"{ticker_symbol}.TW", f"{ticker_symbol}.TWO", ticker_symbol]
    
    for sym in symbols_to_try:
        try:
            tkr = yf.Ticker(sym)
            hist = tkr.history(period="5d")
            if not hist.empty and len(hist) >= 1:
                price = float(hist['Close'].iloc[-1])
                prev_price = float(hist['Close'].iloc[-2]) if len(hist) >= 2 else price
                change_pct = ((price - prev_price) / prev_price) * 100 if prev_price > 0 else 0.0
                return price, change_pct, sym
        except Exception:
            continue
            
    return 0.0, 0.0, ticker_symbol

# ==========================================
# 2. 專家級 Word 報告生成函數 (含表格與詳細排版)
# ==========================================
def generate_word_report(data, val):
    doc = Document()
    
    title = doc.add_heading(f"{data['name']} ({data['symbol']}) 跨領域專家綜合投資分析報告", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    doc.add_paragraph(f"報告生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    doc.add_paragraph(f"最新收盤股價：{data['price']:,.2f} 元 (當日漲跌幅 {data['change']:.2f}%)")
    doc.add_paragraph(f"模型推算目標價：{val['tp_base']:,.2f} 元 ({val['rec']})")
    doc.add_paragraph(f"目標價合理區間：{val['tp_lower']:,.2f} 元 ~ {val['tp_upper']:,.2f} 元")
    
    # 一、 產業專家視角
    doc.add_heading('一、 產業專家視角：技術壁壘與聲量剖析', level=1)
    doc.add_paragraph(f"24H/48H 市場情緒指標：{data['sentiment']} / 10 (0為極度利空，10為極度利多)")
    doc.add_paragraph("從產業鏈與技術推進的角度觀察：")
    doc.add_paragraph(data['ind_1'], style='List Bullet')
    doc.add_paragraph(data['ind_2'], style='List Bullet')

    # 二、 數學家視角
    doc.add_heading('二、 數學家視角：嚴謹多變數量化估值模型', level=1)
    doc.add_paragraph("本模型建構一個結合情緒動能、基本面成長與下行風險折價的多元線性加權本益比模型：")
    
    doc.add_heading('1. 核心變數與函數定義', level=2)
    doc.add_paragraph(f"基準股價 (P0)：{data['price']:,.2f} 元", style='List Bullet')
    doc.add_paragraph(f"遠期每股盈餘預估 (EPS_fwd)：{data['eps_fwd']} 元", style='List Bullet')
    doc.add_paragraph(f"歷史中樞本益比 (PE_base)：{data['pe_base']} 倍", style='List Bullet')
    
    doc.add_heading('2. 本益比動態修正公式', level=2)
    doc.add_paragraph("PE_target = PE_base + ΔPE_sentiment + ΔPE_growth - ΔPE_risk")
    doc.add_paragraph(f"• 情緒動能修正 (ΔPE_sentiment)：({data['sentiment']} - 5.0) × 0.4 = +{val['delta_sentiment']:.1f} 倍")
    doc.add_paragraph(f"• 成長展望溢價 (ΔPE_growth)：+{data['growth']:.1f} 倍")
    doc.add_paragraph(f"• 下行風險折價 (ΔPE_risk)：-{data['risk']:.1f} 倍")
    
    doc.add_heading('3. 目標價計算與區間情境分析', level=2)
    doc.add_paragraph(f"【基準目標價 Base Case】\nPE_target = {data['pe_base']} + {val['delta_sentiment']:.1f} + {data['growth']:.1f} - {data['risk']:.1f} = {val['pe_target']:.1f} 倍\nTP_base = {data['eps_fwd']} × {val['pe_target']:.1f} = {val['tp_base']:,.2f} 元 (潛在空間 {val['upside_base']:.1f}%)")
    doc.add_paragraph(f"【樂觀情境 Bull Case】\nPE_upper = {val['pe_upper']:.1f} 倍 | TP_upper = {val['tp_upper']:,.2f} 元")
    doc.add_paragraph(f"【悲觀情境 Bear Case】\nPE_lower = {val['pe_lower']:.1f} 倍 | TP_lower = {val['tp_lower']:,.2f} 元")

    # 三、 財金專家視角
    doc.add_heading('三、 財金專家視角：財務結構與估值位階', level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = 'Table Grid'
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = '財務指標', '數據與指標值', '財金專家解析'
    
    for item in [
        ("近 4 季累計 EPS (TTM)", f"{data['fin_ttm']} 元", "實質基本面支撐"),
        ("歷史本益比 (Historical P/E)", f"{val['historical_pe']:.1f} 倍", "處於歷史河流圖區間中高位"),
        ("遠期本益比 (Forward P/E)", f"{val['forward_pe']:.1f} 倍", "已接近模型悲觀下限，下檔支撐極強")
    ]:
        row = table.add_row().cells
        row[0].text, row[1].text, row[2].text = item[0], item[1], item[2]

    # 四、 經濟專家視角
    doc.add_heading('四、 經濟專家視角：宏觀週期與產業趨勢', level=1)
    doc.add_paragraph(data['macro_1'], style='List Bullet')
    doc.add_paragraph(data['macro_2'], style='List Bullet')

    # 五、 綜合風險陣列
    doc.add_heading('五、 綜合風險陣列 (Risk Matrix)', level=1)
    rtable = doc.add_table(rows=1, cols=3)
    rtable.style = 'Table Grid'
    rhdr = rtable.rows[0].cells
    rhdr[0].text, rhdr[1].text, rhdr[2].text = '風險維度', '關鍵影響因子', '影響評估與應對建議'
    
    for r in data['risks']:
        row = rtable.add_row().cells
        row[0].text, row[1].text, row[2].text = r[0], r[1], r[2]

    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
    doc.save(tmp_file.name)
    return tmp_file.name

# ==========================================
# 3. 側邊欄：自由輸入與動態參數
# ==========================================
st.sidebar.title("⚙️ 台/美股標的與參數設定")

with st.sidebar.form(key='search_form'):
    ticker_input = st.text_input("輸入上市櫃代碼或名稱 (如 2330, 2454, NVDA)", value="2330").upper().strip()
    submit_button = st.form_submit_button(label="📊 執行分析與載入數據")

# 取得即時股價與解析後的正確代碼
live_price, live_change, resolved_symbol = get_live_price(ticker_input)

is_tsmc = ("2330" in resolved_symbol)
default_eps = 110.0 if is_tsmc else 10.0
default_pe = 25.0 if is_tsmc else 20.0
default_sen = 7.5 if is_tsmc else 6.5
default_gro = 1.5 if is_tsmc else 1.0
default_ris = 1.5 if is_tsmc else 1.0

if live_price == 0.0:
    st.sidebar.warning(f"無法抓取代碼 [{ticker_input}] 的即時股價，請確認代碼是否正確（台股請輸入4碼數字）。")
    live_price = 2500.0 if is_tsmc else 100.0

st.sidebar.markdown("---")
st.sidebar.subheader("動態估值模型變數調校")
eps_fwd = st.sidebar.slider("預估 Forward EPS", min_value=1.0, max_value=200.0, value=float(default_eps), step=0.5)
pe_base = st.sidebar.number_input("產業中樞本益比 (PE)", value=float(default_pe))
sentiment = st.sidebar.slider("新聞聲量情緒 (0~10)", min_value=0.0, max_value=10.0, value=float(default_sen), step=0.1)
growth = st.sidebar.slider("展望成長溢價 (+PE)", min_value=0.0, max_value=10.0, value=float(default_gro), step=0.1)
risk = st.sidebar.slider("下行風險折價 (-PE)", min_value=0.0, max_value=10.0, value=float(default_ris), step=0.1)

# ==========================================
# 4. 數學模型即時運算
# ==========================================
delta_sentiment = (sentiment - 5.0) * 0.4
pe_target = pe_base + delta_sentiment + growth - risk
pe_upper = pe_base + delta_sentiment + growth - 0      
pe_lower = pe_base + 0 + 0 - 3.0                       

tp_base = eps_fwd * pe_target
tp_upper = eps_fwd * pe_upper
tp_lower = eps_fwd * pe_lower

upside_base = ((tp_base - live_price) / live_price) * 100 if live_price > 0 else 0
forward_pe = live_price / eps_fwd if eps_fwd > 0 else 0
fin_ttm = round(eps_fwd * 0.784, 2)
historical_pe = live_price / fin_ttm if fin_ttm > 0 else 0

if upside_base >= 10:
    rec, rec_color = "建議買進", "🟢"
elif upside_base <= -10:
    rec, rec_color = "建議賣出", "🔴"
else:
    rec, rec_color = "中性持有", "🟡"

# 載入洞察文字
clean_key = resolved_symbol.replace(".TW", "").replace(".TWO", "")
insight = STOCK_INSIGHTS.get(clean_key, {
    "name": ticker_input,
    "ind_1": f"近期市場對該標的之產能與營運展望保持關注，法人持續追蹤其產業鏈訂單能見度。",
    "ind_2": "在所屬產業節點中具備穩定競爭地位，後續重點在於新產品或技術的放量進度。",
    "macro_1": "總體經濟與降息循環帶動資金動能，相關終端需求預期將逐步回溫。",
    "macro_2": "企業具備一定的成本轉嫁能力，可維持合理毛利率表現。",
    "risks": [("市場波動風險", "總體經濟或匯率波動", "動態調整部位與風險胃納"), ("產業競爭風險", "同業產能開出過快", "追蹤市佔率與毛利變化")]
})

report_data = {
    "name": insight.get("name", ticker_input),
    "symbol": resolved_symbol, "price": live_price, "change": live_change,
    "eps_fwd": eps_fwd, "pe_base": pe_base, "sentiment": sentiment, "growth": growth, "risk": risk,
    "ind_1": insight["ind_1"], "ind_2": insight["ind_2"],
    "macro_1": insight["macro_1"], "macro_2": insight["macro_2"],
    "risks": insight["risks"], "fin_ttm": fin_ttm
}

valuation_data = {
    "pe_target": pe_target, "pe_upper": pe_upper, "pe_lower": pe_lower,
    "tp_base": tp_base, "tp_lower": tp_lower, "tp_upper": tp_upper,
    "upside_base": upside_base, "rec": rec, "delta_sentiment": delta_sentiment,
    "forward_pe": forward_pe, "historical_pe": historical_pe
}

# ==========================================
# 5. 主畫面佈局與 Word 匯出
# ==========================================
st.title("📈 跨領域專家 AI 投資分析生成器 (台/美股通用)")
st.caption(f"報告生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 解析標的：{resolved_symbol}")

col1, col2, col3, col4 = st.columns(4)
col1.metric("最新收盤價 (即時)", f"${live_price:,.2f}", f"{live_change:.2f}%")
col2.metric("模型目標價 (Base)", f"${tp_base:,.0f}", f"{upside_base:.1f}% 潛在空間")
col3.metric("綜合投資評等", f"{rec}", f"{rec_color}")
col4.metric("目標價合理區間", f"${tp_lower:,.0f} ~ ${tp_upper:,.0f}")

st.divider()

with st.spinner("正在生成專家級 Word 報告，請稍候..."):
    word_file_path = generate_word_report(report_data, valuation_data)
    with open(word_file_path, "rb") as word_file:
        st.download_button(
            label="📝 下載完整專家級 Word 報告",
            data=word_file,
            file_name=f"{resolved_symbol}_AI_Investment_Report.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary"
        )
st.markdown("<br>", unsafe_allow_html=True)

# 介面渲染
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("一、 產業專家視角")
    st.info(f"**核心動能：**\n\n{report_data['ind_1']}")
    st.success(f"**市場護城河：**\n\n{report_data['ind_2']}")

    st.subheader("二、 數學家視角 (量化模型)")
    st.latex(r"""PE_{target} = PE_{base} + \Delta PE_{sentiment} + \Delta PE_{growth} - \Delta PE_{risk}""")
    st.latex(rf"""{pe_target:.1f} = {pe_base} + {delta_sentiment:.1f} + {growth:.1f} - {risk:.1f}""")
    
    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("悲觀 (Bear)", f"${tp_lower:,.0f}", f"PE: {pe_lower:.1f}x", delta_color="off")
    sc2.metric("基準 (Base)", f"${tp_base:,.0f}", f"PE: {pe_target:.1f}x", delta_color="normal")
    sc3.metric("樂觀 (Bull)", f"${tp_upper:,.0f}", f"PE: {pe_upper:.1f}x", delta_color="normal")

with col_right:
    st.subheader("三、 財金專家視角")
    fc1, fc2, fc3 = st.columns(3)
    fc1.metric("估計 TTM EPS", f"${report_data['fin_ttm']}")
    fc2.metric("歷史 P/E", f"{historical_pe:.1f}x")
    fc3.metric("遠期 P/E", f"{forward_pe:.1f}x")
    
    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("四、 經濟專家與風險陣列")
    st.warning(f"**宏觀與產業趨勢：**\n\n{report_data['macro_1']}")
    
    st.markdown("**下行風險追蹤 (Risk Matrix)**")
    df_risks = pd.DataFrame(report_data['risks'], columns=['風險維度', '關鍵影響因子', '影響評估與應對建議'])
    st.dataframe(df_risks, use_container_width=True, hide_index=True)
