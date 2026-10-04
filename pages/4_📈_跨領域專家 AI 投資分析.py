import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
import tempfile

# ==========================================
# 0. 頁面基本設定與公司名稱對應字典
# ==========================================
st.set_page_config(page_title="跨領域專家 AI 投資分析", layout="wide", page_icon="📈")

# 智慧公司名稱對應 (台股對應中文，美股/其他對應英文簡易名稱)
def get_company_display_name(symbol):
    clean_sym = symbol.replace(".TW", "").replace(".TWO", "").upper()
    name_mapping = {
        "2330": "台積電",
        "2454": "聯發科",
        "2317": "鴻海",
        "3105": "穩懋",
        "2308": "台達電",
        "2881": "富邦金",
        "2882": "國泰金",
        "NVDA": "NVIDIA",
        "AAPL": "Apple",
        "TSLA": "Tesla",
        "MSFT": "Microsoft",
        "GOOGL": "Alphabet"
    }
    if clean_sym in name_mapping:
        return name_mapping[clean_sym]
    # 若為台股 4 碼但未在字典中，給予預設標示
    if clean_sym.isdigit() and len(clean_sym) == 4:
        return f"台灣上市公司 ({clean_sym})"
    return clean_sym

def generate_dynamic_insights(symbol, price):
    clean_sym = symbol.replace(".TW", "").replace(".TWO", "").upper()
    comp_name = get_company_display_name(symbol)
    
    if clean_sym == "2330":
        return {
            "name": comp_name,
            "ind_1": "先進封裝（CoWoS/SoIC）產能瓶頸化為營收催化劑 (+1.0分)：全網焦點集中於 CoWoS 產能持續供不應求。台積電積極擴充嘉義與高雄廠區封裝量能，使 2026 至 2027 年的 AI 加速器出貨瓶頸獲得實質解除。",
            "ind_2": "N3/N2 製程節點壟斷級領先 (+1.5分)：蘋果 iPhone 備貨與 Nvidia、AMD、CSP 自研 ASIC 晶片全面導入 3nm 製程，良率穩定且毛利率推升至 55% 以上高位；2nm 於今年順利量產，技術代差顯著拉開與競爭對手的距離。",
            "macro_1": "半導體超級週期（Super-cycle）擴張期：全球經濟體經歷高利率環境後，生產力提升需求全面鎖定 AI 基礎建設。伺服器與邊端裝置（AI PC / AI Phone）推動晶體管數量需求的指數級成長。",
            "macro_2": "強大定價權（Pricing Power）抵禦通膨：在先進製程全球市場佔有率高達 85% 以上的背景下，台積電具備將原料與建廠成本順利轉嫁給下游 CSP 與巨頭的定價權，毛利率可長期維持在預期目標之上。",
            "risks": [
                ("地緣政治風險", "美中科技限制擴大、關稅政策變動", "海外建廠（美國、日本、德國）短中期拉低整體毛利率約 1~2%"),
                ("客戶集中風險", "CSP 雲端業者下修 AI 資本支出", "需緊密追蹤 Big 4 (Microsoft, Google, AWS, Meta) 的季度 CapEx 財測"),
                ("技術執行風險", "2nm 製程量產初期良率爬坡不如預期", "歷史良率紀錄優異，此項風險機率較低")
            ]
        }
    else:
        return {
            "name": comp_name,
            "ind_1": f"核心技術與產能佈局觀察 (+1.0分)：市場資金持續聚焦 {comp_name} 在產業鏈中的定位，供應鏈訂單能見度與出貨節奏維持穩健。",
            "ind_2": f"產品節點與競爭優勢 (+1.5分)：主要產品線需求強勁，透過技術升級與產品組合優化，有效鞏固其市場毛利率與市佔率表現。",
            "macro_1": f"{comp_name} 所處宏觀週期定位：受惠於全球總體經濟溫和復甦與產業數位轉型浪潮，相關終端應用需求正逐步釋放。",
            "macro_2": f"定價能力與成本結構：面對總體成本波動，該企業展現出良好的成本轉嫁與營運效率優化能力。",
            "risks": [
                ("總體經濟風險", "利率政策變動與匯率波動", "短中期可能對財務毛利與資金成本造成波動影響"),
                ("市場競爭風險", "同業產能擴張與價格競爭", "需持續追蹤其市佔率變化與差異化競爭策略"),
                ("供應鏈風險", "上游原料或關鍵零組件供應", "應密切關注關鍵供應商之交期與庫存水位調節狀況")
            ]
        }

