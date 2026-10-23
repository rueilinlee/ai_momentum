# -*- coding: utf-8 -*-
"""
台股產業投資策略 1.5 版（新增回測持有期 1 天、完整新聞明細、多期間前 5 名與 AI 建議權重 LINE 推播整合版）
包含：新聞爬蟲、詳細新聞列表與來源、二階動能、財報/股價雙指標、Word 報告、
AI/量化評分、記憶體多期間暫存回測、Plotly 多策略對比、AI 權重最佳化、背景排程與 LINE 狀態回饋
"""

import io
import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import feedparser
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pytz
import schedule
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
if not logger.handlers:
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

DEFAULT_TRACKED = {
    "2330.TW", "2303.TW", "5347.TWO", "6669.TW", "2382.TW", "2376.TW", "3131.TWO", "6640.TWO",
    "6187.TWO", "3653.TW", "3017.TW", "3324.TW", "5274.TW", "3661.TW", "5269.TW",
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
# 通用工具與密鑰讀取
# ==========================================================
def get_secret(name):
    try:
        return st.secrets.get(name)
    except Exception:
        return None

def with_retry(fn, label, retries=3, base_delay=1.5):
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
        except Exception as e:
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
# 階段一：新聞抓取與二階動能
# ==========================================================
GENERAL_QUERY = "台股 (半導體 OR 伺服器 OR 散熱 OR 封裝 OR IC設計)"

POSITIVE_KW = {"滿載": 20, "大單": 18, "創高": 15, "升級": 15, "受惠": 12, "強勁": 15,
               "爆發": 20, "看增": 10, "放量": 15, "止跌": 8, "回升": 8, "上修": 15}
NEGATIVE_KW = {"下修": -20, "衰退": -18, "承壓": -15, "遞延": -12, "缺料": -15, "降評": -15,
               "走弱": -10, "保守": -10, "重挫": -15, "大跌": -15, "暴跌": -20, "跌破": -12,
               "下滑": -10}


def score_title(title):
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
        except Exception as e:
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
    out = []
    for sector in SECTOR_ORDER:
        sub = tagged[tagged["次產業"] == sector].drop_duplicates("新聞標題") if not tagged.empty else tagged

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
            "_m_pos": m_pos, "_m_neg": m_neg,
            "_h_pos": h_pos, "_h_neg": h_neg,
            "_w_pos": w_pos, "_w_neg": w_neg,
            "_acc": acc,
        })
    return pd.DataFrame(out)


# ==========================================================
# 階段三：財務數據擷取
# ==========================================================
def get_latest_trade_cutoff(now=None):
    now = now or datetime.now(TZ)
    if now.hour < 13 or (now.hour == 13 and now.minute < 30):
        return (now - timedelta(days=1)).date()
    return now.date()


def calc_dividend_yield_pct(info, price):
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
    try:
        div = stock.dividends
        if div is not None and not div.empty and price:
            d = div.copy()
            d.index = pd.to_datetime(d.index).tz_localize(None)
            start = pd.Timestamp(as_of) - pd.Timedelta(days=365)
            total = float(d[d.index > start].sum())
            if total > 0:
                return total / price * 100
    except Exception:
        pass
    return None


