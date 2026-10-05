import math
from datetime import datetime, timezone, timedelta
from io import BytesIO

import pandas as pd
import streamlit as st
import yfinance as yf
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

# ==========================================
# 0. 頁面設定
# ==========================================
st.set_page_config(page_title="跨領域專家 AI 投資分析", layout="wide", page_icon="📈")

DISCLAIMER = (
    "免責聲明：本報告由程式依公開資料與使用者設定之參數自動試算，僅供研究與學習參考，"
    "不構成任何投資建議。資料來源為 Yahoo Finance，可能有延遲或缺漏；標示「手動」者為使用者自行輸入。"
)


def get_taiwan_time_str(fmt="%Y-%m-%d %H:%M:%S"):
    return datetime.now(timezone(timedelta(hours=8))).strftime(fmt)


# ==========================================
# 1. 代號解析（自動探測上市 .TW / 上櫃 .TWO）
# ==========================================
NAME_TO_CODE = {
    "穩懋": "3105", "亞元": "6109", "台積電": "2330",
    "聯發科": "2454", "鴻海": "2317", "台達電": "2308",
}


def _has_price(symbol):
    try:
        return not yf.Ticker(symbol).history(period="5d").empty
    except Exception:
        return False


@st.cache_data(ttl=3600)
def resolve_symbol(user_input):
    text = user_input.strip().upper()
    text = NAME_TO_CODE.get(user_input.strip(), text)

    if text.endswith(".TW") or text.endswith(".TWO"):
        return text
    if text.isdigit() and len(text) in (4, 5, 6):
        for suffix in (".TW", ".TWO"):
            if _has_price(text + suffix):
                return text + suffix
        return text + ".TW"
    return text  # 美股等其他代號


@st.cache_data(ttl=3600)
def get_company_name(symbol):
    try:
        info = yf.Ticker(symbol).info
        name = info.get("longName") or info.get("shortName")
        if name:
            return f"{name} ({symbol})"
    except Exception:
        pass
    return f"{symbol}"


# ==========================================
# 2. 行情與財報（抓不到就回傳 None，不使用假資料）
# ==========================================
def _eps_series(df):
    if df is None or getattr(df, "empty", True):
        return None
    for key in ("Diluted EPS", "Basic EPS"):
        if key in df.index:
            s = df.loc[key].dropna()
            if not s.empty:
                return s.sort_index(ascending=False)
    return None


@st.cache_data(ttl=300)
def get_market_data(symbol):
    out = {
        "price": None, "change": None, "trade_date": None,
        "ret_05m": None, "ret_3m": None,
        "q_eps": [], "ttm": None, "annual": None,
    }
    tkr = yf.Ticker(symbol)

    try:
        hist = tkr.history(period="6mo")
        if not hist.empty:
            close = hist["Close"]
            price = float(close.iloc[-1])
            out["price"] = price
            out["trade_date"] = hist.index[-1].strftime("%Y-%m-%d")
            if len(close) >= 2 and close.iloc[-2] > 0:
                out["change"] = (price / float(close.iloc[-2]) - 1) * 100
            if len(close) > 10:
                out["ret_05m"] = (price / float(close.iloc[-11]) - 1) * 100
            if len(close) > 60:
                out["ret_3m"] = (price / float(close.iloc[-61]) - 1) * 100
    except Exception:
        pass

    try:
        s = _eps_series(tkr.quarterly_income_stmt)
        if s is not None:
            out["q_eps"] = [
                (f"{d.year}Q{(d.month - 1) // 3 + 1}", float(v))
                for d, v in list(s.items())[:4]
            ]
            if len(out["q_eps"]) == 4:
                out["ttm"] = round(sum(v for _, v in out["q_eps"]), 2)
    except Exception:
        pass

    try:
        s = _eps_series(tkr.income_stmt)
        if s is not None:
            out["annual"] = round(float(s.iloc[0]), 2)
    except Exception:
        pass

    return out


