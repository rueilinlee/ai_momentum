import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
from fpdf import FPDF
import tempfile
import os

st.set_page_config(page_title="跨領域專家 AI 投資分析", layout="wide", page_icon="📈")

# ==========================================
# 1. 即時股價獲取函數
# ==========================================
@st.cache_data(ttl=300) # 快取 5 分鐘避免重複請求
def get_live_price(ticker_symbol):
    try:
        tkr = yf.Ticker(ticker_symbol)
        hist = tkr.history(period="5d")
        if len(hist) >= 2:
            price = hist['Close'].iloc[-1]
            prev_price = hist['Close'].iloc[-2]
            change_pct = ((price - prev_price) / prev_price) * 100
        else:
            price = hist['Close'].iloc[-1] if not hist.empty else 100.0
            change_pct = 0.0
        return float(price), float(change_pct)
    except Exception:
        return 0.0, 0.0

# ==========================================
# 2. PDF 完整報告生成函數
# ==========================================
def generate_pdf_report(data_dict, valuation_dict):
    pdf = FPDF()
    pdf.add_page()
    
    # 載入中文字型 (請確保同目錄下有 chinese.ttf)
    font_path = "chinese.ttf"
    has_font = os.path.exists(font_path)
    if has_font:
        pdf.add_font("ChineseFont", "", font_path, uni=True)
        pdf.set_font("ChineseFont", size=16)
    else:
        pdf.set_font("Arial", size=16)
        
    # 標題與基本資料
    pdf.cell(200, 10, txt=f"跨領域專家 AI 投資分析報告 - {data_dict['symbol']}", ln=True, align='C')
    if has_font: pdf.set_font("ChineseFont", size=10)
    pdf.cell(200, 8, txt=f"報告生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ln=True, align='C')
    pdf.ln(5)
    
    # 核心數據區塊
    pdf.set_font("ChineseFont" if has_font else "Arial", size=12)
    pdf.cell(200, 8, txt=f"【最新收盤價】 ${data_dict['price']:,.2f} (漲跌幅 {data_dict['change']:.2f}%)", ln=True)
    pdf.cell(200, 8, txt=f"【綜合投資評等】 {valuation_dict['rec']} (目標價區間: ${valuation_dict['tp_lower']:,.0f} ~ ${valuation_dict['tp_upper']:,.0f})", ln=True)
    pdf.cell(200, 8, txt=f"【基準目標價】 ${valuation_dict['tp_base']:,.0f} (潛在空間 {valuation_dict['upside_base']:.1f}%)", ln=True)
    pdf.ln(5)
    
    # 四大專家視角
    sections = [
        ("一、 產業專家視角", f"技術動態：{data_dict['ind_cowos']}\n市場護城河：{data_dict['ind_nodes']}"),
        ("二、 數學家視角 (量化模型)", f"PE目標 = 基準({data_dict['pe_base']}) + 聲量({valuation_dict['delta_sentiment']:.1f}) + 成長({data_dict['growth']}) - 風險({data_dict['risk']}) = {valuation_dict['pe_target']:.1f}x\n目標價推算 = 遠期EPS({data_dict['eps_fwd']}) x 目標PE = ${valuation_dict['tp_base']:,.0f}"),
        ("三、 財金專家視角", f"近四季 TTM EPS：${data_dict['fin_ttm']}\n歷史 P/E：{valuation_dict['historical_pe']:.1f}x | 遠期 P/E：{valuation_dict['forward_pe']:.1f}x"),
        ("四、 經濟專家與風險陣列", f"宏觀動能：{data_dict['macro']}")
    ]
    
    for title, content in sections:
        pdf.set_font("ChineseFont" if has_font else "Arial", size=14)
        pdf.cell(200, 10, txt=title, ln=True)
        pdf.set_font("ChineseFont" if has_font else "Arial", size=11)
        pdf.multi_cell(0, 8, txt=content)
        pdf.ln(3)

    # 匯出暫存檔
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp_file.name)
    return tmp_file.name

# ==========================================
# 3. 側邊欄：自由輸入與參數調校
# ==========================================
st.sidebar.title("⚙️ 標的與參數設定")

# 改為自由輸入框 (預設帶入 NVDA)
ticker_input = st.sidebar.text_input("輸入公司名稱或代碼 (如 NVDA, 2330.TW)", value="NVDA").upper().strip()