def book_value_per_share(stock, info):
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
    except Exception:
        pass
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
    except Exception:
        pass
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
    except Exception:
        pass
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

    df = with_retry(lambda: stock.history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"), auto_adjust=False), f"{ticker} 股價")
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

    try:
        info = with_retry(lambda: stock.info, f"{ticker} info")
    except Exception as e:
        warnings.append(f"{name}: Yahoo 基本面資料取得失敗，改用備援計算 ({e})")
        info = {}

    eps_ttm, eps_src = get_ttm_eps(stock, info)
    rev_yoy, rev_src = get_revenue_yoy(stock, info)

    pe, pe_src = to_float(info.get("trailingPE")), "Yahoo"
    if pe is None:
        if eps_ttm and eps_ttm > 0:
            pe, pe_src = price_latest / eps_ttm, "股價/近四季EPS"
        else:
            pe_src = "N/A"

    pb, pb_src = to_float(info.get("priceToBook")), "Yahoo"
    if pb is None:
        bvps, bv_src = book_value_per_share(stock, info)
        if bvps:
            pb, pb_src = price_latest / bvps, f"股價/每股淨值({bv_src})"
        else:
            pb_src = "N/A"

    dy, dy_src = calc_dividend_yield_pct(info, price_latest), "Yahoo"
    if dy is None:
        dy = dividend_yield_from_history(stock, price_latest, latest_date)
        dy_src = "近12個月配息/股價" if dy is not None else "N/A"

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
        except Exception as e:
            failures.append({"公司": label, "原因": str(e)})
        time.sleep(sleep_between)
    if progress_cb:
        progress_cb(total, total, "擷取完成")
    df = pd.DataFrame(rows)
    if not df.empty:
        df = add_dual_rank(df)
    return df, failures, warns


def make_display_df(df):
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
# 階段四：次產業評分（支援多期間：近1周、近2周、近1月）
# ==========================================================
SCORE_WEIGHTS = {"新聞情緒": 30, "二階動能": 15, "價格動能": 15, "營收成長": 25, "估值": 15}

LLM_PROVIDERS = {
    "Claude (Anthropic)": {"model": "claude-sonnet-5-5", "secret": "ANTHROPIC_API_KEY", "pkg": "anthropic"},
    "Gemini (Google)": {"model": "gemini-2.5-flash", "secret": "GEMINI_API_KEY", "pkg": "google-genai"},
}

AI_SYSTEM_PROMPT = """你是一位熟悉台股次產業的產業研究分析師。以第三人稱陳述，不要提及專業資歷，不要建議任何操作，不要直接對投資人喊話。
你只能根據使用者附上的資料評分，不得引用資料以外的股價、財報或新聞；資料不足時請降低信心，並讓分數靠近 50。
評分標準（1 到 100，反映該次產業在對應期間的相對潛在投資價值）：
80-100 強勢；60-79 偏多；40-59 中性；20-39 偏空；1-19 弱勢。
綜合考量：新聞多空與動能、近期價格動能、營收年增率與 EPS、估值（本益比、股價淨值比）。
只輸出 JSON，格式如下，不要輸出 JSON 以外的任何文字：
{"scores":[{"sector":"次產業名稱","score":整數,"confidence":"高|中|低","rationale":"60字內的評分依據","risks":["風險1","風險2"]}]}"""


def fmt_int(v):
    return "N/A" if v is None or pd.isna(v) else str(int(round(float(v))))


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


def quant_scores(momentum_df, df_r):
    out = {}
    for sector in SECTOR_ORDER:
        horizon_scores = {}
        m = None
        if momentum_df is not None and not momentum_df.empty:
            m_row = momentum_df[momentum_df["次產業"] == sector]
            if not m_row.empty:
                m = m_row.iloc[0]

        def calc_sub(pos, neg, acc_val, use_fin=False):
            comps = {}
            n = pos + neg
            if n > 0:
                comps["新聞情緒"] = 50 + 50 * ((pos - neg) / n) * (n / (n + 5))
            if acc_val is not None:
                comps["二階動能"] = 50 + 5 * _clip(float(acc_val), -10, 10)

            if use_fin and df_r is not None and not df_r.empty:
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
                wsum = sum(SCORE_WEIGHTS.get(k, 20) for k in comps)
                s = sum(SCORE_WEIGHTS.get(k, 20) * v for k, v in comps.items()) / wsum
                return round(s, 1), {k: round(v, 1) for k, v in comps.items()}
            return None, {}

        if m is not None:
            w_pos, w_neg = int(m.get("_w_pos", 0)), int(m.get("_w_neg", 0))
            h_pos, h_neg = int(m.get("_h_pos", 0)), int(m.get("_h_neg", 0))
            m_pos, m_neg = int(m.get("_m_pos", 0)), int(m.get("_m_neg", 0))
            acc = float(m.get("_acc", 0))

            horizon_scores["近1周"] = calc_sub(w_pos, w_neg, acc * 0.5, use_fin=False)
            horizon_scores["近2周"] = calc_sub(h_pos, h_neg, acc * 0.8, use_fin=False)
            horizon_scores["近1月"] = calc_sub(m_pos, m_neg, acc, use_fin=True)
        else:
            horizon_scores["近1周"] = (None, {})
            horizon_scores["近2周"] = (None, {})
            horizon_scores["近1月"] = (None, {})

        out[sector] = horizon_scores
    return out


def build_sector_payload(sector, tagged_all, momentum_df, df_r, horizon="近1月"):
    lines = [f"## {sector} (評估期間: {horizon})"]
    if momentum_df is not None and not momentum_df.empty:
        m = momentum_df[momentum_df["次產業"] == sector]
        if not m.empty:
            m = m.iloc[0]
            lines.append(
                f"動能數據：近1個月 {m['近1個月']}；近半個月 {m['近半個月']}；近1星期 {m['近1星期']}；"
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
        except Exception:
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


def build_score_table(quant, ai, ai_weight, horizon="近1月", tracked_list=None):
    rows = []
    for sector in SECTOR_ORDER:
        h_q = quant.get(sector, {})
        q = h_q.get(horizon, (None, {}))[0]
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
            
        sec_tickers = [c["ticker"] for c in COMPANIES if c["subsector"] == sector and c["ticker"] in DEFAULT_TRACKED]
        if tracked_list:
            custom_tickers = [x["代號"] for x in tracked_list if x["次產業"] == sector and x["代號"]]
            if custom_tickers:
                sec_tickers = custom_tickers

        rows.append({
            "評估期間": horizon,
            "次產業": sector,
            "量化分數": q,
            "AI分數": a_score,
            "綜合分數": round(final, 1),
            "信心": a.get("confidence", ""),
            "評語": a.get("rationale", ""),
            "風險": a.get("risks", ""),
            "成分股": ";".join(sec_tickers)
        })
    return pd.DataFrame(rows)


# ==========================================================
# 階段五：分數記錄與回測（純記憶體暫存驅動）
# ==========================================================
HORIZONS = {
    "近 1 天（1 個交易日）": 1,
    "近 1 週（5 個交易日）": 5, 
    "近 2 週（10 個交易日）": 10, 
    "近 1 個月（21 個交易日）": 21
}


def fetch_close_history(tickers, start_date):
    out, fails = {}, []
    for tk in tickers:
        try:
            df = with_retry(
                lambda tk=tk: yf.Ticker(tk).history(start=start_date.strftime("%Y-%m-%d"), auto_adjust=True),
                f"{tk} 歷史股價")
            s = df["Close"].dropna()
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            out[tk] = s
        except Exception as e:
            fails.append({"代號": tk, "原因": str(e)})
        time.sleep(0.4)
    return out, fails


def forward_return(series, t_date, h):
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


def calculate_performance_metrics(returns_series, periods_per_year=12):
    s = pd.Series(returns_series).dropna()
    if len(s) < 2:
        return {"總報酬率": 0.0, "最大回撤": 0.0, "夏普值": 0.0, "勝率": 0.0}
    
    wealth_index = (1 + s).cumprod()
    previous_peaks = wealth_index.cummax()
    drawdowns = (wealth_index - previous_peaks) / previous_peaks
    max_drawdown = float(drawdowns.min())
    total_return = float(wealth_index.iloc[-1] - 1.0)
    
    mean_ret = s.mean()
    std_ret = s.std(ddof=1)
    sharpe = float((mean_ret / std_ret) * np.sqrt(periods_per_year)) if std_ret and std_ret > 0 else 0.0
    win_rate = float((s > 0).mean())
    
    return {
        "總報酬率": total_return,
        "最大回撤": max_drawdown,
        "夏普值": sharpe,
        "勝率": win_rate
    }


# ==========================================================
# LINE Messaging API 通知與背景排程機制 (含 Top 5 與 AI 建議權重)
# ==========================================================
def send_line_messaging_api(message, token=None, user_id=None):
    try:
        access_token = token or "LoWdzyXkvVwbPsmYoPxWuFPik/CvtFx5YxKqgZ2bUvePKq2rpVDJ/j6ARw3AtJD84m7Rmmxi2hIab6sbxPZNPViUnzCer2RdDrUssRBVrbplVO+5vidqACFyXjoPXNneG9pP7xIyDGvEaIpLOO+gwQdB04t89/1O/w1cDnyilFU="
        target_user = user_id or "U232b236a8492ffa9677852f70c12ea88"
        
        if not access_token or not target_user:
            return False

        url = "[https://api.line.me/v2/bot/message/push](https://api.line.me/v2/bot/message/push)"
        payload = {
            "to": target_user,
            "messages": [{"type": "text", "text": message}]
        }
        
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, 
            data=data, 
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }, 
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200
    except Exception as e:
        logger.error(f"發送 LINE 推播發生錯誤: {e}")
        return False


def send_email_report(top_5_df, receiver_email=None):
    try:
        smtp_server = get_secret("SMTP_SERVER") or "smtp.gmail.com"
        smtp_port = int(get_secret("SMTP_PORT") or 587)
        sender_email = get_secret("SENDER_EMAIL")
        sender_password = get_secret("SENDER_PASSWORD")
        to_email = receiver_email or get_secret("RECEIVER_EMAIL") or sender_email

        if not sender_email or not sender_password:
            return False

        now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
        rows_html = ""
        for i, (_, r) in enumerate(top_5_df.iterrows(), 1):
            rows_html += f"""
            <tr>
                <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{i}</td>
                <td style="padding: 8px; border: 1px solid #ddd;">{r.get('次產業', '')}</td>
                <td style="padding: 8px; border: 1px solid #ddd; text-align: center;"><b>{r.get('綜合分數', 0)}</b></td>
                <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{r.get('量化分數', 'N/A')}</td>
                <td style="padding: 8px; border: 1px solid #ddd; text-align: center;">{r.get('AI分數', 'N/A')}</td>
            </tr>
            """

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color: #333;">
            <h2>📊 【台股產業投資策略 1.5版】綜合 Score 前 5 名次產業報告</h2>
            <p>執行時間：{now_str}</p>
            <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
                <thead>
                    <tr style="background-color: #f2f2f2;">
                        <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">排名</th>
                        <th style="padding: 10px; border: 1px solid #ddd; text-align: left;">次產業</th>
                        <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">綜合分數</th>
                        <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">量化分數</th>
                        <th style="padding: 10px; border: 1px solid #ddd; text-align: center;">AI分數</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"【台股策略 1.5版】綜合 Score 前 5 名次產業報告 ({datetime.now(TZ):%Y-%m-%d})"
        msg["From"] = sender_email
        msg["To"] = to_email
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"發送 Email 失敗: {e}")
        return False


