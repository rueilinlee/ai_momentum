import json
import math
import re
from datetime import datetime, timezone, timedelta
from io import BytesIO

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import lightgbm as lgb
import numpy as np
import pandas as pd
import requests
import shap
import statsmodels.api as sm
import streamlit as st
import yfinance as yf
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from statsmodels.regression.rolling import RollingOLS

# ==========================================
# 0. 頁面設定與常數
# ==========================================
st.set_page_config(page_title="跨領域專家 AI 投資分析與量化預測系統", layout="wide", page_icon="📈")

DISCLAIMER = (
    "免責聲明：本報告由程式依公開資料、使用者設定之參數與機器學習模型自動試算，僅供研究與學習參考，"
    "不構成任何投資建議。資料來源為 Yahoo Finance（部分 EPS 可能由 Gemini 聯網搜尋補充），"
    "可能有延遲、缺漏或錯誤；標示「手動」者為使用者自行輸入。機器學習預測不保證未來表現。"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
}
RF_TW_DAILY = 0.017 / 365
LABEL_THRESHOLD = 0.005
WINDOW_ORTHO = 126
WINDOW_MAIN = 252
FEATURES = [
    "Beta_3_Rolling", "Beta_3_Trend_5D", "Gamma_Rolling", "Gamma_Trend_5D",
    "NVDA_Pure_Shock", "Price_Mom_30D", "Price_Mom_5D", "RSI_14", "Vol_10D",
]
AI_SRC = "Google Gemini 聯網搜尋（請自行核對）"


def tw_now():
    return datetime.now(timezone(timedelta(hours=8)))


def get_taiwan_time_str(fmt="%Y-%m-%d %H:%M:%S"):
    return tw_now().strftime(fmt)


def fmt_pct(v):
    return "資料不足" if v is None else f"{v:+.2f}%"


# ==========================================
# 1. 代號與公司名稱解析
# ==========================================
NAME_TO_CODE = {
    "穩懋": "3105.TWO", "亞元": "6109.TWO", "台積電": "2330.TW",
    "聯發科": "2454.TW", "鴻海": "2317.TW", "台達電": "2308.TW",
    "創意": "3443.TW", "長榮": "2603.TW", "聯電": "2303.TW",
    "廣達": "2382.TW", "緯創": "3231.TW", "緯穎": "6669.TW",
}


def _has_price(symbol):
    try:
        return not yf.Ticker(symbol).history(period="5d").empty
    except Exception:
        return False


@st.cache_data(ttl=3600)
def resolve_symbol(user_input):
    text = user_input.strip()
    if text in NAME_TO_CODE:
        return NAME_TO_CODE[text]
    for name, code in NAME_TO_CODE.items():
        if name in text:
            return code

    upper = text.upper()
    if upper.endswith(".TW") or upper.endswith(".TWO"):
        return upper
    if upper.isdigit() and len(upper) in (4, 5, 6):
        for suffix in (".TW", ".TWO"):
            if _has_price(upper + suffix):
                return upper + suffix
        return upper + ".TW"
    return upper


@st.cache_data(ttl=3600)
def get_company_name(symbol):
    if symbol.endswith((".TW", ".TWO")):
        try:
            url = f"https://tw.stock.yahoo.com/quote/{symbol.split('.')[0]}"
            res = requests.get(url, headers=HEADERS, timeout=5)
            m = re.search(r"<title>(.*?)\(", res.text)
            if m:
                name = m.group(1).strip()
                if name and "Yahoo" not in name and "找不到" not in name:
                    return f"{name} ({symbol})"
        except Exception:
            pass
    try:
        info = yf.Ticker(symbol).info
        name = info.get("longName") or info.get("shortName")
        if name:
            return f"{name} ({symbol})"
    except Exception:
        pass
    return symbol


# ==========================================
# 2. 財報 EPS（Yahoo + Gemini 補查）
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


@st.cache_data(ttl=3600)
def get_financials(symbol):
    out = {"q_eps": [], "annual": None}
    try:
        tkr = yf.Ticker(symbol)
        s_q = _eps_series(tkr.quarterly_income_stmt)
        if s_q is not None:
            out["q_eps"] = [
                (f"{d.year}Q{(d.month - 1) // 3 + 1}", float(v))
                for d, v in list(s_q.items())[:4]
            ]
        s_a = _eps_series(tkr.income_stmt)
        if s_a is not None:
            out["annual"] = round(float(s_a.iloc[0]), 2)
    except Exception:
        pass
    return out


def get_default_gemini_key():
    try:
        return st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        return ""