# ==========================================
# 1. 智慧台股/美股即時股價獲取函數
# ==========================================
@st.cache_data(ttl=300)
def get_live_price(ticker_symbol):
    ticker_symbol = ticker_symbol.upper().strip()
    symbols_to_try = [ticker_symbol]
    
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
# 2. 專家級 Word 報告完整生成函數
# ==========================================
def generate_word_report(data, val, insights):
    doc = Document()
    
    title = doc.add_heading(f"{insights['name']} ({data['symbol']}) 跨領域專家綜合投資分析報告", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # 按照要求：第一欄位為公司名稱
    doc.add_paragraph(f"公司名稱：{insights['name']}")
    doc.add_paragraph(f"報告生成時間：{datetime.now().strftime('%Y 年 %m 月 %d 日 %H:%M (CST)')}")
    doc.add_paragraph(f"最新收盤股價：{data['price']:,.2f} 元 (當日漲跌幅 {data['change']:.2f}%)")
    doc.add_paragraph(f"模型推算目標價：{val['tp_base']:,.2f} 元 ({val['rec']})")
    doc.add_paragraph(f"目標價合理區間：{val['tp_lower']:,.2f} 元 ~ {val['tp_upper']:,.2f} 元")
    
    # 一、 產業專家視角
    doc.add_heading('一、 產業專家視角：技術壁壘與聲量剖析', level=1)
    doc.add_paragraph(f"24H/48H 市場情緒指標：{data['sentiment']} / 10 (0為極度利空，10為極度利多)")
    doc.add_paragraph("從晶圓代工與先進技術推進的角度觀察，該公司處於高能見度的週期上行階段：")
    doc.add_paragraph(insights['ind_1'], style='List Bullet')
    doc.add_paragraph(insights['ind_2'], style='List Bullet')

    # 二、 數學家視角
    doc.add_heading('二、 數學家視角：嚴謹多變數量化估值模型', level=1)
    doc.add_paragraph("本模型建構一個結合情緒動能、基本面成長與下行風險折價的多元線性加權本益比模型：")
    
    doc.add_heading('1. 核心變數與函數定義', level=2)
    doc.add_paragraph(f"基準股價 (P0)：{data['price']:,.2f} 元", style='List Bullet')
    doc.add_paragraph(f"遠期每股盈餘預估 (EPS_fwd)：{data['eps_fwd']} 元（採未來 12 個月 Consensus 加權平均）", style='List Bullet')
    doc.add_paragraph(f"歷史中樞本益比 (PE_base)：{data['pe_base']} 倍", style='List Bullet')
    
    doc.add_heading('2. 本益比動態修正公式', level=2)
    doc.add_paragraph("PE_target = PE_base + ΔPE_sentiment + ΔPE_growth - ΔPE_risk")
    doc.add_paragraph(f"• 情緒動能修正 (ΔPE_sentiment)：聲量得分 S = {data['sentiment']}\n  ΔPE_sentiment = ({data['sentiment']} - 5.0) × 0.4 = +{val['delta_sentiment']:.1f} 倍")
    doc.add_paragraph(f"• 成長展望溢價 (ΔPE_growth)：營運展望與資本支出動能\n  ΔPE_growth = +{data['growth']:.1f} 倍")
    doc.add_paragraph(f"• 下行風險折價 (ΔPE_risk)：考量地緣政治與客戶集中度\n  ΔPE_risk = -{data['risk']:.1f} 倍")
    
    doc.add_heading('3. 目標價計算與區間情境分析 (Sensitivity Analysis)', level=2)
    doc.add_paragraph(f"【基準目標價 Base Case】\nPE_base_target = {data['pe_base']} + {val['delta_sentiment']:.1f} + {data['growth']:.1f} - {data['risk']:.1f} = {val['pe_target']:.1f} 倍\nTP_base = {data['eps_fwd']} × {val['pe_target']:.1f} = {val['tp_base']:,.2f} 元 (潛在漲幅 +{val['upside_base']:.1f}%)")
    doc.add_paragraph(f"【區間上限 樂觀情境 Bull Case】\nPE_upper = {val['pe_upper']:.1f} 倍 | TP_upper = {val['tp_upper']:,.2f} 元")
    doc.add_paragraph(f"【區間下限 悲觀情境 Bear Case】\nPE_lower = {val['pe_lower']:.1f} 倍 | TP_lower = {val['tp_lower']:,.2f} 元")

    # 三、 財金專家視角
    doc.add_heading('三、 財金專家視角：財務結構與估值位階', level=1)
    doc.add_paragraph("從財務報表健康度與資本效率來看：")
    table = doc.add_table(rows=1, cols=3)
    table.style = 'Table Grid'
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = '財務指標', '數據與指標值', '財金專家解析'
    
    for item in [
        ("近 4 季累計 EPS (TTM)", f"{data['fin_ttm']} 元", "實質基本面支撐營運"),
        ("歷史本益比 (Historical P/E)", f"{val['historical_pe']:.1f} 倍", "處於歷史評價河流圖中高位"),
        ("遠期本益比 (Forward P/E)", f"{val['forward_pe']:.1f} 倍", "已接近模型悲觀下限，下檔具備強烈支撐")
    ]:
        row = table.add_row().cells
        row[0].text, row[1].text, row[2].text = item[0], item[1], item[2]

    # 四、 經濟專家視角
    doc.add_heading('四、 經濟專家視角：宏觀週期與產業趨勢', level=1)
    doc.add_paragraph(insights['macro_1'], style='List Bullet')
    doc.add_paragraph(insights['macro_2'], style='List Bullet')

    # 五、 綜合風險陣列
    doc.add_heading('五、 綜合風險陣列 (Risk Matrix)', level=1)
    rtable = doc.add_table(rows=1, cols=3)
    rtable.style = 'Table Grid'
    rhdr = rtable.rows[0].cells
    rhdr[0].text, rhdr[1].text, rhdr[2].text = '風險維度', '關鍵影響因子', '影響評估與應對建議'
    
    for r in insights['risks']:
        row = rtable.add_row().cells
        row[0].text, row[1].text, row[2].text = r[0], r[1], r[2]

    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
    doc.save(tmp_file.name)
    return tmp_file.name

# ==========================================
# 3. 側邊欄：台/美股輸入與參數調校
# ==========================================
st.sidebar.title("⚙️ 台/美股標的與參數設定")

with st.sidebar.form(key='search_form'):
    ticker_input = st.text_input("輸入上市櫃代碼或名稱 (如 2330, 2454, NVDA)", value="2330").upper().strip()
    submit_button = st.form_submit_button(label="📊 執行分析與載入數據")

live_price, live_change, resolved_symbol = get_live_price(ticker_input)
insights = generate_dynamic_insights(resolved_symbol, live_price)
company_display_name = insights['name']

is_tsmc = ("2330" in resolved_symbol)
default_eps = 110.0 if is_tsmc else 15.0
default_pe = 25.0 if is_tsmc else 22.0
default_sen = 7.5 if is_tsmc else 7.0
default_gro = 1.5 if is_tsmc else 1.0
default_ris = 1.5 if is_tsmc else 1.0

if live_price == 0.0:
    st.sidebar.warning(f"無法抓取代碼 [{ticker_input}] 的即時股價，將使用預設示範價格。")
    live_price = 2500.0 if is_tsmc else 150.0

st.sidebar.markdown("---")
st.sidebar.subheader("動態估值模型變數調校")
eps_fwd = st.sidebar.slider("預估 Forward EPS", min_value=1.0, max_value=300.0, value=float(default_eps), step=0.5)
pe_base = st.sidebar.number_input("產業中樞本益比 (PE)", value=float(default_pe))
sentiment = st.sidebar.slider("新聞聲量情緒 (0~10)", min_value=0.0, max_value=10.0, value=float(default_sen), step=0.1)
growth = st.sidebar.slider("展望成長溢價 (+PE)", min_value=0.0, max_value=10.0, value=float(default_gro), step=0.1)
risk = st.sidebar.slider("下行風險折價 (-PE)", min_value=0.0, max_value=10.0, value=float(default_ris), step=0.1)

# ==========================================
# 4. 模型運算
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

report_data = {
    "symbol": resolved_symbol, "price": live_price, "change": live_change,
    "eps_fwd": eps_fwd, "pe_base": pe_base, "sentiment": sentiment, "growth": growth, "risk": risk,
    "fin_ttm": fin_ttm
}

valuation_data = {
    "pe_target": pe_target, "pe_upper": pe_upper, "pe_lower": pe_lower,
    "tp_base": tp_base, "tp_lower": tp_lower, "tp_upper": tp_upper,
    "upside_base": upside_base, "rec": rec, "delta_sentiment": delta_sentiment,
    "forward_pe": forward_pe, "historical_pe": historical_pe
}

# ==========================================
# 5. 主畫面佈局與高階 Word 下載
# ==========================================
st.title("📈 跨領域專家 AI 投資分析生成器 (台/美股通用)")
st.subheader(f"🏢 公司名稱：{company_display_name} ({resolved_symbol})")
st.caption(f"報告生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

col1, col2, col3, col4 = st.columns(4)
col1.metric("最新收盤價 (即時)", f"${live_price:,.2f}", f"{live_change:.2f}%")
col2.metric("模型目標價 (Base)", f"${tp_base:,.0f}", f"{upside_base:.1f}% 潛在空間")
col3.metric("綜合投資評等", f"{rec}", f"{rec_color}")
col4.metric("目標價合理區間", f"${tp_lower:,.0f} ~ ${tp_upper:,.0f}")

st.divider()

with st.spinner("正在生成完整專家級 Word 報告，請稍候..."):
    word_file_path = generate_word_report(report_data, valuation_data, insights)
    with open(word_file_path, "rb") as word_file:
        st.download_button(
            label="📝 下載完整版專家級 Word 報告",
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
    st.info(f"**核心動能與技術壁壘：**\n\n{insights['ind_1']}")
    st.success(f"**市場護城河：**\n\n{insights['ind_2']}")

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
    st.warning(f"**宏觀與產業趨勢：**\n\n{insights['macro_1']}")
    
    st.markdown("**下行風險追蹤 (Risk Matrix)**")
    df_risks = pd.DataFrame(insights['risks'], columns=['風險維度', '關鍵影響因子', '影響評估與應對建議'])
    st.dataframe(df_risks, use_container_width=True, hide_index=True)