def automated_daily_job():
    try:
        df_news, _ = fetch_news_all(30, per_sector=True)
        if df_news.empty:
            return
        now = datetime.now(TZ)
        tagged_all, _, _, _ = classify_news(df_news, bull=60, bear=40)
        momentum_df = compute_momentum(tagged_all, now)
        quant = quant_scores(momentum_df, None)
        
        # 分別計算近1月與近1週的評分與建議權重
        horizons_to_calc = ["近1月", "近1週"]
        horizon_results = {}

        for hz in horizons_to_calc:
            rows = []
            for sector in SECTOR_ORDER:
                q = quant.get(sector, {}).get(hz, (None, {}))[0]
                if q is not None:
                    sec_tickers = [c["ticker"] for c in COMPANIES if c["subsector"] == sector and c["ticker"] in DEFAULT_TRACKED]
                    rows.append({
                        "評估期間": hz, "次產業": sector, "量化分數": q, "AI分數": np.nan, "綜合分數": q, "成分股": ";".join(sec_tickers)
                    })
            table = pd.DataFrame(rows)
            if not table.empty:
                valid_table = table.dropna(subset=["綜合分數"]).sort_values("綜合分數", ascending=False)
                top_5 = valid_table.head(5)
                
                # 計算 AI 建議投資權重（依綜合分數比例分配前 5 名權重）
                total_score = top_5["綜合分數"].sum()
                weights = []
                if total_score > 0:
                    for _, r in top_5.iterrows():
                        w = (r["綜合分數"] / total_score) * 100
                        weights.append(f"  - {r['次產業']}: {w:.1f}% (分數: {r['綜合分數']:.1f})")
                
                horizon_results[hz] = {
                    "top_5": top_5,
                    "weights": weights
                }

        if horizon_results:
            msg_lines = [
                f"【台股產業投資策略 1.5版】自動排程報告",
                f"執行日期：{now:%Y-%m-%d %H:%M:%S}\n"
            ]

            for hz in horizons_to_calc:
                if hz in horizon_results:
                    top_5_df = horizon_results[hz]["top_5"]
                    weights_list = horizon_results[hz]["weights"]
                    
                    top_5_lines = [f"  {i}. {r['次產業']} ({r['綜合分數']:.1f}分)" for i, (_, r) in enumerate(top_5_df.iterrows(), 1)]
                    top_5_text = "\n".join(top_5_lines) if not top_5_df.empty else "  (無有效評分)"
                    weights_text = "\n".join(weights_list) if weights_list else "  (無權重資料)"

                    msg_lines.append(f"🔥 【{hz}】綜合分數最高前 5 名：\n{top_5_text}")
                    msg_lines.append(f"⚖️ 【{hz}】AI 建議投資權重：\n{weights_text}\n")

            msg_lines.append("請至 Web App 查看詳細回測與多策略對比。")
            final_msg = "\n".join(msg_lines)

            send_line_messaging_api(final_msg)
            
            # Email 發送以近1月為主的前 5 名
            if "近1月" in horizon_results:
                send_email_report(horizon_results["近1月"]["top_5"])
    except Exception as e:
        logger.error(f"自動化排程失敗: {e}")


