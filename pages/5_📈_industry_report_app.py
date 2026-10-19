"""
產業分析報告 1.0 版 (台股策略 1.4 核心) - Streamlit 版

執行方式:
    pip install streamlit yfinance pandas pytz
    streamlit run industry_report_app.py
"""

import logging
import time
from datetime import datetime, timedelta

import pandas as pd
import pytz
import streamlit as st
import yfinance as yf

# =======================================================
# 基本設定與日誌
# =======================================================
TZ = pytz.timezone("Asia/Taipei")
LOG_FILE = "fetch_errors.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("industry_report")

DEFAULT_MACRO_NEWS = "近期出口數據強勁，美國聯準會降息預期明朗，科技業庫存健康。"

SECTORS_INFO = [
    {"subsector": "先進製程與晶圓代工", "ticker": "2330.TW", "name": "台積電"},
    {"subsector": "先進製程與晶圓代工", "ticker": "2303.TW", "name": "聯電"},
    {"subsector": "先進製程與晶圓代工", "ticker": "5347.TWO", "name": "世界先進"},
    {"subsector": "AI伺服器與系統整合", "ticker": "6669.TW", "name": "緯穎"},
    {"subsector": "AI伺服器與系統整合", "ticker": "2382.TW", "name": "廣達"},
    {"subsector": "AI伺服器與系統整合", "ticker": "2376.TW", "name": "技嘉"},
    {"subsector": "先進封裝與設備", "ticker": "3131.TWO", "name": "弘塑"},
    {"subsector": "先進封裝與設備", "ticker": "6640.TWO", "name": "均華"},
    {"subsector": "先進封裝與設備", "ticker": "6187.TWO", "name": "萬潤"},
    {"subsector": "散熱與水冷模組", "ticker": "3653.TW", "name": "健策"},
    {"subsector": "散熱與水冷模組", "ticker": "3017.TW", "name": "奇鋐"},
    {"subsector": "散熱與水冷模組", "ticker": "3324.TWO", "name": "雙鴻"},
    {"subsector": "高速傳輸與 IC 設計", "ticker": "5274.TWO", "name": "信驊"},
    {"subsector": "高速傳輸與 IC 設計", "ticker": "3661.TW", "name": "世芯-KY"},
    {"subsector": "高速傳輸與 IC 設計", "ticker": "5269.TW", "name": "祥碩"},
]


