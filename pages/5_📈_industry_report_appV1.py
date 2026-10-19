# -*- coding: utf-8 -*-
"""
台股產業投資策略 1.5 版（完整整合版，單一檔案）

安裝：
    pip install streamlit yfinance pandas pytz feedparser python-docx
執行：
    streamlit run app.py
"""

import io
import json
import logging
import math
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta

import feedparser
import numpy as np
import pandas as pd
import pytz
import streamlit as st
import yfinance as yf
from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

# ==========================================================
# 基本設定與日誌
# ==========================================================
TZ = pytz.timezone("Asia/Taipei")
LOG_FILE = "fetch_errors.log"

logger = logging.getLogger("tw_strategy")
if not logger.handlers:  # Streamlit 每次互動都會重跑腳本，避免重複加 handler
    logger.setLevel(logging.INFO)
    _h = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_h)

DEFAULT_MACRO_TEXT = (
    "近期出口數據強勁，台灣半導體與 AI 資通訊產品外銷維持擴張。新台幣匯率處於平穩區間，"
    "美國聯準會利率政策逐漸明朗化，整體科技供應鏈具備實質基本面支撐。"
)

# ==========================================================
# 公司字典：次產業 / 供應鏈位置 / 細分角色
# 供應鏈位置：上游=IC設計與設備、中游=製造封測與零組件、下游=系統組裝、終端需求=國際客戶
# ==========================================================
COMPANIES = [
    {"name": "台積電", "ticker": "2330.TW", "aliases": ["台積電", "TSMC"],
     "subsector": "先進製程與晶圓代工", "chain": "中游", "role": "晶圓代工・先進製程"},
    {"name": "聯電", "ticker": "2303.TW", "aliases": ["聯電"],
     "subsector": "先進製程與晶圓代工", "chain": "中游", "role": "晶圓代工・成熟製程"},
    {"name": "世界先進", "ticker": "5347.TWO", "aliases": ["世界先進"],
     "subsector": "先進製程與晶圓代工", "chain": "中游", "role": "晶圓代工・特殊製程"},
    {"name": "緯穎", "ticker": "6669.TW", "aliases": ["緯穎"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "雲端資料中心伺服器"},
    {"name": "廣達", "ticker": "2382.TW", "aliases": ["廣達"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "伺服器 ODM 組裝"},
    {"name": "技嘉", "ticker": "2376.TW", "aliases": ["技嘉"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "伺服器・板卡與系統"},
    {"name": "鴻海", "ticker": "2317.TW", "aliases": ["鴻海", "鴻海精密"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "伺服器 / 機櫃組裝"},
    {"name": "緯創", "ticker": "3231.TW", "aliases": ["緯創"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "伺服器 ODM 組裝"},
    {"name": "弘塑", "ticker": "3131.TWO", "aliases": ["弘塑"],
     "subsector": "先進封裝與設備", "chain": "上游", "role": "封裝設備・濕製程"},
    {"name": "均華", "ticker": "6640.TWO", "aliases": ["均華"],
     "subsector": "先進封裝與設備", "chain": "上游", "role": "封裝設備・黏晶 / 挑揀"},
    {"name": "萬潤", "ticker": "6187.TWO", "aliases": ["萬潤"],
     "subsector": "先進封裝與設備", "chain": "上游", "role": "封裝設備・自動化"},
    {"name": "日月光投控", "ticker": "3711.TW", "aliases": ["日月光"],
     "subsector": "先進封裝與設備", "chain": "中游", "role": "封裝測試代工"},
    {"name": "欣興", "ticker": "3037.TW", "aliases": ["欣興"],
     "subsector": "先進封裝與設備", "chain": "中游", "role": "載板 / PCB"},
    {"name": "健策", "ticker": "3653.TW", "aliases": ["健策"],
     "subsector": "散熱與水冷模組", "chain": "中游", "role": "均熱片・散熱零組件"},
    {"name": "奇鋐", "ticker": "3017.TW", "aliases": ["奇鋐"],
     "subsector": "散熱與水冷模組", "chain": "中游", "role": "風扇與散熱模組"},
    {"name": "雙鴻", "ticker": "3324.TWO", "aliases": ["雙鴻"],
     "subsector": "散熱與水冷模組", "chain": "中游", "role": "水冷 / 散熱模組"},
    {"name": "信驊", "ticker": "5274.TWO", "aliases": ["信驊"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・BMC 伺服器管理晶片"},
    {"name": "世芯-KY", "ticker": "3661.TW", "aliases": ["世芯"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・ASIC 設計服務"},
    {"name": "祥碩", "ticker": "5269.TW", "aliases": ["祥碩"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・高速傳輸介面"},
    {"name": "創意", "ticker": "3443.TW", "aliases": ["創意電子"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・ASIC 設計服務"},
    {"name": "聯發科", "ticker": "2454.TW", "aliases": ["聯發科"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・終端 SoC"},
]

FOREIGN = [
    {"name": "輝達", "aliases": ["輝達", "NVIDIA", "Nvidia"], "role": "AI 晶片客戶 / 需求領頭羊"},
    {"name": "超微", "aliases": ["超微", "AMD"], "role": "AI 晶片客戶"},
    {"name": "博通", "aliases": ["博通", "Broadcom"], "role": "ASIC / 網通晶片客戶"},
    {"name": "蘋果", "aliases": ["蘋果", "Apple"], "role": "終端消費性客戶"},
    {"name": "微軟", "aliases": ["微軟", "Microsoft"], "role": "雲端資本支出"},
    {"name": "Google", "aliases": ["Google", "谷歌"], "role": "雲端資本支出"},
    {"name": "亞馬遜", "aliases": ["亞馬遜", "AWS", "Amazon"], "role": "雲端資本支出"},
    {"name": "Meta", "aliases": ["Meta"], "role": "雲端資本支出"},
]

# 預設勾選追蹤的 15 檔（其餘為可選的擴充名單）
DEFAULT_TRACKED = {
    "2330.TW", "2303.TW", "5347.TWO", "6669.TW", "2382.TW", "2376.TW", "3131.TWO", "6640.TWO",
    "6187.TWO", "3653.TW", "3017.TW", "3324.TWO", "5274.TWO", "3661.TW", "5269.TW",
}

SECTOR_ORDER = list(dict.fromkeys(c["subsector"] for c in COMPANIES))
CHAIN_ORDER = ["上游", "中游", "下游", "終端需求"]
FOREIGN_SECTOR = "（國際客戶 / 終端需求）"

ENTRIES = [
    {"公司": c["name"], "代號": c["ticker"], "次產業": c["subsector"],
     "供應鏈位置": c["chain"], "細分角色": c["role"], "aliases": c["aliases"]}
    for c in COMPANIES
] + [
    {"公司": f["name"], "代號": "", "次產業": FOREIGN_SECTOR,
     "供應鏈位置": "終端需求", "細分角色": f["role"], "aliases": f["aliases"]}
    for f in FOREIGN
]

# ==========================================================
# 通用工具
# ==========================================================
def with_retry(fn, label, retries=3, base_delay=1.5):
    """失敗或回傳空資料時以指數退避重試；最終失敗拋出例外。"""
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            result = fn()
            empty = (
                result is None
                or (hasattr(result, "empty") and result.empty)
                or (isinstance(result, (list, dict)) and len(result) == 0)
            )
            if empty:
                raise ValueError("回傳空資料")
            return result
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("%s 第 %d/%d 次失敗: %s", label, attempt, retries, e)
            if attempt < retries:
                time.sleep(base_delay * (2 ** (attempt - 1)))
    raise RuntimeError(f"{label} 重試 {retries} 次仍失敗: {last_err}")


def to_float(x):
    try:
        if x is None or pd.isna(x):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def fmt(v, suffix="", nd=2):
    return "N/A" if v is None or pd.isna(v) else f"{round(float(v), nd)}{suffix}"


# ==========================================================
# 階段一：新聞抓取、評分、標的歸類、二階動能
# ==========================================================
GENERAL_QUERY = "台股 (半導體 OR 伺服器 OR 散熱 OR 封裝 OR IC設計)"

POSITIVE_KW = {"滿載": 20, "大單": 18, "創高": 15, "升級": 15, "受惠": 12, "強勁": 15,
               "爆發": 20, "看增": 10, "放量": 15, "止跌": 8, "回升": 8, "上修": 15}
NEGATIVE_KW = {"下修": -20, "衰退": -18, "承壓": -15, "遞延": -12, "缺料": -15, "降評": -15,
               "走弱": -10, "保守": -10, "重挫": -15, "大跌": -15, "暴跌": -20, "跌破": -12,
               "下滑": -10}


def score_title(title):
    """關鍵字加權評分（已移除隨機擾動，結果可重現）。"""
    score = 50
    for kw, w in POSITIVE_KW.items():
        if kw in title:
            score += w
    for kw, w in NEGATIVE_KW.items():
        if kw in title:
            score += w
    return max(1, min(100, score))


def _fetch_feed(query, days):
    enc = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={enc}+when:{days}d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    return list(feedparser.parse(url).entries)


def _entry_to_row(entry):
    title = entry.get("title", "")
    if " - " in title:
        clean, source = title.rsplit(" - ", 1)
    else:
        clean, source = title, "財經媒體"
    p = entry.get("published_parsed")
    pub = datetime(*p[:6], tzinfo=pytz.utc).astimezone(TZ) if p else datetime.now(TZ)
    return {
        "新聞標題": clean,
        "來源與日期": f"({source}, {pub:%Y/%m/%d})",
        "發布時間": pub,
        "分數": score_title(clean),
    }


def fetch_news_all(days=30, per_sector=True):
    """抓取通用查詢 + （可選）各次產業公司名稱查詢，合併去重。回傳 (DataFrame, 失敗查詢清單)。"""
    queries = [GENERAL_QUERY]
    if per_sector:
        for sector in SECTOR_ORDER:
            names = [c["aliases"][0] for c in COMPANIES if c["subsector"] == sector]
            queries.append("(" + " OR ".join(names) + ")")

    rows, failed = [], []
    for q in queries:
        try:
            entries = with_retry(lambda q=q: _fetch_feed(q, days), f"新聞查詢 {q[:24]}")
            rows.extend(_entry_to_row(e) for e in entries)
            logger.info("新聞查詢成功: %s (%d 則)", q[:40], len(entries))
        except Exception as e:  # noqa: BLE001
            logger.error("%s", e)
            failed.append({"查詢": q, "原因": str(e)})
        time.sleep(0.5)

    df = pd.DataFrame(rows)
    if df.empty:
        return df, failed
    df = df.drop_duplicates(subset="新聞標題").sort_values("發布時間", ascending=False)
    return df.reset_index(drop=True), failed


def match_companies(title):
    return [e for e in ENTRIES if any(a in title for a in e["aliases"])]


def sentiment_label(score, bull, bear):
    if score >= bull:
        return "利多"
    if score <= bear:
        return "利空"
    return "中性"


def classify_news(df_news, bull=60, bear=40):
    """回傳 (tagged 明細, by_company 公司彙總, by_sector 次產業×供應鏈位置矩陣, untagged 未歸類新聞)。"""
    rows, untagged_rows = [], []
    for _, r in df_news.iterrows():
        hits = match_companies(r["新聞標題"])
        if not hits:
            untagged_rows.append(r.to_dict())
            continue
        label = sentiment_label(r["分數"], bull, bear)
        value = {"利多": 1, "利空": -1, "中性": 0}[label]
        for h in hits:
            rows.append({
                "新聞標題": r["新聞標題"], "來源與日期": r.get("來源與日期", ""),
                "發布時間": r.get("發布時間"), "榜單": r.get("榜單", ""),
                "分數": r["分數"], "情緒": label, "多空值": value,
                "公司": h["公司"], "代號": h["代號"], "次產業": h["次產業"],
                "供應鏈位置": h["供應鏈位置"], "細分角色": h["細分角色"],
            })

    tagged = pd.DataFrame(rows)
    untagged = pd.DataFrame(untagged_rows)
    if tagged.empty:
        return tagged, pd.DataFrame(), pd.DataFrame(), untagged

    by_company = (
        tagged.groupby(["次產業", "供應鏈位置", "細分角色", "公司", "代號"])
        .agg(新聞數=("新聞標題", "count"),
             利多=("情緒", lambda s: int((s == "利多").sum())),
             利空=("情緒", lambda s: int((s == "利空").sum())),
             平均分數=("分數", "mean"))
        .reset_index()
    )
    by_company["淨多空"] = by_company["利多"] - by_company["利空"]
    by_company["平均分數"] = by_company["平均分數"].round(1)
    by_company = by_company.sort_values(["次產業", "供應鏈位置", "淨多空"], ascending=[True, True, False])

    by_sector = tagged.pivot_table(index="次產業", columns="供應鏈位置", values="多空值",
                                   aggfunc="sum", fill_value=0)
    by_sector = by_sector.reindex(columns=[c for c in CHAIN_ORDER if c in by_sector.columns])
    by_sector["合計"] = by_sector.sum(axis=1)
    return tagged, by_company.reset_index(drop=True), by_sector, untagged


def compute_momentum(tagged, now):
    """依次產業統計近1個月/半個月/1星期的利多利空則數，並套用二階動能加速度公式。"""
    out = []
    for sector in SECTOR_ORDER:
        if tagged.empty:
            sub = tagged
        else:
            sub = tagged[tagged["次產業"] == sector].drop_duplicates("新聞標題")

        def cnt(days):
            if sub.empty:
                return 0, 0
            w = sub[sub["發布時間"] >= now - timedelta(days=days)]
            return int((w["情緒"] == "利多").sum()), int((w["情緒"] == "利空").sum())

        m_pos, m_neg = cnt(30)
        h_pos, h_neg = cnt(15)
        w_pos, w_neg = cnt(7)
        prev = (h_pos - h_neg) - (m_pos - m_neg)
        curr = (w_pos - w_neg) - (h_pos - h_neg)
        acc = curr - prev
        out.append({
            "次產業": sector,
            "近1個月": f"利多({m_pos})/利空({m_neg})",
            "近半個月": f"利多({h_pos})/利空({h_neg})",
            "近1星期": f"利多({w_pos})/利空({w_neg})",
            "前期多空增減": f"({prev})",
            "近期多空增減": f"({curr})",
            "二階動能加速度": f"(+{acc})" if acc > 0 else f"({acc})",
            "_m_pos": m_pos, "_m_neg": m_neg, "_acc": acc,
        })
    return pd.DataFrame(out)


# ==========================================================
# 階段三：財務數據擷取（重試 + 日誌 + EPS + 營收年增率）
# ==========================================================
def get_latest_trade_cutoff(now=None):
    now = now or datetime.now(TZ)
    if now.hour < 13 or (now.hour == 13 and now.minute < 30):
        return (now - timedelta(days=1)).date()
    return now.date()


def calc_dividend_yield_pct(info, price):
    """殖利率(%)。優先用 每股股利/股價 自行計算，避免 yfinance 單位不一致。"""
    rate = to_float(info.get("dividendRate"))
    if rate and price:
        return rate / price * 100
    trailing = to_float(info.get("trailingAnnualDividendYield"))
    if trailing:
        return trailing * 100
    dy = to_float(info.get("dividendYield"))
    if dy:
        return dy if dy >= 1 else dy * 100
    return None


def dividend_yield_from_history(stock, price, as_of):
    """備援：近 12 個月實際配息合計 / 最新股價。"""
    try:
        div = stock.dividends
        if div is not None and not div.empty and price:
            d = div.copy()
            d.index = pd.to_datetime(d.index).tz_localize(None)
            start = pd.Timestamp(as_of) - pd.Timedelta(days=365)
            total = float(d[d.index > start].sum())
            if total > 0:
                return total / price * 100
    except Exception as e:  # noqa: BLE001
        logger.warning("%s 配息歷史取得失敗: %s", stock.ticker, e)
    return None


def book_value_per_share(stock, info):
    """備援：每股淨值。優先 info.bookValue，其次資產負債表（股東權益 / 股數）。"""
    bv = to_float(info.get("bookValue"))
    if bv and bv > 0:
        return bv, "info.bookValue"
    try:
        bs = stock.quarterly_balance_sheet
        if bs is not None and not bs.empty:
            eq_row = next((r for r in ("Stockholders Equity", "Common Stock Equity",
                                       "Total Equity Gross Minority Interest") if r in bs.index), None)
            sh_row = next((r for r in ("Ordinary Shares Number", "Share Issued") if r in bs.index), None)
            if eq_row and sh_row:
                eq = bs.loc[eq_row].dropna()
                sh = bs.loc[sh_row].dropna()
                common = eq.index.intersection(sh.index)
                if len(common):
                    d = max(common)
                    if float(sh.loc[d]) > 0 and float(eq.loc[d]) > 0:
                        return float(eq.loc[d]) / float(sh.loc[d]), "資產負債表(權益/股數)"
    except Exception as e:  # noqa: BLE001
        logger.warning("%s 每股淨值計算失敗: %s", stock.ticker, e)
    return None, "N/A"


def get_ttm_eps(stock, info):
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
    try:
        income = stock.quarterly_income_stmt
        if income is not None and not income.empty and "Total Revenue" in income.index:
            s = income.loc["Total Revenue"].dropna().sort_index(ascending=False)
            if len(s) >= 2:
                latest_date, latest_val = s.index[0], float(s.iloc[0])
                target = latest_date - timedelta(days=365)
                gap, match = min(((abs((d - target).days), d) for d in s.index[1:]), key=lambda t: t[0])
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
    ticker, name, subsector = item["代號"], item["名稱"], item["次產業"]
    warnings = []
    stock = yf.Ticker(ticker)

    start = cutoff_date - timedelta(days=60)
    end = cutoff_date + timedelta(days=1)

    def _history():
        return stock.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"),
                             auto_adjust=False)

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

    def _get_info():
        data = stock.info
        # Yahoo 被限流時常只回傳寥寥數個欄位，視為失敗並重試
        if not isinstance(data, dict) or len(data) < 10:
            raise ValueError(f"info 欄位過少 ({len(data) if isinstance(data, dict) else 0})")
        return data

    try:
        info = with_retry(_get_info, f"{ticker} info")
    except Exception as e:  # noqa: BLE001
        logger.error("%s", e)
        warnings.append(f"{name}: Yahoo 基本面資料取得失敗，改用備援計算 ({e})")
        info = {}

    eps_ttm, eps_src = get_ttm_eps(stock, info)
    rev_yoy, rev_src = get_revenue_yoy(stock, info)

    # 本益比：Yahoo → 股價 / 近四季 EPS
    pe, pe_src = to_float(info.get("trailingPE")), "Yahoo"
    if pe is None:
        if eps_ttm and eps_ttm > 0:
            pe, pe_src = price_latest / eps_ttm, "股價/近四季EPS"
        else:
            pe_src = "N/A"
            warnings.append(f"{name}: 本益比無法取得（EPS 缺值或為負）")

    # 股價淨值比：Yahoo → 股價 / 每股淨值
    pb, pb_src = to_float(info.get("priceToBook")), "Yahoo"
    if pb is None:
        bvps, bv_src = book_value_per_share(stock, info)
        if bvps:
            pb, pb_src = price_latest / bvps, f"股價/每股淨值({bv_src})"
        else:
            pb_src = "N/A"
            warnings.append(f"{name}: 股價淨值比無法取得")

    # 殖利率：Yahoo 欄位 → 近 12 個月配息 / 股價
    dy, dy_src = calc_dividend_yield_pct(info, price_latest), "Yahoo"
    if dy is None:
        dy = dividend_yield_from_history(stock, price_latest, latest_date)
        dy_src = "近12個月配息/股價" if dy is not None else "N/A"
        if dy is None:
            warnings.append(f"{name}: 殖利率無法取得（查無近 12 個月配息紀錄）")
    if eps_src != "四季單季加總":
        warnings.append(f"{name}: EPS 來源為 {eps_src}")
    if rev_src != "單季同期比":
        warnings.append(f"{name}: 營收年增率來源為 {rev_src}")

    return {
        "次產業": subsector, "公司名稱": name, "代號": ticker,
        "本益比": pe, "股價淨值比": pb, "殖利率(%)": dy,
        "本益比來源": pe_src, "股價淨值比來源": pb_src, "殖利率來源": dy_src,
        "近四季EPS合計": eps_ttm, "EPS來源": eps_src,
        "營收年增率(%)": rev_yoy, "營收來源": rev_src,
        "最新股價": price_latest, "最新股價日期": latest_date,
        "二周前股價": price_past, "二周前日期": past_date,
        "近2周報酬率(%)": return_rate,
    }, warnings


def add_dual_rank(df):
    df = df.copy()
    df["EPS排名"] = df.groupby("次產業")["近四季EPS合計"].rank(ascending=False, method="min")
    df["營收年增率排名"] = df.groupby("次產業")["營收年增率(%)"].rank(ascending=False, method="min")
    df["雙指標排名總和"] = df["EPS排名"] + df["營收年增率排名"]
    df["雙指標名次"] = df.groupby("次產業")["雙指標排名總和"].rank(method="min")
    order = {s: i for i, s in enumerate(dict.fromkeys(df["次產業"]))}
    df["_o"] = df["次產業"].map(order)
    return df.sort_values(["_o", "雙指標名次"]).drop(columns="_o").reset_index(drop=True)


def fetch_all(stock_list, progress_cb=None, sleep_between=0.6):
    cutoff = get_latest_trade_cutoff()
    rows, failures, warns = [], [], []
    total = len(stock_list)
    for i, item in enumerate(stock_list, start=1):
        label = f"{item['名稱']} ({item['代號']})"
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
        time.sleep(sleep_between)
    if progress_cb:
        progress_cb(total, total, "擷取完成")
    df = pd.DataFrame(rows)
    if not df.empty:
        df = add_dual_rank(df)
    return df, failures, warns


def make_display_df(df):
    """轉成報表用的文字欄位。"""
    rank = df["雙指標名次"].map(lambda v: "N/A" if pd.isna(v) else str(int(v)))
    return pd.DataFrame({
        "次產業": df["次產業"],
        "公司名稱": df["公司名稱"],
        "本益比": df["本益比"].map(lambda v: fmt(v, " 倍")),
        "股價淨值比": df["股價淨值比"].map(lambda v: fmt(v, " 倍")),
        "殖利率": df["殖利率(%)"].map(lambda v: fmt(v, "%")),
        "近四季EPS合計": df["近四季EPS合計"].map(fmt),
        "營收年增率": df["營收年增率(%)"].map(lambda v: fmt(v, "%")),
        "雙指標名次": rank,
        "最新股價(日期)": [f"TWD {fmt(p)} ({d:%Y/%m/%d})" for p, d in zip(df["最新股價"], df["最新股價日期"])],
        "二周前股價(日期)": [f"TWD {fmt(p)} ({d:%Y/%m/%d})" for p, d in zip(df["二周前股價"], df["二周前日期"])],
        "近2周報酬率": df["近2周報酬率(%)"].map(lambda v: fmt(v, "%")),
    })


# ==========================================================
# 階段四：次產業評分（量化分數 + AI 分數）
# ==========================================================
# 量化分數的各項權重（可自行調整；缺資料的項目會自動排除並重新配比）
SCORE_WEIGHTS = {"新聞情緒": 30, "二階動能": 15, "價格動能": 15, "營收成長": 25, "估值": 15}

LLM_PROVIDERS = {
    "Claude (Anthropic)": {"model": "claude-sonnet-5-5", "secret": "ANTHROPIC_API_KEY", "pkg": "anthropic"},
    "Gemini (Google)": {"model": "gemini-2.5-flash", "secret": "GEMINI_API_KEY", "pkg": "google-genai"},
}

AI_SYSTEM_PROMPT = """你是一位熟悉台股次產業的產業研究分析師。以第三人稱陳述，不要提及專業資歷，不要建議任何操作，不要直接對投資人喊話。
你只能根據使用者附上的資料評分，不得引用資料以外的股價、財報或新聞；資料不足時請降低信心，並讓分數靠近 50。
評分標準（1 到 100，反映該次產業在下個月的相對潛在投資價值）：
80-100 強勢；60-79 偏多；40-59 中性；20-39 偏空；1-19 弱勢。
綜合考量：新聞多空與二階動能、近期價格動能、營收年增率與 EPS、估值（本益比、股價淨值比）。
只輸出 JSON，格式如下，不要輸出 JSON 以外的任何文字：
{"scores":[{"sector":"次產業名稱","score":整數,"confidence":"高|中|低","rationale":"60字內的評分依據","risks":["風險1","風險2"]}]}"""


def fmt_int(v):
    return "N/A" if v is None or pd.isna(v) else str(int(round(float(v))))


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def get_secret(name):
    try:
        return st.secrets.get(name)
    except Exception:  # noqa: BLE001  (沒有 secrets.toml 時會拋例外)
        return None


def quant_scores(momentum_df, df_r):
    """
    規則式量化分數（0-100，50 為中性）。回傳 {次產業: (分數, {分項: 分數})}。
    新聞情緒依樣本數向 50 收縮（樣本越少越不採信）。
    """
    out = {}
    for sector in SECTOR_ORDER:
        comps = {}
        if momentum_df is not None and not momentum_df.empty:
            m = momentum_df[momentum_df["次產業"] == sector]
            if not m.empty:
                m = m.iloc[0]
                pos, neg = int(m["_m_pos"]), int(m["_m_neg"])
                n = pos + neg
                if n > 0:
                    ratio = (pos - neg) / n
                    comps["新聞情緒"] = 50 + 50 * ratio * (n / (n + 5))
                    comps["二階動能"] = 50 + 5 * _clip(float(m["_acc"]), -10, 10)
        if df_r is not None and not df_r.empty:
            f = df_r[df_r["次產業"] == sector]
            if not f.empty:
                ret = f["近2周報酬率(%)"].dropna()
                if not ret.empty:
                    comps["價格動能"] = 50 + _clip(float(ret.mean()), -15, 15) / 15 * 50
                yoy = f["營收年增率(%)"].dropna()
                if not yoy.empty:
                    comps["營收成長"] = (_clip(float(yoy.mean()), -30, 60) + 30) / 90 * 100
                pe = f["本益比"].dropna()
                pe = pe[pe > 0]
                if not pe.empty:
                    comps["估值"] = 100 - _clip((float(pe.median()) - 15) / 30, 0, 1) * 100
        if comps:
            wsum = sum(SCORE_WEIGHTS[k] for k in comps)
            score = sum(SCORE_WEIGHTS[k] * v for k, v in comps.items()) / wsum
            out[sector] = (round(score, 1), {k: round(v, 1) for k, v in comps.items()})
    return out


def build_sector_payload(sector, tagged_all, momentum_df, df_r):
    """整理單一次產業送給 AI 的資料（也會顯示在畫面上供檢視）。"""
    lines = [f"## {sector}"]
    if momentum_df is not None and not momentum_df.empty:
        m = momentum_df[momentum_df["次產業"] == sector]
        if not m.empty:
            m = m.iloc[0]
            lines.append(
                f"新聞多空：近1個月 {m['近1個月']}；近半個月 {m['近半個月']}；近1星期 {m['近1星期']}；"
                f"前期多空增減 {m['前期多空增減']}；近期多空增減 {m['近期多空增減']}；二階動能加速度 {m['二階動能加速度']}"
            )
    if tagged_all is not None and not tagged_all.empty:
        sub = (tagged_all[tagged_all["次產業"] == sector]
               .drop_duplicates("新聞標題").sort_values("發布時間", ascending=False).head(10))
        if not sub.empty:
            lines.append("近期相關新聞標題：")
            for _, r in sub.iterrows():
                lines.append(f"- [{r['情緒']}] {r['發布時間']:%m/%d} {r['新聞標題']}")
    if df_r is not None and not df_r.empty:
        f = df_r[df_r["次產業"] == sector]
        if not f.empty:
            lines.append("公司數據：")
            for _, r in f.iterrows():
                lines.append(
                    f"- {r['公司名稱']}：本益比 {fmt(r['本益比'])}、股價淨值比 {fmt(r['股價淨值比'])}、"
                    f"殖利率 {fmt(r['殖利率(%)'], '%')}、近四季EPS合計 {fmt(r['近四季EPS合計'])}、"
                    f"營收年增率 {fmt(r['營收年增率(%)'], '%')}、近2周報酬率 {fmt(r['近2周報酬率(%)'], '%')}"
                )
    return "\n".join(lines)


def call_llm(provider, api_key, model, system, user):
    if provider == "Claude (Anthropic)":
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        kwargs = dict(model=model, max_tokens=2000, system=system,
                      messages=[{"role": "user", "content": user}])
        try:
            msg = client.messages.create(temperature=0, **kwargs)
        except Exception:  # noqa: BLE001  (部分模型不接受 temperature)
            msg = client.messages.create(**kwargs)
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=model, contents=user,
        config={"system_instruction": system, "temperature": 0, "response_mime_type": "application/json"},
    )
    return resp.text


def parse_ai_json(text):
    cleaned = re.sub(r"```(?:json)?", "", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("AI 回覆中找不到 JSON")
    data = json.loads(cleaned[start:end + 1])
    result = {}
    for item in data.get("scores", []):
        name = str(item.get("sector", "")).strip()
        sector = name if name in SECTOR_ORDER else next(
            (s for s in SECTOR_ORDER if name and (s in name or name in s)), None)
        try:
            score = int(round(float(item.get("score"))))
        except (TypeError, ValueError):
            continue
        if sector:
            risks = item.get("risks") or []
            result[sector] = {
                "score": int(_clip(score, 1, 100)),
                "confidence": str(item.get("confidence", "")),
                "rationale": str(item.get("rationale", "")),
                "risks": "；".join(str(x) for x in risks) if isinstance(risks, list) else str(risks),
            }
    if not result:
        raise ValueError("AI 回覆中沒有可用的評分")
    return result


def build_score_table(quant, ai, ai_weight):
    """合併量化與 AI 分數；沒有 AI 分數的次產業只用量化分數。"""
    rows = []
    for sector in SECTOR_ORDER:
        q = quant.get(sector, (None, {}))[0]
        a = ai.get(sector, {}) if ai else {}
        a_score = a.get("score")
        if q is not None and a_score is not None:
            final = ai_weight * a_score + (1 - ai_weight) * q
        elif q is not None:
            final = q
        elif a_score is not None:
            final = float(a_score)
        else:
            continue
        rows.append({
            "次產業": sector,
            "量化分數": q,
            "AI分數": a_score,
            "綜合分數": round(final, 1),
            "信心": a.get("confidence", ""),
            "評語": a.get("rationale", ""),
            "風險": a.get("risks", ""),
        })
    return pd.DataFrame(rows)


# ==========================================================
# 階段五：分數記錄與回測（IC、命中率、多空價差、AI 權重估計）
# ==========================================================
SCORE_LOG = "score_log.csv"
LOG_COLS = ["記錄日期", "記錄時間", "次產業", "量化分數", "AI分數", "綜合分數", "AI權重", "模型", "成分股"]
HORIZONS = {"1 週（5 個交易日）": 5, "2 週（10 個交易日）": 10, "1 個月（21 個交易日）": 21}
T0_SHRINK = 24          # 權重收縮：有效期數達 T0 時，估計值與先驗各占一半
W_MIN, W_MAX = 0.2, 0.8  # 建議權重的上下限


def load_score_log():
    if os.path.exists(SCORE_LOG):
        df = pd.read_csv(SCORE_LOG, encoding="utf-8-sig", dtype={"記錄日期": str})
        for c in LOG_COLS:
            if c not in df.columns:
                df[c] = np.nan
        return df[LOG_COLS]
    return pd.DataFrame(columns=LOG_COLS)


def save_score_log(df):
    df = df.drop_duplicates(subset=["記錄日期", "次產業"], keep="last")
    df.sort_values(["記錄日期", "次產業"]).to_csv(SCORE_LOG, index=False, encoding="utf-8-sig")


def append_score_log(table, ai_weight, model, members, when):
    """追加本次分數；同一天同一次產業只保留最後一次。回傳寫入筆數。"""
    new = pd.DataFrame([{
        "記錄日期": when.strftime("%Y-%m-%d"),
        "記錄時間": when.strftime("%H:%M:%S"),
        "次產業": r["次產業"],
        "量化分數": r["量化分數"],
        "AI分數": r["AI分數"],
        "綜合分數": r["綜合分數"],
        "AI權重": ai_weight,
        "模型": model,
        "成分股": ";".join(members.get(r["次產業"], [])),
    } for _, r in table.iterrows()])
    log = load_score_log()
    save_score_log(pd.concat([log, new], ignore_index=True) if not log.empty else new)
    return len(new)


def fetch_close_history(tickers, start_date):
    """抓還原收盤價（含股利）。回傳 ({代號: Series}, 失敗清單)。"""
    out, fails = {}, []
    for tk in tickers:
        try:
            df = with_retry(
                lambda tk=tk: yf.Ticker(tk).history(start=start_date.strftime("%Y-%m-%d"), auto_adjust=True),
                f"{tk} 歷史股價")
            s = df["Close"].dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            out[tk] = s
        except Exception as e:  # noqa: BLE001
            logger.error("%s", e)
            fails.append({"代號": tk, "原因": str(e)})
        time.sleep(0.4)
    return out, fails


def forward_return(series, t_date, h):
    """記錄日（含）之前最後一個收盤價 → 之後第 h 個交易日收盤價的報酬；未到期回傳 NaN。"""
    idx = series.index
    pos = idx.searchsorted(pd.Timestamp(t_date), side="right") - 1
    if pos < 0 or pos + h >= len(idx):
        return np.nan
    return float(series.iloc[pos + h] / series.iloc[pos] - 1)


def spearman(x, y):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 4 or d["x"].nunique() < 2 or d["y"].nunique() < 2:
        return np.nan
    return float(np.corrcoef(d["x"].rank(), d["y"].rank())[0, 1])


def newey_west_t(x, lag):
    """平均數的 Newey-West t 值（處理重疊視窗造成的自相關）。"""
    x = np.asarray(pd.Series(x).dropna(), dtype=float)
    T = len(x)
    if T < 3:
        return np.nan
    e = x - x.mean()
    var = (e @ e) / T
    for l in range(1, min(lag, T - 1) + 1):
        var += 2 * (1 - l / (lag + 1)) * ((e[l:] @ e[:-l]) / T)
    var = max(var, 1e-12)
    return float(x.mean() / math.sqrt(var / T))


def _z(s):
    sd = s.std(ddof=0)
    return (s - s.mean()) / sd if sd and sd > 0 else s * np.nan


def summarize_ic(series, lag):
    x = series.dropna()
    if len(x) < 2:
        return {"期數": len(x), "平均IC": np.nan, "ICIR": np.nan, "NW t值": np.nan, "IC>0比例": np.nan}
    sd = x.std(ddof=1)
    return {"期數": len(x), "平均IC": x.mean(), "ICIR": x.mean() / sd if sd > 0 else np.nan,
            "NW t值": newey_west_t(x, lag), "IC>0比例": float((x > 0).mean())}


def estimate_weight(d, T_eff):
    """用標準化後的 zA、zQ、zr 估計 AI 權重（含向 0.5 收縮與上下限）。"""
    d = d.dropna(subset=["zA", "zQ", "zr"])
    if len(d) < 10:
        return None
    rhoA, rhoQ, c = d["zA"].corr(d["zr"]), d["zQ"].corr(d["zr"]), d["zA"].corr(d["zQ"])
    if pd.isna(rhoA) or pd.isna(rhoQ) or pd.isna(c) or abs(c) >= 0.999:
        return None
    bA = (rhoA - c * rhoQ) / (1 - c ** 2)
    bQ = (rhoQ - c * rhoA) / (1 - c ** 2)
    if bA > 0 and bQ > 0:
        raw = bA / (bA + bQ)
    elif bA > 0:
        raw = 1.0
    elif bQ > 0:
        raw = 0.0
    else:
        raw = None
    lam = T_eff / (T_eff + T0_SHRINK)
    shrunk = 0.5 if raw is None else lam * raw + (1 - lam) * 0.5
    return {"rhoA": rhoA, "rhoQ": rhoQ, "c": c, "bA": bA, "bQ": bQ, "raw": raw,
            "lam": lam, "shrunk": shrunk, "final": _clip(shrunk, W_MIN, W_MAX), "n": len(d)}


def evaluate_log(log, prices, h, monthly=False):
    df = log.copy()
    df["記錄日期"] = df["記錄日期"].astype(str)
    if monthly:  # 每月只取第一筆，避免相鄰記錄的報酬視窗重疊
        df["_ym"] = df["記錄日期"].str[:7]
        df = df.sort_values("記錄日期").groupby(["_ym", "次產業"]).head(1).drop(columns="_ym")

    Rs = []
    for _, r in df.iterrows():
        members = [m for m in str(r["成分股"]).split(";") if m in prices]
        rets = [forward_return(prices[m], r["記錄日期"], h) for m in members]
        rets = [x for x in rets if not np.isnan(x)]
        Rs.append(float(np.mean(rets)) if rets else np.nan)
    df["R"] = Rs
    df["r"] = df["R"] - df.groupby("記錄日期")["R"].transform("mean")  # 次產業間的超額報酬

    ic_rows = []
    for dt, g in df.groupby("記錄日期"):
        g = g.dropna(subset=["r"])
        if len(g) < 4:
            continue
        ic_rows.append({"記錄日期": dt,
                        "綜合": spearman(g["綜合分數"], g["r"]),
                        "AI": spearman(g["AI分數"], g["r"]),
                        "量化": spearman(g["量化分數"], g["r"])})
    ic = pd.DataFrame(ic_rows)

    step_td = float(h)
    if len(ic) >= 2:
        gap = pd.to_datetime(ic["記錄日期"]).diff().dt.days.median()
        if pd.notna(gap):
            step_td = max(1.0, gap * 5 / 7)
    lag = max(0, math.ceil(h / step_td) - 1)
    overlap = min(1.0, step_td / h)

    summary = pd.DataFrame(
        {name: summarize_ic(ic[name], lag) if not ic.empty else summarize_ic(pd.Series(dtype=float), lag)
         for name in ["綜合", "AI", "量化"]}
    ).T

    # 保守 t：只用「有效獨立期數」計算，不讓重疊視窗灌水
    summary["保守t(有效期數)"] = summary["ICIR"].astype(float) * math.sqrt(max(len(ic) * overlap, 0))

    d = df.dropna(subset=["r"]).copy()
    for col, z in [("綜合分數", "zS"), ("AI分數", "zA"), ("量化分數", "zQ"), ("r", "zr")]:
        d[z] = d.groupby("記錄日期")[col].transform(_z)

    hit = None
    dh = d.dropna(subset=["zS", "zr"])
    dh = dh[(dh["zS"] != 0) & (dh["zr"] != 0)]
    if len(dh) >= 10:
        n = len(dh)
        H = int((np.sign(dh["zS"]) == np.sign(dh["zr"])).sum())
        hit = {"n": n, "H": H, "rate": H / n, "z": (H - n / 2) / (math.sqrt(n) / 2)}

    spreads = []
    for _, g in d.dropna(subset=["綜合分數"]).groupby("記錄日期"):
        if len(g) >= 4:
            g = g.sort_values("綜合分數")
            spreads.append(float(g["r"].iloc[-1] - g["r"].iloc[0]))
    spread = None
    if len(spreads) >= 3:
        spread = {"n": len(spreads), "mean": float(np.mean(spreads)), "t": newey_west_t(spreads, lag)}

    n_dates_A = d.dropna(subset=["zA", "zQ", "zr"])["記錄日期"].nunique()
    weight = estimate_weight(d, n_dates_A * overlap)

    return {"ic": ic, "summary": summary, "hit": hit, "spread": spread, "weight": weight,
            "matured": int(df["R"].notna().sum()), "total": len(df), "lag": lag,
            "step_td": step_td, "T_eff": len(ic) * overlap}


# ==========================================================
# Word 報告
# ==========================================================
def _set_cell(cell, text, bold=False, size=9):
    p = cell.paragraphs[0]
    p.text = ""
    run = p.add_run(str(text))
    run.font.bold = bold
    run.font.size = Pt(size)


def generate_word_report(df_results, macro_text, momentum_df, score, sector_scores=None):
    doc = Document()

    normal = doc.styles["Normal"]
    normal.font.name = "微軟正黑體"
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "微軟正黑體")

    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    if sec.page_width < sec.page_height:
        sec.page_width, sec.page_height = sec.page_height, sec.page_width
    sec.left_margin = sec.right_margin = Cm(1.5)

    title = doc.add_heading("台股產業投資策略 1.5 版 - 動態量化分析報告", level=0)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    doc.add_paragraph(f"報告生成日期：{datetime.now(TZ):%Y/%m/%d}").alignment = WD_PARAGRAPH_ALIGNMENT.RIGHT

    doc.add_heading("市場動態與產業展望", level=1)
    doc.add_paragraph(macro_text)

    disp = make_display_df(df_results)
    for i, sector in enumerate(dict.fromkeys(disp["次產業"]), 1):
        doc.add_heading(f"{i}. {sector}", level=2)

        if momentum_df is not None and not momentum_df.empty:
            m = momentum_df[momentum_df["次產業"] == sector]
            if not m.empty:
                m = m.iloc[0]
                for key in ["近1個月", "近半個月", "近1星期", "前期多空增減", "近期多空增減", "二階動能加速度"]:
                    doc.add_paragraph(f"{key}：{m[key]}", style="List Bullet")

        if sector_scores is not None and not sector_scores.empty:
            sc = sector_scores[sector_scores["次產業"] == sector]
            if not sc.empty:
                sc = sc.iloc[0]
                doc.add_paragraph(
                    f"次產業評分：{fmt_int(sc['綜合分數'])}（量化 {fmt_int(sc['量化分數'])}／AI {fmt_int(sc['AI分數'])}）",
                    style="List Bullet")
                if isinstance(sc["評語"], str) and sc["評語"]:
                    doc.add_paragraph(f"評分依據：{sc['評語']}", style="List Bullet")

        sub = disp[disp["次產業"] == sector].drop(columns=["次產業"])
        table = doc.add_table(rows=1, cols=len(sub.columns))
        table.style = "Table Grid"
        for j, header in enumerate(sub.columns):
            _set_cell(table.rows[0].cells[j], header, bold=True)
        for _, row in sub.iterrows():
            cells = table.add_row().cells
            for j, val in enumerate(row):
                _set_cell(cells[j], val)
        doc.add_paragraph("")

    p = doc.add_paragraph()
    run = p.add_run(f"Score: {score}")
    run.font.bold = True
    run.font.size = Pt(14)

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


# ==========================================================
# Streamlit 介面
# ==========================================================
st.set_page_config(page_title="台股產業投資策略 1.5 版", layout="wide")
st.title("📊 台股產業投資策略 1.5 版 - AI 互動式 Web App")

_defaults = {
    "news_all": pd.DataFrame(), "news_failed": [], "news_fetched": False,
    "momentum_df": pd.DataFrame(), "final_stock_list": [],
    "report_df": pd.DataFrame(), "fetch_failures": [], "fetch_warns": [], "fetched_at": "",
    "score_table": pd.DataFrame(), "quant_detail": pd.DataFrame(), "ai_debug": {}, "overall_score": 50,
    "price_cache": {}, "price_fails": [],
}
for _k, _v in _defaults.items():
    st.session_state.setdefault(_k, _v)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "第一階段：新聞熱點與二階動能",
    "第二階段：次產業與個股動態篩選",
    "第三階段：財務數據與報告匯出",
    "第四階段：AI 評分",
    "第五階段：分數記錄與回測",
])

# ---------------------------- Tab 1 ----------------------------
with tab1:
    st.header("近期產業新聞與多空熱點分析")
    c1, c2, c3, c4 = st.columns(4)
    list_days = c1.slider("榜單取近幾天新聞", 3, 30, 7)
    bull = c2.slider("利多門檻（分數 ≥）", 50, 90, 60)
    bear = c3.slider("利空門檻（分數 ≤）", 10, 50, 40)
    per_sector = c4.checkbox("以追蹤公司名稱補抓各次產業新聞", value=True)

    if st.button("啟動新聞爬蟲與情感評分 (Stage 1)", type="primary"):
        with st.spinner("正在連線 Google News 抓取近 30 天新聞並評分..."):
            df_news, failed = fetch_news_all(30, per_sector)
        st.session_state.news_all = df_news
        st.session_state.news_failed = failed
        st.session_state.news_fetched = not df_news.empty
        if df_news.empty:
            st.warning("目前無法取得新聞資料，請稍後再試。詳見 fetch_errors.log。")

    if st.session_state.news_failed:
        with st.expander(f"⚠️ {len(st.session_state.news_failed)} 個新聞查詢失敗"):
            st.dataframe(pd.DataFrame(st.session_state.news_failed), hide_index=True)

    if st.session_state.news_fetched:
        df_all = st.session_state.news_all
        now = datetime.now(TZ)
        st.success(f"已取得 {len(df_all)} 則不重複新聞（近 30 天）。")

        # --- 榜單 ---
        recent = df_all[df_all["發布時間"] >= now - timedelta(days=list_days)]
        top_10 = recent.sort_values("分數", ascending=False).head(10)
        rest = recent[~recent["新聞標題"].isin(top_10["新聞標題"])]
        bottom_10 = rest.sort_values("分數", ascending=True).head(10)
        show_cols = ["新聞標題", "來源與日期", "分數"]

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🔥 最高分數前 10 則產業新聞")
            st.dataframe(top_10[show_cols].reset_index(drop=True))
        with col2:
            st.subheader("❄️ 最低分數前 10 則產業新聞")
            st.dataframe(bottom_10[show_cols].reset_index(drop=True))

        # --- 二階動能（依次產業，以全部新聞計算）---
        st.divider()
        tagged_all, _, _, _ = classify_news(df_all, bull, bear)
        momentum_df = compute_momentum(tagged_all, now)
        st.session_state.momentum_df = momentum_df
        st.subheader("📈 各次產業二階動能加速度")
        st.caption("依新聞標題命中的公司歸入次產業後計算；近 1 個月 / 半個月 / 1 星期為巢狀時間窗。")
        st.dataframe(momentum_df[[c for c in momentum_df.columns if not c.startswith("_")]], hide_index=True)

        # --- 標的歸類 ---
        st.divider()
        st.subheader("🏭 新聞標的與產業位置歸類")
        scope = st.radio("歸類範圍", ["僅榜單前 / 後 10 則", "全部抓取新聞"], horizontal=True)
        if scope.startswith("僅"):
            src = pd.concat(
                [top_10.assign(榜單="利多榜"), bottom_10.assign(榜單="利空榜")], ignore_index=True
            ).drop_duplicates(subset="新聞標題")
        else:
            src = df_all

        tagged, by_company, by_sector, untagged = classify_news(src, bull, bear)
        total_titles = src["新聞標題"].nunique()
        unmatched = untagged["新聞標題"].nunique() if not untagged.empty else 0
        st.caption(f"{total_titles - unmatched} / {total_titles} 則新聞對應到名單內公司")

        if tagged.empty:
            st.info("這批新聞沒有出現名單內的公司。")
        else:
            t1, t2, t3, t4 = st.tabs(["公司歸類", "次產業 × 供應鏈位置", "新聞明細", "未歸類新聞"])
            with t1:
                st.dataframe(by_company, hide_index=True)
            with t2:
                st.caption("數字為利多則數減利空則數（淨多空）。")
                st.dataframe(by_sector)
            with t3:
                detail = tagged.drop(columns=["發布時間"])
                st.dataframe(detail, hide_index=True)
                st.download_button("下載歸類明細 CSV", detail.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="news_tagged.csv", mime="text/csv")
            with t4:
                st.caption("沒命中名單的新聞，可從中找出需要補進字典的公司。")
                if untagged.empty:
                    st.write("無。")
                else:
                    st.dataframe(untagged.drop(columns=["發布時間"], errors="ignore"), hide_index=True)

# ---------------------------- Tab 2 ----------------------------
with tab2:
    st.header("熱點次產業與領先個股對接")
    st.write("勾選要追蹤的標的；表格最下方可新增列（需填代號、名稱、次產業）。")

    candidates = pd.DataFrame([
        {"追蹤": c["ticker"] in DEFAULT_TRACKED, "次產業": c["subsector"],
         "代號": c["ticker"], "名稱": c["name"]}
        for c in COMPANIES
    ])
    edited_df = st.data_editor(candidates, hide_index=True, num_rows="dynamic", key="candidates_editor")

    if st.button("鎖定個股名單並進入第三階段"):
        chosen = edited_df[edited_df["追蹤"] == True]  # noqa: E712
        final_list = []
        for rec in chosen.to_dict("records"):
            tk, nm, sec_ = str(rec.get("代號") or "").strip(), str(rec.get("名稱") or "").strip(), str(rec.get("次產業") or "").strip()
            if tk and nm and sec_ and tk.lower() != "nan":
                final_list.append({"代號": tk, "名稱": nm, "次產業": sec_})
        st.session_state.final_stock_list = final_list
        st.success(f"已成功鎖定 {len(final_list)} 檔核心標的！請切換至第三階段。")

# ---------------------------- Tab 3 ----------------------------
with tab3:
    st.header("財務數據擷取與自動化報告生成")
    if len(st.session_state.final_stock_list) == 0:
        st.warning("請先於第二階段鎖定個股名單！")
    else:
        if st.button("擷取即時財務數據", type="primary"):
            bar = st.progress(0.0, text="準備中…")

            def _cb(done, total, msg):
                bar.progress(done / total, text=msg)

            df_r, fails, warns = fetch_all(st.session_state.final_stock_list, progress_cb=_cb)
            bar.empty()
            st.session_state.report_df = df_r
            st.session_state.fetch_failures = fails
            st.session_state.fetch_warns = warns
            st.session_state.fetched_at = datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")

        df_r = st.session_state.report_df
        if st.session_state.fetched_at:
            st.write(f"擷取時間：**{st.session_state.fetched_at}**　｜　成功 **{len(df_r)}** / "
                     f"{len(st.session_state.final_stock_list)} 檔")

        if st.session_state.fetch_failures:
            st.error(f"{len(st.session_state.fetch_failures)} 檔擷取失敗（詳見 {LOG_FILE}）")
            st.dataframe(pd.DataFrame(st.session_state.fetch_failures), hide_index=True)
        if st.session_state.fetch_warns:
            with st.expander("警告（資料來源退回、基準日調整等）"):
                for w in st.session_state.fetch_warns:
                    st.write(f"- {w}")

        if not df_r.empty:
            st.dataframe(make_display_df(df_r), hide_index=True)

            st.subheader("報告設定")
            macro_text = st.text_area("市場動態與產業展望", value=DEFAULT_MACRO_TEXT, height=120)
            score = st.slider("報告 Score（預設為第四階段綜合分數，可手動調整）", 1, 100,
                              int(st.session_state.overall_score))

            doc_buffer = generate_word_report(df_r, macro_text, st.session_state.momentum_df, score,
                                           st.session_state.score_table)
            st.download_button(
                label="📥 下載橫向排版 Word 投資報告",
                data=doc_buffer,
                file_name=f"台股產業投資策略_1.5版_{datetime.now(TZ):%Y%m%d}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

# ---------------------------- Tab 4 ----------------------------
with tab4:
    st.header("次產業評分：量化分數 + AI 分數")
    st.caption("量化分數是依固定規則計算的透明指標；AI 分數由大型語言模型閱讀新聞標題與財務數據後判斷。"
               "兩者可依權重合成。分數未經歷史回測，僅作研究參考，不構成投資建議。")

    if not st.session_state.news_fetched:
        st.warning("請先於第一階段抓取新聞。")
    else:
        if st.session_state.report_df.empty:
            st.info("尚未擷取第三階段財務數據：目前只能依新聞計算，價格、營收與估值項目會被排除。")

        c1, c2 = st.columns(2)
        provider = c1.selectbox("AI 模型供應商", list(LLM_PROVIDERS.keys()))
        cfg = LLM_PROVIDERS[provider]
        model_name = c2.text_input("模型名稱", value=cfg["model"], key=f"model_{provider}")

        api_key = get_secret(cfg["secret"])
        if api_key:
            st.caption(f"已從 secrets 讀取 {cfg['secret']}。")
        else:
            api_key = st.text_input(f"{cfg['secret']}（不會被儲存）", type="password")

        c3, c4 = st.columns(2)
        use_ai = c3.checkbox("使用 AI 評分", value=True)
        ai_pct = c4.slider("AI 分數權重 (%)", 0, 100, 50, disabled=not use_ai)
        log_it = st.checkbox("記錄本次分數，供日後回測（同一天同一次產業只保留最後一次）", value=True)

        if st.button("計算次產業分數", type="primary"):
            df_all = st.session_state.news_all
            df_fin = st.session_state.report_df
            momentum = st.session_state.momentum_df
            tagged_all, _, _, _ = classify_news(df_all, bull, bear)

            quant = quant_scores(momentum, df_fin)
            payload = "\n\n".join(
                build_sector_payload(s, tagged_all, momentum, df_fin) for s in SECTOR_ORDER
            )

            ai_result, raw_text = {}, ""
            if use_ai:
                if not api_key:
                    st.error("請輸入 API Key，或取消勾選「使用 AI 評分」。")
                else:
                    try:
                        with st.spinner(f"正在請 {provider} 評分…"):
                            raw_text = with_retry(
                                lambda: call_llm(provider, api_key, model_name, AI_SYSTEM_PROMPT,
                                                 "以下為各次產業最新資料，請逐一評分：\n\n" + payload),
                                "AI 評分", retries=2,
                            )
                            ai_result = parse_ai_json(raw_text)
                        logger.info("AI 評分成功 (%s / %s)", provider, model_name)
                    except Exception as e:  # noqa: BLE001
                        logger.error("AI 評分失敗: %s", e)
                        st.error(f"AI 評分失敗，已改用量化分數：{e}")

            table = build_score_table(quant, ai_result, ai_pct / 100 if ai_result else 0.0)
            st.session_state.score_table = table
            if log_it and not table.empty:
                tracked = st.session_state.final_stock_list
                members = {}
                for sec_name in SECTOR_ORDER:
                    if tracked:
                        members[sec_name] = [x["代號"] for x in tracked if x["次產業"] == sec_name]
                    else:
                        members[sec_name] = [c["ticker"] for c in COMPANIES
                                             if c["subsector"] == sec_name and c["ticker"] in DEFAULT_TRACKED]
                n_logged = append_score_log(table, ai_pct / 100 if ai_result else 0.0,
                                            model_name if ai_result else "", members, datetime.now(TZ))
                st.success(f"已記錄 {n_logged} 筆至 {SCORE_LOG}。")
            st.session_state.quant_detail = pd.DataFrame(
                [{"次產業": s, **comps} for s, (_, comps) in quant.items()]
            )
            st.session_state.ai_debug = {"payload": payload, "raw": raw_text}
            if not table.empty:
                st.session_state.overall_score = int(round(table["綜合分數"].mean()))

        table = st.session_state.score_table
        if not table.empty:
            st.metric("綜合 Score（各次產業平均）", st.session_state.overall_score)
            show = table.copy()
            for col in ["量化分數", "AI分數", "綜合分數"]:
                show[col] = show[col].astype(float)
            st.dataframe(show, hide_index=True)
            st.bar_chart(show.set_index("次產業")[["量化分數", "AI分數", "綜合分數"]])

            with st.expander("量化分數分項（0-100，50 為中性）"):
                st.dataframe(st.session_state.quant_detail, hide_index=True)
                st.caption("權重：" + "、".join(f"{k} {v}" for k, v in SCORE_WEIGHTS.items())
                           + "。缺資料的項目會被排除並重新配比。")
            with st.expander("送給 AI 的資料與原始回覆（供檢視）"):
                st.text(st.session_state.ai_debug.get("payload", ""))
                if st.session_state.ai_debug.get("raw"):
                    st.code(st.session_state.ai_debug["raw"], language="json")

# ---------------------------- Tab 5 ----------------------------
with tab5:
    st.header("分數記錄與回測")
    st.caption("每次在第四階段計算分數時記錄下來；持有期間結束後，用次產業成分股的還原股價結算超額報酬，"
               "再檢驗分數與之後表現是否一致。樣本期數少時結論不可靠，請累積足夠期數後再參考。")

    log = load_score_log()
    if log.empty:
        st.info("尚無記錄。請到第四階段計算分數並勾選「記錄本次分數」。")
    else:
        st.write(f"共 **{log['記錄日期'].nunique()}** 個記錄日期、{len(log)} 筆"
                 f"（{log['記錄日期'].min()} ～ {log['記錄日期'].max()}）。")
        c1, c2 = st.columns(2)
        h_label = c1.selectbox("持有期間", list(HORIZONS.keys()), index=2)
        sampling = c2.radio("取樣方式", ["全部記錄", "每月第一筆（不重疊）"], horizontal=True)
        h = HORIZONS[h_label]

        if st.button("更新股價並結算", type="primary"):
            tickers = sorted({t for s in log["成分股"].dropna() for t in str(s).split(";") if t})
            start = pd.to_datetime(log["記錄日期"].min()).date() - timedelta(days=10)
            with st.spinner(f"正在抓取 {len(tickers)} 檔歷史股價…"):
                prices, fails = fetch_close_history(tickers, start)
            st.session_state.price_cache = prices
            st.session_state.price_fails = fails

        if st.session_state.price_fails:
            st.error(f"{len(st.session_state.price_fails)} 檔股價抓取失敗（詳見 {LOG_FILE}）")
            st.dataframe(pd.DataFrame(st.session_state.price_fails), hide_index=True)

        if not st.session_state.price_cache:
            st.info("請按「更新股價並結算」。")
        else:
            res = evaluate_log(log, st.session_state.price_cache, h, monthly=sampling.startswith("每月"))
            st.write(f"已到期並可結算：**{res['matured']} / {res['total']}** 筆；"
                     f"相鄰記錄約間隔 {res['step_td']:.1f} 個交易日，Newey-West 落後期數 {res['lag']}，"
                     f"有效獨立期數約 **{res['T_eff']:.1f}**。")

            if res["ic"].empty:
                st.warning("目前沒有任何記錄日期同時有 4 個以上已到期的次產業，還無法計算 IC。"
                           f"請等持有期間（{h} 個交易日）過後再來。")
            else:
                st.subheader("排名相關係數 IC（分數排名 vs 超額報酬排名）")
                if res["T_eff"] < 10:
                    st.warning(f"有效獨立期數只有 {res['T_eff']:.1f}（建議至少 10 以上，最好 24 以上）。"
                               "此時 t 值與權重估計雜訊很大，Newey-West t 值在樣本少時尤其容易高估，請以「保守t」為準並僅作參考。")
                st.dataframe(res["summary"].round(3))
                st.line_chart(res["ic"].set_index("記錄日期")[["綜合", "AI", "量化"]])

                m1, m2 = st.columns(2)
                with m1:
                    st.subheader("方向命中率")
                    if res["hit"]:
                        st.metric("命中率", f"{res['hit']['rate']:.1%}",
                                  help=f"{res['hit']['H']} / {res['hit']['n']}；z = {res['hit']['z']:.2f}（|z|>2 才較可能非巧合）")
                        st.caption(f"{res['hit']['H']} / {res['hit']['n']} 筆；z = {res['hit']['z']:.2f}")
                    else:
                        st.write("樣本不足。")
                with m2:
                    st.subheader("最高分 − 最低分 價差")
                    if res["spread"]:
                        st.metric("平均超額報酬差", f"{res['spread']['mean']:.2%}")
                        st.caption(f"{res['spread']['n']} 期；Newey-West t = {res['spread']['t']:.2f}")
                    else:
                        st.write("樣本不足。")

                st.subheader("AI 權重估計")
                w = res["weight"]
                if w is None:
                    st.write("需要至少 10 筆同時含 AI 與量化分數、且已到期的記錄。")
                else:
                    a, b, c_ = st.columns(3)
                    a.metric("原始估計 w*", "無正向訊號" if w["raw"] is None else f"{w['raw']:.2f}")
                    b.metric("收縮後", f"{w['shrunk']:.2f}", help=f"λ = {w['lam']:.2f}，先驗 0.5")
                    c_.metric("建議 AI 權重（限制 0.2–0.8）", f"{w['final']:.2f}")
                    st.caption(f"ρ(AI)= {w['rhoA']:.3f}、ρ(量化)= {w['rhoQ']:.3f}、AI 與量化相關 c = {w['c']:.3f}；"
                               f"β(AI)= {w['bA']:.3f}、β(量化)= {w['bQ']:.3f}；樣本 {w['n']} 筆。"
                               "有效期數越少，估計越不可靠，因此會向 0.5 收縮。")

    st.divider()
    st.subheader("記錄檔管理")
    st.caption(f"記錄存在執行目錄的 {SCORE_LOG}。若部署在雲端主機，檔案可能在重啟後消失，請定期下載備份。")
    if not log.empty:
        st.download_button("下載記錄 CSV", log.to_csv(index=False).encode("utf-8-sig"),
                           file_name=SCORE_LOG, mime="text/csv")
    up = st.file_uploader("匯入先前備份的記錄（會與現有記錄合併）", type="csv")
    if up is not None and st.button("合併匯入"):
        try:
            old = pd.read_csv(up, encoding="utf-8-sig", dtype={"記錄日期": str})
            merged = pd.concat([load_score_log(), old.reindex(columns=LOG_COLS)], ignore_index=True)
            save_score_log(merged)
            st.success("已合併。請重新整理頁面。")
        except Exception as e:  # noqa: BLE001
            st.error(f"匯入失敗：{e}")