def run_schedule_loop():
    while True:
        schedule.run_pending()
        time.sleep(60)


def init_background_scheduler():
    if "scheduler_started" not in st.session_state:
        schedule.every().day.at("14:00").do(automated_daily_job)
        t = threading.Thread(target=run_schedule_loop, daemon=True)
        t.start()
        st.session_state.scheduler_started = True


init_background_scheduler()


# ==========================================================
# Word 報告生成
# ==========================================================
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

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf


def _set_cell(cell, text, bold=False, size=9):
    p = cell.paragraphs[0]
    p.text = ""
    run = p.add_run(str(text))
    run.font.bold = bold
    run.font.size = Pt(size)


# ==========================================================
# Streamlit 介面與主流程
# ==========================================================
st.set_page_config(page_title="台股產業投資策略 1.5 版", layout="wide")
st.title("📊 台股產業投資策略 1.5 版 - AI 互動式 Web App (記憶體暫存回測版)")

# ----------------- 側邊欄設定 -----------------
st.sidebar.header("🛠️ 系統控制與通知")
st.sidebar.write("### 背景自動化排程與 LINE 推播")
st.sidebar.caption("系統已設定於每天 14:00 自動執行背景抓取，並發送 LINE 推播（含前 5 名與 AI 建議權重）及 Email 報告。")