# =======================================================
# 工具函式
# =======================================================
def with_retry(fn, label, retries=3, base_delay=1.5):
    """執行 fn，失敗或回傳空值時以指數退避重試；最終失敗則拋出例外。"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            result = fn()
            if result is None or (hasattr(result, "empty") and result.empty):
                raise ValueError("回傳空資料")
            if isinstance(result, dict) and not result:
                raise ValueError("回傳空字典")
            return result
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("%s 第 %d/%d 次失敗: %s", label, attempt, retries, e)
            if attempt < retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
    raise RuntimeError(f"{label} 重試 {retries} 次仍失敗: {last_err}")


def get_latest_trade_cutoff(now=None):
    """台股 13:30 收盤；未收盤時不使用當日（可能為盤中未完成的 K 棒）。"""
    now = now or datetime.now(TZ)
    if now.hour < 13 or (now.hour == 13 and now.minute < 30):
        return (now - timedelta(days=1)).date()
    return now.date()


def to_float(x):
    try:
        if x is None or pd.isna(x):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def calc_dividend_yield_pct(info, price):
    """
    殖利率 (%)。yfinance 的 dividendYield 單位在不同版本不一致 (小數或百分比)，
    因此優先用「每股股利 / 股價」自行計算，再退而求其次。
    """
    rate = to_float(info.get("dividendRate"))
    if rate and price:
        return rate / price * 100
    trailing = to_float(info.get("trailingAnnualDividendYield"))  # 固定為小數
    if trailing:
        return trailing * 100
    dy = to_float(info.get("dividendYield"))
    if dy:
        return dy if dy >= 1 else dy * 100  # 啟發式：>=1 視為已是百分比
    return None


def get_ttm_eps(stock, info):
    """近四季 EPS 合計；取不到 4 季單季資料時退回 trailingEps。回傳 (值, 來源說明)。"""
    try:
        income = stock.quarterly_income_stmt
        if income is not None and not income.empty:
            for row in ("Diluted EPS", "Basic EPS"):
                if row in income.index:
                    s = income.loc[row].dropna().sort_index(ascending=False)
                    if len(s) >= 4:
                        return float(s.iloc[:4].sum()), "四季單季加總"
    except Exception as e:  # noqa: BLE001
        logger.warning("%s 單季 EPS 取得失敗: %s", stock.ticker, e)
    eps = to_float(info.get("trailingEps"))
    if eps is not None:
        return eps, "trailingEps(退回)"
    return None, "N/A"


def get_revenue_yoy(stock, info):
    """最新單季營收年增率 (%)；找不到去年同季時退回 info['revenueGrowth']。"""
    try:
        income = stock.quarterly_income_stmt
        if income is not None and not income.empty and "Total Revenue" in income.index:
            s = income.loc["Total Revenue"].dropna().sort_index(ascending=False)
            if len(s) >= 2:
                latest_date, latest_val = s.index[0], float(s.iloc[0])
                target = latest_date - timedelta(days=365)
                diffs = [(abs((d - target).days), d) for d in s.index[1:]]
                gap, match = min(diffs, key=lambda t: t[0])
                prev_val = float(s.loc[match])
                if gap <= 25 and prev_val > 0:
                    return (latest_val / prev_val - 1) * 100, "單季同期比"
    except Exception as e:  # noqa: BLE001
        logger.warning("%s 營收年增率計算失敗: %s", stock.ticker, e)
    g = to_float(info.get("revenueGrowth"))
    if g is not None:
        return g * 100, "revenueGrowth(退回)"
    return None, "N/A"


def fetch_one(item, cutoff_date):
    """擷取單一標的，回傳 (資料列 dict, 警告清單)。完全失敗則拋出例外。"""
    ticker, name, subsector = item["ticker"], item["name"], item["subsector"]
    warnings = []
    stock = yf.Ticker(ticker)

    # 股價 (必要)
    start = cutoff_date - timedelta(days=60)
    end = cutoff_date + timedelta(days=1)

    def _history():
        return stock.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            auto_adjust=False,
        )

    df = with_retry(_history, f"{ticker} 股價")
    if "Close" not in df.columns:
        raise RuntimeError(f"{ticker} 股價資料缺少 Close 欄位")
    df = df.dropna(subset=["Close"])
    df.index = pd.to_datetime(df.index).date
    df = df[df.index <= cutoff_date]
    if df.empty:
        raise RuntimeError(f"{ticker} 無截至 {cutoff_date} 的股價")

    latest_date = df.index[-1]
    price_latest = float(df["Close"].iloc[-1])
    past_df = df[df.index <= latest_date - timedelta(days=14)]
    if past_df.empty:
        past_date, price_past = df.index[0], float(df["Close"].iloc[0])
        warnings.append(f"{name}: 歷史資料不足 14 天，改用 {past_date} 作為基準")
    else:
        past_date, price_past = past_df.index[-1], float(past_df["Close"].iloc[-1])
    return_rate = (price_latest - price_past) / price_past * 100

    # 基本面 (非必要，失敗僅警告)
    try:
        info = with_retry(lambda: stock.info, f"{ticker} info")
    except Exception as e:  # noqa: BLE001
        logger.error("%s", e)
        warnings.append(f"{name}: 基本面資料取得失敗 ({e})")
        info = {}

    pe = to_float(info.get("trailingPE"))
    pb = to_float(info.get("priceToBook"))
    dy = calc_dividend_yield_pct(info, price_latest)
    eps_ttm, eps_src = get_ttm_eps(stock, info)
    rev_yoy, rev_src = get_revenue_yoy(stock, info)
    if eps_src != "四季單季加總":
        warnings.append(f"{name}: EPS 來源為 {eps_src}")
    if rev_src != "單季同期比":
        warnings.append(f"{name}: 營收年增率來源為 {rev_src}")

    row = {
        "次產業": subsector,
        "公司名稱": name,
        "代號": ticker,
        "本益比": pe,
        "股價淨值比": pb,
        "殖利率(%)": dy,
        "近四季EPS合計": eps_ttm,
        "EPS來源": eps_src,
        "營收年增率(%)": rev_yoy,
        "營收來源": rev_src,
        "最新股價": price_latest,
        "最新股價日期": latest_date,
        "二周前股價": price_past,
        "二周前日期": past_date,
        "近2周報酬率(%)": return_rate,
    }
    return row, warnings


def add_dual_rank(df):
    """次產業內以「近四季EPS合計」與「營收年增率」排名，加總越小越前面。"""
    df = df.copy()
    df["EPS排名"] = df.groupby("次產業")["近四季EPS合計"].rank(ascending=False, method="min")
    df["營收年增率排名"] = df.groupby("次產業")["營收年增率(%)"].rank(ascending=False, method="min")
    df["雙指標排名總和"] = df["EPS排名"] + df["營收年增率排名"]
    df["雙指標名次"] = df.groupby("次產業")["雙指標排名總和"].rank(method="min")
    return df.sort_values(["次產業", "雙指標名次"]).reset_index(drop=True)


def fetch_all(progress_cb=None, sleep_between=0.6):
    """擷取全部標的。回傳 (DataFrame, 失敗清單, 警告清單)。"""
    cutoff = get_latest_trade_cutoff()
    rows, failures, warns = [], [], []
    total = len(SECTORS_INFO)
    for i, item in enumerate(SECTORS_INFO, start=1):
        label = f"{item['name']} ({item['ticker']})"
        if progress_cb:
            progress_cb(i - 1, total, f"正在擷取 {label}")
        try:
            row, w = fetch_one(item, cutoff)
            rows.append(row)
            warns.extend(w)
            logger.info("%s 擷取成功", label)
        except Exception as e:  # noqa: BLE001
            logger.error("%s 擷取失敗: %s", label, e)
            failures.append({"公司": label, "原因": str(e)})
        time.sleep(sleep_between)  # 降低被 Yahoo 限流的機率
    if progress_cb:
        progress_cb(total, total, "擷取完成")
    df = pd.DataFrame(rows)
    if not df.empty:
        df = add_dual_rank(df)
    return df, failures, warns


# =======================================================
# 顯示與提示詞
# =======================================================
def fmt(v, suffix="", nd=2):
    return "N/A" if v is None or pd.isna(v) else f"{round(float(v), nd)}{suffix}"


def build_stock_table_text(df):
    lines = []
    for _, r in df.iterrows():
        lines.append(
            f"{r['次產業']} | {r['公司名稱']} | 本益比 {fmt(r['本益比'], ' 倍')} | "
            f"股價淨值比 {fmt(r['股價淨值比'], ' 倍')} | 殖利率 {fmt(r['殖利率(%)'], '%')} | "
            f"近四季EPS合計 {fmt(r['近四季EPS合計'])} | 營收年增率 {fmt(r['營收年增率(%)'], '%')} | "
            f"雙指標名次 {int(r['雙指標名次'])} | "
            f"最新股價(日期) TWD {fmt(r['最新股價'])} ({r['最新股價日期']:%Y/%m/%d}) | "
            f"二周前股價(日期) TWD {fmt(r['二周前股價'])} ({r['二周前日期']:%Y/%m/%d}) | "
            f"近2周報酬率 {fmt(r['近2周報酬率(%)'], '%')}"
        )
    return "\n".join(lines)


def build_prompt(macro_news, df, failures):
    stock_data_str = build_stock_table_text(df)
    missing_note = ""
    if failures:
        names = "、".join(f["公司"] for f in failures)
        missing_note = f"\n注意：下列公司數據擷取失敗，不得自行臆測其數值：{names}\n"

    return f"""