def fetch_eps_via_gemini(company, symbol, api_key, model):
    """以 Gemini + Google 搜尋查詢近 4 季單季 EPS 與最近年度 EPS；查不到就丟例外，不回傳猜測值。"""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = f"""請使用 Google 搜尋，查詢「{company}」（代號 {symbol}）最近 4 個已公告財報季度的「單季」稅後每股盈餘 (EPS)，
以及最近一個完整會計年度的全年 EPS。

規則：
1. 必須是已公告的實際數字，不可推測或估算；若找不到某個數字，該欄位填 null。
2. 季度 EPS 為「單季」數字，不是累計數字（例如 Q2 不可是上半年累計）。
3. quarters 由最新到最舊排序，label 格式如 "2026Q2"。
4. 金額以公司財報幣別為準（台股為新台幣）。
5. 只輸出一個 JSON 物件，不要加任何說明文字或 Markdown，格式如下：
{{"quarters":[{{"label":"2026Q2","eps":0.00}},{{"label":"2026Q1","eps":0.00}},{{"label":"2025Q4","eps":0.00}},{{"label":"2025Q3","eps":0.00}}],
"annual_eps":0.00,"annual_year":"2025","sources":["來源網址"]}}"""

    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.0,
        ),
    )
    m = re.search(r"\{.*\}", resp.text or "", re.S)
    if not m:
        raise ValueError("Gemini 回傳內容無法解析為 JSON，請重試。")
    data = json.loads(m.group(0))

    quarters = []
    for q in (data.get("quarters") or [])[:4]:
        if q.get("eps") is None:
            continue
        eps = float(q["eps"])
        if abs(eps) > 1000:
            raise ValueError("Gemini 回傳的 EPS 數值不合理，請重試或手動輸入。")
        quarters.append((str(q["label"]), eps))
    if len(quarters) != 4:
        raise ValueError("Gemini 未能找到完整的 4 季 EPS，請手動輸入。")

    annual = data.get("annual_eps")
    annual = float(annual) if annual is not None else None

    sources = list(data.get("sources") or [])
    try:
        for ch in resp.candidates[0].grounding_metadata.grounding_chunks:
            if ch.web and ch.web.uri:
                sources.append(ch.web.uri)
    except Exception:
        pass
    sources = list(dict.fromkeys(s for s in sources if isinstance(s, str)))[:5]
    return {"quarters": quarters, "annual_eps": annual, "sources": sources}