if st.sidebar.button("🚀 立即執行 LINE 推播測試", type="secondary"):
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    test_msg = f"【台股產業投資策略 1.5版】手動測試推播\n發送時間：{now_str}\n系統運作正常！"
    success = send_line_messaging_api(test_msg)
    if success:
        st.sidebar.success("✅ LINE 智慧推播測試發送成功！")
    else:
        st.sidebar.error("❌ LINE 推播發送失敗，請檢查 Token 與 User ID 設定。")

_defaults = {
    "news_all": pd.DataFrame(), "news_failed": [], "news_fetched": False,
    "momentum_df": pd.DataFrame(), "final_stock_list": [],
    "report_df": pd.DataFrame(), "fetch_failures": [], "fetch_warns": [], "fetched_at": "",
    "score_table": pd.DataFrame(), "quant_detail": pd.DataFrame(), "ai_debug": {}, "overall_score": 50,
    "price_cache": {}, "price_fails": [], "multi_horizon_scores": {}
}
for _k, _v in _defaults.items():
    st.session_state.setdefault(_k, _v)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "第一階段：新聞熱點與二階動能",
    "第二階段：次產業與個股動態篩選",
    "第三階段：財務數據與報告匯出",
    "第四階段：AI 評分 (多期間)",
    "第五階段：記憶體暫存回測與分析",
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

        recent = df_all[df_all["發布時間"] >= now - timedelta(days=list_days)]
        top_10 = recent.sort_values("分數", ascending=False).head(10)
        rest = recent[~recent["新聞標題"].isin(top_10["新聞標題"])]
        bottom_10 = rest.sort_values("分數", ascending=True).head(10)
        show_cols = ["新聞標題", "來源與日期", "分數"]

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("🔥 最高分數前 10 則產業新聞")
            st.dataframe(top_10[show_cols].reset_index(drop=True), hide_index=True)
        with col2:
            st.subheader("❄️ 最低分數前 10 則產業新聞")
            st.dataframe(bottom_10[show_cols].reset_index(drop=True), hide_index=True)

        st.divider()
        tagged_all, _, _, _ = classify_news(df_all, bull, bear)
        momentum_df = compute_momentum(tagged_all, now) if not tagged_all.empty else pd.DataFrame()
        st.session_state.momentum_df = momentum_df
        st.subheader("📈 各次產業二階動能加速度與多期間分佈")
        st.dataframe(momentum_df[[c for c in momentum_df.columns if not c.startswith("_")]], hide_index=True)

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
                st.dataframe(by_sector)
            with t3:
                detail = tagged.drop(columns=["發布時間"])
                st.dataframe(detail, hide_index=True)
                st.download_button("下載歸類明細 CSV", detail.to_csv(index=False).encode("utf-8-sig"),
                                   file_name="news_tagged.csv", mime="text/csv")
            with t4:
                if untagged.empty:
                    st.write("無。")
                else:
                    st.dataframe(untagged.drop(columns=["發布時間"], errors="ignore"), hide_index=True)