# ==========================================
# 3. Word 報告（回傳 bytes，不產生暫存檔）
# ==========================================
def generate_word_report(ctx):
    doc = Document()
    t = doc.add_heading(f"{ctx['name']} 投資分析試算報告", 0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"報告生成時間：{get_taiwan_time_str('%Y 年 %m 月 %d 日 %H:%M (CST)')}")
    doc.add_paragraph(
        f"最新收盤價：{ctx['price']:,.2f}（交易日 {ctx['trade_date']}，當日漲跌 {ctx['change_txt']}）"
    )
    doc.add_paragraph(f"動態非線性模型目標價：{ctx['tp_base']:,.2f}（{ctx['rec']}）")
    doc.add_paragraph(f"線性基準模型目標價：{ctx['tp_linear']:,.2f}")
    doc.add_paragraph(f"目標價區間：[{ctx['tp_lower']:,.0f}, {ctx['tp_upper']:,.0f}]")

    doc.add_heading("一、技術動能與產業熱點", level=1)
    doc.add_paragraph(
        f"熱點分數（手動輸入）：近 1 月 {ctx['hot_1m']} / 10，近 3 月 {ctx['hot_3m']} / 10"
    )
    doc.add_paragraph(f"近 10 個交易日報酬：{ctx['ret_05m_txt']}；近 60 個交易日報酬：{ctx['ret_3m_txt']}")

    doc.add_heading("二、估值模型", level=1)
    doc.add_paragraph(f"動態非線性 PE = {ctx['pe_target']:.1f}x，目標價 {ctx['tp_base']:,.2f}")
    doc.add_paragraph(f"線性基準 PE = {ctx['pe_linear']:.1f}x，目標價 {ctx['tp_linear']:,.2f}（使用未調整 EPS）")
    doc.add_paragraph(f"調整後預估 EPS：{ctx['eps_adj']:.2f}（基礎 {ctx['eps_base']}）")

    doc.add_heading("三、財務數據", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    h = table.rows[0].cells
    h[0].text, h[1].text, h[2].text = "指標", "數值", "資料來源"
    for label, val in ctx["q_eps"]:
        r = table.add_row().cells
        r[0].text, r[1].text, r[2].text = f"單季 EPS ({label})", f"{val:.2f}", "Yahoo Finance"
    rows = [
        ("近 4 季 EPS (TTM)", f"{ctx['ttm']:.2f}", ctx["ttm_src"]),
        ("最近年度 EPS", f"{ctx['annual']:.2f}", ctx["annual_src"]),
        ("歷史本益比", f"{ctx['hist_pe']:.1f} 倍", "股價 / TTM EPS"),
        ("遠期本益比", f"{ctx['fwd_pe']:.1f} 倍", "股價 / 調整後預估 EPS"),
    ]
    for a, b, c in rows:
        r = table.add_row().cells
        r[0].text, r[1].text, r[2].text = a, b, c

    doc.add_heading("四、風險提示", level=1)
    for r in ctx["risks"]:
        doc.add_paragraph(f"{r[0]}：{r[1]} — {r[2]}", style="List Bullet")

    doc.add_paragraph("")
    doc.add_paragraph(DISCLAIMER)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ==========================================
# 4. 側邊欄
# ==========================================
st.sidebar.title("⚙️ 標的與參數設定")

with st.sidebar.form(key="search_form"):
    user_query = st.text_input(
        "輸入公司名稱或代號（如 6109, 3105, 2330, NVDA）", value="6109"
    ).strip()
    st.form_submit_button("📊 執行分析與載入數據")

if not user_query:
    st.info("請在左側輸入公司名稱或代號。")
    st.stop()

symbol = resolve_symbol(user_query)
company_name = get_company_name(symbol)
mkt = get_market_data(symbol)

if mkt["price"] is None:
    st.error(f"無法取得 {symbol} 的股價資料，請確認代號是否正確，或稍後再試（可能遭 Yahoo 限流）。")
    st.stop()

price = mkt["price"]

# ---- EPS：抓得到就用，抓不到或想覆寫時手動輸入 ----
st.sidebar.markdown("---")
st.sidebar.subheader("財報 EPS")
fetched_ok = mkt["ttm"] is not None and mkt["annual"] is not None
if fetched_ok:
    st.sidebar.success("已自動取得 TTM 與年度 EPS")
else:
    st.sidebar.warning("財報資料不完整，請手動輸入 EPS")

use_manual = st.sidebar.checkbox("手動輸入 / 覆寫 EPS", value=not fetched_ok)
if use_manual:
    ttm_eps = st.sidebar.number_input(
        "近 4 季 EPS (TTM)", value=float(mkt["ttm"] or 1.0), step=0.1, format="%.2f"
    )
    annual_eps = st.sidebar.number_input(
        "最近年度 EPS", value=float(mkt["annual"] or ttm_eps), step=0.1, format="%.2f"
    )
    ttm_src = annual_src = "手動輸入"
else:
    ttm_eps, annual_eps = mkt["ttm"], mkt["annual"]
    ttm_src = annual_src = "Yahoo Finance"

# ---- 熱點分數（無法自動量化，改為手動） ----
st.sidebar.markdown("---")
st.sidebar.subheader("產業熱點（手動評分）")
hot_1m = st.sidebar.slider("近 1 個月熱點 (0~10)", 0.0, 10.0, 5.0, 0.1)
hot_3m = st.sidebar.slider("近 3 個月熱點 (0~10)", 0.0, 10.0, 5.0, 0.1)

# ---- 估值參數 ----
st.sidebar.markdown("---")
st.sidebar.subheader("估值模型變數")
eps_fwd_base = st.sidebar.number_input(
    "基礎預估 Forward EPS",
    min_value=0.01,
    value=float(max(0.5, round(ttm_eps * 1.1, 2))),
    step=0.1,
    format="%.2f",
)
pe_base = st.sidebar.number_input("產業中樞本益比 (PE)", min_value=1.0, value=22.0)
sentiment = st.sidebar.slider("新聞聲量情緒 (0~10)", 0.0, 10.0, 5.0, 0.1)
growth_score = st.sidebar.slider("展望成長評分 (0~10)", 0.0, 10.0, 5.0, 0.1)
risk = st.sidebar.slider("下行風險折價 (-PE)", 0.0, 10.0, 1.0, 0.1)

# ==========================================
# 5. 估值模型
#    設計：熱點訊號只放大「PE 端」，EPS 成長訊號只調整「EPS 端」，避免同一訊號重複計入。
# ==========================================
hot_triggered = hot_1m > hot_3m
eps_triggered = ttm_eps > annual_eps > 0

# PE 端放大係數（僅受熱點影響）
amp_factor = 1.0 + 0.30 * (hot_1m - hot_3m) if hot_triggered else 1.0

# EPS 端調整（僅受財報成長影響）
eps_multiplier = math.pow(ttm_eps / annual_eps, 0.35) if eps_triggered else 1.0
eps_adj = eps_fwd_base * eps_multiplier

alpha_g, beta_g, alpha_s, beta_s = 0.8, 0.22, 0.5, 0.35
growth_exp = alpha_g * amp_factor * (math.exp(beta_g * growth_score) - 1)
sent_diff = sentiment - 5.0
sentiment_exp = (
    alpha_s * amp_factor * math.copysign(1, sent_diff) * (math.exp(beta_s * abs(sent_diff)) - 1)
)

# PE 上下限保護，避免指數項爆衝
pe_target_raw = pe_base + sentiment_exp + growth_exp - risk
pe_target = min(max(pe_target_raw, pe_base * 0.5), pe_base * 2.0)
pe_capped = pe_target != pe_target_raw

# 線性基準模型：不放大、使用未調整 EPS，作為公平對照
pe_linear = pe_base + (sentiment - 5.0) * 0.4 + max(growth_score - 5.0, 0.0) * 0.6 - risk
pe_linear = max(pe_linear, 1.0)
tp_linear = eps_fwd_base * pe_linear

pe_upper = pe_target + 4.0
pe_lower = max(min(pe_base - 3.0, pe_target - 3.0), 1.0)

tp_base = eps_adj * pe_target
tp_upper = eps_adj * pe_upper
tp_lower = eps_adj * pe_lower

upside = (tp_base / price - 1) * 100
fwd_pe = price / eps_adj if eps_adj > 0 else 0.0
hist_pe = price / ttm_eps if ttm_eps > 0 else 0.0

if upside >= 10:
    rec, rec_icon = "建議買進", "🟢"
elif upside <= -10:
    rec, rec_icon = "建議賣出", "🔴"
else:
    rec, rec_icon = "中性持有", "🟡"

risks = [
    ("總體經濟風險", "利率與匯率波動", "可能造成毛利與評價短期波動"),
    ("市場競爭風險", "同業擴產與需求變化", "需持續追蹤訂單能見度"),
    ("模型風險", "參數多為主觀設定", "請以多組情境檢視，勿單一依賴目標價"),
]


def fmt_pct(v):
    return "資料不足" if v is None else f"{v:+.2f}%"


# ==========================================
# 6. 主畫面
# ==========================================
st.title("📈 跨領域專家 AI 投資分析")
st.subheader(f"🏢 {company_name}")
st.caption(f"報告生成時間：{get_taiwan_time_str()} (CST)")
st.warning(DISCLAIMER)

change_txt = fmt_pct(mkt["change"])
c1, c2, c3, c4 = st.columns(4)
c1.metric("最新收盤價", f"${price:,.2f}", f"{mkt['trade_date']} ({change_txt})")
c2.metric("動態模型目標價", f"${tp_base:,.0f}", f"{upside:.1f}% 潛在空間")
c3.metric("綜合評等", rec, rec_icon)
c4.metric("目標價區間", f"[{tp_lower:,.0f}, {tp_upper:,.0f}]")

ctx = {
    "name": company_name, "price": price, "trade_date": mkt["trade_date"],
    "change_txt": change_txt, "tp_base": tp_base, "tp_linear": tp_linear,
    "tp_lower": tp_lower, "tp_upper": tp_upper, "rec": rec,
    "hot_1m": hot_1m, "hot_3m": hot_3m,
    "ret_05m_txt": fmt_pct(mkt["ret_05m"]), "ret_3m_txt": fmt_pct(mkt["ret_3m"]),
    "pe_target": pe_target, "pe_linear": pe_linear,
    "eps_adj": eps_adj, "eps_base": eps_fwd_base,
    "q_eps": mkt["q_eps"], "ttm": ttm_eps, "annual": annual_eps,
    "ttm_src": ttm_src, "annual_src": annual_src,
    "hist_pe": hist_pe, "fwd_pe": fwd_pe, "risks": risks,
}

st.download_button(
    "📝 下載 Word 報告",
    data=generate_word_report(ctx),
    file_name=f"{symbol}_AI_Investment_Report.docx",
    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    type="primary",
)
st.divider()

left, right = st.columns(2)

with left:
    st.subheader("一、技術動能與產業熱點")
    st.info(f"熱點分數（手動）：近 1 月 {hot_1m} / 10，近 3 月 {hot_3m} / 10")
    t1, t2 = st.columns(2)
    t1.metric("近 10 個交易日報酬", fmt_pct(mkt["ret_05m"]))
    t2.metric("近 60 個交易日報酬", fmt_pct(mkt["ret_3m"]))

    st.subheader("二、估值模型（動態非線性 vs 線性）")
    st.markdown(
        f"🔥 **熱點觸發（放大 PE 端）：** `{'是' if hot_triggered else '否'}` "
        f"（近1月 {hot_1m} vs 近3月 {hot_3m}，放大係數 {amp_factor:.3f}x）"
    )
    st.markdown(
        f"📈 **EPS 成長觸發（調整 EPS 端）：** `{'是' if eps_triggered else '否'}` "
        f"（TTM {ttm_eps} vs 年度 {annual_eps}，EPS 乘數 {eps_multiplier:.3f}x）"
    )
    st.markdown("---")
    st.markdown(f"🚀 **動態非線性：** PE **{pe_target:.1f}x** → 目標價 **${tp_base:,.0f}**")
    if pe_capped:
        st.caption(f"⚠️ 原始 PE {pe_target_raw:.1f}x 超出 0.5~2 倍產業 PE 範圍，已套用上下限。")
    st.markdown(f"📉 **線性基準：** PE **{pe_linear:.1f}x** → 目標價 **${tp_linear:,.0f}**（使用未調整 EPS）")
    st.markdown("---")
    st.markdown(f"✨ **調整後 Forward EPS：** **{eps_adj:.2f}**（基礎 {eps_fwd_base}）")

    s1, s2, s3 = st.columns(3)
    s1.metric("悲觀 (Bear)", f"${tp_lower:,.0f}", f"PE: {pe_lower:.1f}x", delta_color="off")
    s2.metric("基準 (Base)", f"${tp_base:,.0f}", f"PE: {pe_target:.1f}x", delta_color="off")
    s3.metric("樂觀 (Bull)", f"${tp_upper:,.0f}", f"PE: {pe_upper:.1f}x", delta_color="off")

with right:
    st.subheader("三、財務檢核")
    f1, f2, f3 = st.columns(3)
    f1.metric("TTM EPS", f"{ttm_eps:.2f}", ttm_src, delta_color="off")
    f2.metric("年度 EPS", f"{annual_eps:.2f}", annual_src, delta_color="off")
    f3.metric("遠期 P/E", f"{fwd_pe:.1f}x")

    st.markdown("**近 4 季單季 EPS：**")
    if mkt["q_eps"]:
        st.dataframe(
            pd.DataFrame(mkt["q_eps"], columns=["財報季度", "單季 EPS (元)"]),
            hide_index=True,
        )
    else:
        st.caption("Yahoo Finance 未提供單季 EPS 資料。")

    st.subheader("四、風險提示")
    st.dataframe(
        pd.DataFrame(risks, columns=["風險維度", "關鍵影響因子", "影響評估"]),
        hide_index=True,
    )