# ==========================================
# 3. RSI 與藍紅動能區價格試算
# ==========================================
def rsi_series(close, window=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(loss != 0, 100.0)


def price_for_rsi(close, target, mode):
    """回傳 (價格, 該價格下的 RSI, 狀態)；狀態: already / reached / not_reached / None"""
    close = close.reset_index(drop=True)
    last = float(close.iloc[-1])
    cur = rsi_series(close).iloc[-1]
    if pd.isna(cur):
        return None
    if (mode == "drop" and cur <= target) or (mode == "rise" and cur >= target):
        return last, float(cur), "already"

    direction = -1 if mode == "drop" else 1
    p, r = last, float(cur)
    for i in range(1, 101):
        p = last * (1 + direction * 0.005 * i)
        if p <= 0:
            break
        sim = pd.concat([close, pd.Series([p])], ignore_index=True)
        r = rsi_series(sim).iloc[-1]
        if pd.isna(r):
            continue
        if (mode == "drop" and r <= target) or (mode == "rise" and r >= target):
            return p, float(r), "reached"
    return p, float(r), "not_reached"


def zone_text(res, target, mode):
    if res is None:
        return "資料不足，無法試算"
    p, r, status = res
    if status == "already":
        side = "≤" if mode == "drop" else "≥"
        return f"目前 RSI 為 {r:.1f}，已 {side} {target}，現價 {p:,.2f} 元已在該區"
    if status == "reached":
        verb = "跌至" if mode == "drop" else "漲至"
        return f"模擬股價{verb}約 {p:,.2f} 元時，RSI 約為 {r:.1f}（目標 {target}）"
    return f"模擬 ±50% 價格範圍內仍未達 RSI {target}（最後試算 {p:,.2f} 元，RSI {r:.1f}）"


# ==========================================
# 4. 量化模型管線（有快取，不會因拖動滑桿重跑）
# ==========================================
def make_model():
    return lgb.LGBMClassifier(
        n_estimators=80, learning_rate=0.03, max_depth=3, min_child_samples=40,
        subsample=0.7, colsample_bytree=0.7, reg_alpha=0.5, reg_lambda=0.5,
        random_state=42, verbose=-1,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def run_quant_pipeline(symbol, day_key):
    now = tw_now()
    start = (now - timedelta(days=365 * 6)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    tickers = [symbol, "NVDA", "^SOX", "^DJI", "^IRX", "^TWII"]

    raw = yf.download(tickers, start=start, end=end, progress=False, auto_adjust=True)["Close"]
    missing = [t for t in tickers if t not in raw.columns or raw[t].dropna().empty]
    if missing:
        return {"error": f"下載不到以下資料：{', '.join(missing)}（可能是代號錯誤或 Yahoo 暫時限流）"}

    stock = raw[symbol].dropna()
    if len(stock) < 700:
        return {"error": f"{symbol} 歷史資料僅 {len(stock)} 筆，不足以訓練量化模型（至少約 700 個交易日）。"}
    idx = stock.index

    def to_tw(series, lag):
        """把其他市場資料對齊到台股交易日；lag=True 表示美股資訊延後 1 個台股交易日使用，避免未來資訊。"""
        s = series.dropna()
        out = s.reindex(s.index.union(idx)).sort_index().ffill().reindex(idx)
        return out.shift(1) if lag else out

    us_ret = {c: to_tw(raw[c].dropna().pct_change(), lag=True) for c in ("NVDA", "^SOX", "^DJI")}
    twii = to_tw(raw["^TWII"], lag=False)
    rf_us = (to_tw(raw["^IRX"], lag=True) / 100) / 365

    stock_ret = stock.pct_change()
    df = pd.DataFrame({
        "R": stock_ret,
        "TWII": twii.pct_change(),
        "NVDA": us_ret["NVDA"],
        "SOX": us_ret["^SOX"],
        "DJI": us_ret["^DJI"],
        "RF_US": rf_us,
    })
    df["Price_Mom_30D"] = (stock.pct_change(30) - twii.pct_change(30)).shift(1)
    df["Price_Mom_5D"] = (stock.pct_change(5) - twii.pct_change(5)).shift(1)
    df["Vol_10D"] = stock_ret.rolling(10).std().shift(1)
    df["RSI_14"] = rsi_series(stock).shift(1)
    df = df.dropna()

    # (1) 滾動式正交化：只用過去資料估計係數，避免全樣本估計造成的未來資訊
    y_o = df["NVDA"] - df["RF_US"]
    x_o = sm.add_constant(pd.DataFrame({
        "DJI_Excess": df["DJI"] - df["RF_US"],
        "SOX_Excess": df["SOX"] - df["RF_US"],
    }))
    p_o = RollingOLS(y_o, x_o, window=WINDOW_ORTHO).fit().params
    fitted = (x_o * p_o).sum(axis=1, min_count=x_o.shape[1])
    df["NVDA_Pure_Shock"] = y_o - fitted
    df = df.dropna()

    # (2) 滾動式主迴歸
    df["Interaction_Term"] = df["NVDA_Pure_Shock"] * df["Price_Mom_30D"]
    x_m = sm.add_constant(df[["TWII", "SOX", "NVDA_Pure_Shock", "Price_Mom_30D", "Interaction_Term"]])
    y_m = df["R"] - RF_TW_DAILY
    params = RollingOLS(y_m, x_m, window=WINDOW_MAIN).fit().params
    df["Beta_3_Rolling"] = params["NVDA_Pure_Shock"]
    df["Gamma_Rolling"] = params["Interaction_Term"]
    df["Beta_3_Trend_5D"] = df["Beta_3_Rolling"].diff(5)
    df["Gamma_Trend_5D"] = df["Gamma_Rolling"].diff(5)

    # (3) 標籤：未來 5 日超額報酬 > 門檻；最後 5 天沒有答案，必須保留 NaN（原本會被誤標為 0）
    excess_fwd = (stock.pct_change(5).shift(-5) - twii.pct_change(5).shift(-5)).reindex(df.index)
    df["Label"] = excess_fwd.gt(LABEL_THRESHOLD).astype(float).where(excess_fwd.notna())

    df_feat = df.dropna(subset=FEATURES)
    df_train = df_feat.dropna(subset=["Label"])
    if len(df_train) < 300 or df_train["Label"].nunique() < 2:
        return {"error": f"可用訓練樣本僅 {len(df_train)} 筆或標籤只有單一類別，無法訓練模型。"}

    X = df_train[FEATURES]
    y = df_train["Label"].astype(int)

    # (4) 時序交叉驗證（gap 避免 5 日標籤重疊）
    tscv = TimeSeriesSplit(n_splits=5, gap=5)
    accs, aucs, bases = [], [], []
    for tr, te in tscv.split(X):
        m = make_model().fit(X.iloc[tr], y.iloc[tr])
        accs.append(accuracy_score(y.iloc[te], m.predict(X.iloc[te])))
        bases.append(max(y.iloc[te].mean(), 1 - y.iloc[te].mean()))
        if y.iloc[te].nunique() == 2:
            aucs.append(roc_auc_score(y.iloc[te], m.predict_proba(X.iloc[te])[:, 1]))

    # (5) 用全部訓練資料重訓最終模型，再預測「最新一天」
    final = make_model().fit(X, y)
    latest = df_feat[FEATURES].iloc[[-1]]
    proba = float(final.predict_proba(latest)[:, 1][0])

    X_shap = X.tail(250)
    sv = shap.TreeExplainer(final).shap_values(X_shap)
    if isinstance(sv, list):
        sv = sv[1]
    elif getattr(sv, "ndim", 2) == 3:
        sv = sv[:, :, 1]

    trend = df_feat["Beta_3_Trend_5D"]
    z = float(trend.iloc[-1] / trend.std()) if trend.std() and trend.std() > 0 else 0.0

    return {
        "stock": stock,
        "ret_05m": (stock.iloc[-1] / stock.iloc[-11] - 1) * 100 if len(stock) > 10 else None,
        "ret_3m": (stock.iloc[-1] / stock.iloc[-61] - 1) * 100 if len(stock) > 60 else None,
        "proba": proba,
        "beta3": params["NVDA_Pure_Shock"].dropna(),
        "gamma": params["Interaction_Term"].dropna(),
        "beta3_now": float(df_feat["Beta_3_Rolling"].iloc[-1]),
        "gamma_now": float(df_feat["Gamma_Rolling"].iloc[-1]),
        "beta3_trend": float(trend.iloc[-1]),
        "beta3_trend_z": z,
        "gamma_trend": float(df_feat["Gamma_Trend_5D"].iloc[-1]),
        "cv_acc": float(np.mean(accs)),
        "cv_base": float(np.mean(bases)),
        "cv_auc": float(np.mean(aucs)) if aucs else None,
        "n_train": int(len(df_train)),
        "shap_values": sv,
        "X_shap": X_shap,
    }


# ==========================================
# 5. Word 報告
# ==========================================
def generate_word_report(ctx):
    doc = Document()
    t = doc.add_heading(f"{ctx['name']} 跨領域 AI 投資與量化分析報告", 0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"報告生成時間：{get_taiwan_time_str('%Y 年 %m 月 %d 日 %H:%M (CST)')}")
    doc.add_paragraph(f"最新收盤價：{ctx['price']:,.2f}（交易日 {ctx['trade_date']}，當日漲跌 {ctx['change_txt']}）")
    doc.add_paragraph(f"動態非線性模型目標價：{ctx['tp_base']:,.2f}（估值評等：{ctx['val_rec']}）")
    doc.add_paragraph(f"目標價區間：[{ctx['tp_lower']:,.0f}, {ctx['tp_upper']:,.0f}]")

    doc.add_heading("一、AI 量化訊號與 RSI 價格試算", level=1)
    doc.add_paragraph(f"未來 5 日超額報酬 > {LABEL_THRESHOLD:.1%} 的模型機率：{ctx['proba']:.2%}（訊號：{ctx['ai_signal']}）")
    doc.add_paragraph(
        f"時序交叉驗證：AUC {ctx['cv_auc_txt']}，準確率 {ctx['cv_acc']:.1%}"
        f"（多數類別基準 {ctx['cv_base']:.1%}），訓練樣本 {ctx['n_train']} 筆"
    )
    doc.add_paragraph(f"RSI 40 試算：{ctx['blue_txt']}")
    doc.add_paragraph(f"RSI 70 試算：{ctx['red_txt']}")
    doc.add_paragraph("註：RSI 價格為技術面反推試算，未經回測驗證其勝率。")

    doc.add_heading("二、基本面估值模型", level=1)
    doc.add_paragraph(f"動態非線性 PE = {ctx['pe_target']:.1f}x，目標價 {ctx['tp_base']:,.2f}")
    doc.add_paragraph(f"線性基準 PE = {ctx['pe_linear']:.1f}x，目標價 {ctx['tp_linear']:,.2f}（使用未調整 EPS）")
    doc.add_paragraph(f"調整後預估 EPS：{ctx['eps_adj']:.2f}（基礎 {ctx['eps_base']}）")

    doc.add_heading("三、財務檢核數據", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    h = table.rows[0].cells
    h[0].text, h[1].text, h[2].text = "指標", "數值", "資料來源"
    for label, val in ctx["q_eps"]:
        r = table.add_row().cells
        r[0].text, r[1].text, r[2].text = f"單季 EPS ({label})", f"{val:.2f}", ctx["q_src"]
    for a, b, c in [
        ("近 4 季 EPS (TTM)", f"{ctx['ttm']:.2f}", ctx["ttm_src"]),
        ("最近年度 EPS", f"{ctx['annual']:.2f}", ctx["annual_src"]),
        ("歷史本益比", f"{ctx['hist_pe']:.1f} 倍", "股價 / TTM EPS"),
        ("遠期本益比", f"{ctx['fwd_pe']:.1f} 倍", "股價 / 調整後預估 EPS"),
    ]:
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
# 6. 側邊欄：標的、EPS、估值參數
# ==========================================
st.sidebar.title("⚙️ 標的與參數設定")

with st.sidebar.form(key="search_form"):
    user_query = st.text_input(
        "輸入公司名稱或代號（如 亞元, 6109, 3105, 2330）", value="亞元"
    ).strip()
    st.form_submit_button("📊 執行 AI 與基本面綜合分析")

if not user_query:
    st.info("請在左側輸入公司名稱或代號。")
    st.stop()

symbol = resolve_symbol(user_query)
if not symbol.endswith((".TW", ".TWO")):
    st.error("量化模型需對照加權指數 (^TWII)，目前僅支援台股（上市 .TW / 上櫃 .TWO）。")
    st.stop()

company_name = get_company_name(symbol)
stock_code = symbol.split(".")[0]
fin = get_financials(symbol)

# ---- EPS：Yahoo 完整就用 Yahoo；不完整可用 Gemini 補查；仍缺則手動 ----
st.sidebar.markdown("---")
st.sidebar.subheader("財報 EPS 設定")

q_yahoo_ok = len(fin["q_eps"]) == 4
ai_all = st.session_state.setdefault("ai_eps", {})
ai_res = ai_all.get(symbol)

if q_yahoo_ok:
    q_final, q_src = fin["q_eps"], "Yahoo Finance"
elif ai_res:
    q_final, q_src = ai_res["quarters"], AI_SRC
else:
    q_final, q_src = fin["q_eps"], "Yahoo Finance（不完整）"

ttm_auto = round(sum(v for _, v in q_final), 2) if len(q_final) == 4 else None

if fin["annual"] is not None:
    annual_auto, annual_src_auto = fin["annual"], "Yahoo Finance"
elif ai_res and ai_res.get("annual_eps") is not None:
    annual_auto, annual_src_auto = ai_res["annual_eps"], AI_SRC
else:
    annual_auto, annual_src_auto = None, None

if not q_yahoo_ok:
    st.sidebar.warning(f"Yahoo 僅提供 {len(fin['q_eps'])} 季 EPS，可用 Gemini 補查。")
    with st.sidebar.expander("🤖 用 Google Gemini 查詢近 4 季 EPS", expanded=ai_res is None):
        gem_key = st.text_input("Gemini API Key", type="password", value=get_default_gemini_key())
        gem_model = st.text_input("Gemini 模型名稱", value="gemini-2.5-flash")
        if st.button("🔎 查詢近 4 季 EPS"):
            if not gem_key:
                st.error("請先輸入 Gemini API Key。")
            else:
                try:
                    with st.spinner("Gemini 正在聯網搜尋財報..."):
                        ai_all[symbol] = fetch_eps_via_gemini(company_name, symbol, gem_key, gem_model)
                    st.rerun()
                except Exception as e:
                    st.error(f"查詢失敗：{e}")
        if ai_res:
            st.success("已取得 Gemini 查詢結果（AI 結果可能有誤，請務必核對）")
            for src in ai_res["sources"]:
                st.caption(src)

fetched_ok = ttm_auto is not None and annual_auto is not None
if fetched_ok:
    st.sidebar.success("已取得 TTM 與年度 EPS")
    use_manual = st.sidebar.checkbox("手動輸入 / 覆寫 EPS", value=False)
else:
    st.sidebar.warning("財報資料不完整，請手動輸入 EPS")
    use_manual = True  # 資料缺漏時強制手動，不再偷偷套用預設值

if use_manual:
    ttm_eps = st.sidebar.number_input("近 4 季 EPS (TTM)", value=float(ttm_auto or 1.0), step=0.1, format="%.2f")
    annual_eps = st.sidebar.number_input("最近年度 EPS", value=float(annual_auto or ttm_eps), step=0.1, format="%.2f")
    ttm_src = annual_src = "手動輸入"
else:
    ttm_eps, annual_eps = ttm_auto, annual_auto
    ttm_src, annual_src = q_src, annual_src_auto

st.sidebar.markdown("---")
st.sidebar.subheader("估值模型變數")
eps_fwd_base = st.sidebar.number_input(
    "基礎預估 Forward EPS", min_value=0.01,
    value=float(max(0.5, round(ttm_eps * 1.1, 2))), step=0.1, format="%.2f",
)
pe_base = st.sidebar.number_input("產業中樞本益比 (PE)", min_value=1.0, value=22.0)
sentiment = st.sidebar.slider("新聞聲量情緒 (0~10)", 0.0, 10.0, 5.0, 0.1)
growth_score = st.sidebar.slider("展望成長評分 (0~10)", 0.0, 10.0, 5.0, 0.1)
risk_val = st.sidebar.slider("下行風險折價 (-PE)", 0.0, 10.0, 1.0, 0.1)

# ==========================================
# 7. 執行量化管線（有快取）
# ==========================================
with st.spinner(f"正在下載 {company_name} 與 NVDA、SOX、加權指數資料，並訓練模型（首次約需數十秒）..."):
    q = run_quant_pipeline(symbol, get_taiwan_time_str("%Y-%m-%d"))

if "error" in q:
    st.error(f"❌ {q['error']}")
    st.stop()

stock = q["stock"]
price = float(stock.iloc[-1])
trade_date = stock.index[-1].strftime("%Y-%m-%d")
change = (price / float(stock.iloc[-2]) - 1) * 100 if len(stock) >= 2 else None

blue_res = price_for_rsi(stock, 40, "drop")
red_res = price_for_rsi(stock, 70, "rise")
blue_txt = zone_text(blue_res, 40, "drop")
red_txt = zone_text(red_res, 70, "rise")

# ==========================================
# 8. 估值核心計算
#    熱點(動能)訊號只放大 PE 端；EPS 成長訊號只調整 EPS 端，避免重複計入。
# ==========================================
hot_triggered = q["beta3_trend_z"] > 0
eps_triggered = ttm_eps > annual_eps > 0
# Beta 趨勢數值很小，先以歷史標準差標準化（z 分數），再限制在 0~3 之間
amp_factor = 1.0 + 0.10 * min(max(q["beta3_trend_z"], 0.0), 3.0)
eps_multiplier = math.pow(ttm_eps / annual_eps, 0.35) if eps_triggered else 1.0
eps_adj = eps_fwd_base * eps_multiplier

alpha_g, beta_g, alpha_s, beta_s = 0.8, 0.22, 0.5, 0.35
growth_exp = alpha_g * amp_factor * (math.exp(beta_g * growth_score) - 1)
sent_diff = sentiment - 5.0
sentiment_exp = alpha_s * amp_factor * math.copysign(1, sent_diff) * (math.exp(beta_s * abs(sent_diff)) - 1)

pe_target_raw = pe_base + sentiment_exp + growth_exp - risk_val
pe_target = min(max(pe_target_raw, pe_base * 0.5), pe_base * 2.0)
pe_capped = pe_target != pe_target_raw

pe_linear = max(pe_base + (sentiment - 5.0) * 0.4 + max(growth_score - 5.0, 0.0) * 0.6 - risk_val, 1.0)
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
    val_rec, val_icon = "估值偏低（可留意買進）", "🟢"
elif upside <= -10:
    val_rec, val_icon = "估值偏高（可留意減碼）", "🔴"
else:
    val_rec, val_icon = "估值中性（持有）", "🟡"

proba = q["proba"]
cv_auc = q["cv_auc"]
model_useful = cv_auc is not None and cv_auc >= 0.55
if proba > 0.55:
    ai_signal = "短線偏多"
elif proba < 0.45:
    ai_signal = "短線偏弱"
else:
    ai_signal = "中性"
cv_auc_txt = f"{cv_auc:.3f}" if cv_auc is not None else "無法計算"

beta3_trend_str = "上升 ↗" if q["beta3_trend"] > 0 else "下降 ↘"
gamma_trend_str = "加速湧入 ↗" if q["gamma_trend"] > 0 else "動能衰退 ↘"

risks = [
    ("總體經濟風險", "利率與匯率波動", "可能造成毛利與評價短期波動"),
    ("市場競爭風險", "同業擴產與需求變化", "需持續追蹤訂單能見度"),
    ("模型風險", "參數多為主觀設定、機器學習樣本有限", "請以多組情境檢視，勿單一依賴目標價或模型機率"),
]

# ==========================================
# 9. 主畫面
# ==========================================
st.title("📈 跨領域專家 AI 投資分析與量化預測")
st.subheader(f"🏢 {company_name}")
st.caption(f"報告生成時間：{get_taiwan_time_str()} (CST)")
st.warning(DISCLAIMER)

change_txt = fmt_pct(change)
c1, c2, c3, c4 = st.columns(4)
c1.metric("最新收盤價", f"${price:,.2f}", f"{trade_date} ({change_txt})")
c2.metric("動態模型目標價", f"${tp_base:,.0f}", f"{upside:.1f}% 潛在空間")
c3.metric("估值評等", val_rec, val_icon)
c4.metric("目標價區間", f"[{tp_lower:,.0f}, {tp_upper:,.0f}]")

ctx = {
    "name": company_name, "price": price, "trade_date": trade_date,
    "change_txt": change_txt, "tp_base": tp_base, "tp_linear": tp_linear,
    "tp_lower": tp_lower, "tp_upper": tp_upper, "val_rec": val_rec,
    "proba": proba, "ai_signal": ai_signal, "cv_auc_txt": cv_auc_txt,
    "cv_acc": q["cv_acc"], "cv_base": q["cv_base"], "n_train": q["n_train"],
    "blue_txt": blue_txt, "red_txt": red_txt,
    "q_eps": q_final, "q_src": q_src, "ttm": ttm_eps, "annual": annual_eps,
    "ttm_src": ttm_src, "annual_src": annual_src,
    "pe_target": pe_target, "pe_linear": pe_linear,
    "eps_adj": eps_adj, "eps_base": eps_fwd_base,
    "hist_pe": hist_pe, "fwd_pe": fwd_pe, "risks": risks,
}

st.download_button(
    "📝 下載 Word 完整投資分析報告",
    data=generate_word_report(ctx),
    file_name=f"{stock_code}_AI_Quantitative_Report.docx",
    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    type="primary",
)
st.divider()

left, right = st.columns(2)

with left:
    st.subheader("一、技術面 RSI 價格試算")
    st.info(f"**🟦 RSI 40 試算（偏弱區，可觀察的低接參考）**\n\n{blue_txt}")
    st.warning(f"**🟥 RSI 70 試算（偏熱區，可觀察的停利參考）**\n\n{red_txt}")
    st.caption("以上為 RSI 反推的技術面參考價，並未經過回測驗證其勝率，不是進出場建議。")
    t1, t2 = st.columns(2)
    t1.metric("近 10 個交易日報酬", fmt_pct(q["ret_05m"]))
    t2.metric("近 60 個交易日報酬", fmt_pct(q["ret_3m"]))

    st.markdown("---")
    st.subheader("二、估值模型對照（動態非線性 vs 線性）")
    st.markdown(
        f"🔥 **動能觸發（放大 PE 端）：** `{'是' if hot_triggered else '否'}` "
        f"（Beta 趨勢 z={q['beta3_trend_z']:.2f}，放大係數 {amp_factor:.3f}x）"
    )
    st.markdown(
        f"📈 **EPS 成長觸發（調整 EPS 端）：** `{'是' if eps_triggered else '否'}` "
        f"（TTM {ttm_eps} vs 年度 {annual_eps}，乘數 {eps_multiplier:.3f}x）"
    )
    st.markdown(f"🚀 **動態非線性：** PE **{pe_target:.1f}x** → 目標價 **${tp_base:,.0f}**")
    if pe_capped:
        st.caption(f"⚠️ 原始 PE {pe_target_raw:.1f}x 超出 0.5~2 倍產業 PE 範圍，已套用上下限。")
    st.markdown(f"📉 **線性基準：** PE **{pe_linear:.1f}x** → 目標價 **${tp_linear:,.0f}**（使用未調整 EPS）")
    st.markdown(f"✨ **調整後 Forward EPS：** **{eps_adj:.2f}**（基礎 {eps_fwd_base}）")

    s1, s2, s3 = st.columns(3)
    s1.metric("悲觀 (Bear)", f"${tp_lower:,.0f}", f"PE: {pe_lower:.1f}x", delta_color="off")
    s2.metric("基準 (Base)", f"${tp_base:,.0f}", f"PE: {pe_target:.1f}x", delta_color="off")
    s3.metric("樂觀 (Bull)", f"${tp_upper:,.0f}", f"PE: {pe_upper:.1f}x", delta_color="off")

with right:
    st.subheader("三、財務檢核與 AI 預測指標")
    f1, f2, f3 = st.columns(3)
    f1.metric("TTM EPS", f"{ttm_eps:.2f}", ttm_src, delta_color="off")
    f2.metric("年度 EPS", f"{annual_eps:.2f}", annual_src, delta_color="off")
    f3.metric("遠期 P/E", f"{fwd_pe:.1f}x")

    st.markdown("**AI 模型核心指標：**")
    m1, m2, m3 = st.columns(3)
    m1.metric("未來 5 日勝率", f"{proba:.2%}", ai_signal, delta_color="off")
    m2.metric("AI 晶片純度趨勢", beta3_trend_str, f"{q['beta3_now']:.4f}")
    m3.metric("資金擁擠度", gamma_trend_str, f"{q['gamma_now']:.4f}", delta_color="inverse")

    st.markdown("**模型時序交叉驗證（樣本外）：**")
    v1, v2, v3 = st.columns(3)
    v1.metric("平均 AUC", cv_auc_txt)
    v2.metric("平均準確率", f"{q['cv_acc']:.1%}")
    v3.metric("多數類別基準", f"{q['cv_base']:.1%}")
    if model_useful:
        st.caption(f"訓練樣本 {q['n_train']} 筆。AUC 高於 0.55，但仍屬弱訊號，請搭配其他資訊判斷。")
    else:
        st.caption(f"訓練樣本 {q['n_train']} 筆。AUC 未明顯高於 0.5，代表模型在樣本外幾乎沒有預測力，上方勝率僅供參考。")

    st.markdown("**近 4 季單季 EPS：**")
    if q_final:
        st.dataframe(pd.DataFrame(q_final, columns=["財報季度", "單季 EPS (元)"]), hide_index=True)
        st.caption(f"資料來源：{q_src}")
    else:
        st.caption("Yahoo Finance 未提供單季 EPS，可在側邊欄用 Gemini 補查或手動輸入。")

    st.subheader("四、風險提示")
    st.dataframe(pd.DataFrame(risks, columns=["風險維度", "關鍵影響因子", "影響評估"]), hide_index=True)

# ==========================================
# 10. 圖表：Beta / Gamma 歷史走勢與 SHAP
# ==========================================
st.markdown("---")
st.markdown("### 📊 歷史走勢（描述性圖表，非回測）與 SHAP 決策邏輯")

plot_beta3, plot_gamma = q["beta3"], q["gamma"]
min_date = plot_beta3.idxmin()
max_date = plot_beta3.loc[min_date:].idxmax()
period_returns = stock.loc[min_date:max_date].pct_change().dropna()

fc1, fc2 = st.columns(2)

with fc1:
    fig1, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 10), sharex=True)
    ax1.plot(plot_gamma.index, plot_gamma, color="purple", label="Gamma (Crowding)")
    ax1.axhline(0, color="red", linestyle="--")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    ax2.plot(plot_beta3.index, plot_beta3, color="forestgreen", label="Beta_3 (Pure AI Shock)")
    if not period_returns.empty:
        ax2.axvspan(period_returns.index[0], period_returns.index[-1], color="yellow", alpha=0.2,
                    label="Beta_3 low -> subsequent high")
    ax2.axhline(0, color="red", linestyle="--")
    ax2.legend(loc="upper left")
    ax2.grid(True, alpha=0.3)

    if not period_returns.empty:
        cum = (1 + period_returns).cumprod() - 1
        ax3.plot(cum.index, cum, color="darkred", label="Cumulative Return in highlighted period")
        ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax3.legend(loc="upper left")
    ax3.grid(True, alpha=0.3)
    fig1.suptitle(f"[{symbol}] Rolling Beta/Gamma (descriptive, hindsight-selected period)", fontsize=13)
    fig1.tight_layout()
    st.pyplot(fig1)
    plt.close(fig1)
    st.caption("黃色區間是事後挑選的 Beta 低點到後續高點，僅作描述，不能視為策略回測。")

with fc2:
    fig2 = plt.figure(figsize=(10, 8))
    shap.summary_plot(q["shap_values"], q["X_shap"], feature_names=FEATURES, show=False)
    plt.title(f"[{symbol}] SHAP (last 250 training rows, in-sample)", fontsize=13)
    plt.tight_layout()
    st.pyplot(fig2)
    plt.close(fig2)