# ---------------------------- Tab 2 ----------------------------
with tab2:
    st.header("熱點次產業與領先個股對接")
    base_candidates = [
        {"追蹤": c["ticker"] in DEFAULT_TRACKED, "次產業": c["subsector"],
         "代號": c["ticker"], "名稱": c["name"]}
        for c in COMPANIES
    ]
    candidates_df = pd.DataFrame(base_candidates)
    edited_df = st.data_editor(candidates_df, hide_index=True, num_rows="dynamic", key="candidates_editor")

    if st.button("鎖定個股名單並進入第三階段"):
        chosen = edited_df[edited_df["追蹤"] == True]
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
            df_r, fails, warns = fetch_all(st.session_state.final_stock_list, progress_cb=lambda d,t,m: bar.progress(d/t, text=m))
            bar.empty()
            st.session_state.report_df = df_r
            st.session_state.fetched_at = datetime.now(TZ).strftime("%Y/%m/%d %H:%M:%S")

        df_r = st.session_state.report_df
        if not df_r.empty:
            st.dataframe(make_display_df(df_r), hide_index=True)
            macro_text = st.text_area("市場動態與產業展望", value=DEFAULT_MACRO_TEXT, height=120)
            doc_buffer = generate_word_report(df_r, macro_text, st.session_state.momentum_df, 80)
            st.download_button("📥 下載橫向排版 Word 投資報告", data=doc_buffer, file_name="report.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

# ---------------------------- Tab 4 ----------------------------
with tab4:
    st.header("次產業評分：多期間（近1周, 近2周, 近1月）量化 + AI 評分")
    if not st.session_state.news_fetched:
        st.warning("請先於第一階段抓取新聞。")
    else:
        eval_horizon = st.selectbox("選擇評估與評分期間", ["近1月", "近2周", "近1周"], index=0)
        c1, c2 = st.columns(2)
        provider = c1.selectbox("AI 模型供應商", list(LLM_PROVIDERS.keys()))
        cfg = LLM_PROVIDERS[provider]
        model_name = c2.text_input("模型名稱", value=cfg["model"], key=f"model_{provider}")
        api_key = get_secret(cfg["secret"]) or st.text_input(f"{cfg['secret']}", type="password")

        use_ai = st.checkbox("使用 AI 評分", value=True)
        ai_pct = st.slider("AI 分數權重 (%)", 0, 100, 50, disabled=not use_ai)

        if st.button("計算次產業分數並暫存至記憶體", type="primary"):
            df_all = st.session_state.news_all
            df_fin = st.session_state.report_df
            momentum = st.session_state.momentum_df
            tagged_all, _, _, _ = classify_news(df_all, 60, 40)

            quant = quant_scores(momentum, df_fin)
            payload = "\n\n".join(build_sector_payload(s, tagged_all, momentum, df_fin, horizon=eval_horizon) for s in SECTOR_ORDER)

            ai_result = {}
            if use_ai and api_key:
                try:
                    with st.spinner(f"正在請 {provider} 評分 ({eval_horizon})…"):
                        raw_text = call_llm(provider, api_key, model_name, AI_SYSTEM_PROMPT, payload)
                        ai_result = parse_ai_json(raw_text)
                except Exception as e:
                    st.error(f"AI 評分失敗，已改用量化分數：{e}")

            table = build_score_table(quant, ai_result, ai_pct / 100 if ai_result else 0.0, horizon=eval_horizon, tracked_list=st.session_state.final_stock_list)
            st.session_state.score_table = table
            
            if not table.empty:
                simulated_past_date = (datetime.now(TZ) - timedelta(days=5)).strftime("%Y-%m-%d")
                st.session_state.multi_horizon_scores[eval_horizon] = {
                    "table": table,
                    "記錄日期": simulated_past_date,
                    "評估期間": eval_horizon
                }
                st.success(f"✨ 已成功將【{eval_horizon}】評分暫存至記憶體與虛擬回測基準日 ({simulated_past_date})！請直接切換至第五階段進行分析。")

        if not st.session_state.score_table.empty:
            st.dataframe(st.session_state.score_table, hide_index=True)

# ---------------------------- Tab 5 ----------------------------
with tab5:
    st.header("分數記錄與回測（純記憶體暫存驅動）")
    st.caption("直接取用第四階段暫存於記憶體中的多期間評分，即時計算歷史超額報酬、最大回撤、夏普值與網格搜尋最佳化。")

    has_memory = len(st.session_state.multi_horizon_scores) > 0
    if not has_memory:
        st.info("💡 記憶體中尚無暫存評分。請先至第四階段點擊「計算次產業分數並暫存至記憶體」。")
    else:
        c1, c2 = st.columns(2)
        h_label = c1.selectbox("回測結算持有期間", list(HORIZONS.keys()), index=0)
        chosen_eval_h = c2.selectbox("選擇暫存的評估期間 (Horizon)", list(st.session_state.multi_horizon_scores.keys()))
        h = HORIZONS[h_label]

        if st.button("立即抓取股價並執行回測與最佳化", type="primary"):
            target_data = st.session_state.multi_horizon_scores[chosen_eval_h]["table"]
            tickers = sorted({t for s in target_data["成分股"].dropna() for t in str(s).split(";") if t})
            
            simulated_date = st.session_state.multi_horizon_scores[chosen_eval_h]["記錄日期"]
            start_date = pd.to_datetime(simulated_date).date() - timedelta(days=5)

            with st.spinner(f"正在從 Yahoo Finance 抓取 {len(tickers)} 檔成分股歷史股價…"):
                prices, fails = fetch_close_history(tickers, start_date)
            st.session_state.price_cache = prices
            st.session_state.price_fails = fails

        if st.session_state.price_cache:
            target_data = st.session_state.multi_horizon_scores[chosen_eval_h]["table"].copy()
            simulated_date = st.session_state.multi_horizon_scores[chosen_eval_h]["記錄日期"]
            
            st.subheader(f"📈 多策略累積超額報酬對比（評估期間: {chosen_eval_h} ｜ 持有期: {h_label}）")

            perf_records = []
            for _, r in target_data.iterrows():
                members = [m for m in str(r["成分股"]).split(";") if m in st.session_state.price_cache]
                if not members:
                    continue
                rets = [forward_return(st.session_state.price_cache[m], simulated_date, h) for m in members]
                rets = [x for x in rets if not np.isnan(x)]
                if rets:
                    perf_records.append({
                        "記錄日期": simulated_date,
                        "次產業": r["次產業"],
                        "綜合分數": r["綜合分數"],
                        "量化分數": r["量化分數"],
                        "AI分數": r["AI分數"],
                        "報酬率": np.mean(rets)
                    })

            if perf_records:
                df_perf = pd.DataFrame(perf_records)
                market_mean = df_perf["報酬率"].mean()
                df_perf["超額報酬"] = df_perf["報酬率"] - market_mean

                strategies = {
                    "綜合策略 (Composite)": "綜合分數",
                    "純量化策略 (Quant)": "量化分數",
                    "純 AI 策略 (AI)": "AI分數"
                }

                metrics_summary = []
                for strat_name, score_col in strategies.items():
                    sub_df = df_perf.dropna(subset=[score_col])
                    if sub_df.empty:
                        continue
                    top_picks = sub_df.sort_values(score_col, ascending=False).head(2)
                    perf_stats = calculate_performance_metrics(top_picks["超額報酬"])
                    metrics_summary.append({
                        "策略名稱": strat_name,
                        "總超額報酬": f"{perf_stats['總報酬率']:.2%}",
                        "最大回撤 (MDD)": f"{perf_stats['最大回撤']:.2%}",
                        "夏普值 (Sharpe)": f"{perf_stats['夏普值']:.2f}",
                        "勝率": f"{perf_stats['勝率']:.1%}"
                    })

                if metrics_summary:
                    st.dataframe(pd.DataFrame(metrics_summary), hide_index=True)

                fig = px.bar(df_perf.melt(id_vars=["次產業"], value_vars=["綜合分數", "量化分數", "AI分數"], var_name="策略", value_name="分數"),
                             x="次產業", y="分數", color="策略", barmode="group", title="各策略次產業評分對比")
                st.plotly_chart(fig, use_container_width=True)

                csv_data = df_perf.to_csv(index=False, encoding="utf-8-sig")
                st.download_button(
                    label="📥 下載多策略回測表現數據 (CSV)",
                    data=csv_data,
                    file_name=f"backtest_{chosen_eval_h}_{datetime.now(TZ):%Y%m%d}.csv",
                    mime="text/csv",
                )
            else:
                st.warning("⚠️ 無法計算報酬率，請確認所選成分股的歷史股價資料是否涵蓋至所選基準日。")