你是一位擁有台股熱門次產業推薦經驗的金融與產業專家。
以第三人稱陳述。
不要提及你的專業資歷。

作為背景參考的總體經濟數據：
{macro_news}

請根據近期的財務數據與新聞標題，給出一個分數（1 到 100 分），以反映台灣股市中熱門5種不同次產業之在下個月的潛在投資價值。輸出 Score: X 。

首先，撰寫一份關於該熱門產業狀況的簡短投資報告。
內容須包含近期新聞、未來產值狀況、年度複合成長率、5種不同次產業。

在5種不同次產業之後，請給出一個分數（1 到 100 分），以反映熱門5種不同次產業之在下個月的潛在投資價值。輸出 Score: X 。

在每個次產業簡介之後，請標註最近3個月的熱點，並嚴格進行以下計算與格式輸出：
- 近1個月利多()/利空()熱點，括號為數字；
- 近半個月利多()/利空()熱點，括號為數字；
- 近1星期利多()/利空()熱點，括號為數字；
- 前期多空增減()(=[近半個月(利多-利空) - 近1個月(利多-利空)])；
- 近期多空增減()(=[近1星期(利多-利空) - 近半個月(利多-利空)])；
- 二階動能加速度()(=[近期多空增減 - 前期多空增減])。

接著，列出「近2周報酬領先公司估值（雙指標篩選：最近四季EPS最高且營收年增率最高）」，包含每次產業至少3家領先公司。
雙指標名次的算法：在同一次產業內，將「近四季EPS合計」與「營收年增率」分別排名後加總，數字越小代表越領先，請以下表的雙指標名次為準，不得自行重算或改用其他數據。
必須使用以下最新真實數據，包含本益比、股價淨值比、殖利率、近四季EPS合計、營收年增率、最新股價(日期)、二周前股價(日期)、近2周報酬率(%)：