# 抓取即時報價
live_price, live_change = get_live_price(ticker_input)
if live_price == 0.0:
    st.sidebar.warning("無法抓取即時股價，將使用預設值。")
    live_price = 135.5

st.sidebar.markdown("---")
st.sidebar.subheader("動態估值模型變數")

# 互動拉桿 (Sliders) - 實務上這裡會由 AI 針對標的動態給定初始值
eps_fwd = st.sidebar.slider("預估 Forward EPS", min_value=1.0, max_value=200.0, value=4.8, step=0.5)
pe_base = st.sidebar.number_input("產業中樞本益比 (PE)", value=35.0)
sentiment = st.sidebar.slider("新聞聲量情緒 (0~10)", min_value=0.0, max_value=10.0, value=8.2, step=0.1)
growth = st.sidebar.slider("展望成長溢價 (+PE)", min_value=0.0, max_value=10.0, value=3.0, step=0.1)
risk = st.sidebar.slider("下行風險折價 (-PE)", min_value=0.0, max_value=10.0, value=2.0, step=0.1)

# ==========================================
# 4. 數學模型即時運算與資料打包
# ==========================================
delta_sentiment = (sentiment - 5.0) * 0.4
pe_target = pe_base + delta_sentiment + growth - risk
pe_upper = pe_base + delta_sentiment + growth - 0      
pe_lower = pe_base + 0 + 0 - 3.0                       

tp_base = eps_fwd * pe_target
tp_upper = eps_fwd * pe_upper
tp_lower = eps_fwd * pe_lower

upside_base = ((tp_base - live_price) / live_price) * 100
forward_pe = live_price / eps_fwd
historical_pe = live_price / (eps_fwd * 0.7) # 簡化預估 TTM EPS

if upside_base >= 10:
    rec, rec_color = "建議買進", "🟢"
elif upside_base <= -10:
    rec, rec_color = "建議賣出", "🔴"
else:
    rec, rec_color = "中性持有", "🟡"

# 打包資料供 UI 與 PDF 使用
report_data = {
    "symbol": ticker_input,
    "price": live_price,
    "change": live_change,
    "eps_fwd": eps_fwd, "pe_base": pe_base, "sentiment": sentiment, "growth": growth, "risk": risk,
    "ind_cowos": f"針對 {ticker_input} 的最新供應鏈產能與技術突破動態分析。",
    "ind_nodes": f"{ticker_input} 主要產品節點與競爭對手之護城河評估。",
    "fin_ttm": round(eps_fwd * 0.7, 2),
    "macro": "總體經濟與產業週期位階概況，考量降息循環與 AI 基礎建設支出。"
}

valuation_data = {
    "pe_target": pe_target, "tp_base": tp_base, "tp_lower": tp_lower, "tp_upper": tp_upper,
    "upside_base": upside_base, "rec": rec, "delta_sentiment": delta_sentiment,
    "forward_pe": forward_pe, "historical_pe": historical_pe
}

# ==========================================
# 5. 主畫面佈局與 PDF 下載
# ==========================================
st.title("📈 跨領域專家 AI 投資分析生成器")
st.caption(f"報告生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 標的：{ticker_input}")

col1, col2, col3, col4 = st.columns(4)
col1.metric("最新收盤價 (即時)", f"${live_price:,.2f}", f"{live_change:.2f}%")
col2.metric("模型目標價 (Base)", f"${tp_base:,.0f}", f"{upside_base:.1f}% 潛在空間")
col3.metric("綜合投資評等", f"{rec}", f"{rec_color}")
col4.metric("目標價合理區間", f"${tp_lower:,.0f} ~ ${tp_upper:,.0f}")

st.divider()

# PDF 下載按鈕
pdf_file_path = generate_pdf_report(report_data, valuation_data)
with open(pdf_file_path, "rb") as pdf_file:
    st.download_button(
        label="📄 下載完整版 PDF 報告",
        data=pdf_file,
        file_name=f"{ticker_input}_AI_Investment_Report.pdf",
        mime="application/pdf",
        type="primary"
    )
st.markdown("<br>", unsafe_allow_html=True)

# 渲染四大專家視角 (與先前版本相同，套用即時計算結果)
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("一、 產業專家視角")
    st.info(f"**關鍵產能與技術動態：**\n\n{report_data['ind_cowos']}")
    st.success(f"**市場節點與護城河：**\n\n{report_data['ind_nodes']}")

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
    st.warning(f"**宏觀週期動能：**\n\n{report_data['macro']}")
