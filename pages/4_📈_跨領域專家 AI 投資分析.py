import streamlit as st
import pandas as pd
from datetime import datetime

# ==========================================
# 1. 頁面設定與預設資料庫
# ==========================================
st.set_page_config(page_title="跨領域專家 AI 投資分析", layout="wide", page_icon="📈")

STOCK_PRESETS = {
    "2330": {
        "name": "台積電", "symbol": "2330.TW", "price": 2500.0, "change": -0.4,
        "eps_fwd": 110.0, "pe_base": 25.0, "sentiment": 7.5, "growth": 1.5, "risk": 1.5,
        "ind_cowos": "先進封裝（CoWoS/SoIC）產能供不應求，中南部擴廠持續加速。",
        "ind_nodes": "3nm/2nm 製程技術優勢顯著，蘋果、Nvidia 及 CSP 大廠訂單能見度高。",
        "fin_q2": 27.25, "fin_ttm": 87.38, "macro": "全球 AI 基礎建設資本支出年增率持高，邊端 AI 裝置帶動晶體管升級超級週期。",
        "risks": [
            {"風險維度": "地緣政治政策", "影響": "高", "說明": "美中科技限制與海外建廠毛利率稀釋"},
            {"風險維度": "客戶集中度", "影響": "中", "說明": "CSP 業者下修 AI 資本支出或產品延遲"}
        ]
    },
    "NVDA": {
        "name": "輝達", "symbol": "NVDA", "price": 135.5, "change": 1.2,
        "eps_fwd": 4.8, "pe_base": 35.0, "sentiment": 8.2, "growth": 3.0, "risk": 2.0,
        "ind_cowos": "Blackwell架構全線爆單，AI加速器市佔率維持85%以上。",
        "ind_nodes": "CUDA軟體生態系形成極高替換壁壘，軟硬整合綜效明確。",
        "fin_q2": 0.68, "fin_ttm": 2.45, "macro": "Generative AI 走向推理階段，企業級與國家級 AI 算力需求強勁。",
        "risks": [
            {"風險維度": "供應鏈產能限制", "影響": "高", "說明": "受限於台積電 CoWoS 產能開出速度"},
            {"風險維度": "反壟斷監管", "影響": "中", "說明": "歐美政府對 AI 晶片反壟斷調查"}
        ]
    }
}

# ==========================================
# 2. 側邊欄：控制面板與參數調校
# ==========================================
st.sidebar.title("⚙️ 參數調校與設定")
selected_ticker = st.sidebar.selectbox("選擇預設標的", ["2330", "NVDA", "自訂標的"])

# 載入預設值
if selected_ticker in STOCK_PRESETS:
    data = STOCK_PRESETS[selected_ticker]
else:
    data = STOCK_PRESETS["2330"].copy() # 自訂預設帶入台積電版型

st.sidebar.markdown("---")
st.sidebar.subheader("動態估值模型變數")

# 互動拉桿 (Sliders)
eps_fwd = st.sidebar.slider("預估 Forward EPS", min_value=1.0, max_value=200.0, value=float(data["eps_fwd"]), step=0.5)
sentiment = st.sidebar.slider("新聞聲量情緒 (0~10)", min_value=0.0, max_value=10.0, value=float(data["sentiment"]), step=0.1)
growth = st.sidebar.slider("展望成長溢價 (+PE)", min_value=0.0, max_value=5.0, value=float(data["growth"]), step=0.1)
risk = st.sidebar.slider("下行風險折價 (-PE)", min_value=0.0, max_value=5.0, value=float(data["risk"]), step=0.1)

# ==========================================
# 3. 數學模型即時運算
# ==========================================
pe_base = data["pe_base"]
price = data["price"]

# 公式運算
delta_sentiment = (sentiment - 5.0) * 0.4
pe_target = pe_base + delta_sentiment + growth - risk
pe_upper = pe_base + delta_sentiment + growth - 0      # 樂觀
pe_lower = pe_base + 0 + 0 - 3.0                       # 悲觀

tp_base = eps_fwd * pe_target
tp_upper = eps_fwd * pe_upper
tp_lower = eps_fwd * pe_lower

upside_base = ((tp_base - price) / price) * 100
forward_pe = price / eps_fwd

# 評等邏輯
if upside_base >= 10:
    rec, rec_color = "建議買進", "🟢"
elif upside_base <= -10:
    rec, rec_color = "建議賣出", "🔴"
else:
    rec, rec_color = "中性持有", "🟡"

# ==========================================
# 4. 主畫面佈局
# ==========================================
st.title("📈 跨領域專家 AI 投資分析生成器")
st.caption(f"報告生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 標的：{data['name']} ({data['symbol']})")

# 頂部核心數據卡片
col1, col2, col3, col4 = st.columns(4)
col1.metric("最新收盤價", f"${price:,.2f}", f"{data['change']}%")
col2.metric("模型目標價 (Base)", f"${tp_base:,.0f}", f"{upside_base:.1f}% 潛在空間")
col3.metric("綜合投資評等", f"{rec}", f"{rec_color}")
col4.metric("目標價合理區間", f"${tp_lower:,.0f} ~ ${tp_upper:,.0f}")

st.divider()

# 四大專家視角
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("一、 產業專家視角")
    st.info(f"**關鍵產能與技術動態：**\n\n{data['ind_cowos']}")
    st.success(f"**市場節點與護城河：**\n\n{data['ind_nodes']}")

    st.subheader("二、 數學家視角 (量化模型)")
    st.markdown("動態本益比加權公式：")
    # Streamlit 原生支援 LaTeX，排版極美
    st.latex(r"""PE_{target} = PE_{base} + \Delta PE_{sentiment} + \Delta PE_{growth} - \Delta PE_{risk}""")
    st.latex(rf"""{pe_target:.1f} = {pe_base} + {delta_sentiment:.1f} + {growth:.1f} - {risk:.1f}""")
    
    st.markdown("🎯 **情境分析 (Sensitivity Analysis)**")
    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("悲觀 (Bear)", f"${tp_lower:,.0f}", f"PE: {pe_lower:.1f}x", delta_color="off")
    sc2.metric("基準 (Base)", f"${tp_base:,.0f}", f"PE: {pe_target:.1f}x", delta_color="normal")
    sc3.metric("樂觀 (Bull)", f"${tp_upper:,.0f}", f"PE: {pe_upper:.1f}x", delta_color="normal")

with col_right:
    st.subheader("三、 財金專家視角")
    fc1, fc2, fc3, fc4 = st.columns(4)
    fc1.metric("單季 EPS", f"${data['fin_q2']}")
    fc2.metric("TTM EPS", f"${data['fin_ttm']}")
    fc3.metric("歷史 P/E", f"{(price/data['fin_ttm']):.1f}x")
    fc4.metric("遠期 P/E", f"{forward_pe:.1f}x")
    
    st.markdown("<br>", unsafe_allow_html=True)
    st.subheader("四、 經濟專家與風險陣列")
    st.warning(f"**宏觀週期動能：**\n\n{data['macro']}")
    
    st.markdown("**下行風險追蹤 (Risk Matrix)**")
    df_risks = pd.DataFrame(data['risks'])
    st.dataframe(df_risks, use_container_width=True, hide_index=True)