{stock_data_str}
{missing_note}
隨後，撰寫影響該次產業的總體經濟展望等章節。
分析總經展望時，請一併考量與台股高度相關的因素，例如：出口與電子業景氣循環、新台幣匯率、半導體與科技供應鏈、美國聯準會利率政策，以及地緣政治情勢。

不要使用『提供的』這個詞，改用『近期』或『最新』。
不要直接對投資人喊話，也不要建議任何操作。
以『投資報告：』開頭。
最後，另起一行，輸出 Score: X。
"""


# =======================================================
# Streamlit 介面
# =======================================================
st.set_page_config(page_title="產業分析報告 1.0", page_icon="📊", layout="wide")
st.title("📊 產業分析報告 1.0 版（台股策略 1.4 核心）")
st.caption("擷取 15 檔台股雙指標數據（含 EPS 與營收年增率），自動組合標準化提示詞。")

with st.sidebar:
    st.header("設定")
    macro_news = st.text_area("總體經濟背景", value=DEFAULT_MACRO_NEWS, height=140)
    run = st.button("🔄 重新擷取數據並生成提示詞", type="primary", use_container_width=True)
    st.caption(f"錯誤日誌檔：{LOG_FILE}")

if run or "df" not in st.session_state:
    bar = st.progress(0.0, text="準備中…")

    def _cb(done, total, msg):
        bar.progress(done / total, text=msg)

    df, failures, warns = fetch_all(progress_cb=_cb)
    bar.empty()
    st.session_state.update(
        df=df,
        failures=failures,
        warns=warns,
        fetched_at=datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S"),
    )

df = st.session_state["df"]
failures = st.session_state["failures"]
warns = st.session_state["warns"]

st.write(
    f"擷取時間：**{st.session_state['fetched_at']}**　｜　成功 **{len(df)}** / {len(SECTORS_INFO)} 檔"
)

if failures:
    st.error(f"有 {len(failures)} 檔擷取失敗，提示詞中已註明這些公司不得臆測數值。")
if df.empty:
    st.stop()

tab_data, tab_prompt, tab_log = st.tabs(["📈 數據", "📝 提示詞", "⚠️ 失敗與警告"])

with tab_data:
    show_cols = [
        "次產業", "公司名稱", "本益比", "股價淨值比", "殖利率(%)",
        "近四季EPS合計", "營收年增率(%)", "雙指標名次",
        "最新股價", "最新股價日期", "二周前股價", "二周前日期", "近2周報酬率(%)",
    ]
    st.dataframe(
        df[show_cols].round(2),
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        "下載 CSV",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name="stock_data.csv",
        mime="text/csv",
    )

with tab_prompt:
    prompt_text = build_prompt(macro_news, df, failures)
    st.code(prompt_text, language="text")  # 右上角內建複製按鈕
    st.download_button(
        "下載提示詞 TXT",
        prompt_text.encode("utf-8"),
        file_name="prompt_payload.txt",
        mime="text/plain",
    )

with tab_log:
    st.subheader("擷取失敗")
    if failures:
        st.dataframe(pd.DataFrame(failures), use_container_width=True, hide_index=True)
    else:
        st.success("全部標的擷取成功。")
    st.subheader("警告（資料來源退回、基準日調整等）")
    if warns:
        for w in warns:
            st.write(f"- {w}")
    else:
        st.write("無。")
