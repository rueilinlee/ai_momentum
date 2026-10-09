import math
import time
from datetime import datetime, timezone, timedelta
from io import BytesIO
import io
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import xml.etree.ElementTree as ET

import pandas as pd
import numpy as np
import streamlit as st
import yfinance as yf
import statsmodels.api as sm
from statsmodels.regression.rolling import RollingOLS
import lightgbm as lgb
import shap
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, roc_auc_score
import matplotlib.pyplot as plt
import requests
import re
from bs4 import BeautifulSoup
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

# FinBERT 與 Hugging Face 相關套件嘗試載入
try:
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    import torch
    HAS_FINBERT = True
except Exception:
    HAS_FINBERT = False

# 記錄程式開始執行時間
start_time_perf = time.time()

# ==========================================
# 0. 頁面設定
# ==========================================
st.set_page_config(page_title="跨領域專家 AI 投資分析與量化預測系統", layout="wide", page_icon="📈")

DISCLAIMER = (
    "免責聲明：本報告由程式依公開資料與使用者設定之參數自動試算，僅供研究與學習參考，"
    "不構成任何投資建議。資料來源為 Yahoo Finance 與各大財經媒體，可能有延遲或缺漏；標示「手動」者為使用者自行輸入。"
)

def get_taiwan_time_str(fmt="%Y-%m-%d %H:%M:%S"):
    return datetime.now(timezone(timedelta(hours=8))).strftime(fmt)

# ==========================================
# 1. 標的解析與中英文名稱對照機制
# ==========================================
LOCAL_NAME_MAP = {
    "今國光": "6209", "台積電": "2330", "鴻海": "2317", "聯發科": "2454",
    "聯電": "2303", "台達電": "2308", "中華電": "2412", "富邦金": "2881",
    "國泰金": "2882", "長榮": "2603", "陽明": "2609", "萬海": "2615",
    "廣達": "2382", "緯創": "3231", "技嘉": "2376", "華碩": "2357",
    "宏碁": "2353", "大立光": "3008", "元太": "8069", "世界": "5347",
    "環球晶": "6488",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
}

def _is_us_ticker(text: str) -> bool:
    return text.isascii() and text.isalpha() and len(text) <= 5

def _has_price(symbol):
    try:
        return not yf.Ticker(symbol).history(period="5d").empty
    except Exception:
        return False

COMPANY_SOURCES = [
    ("TW", [("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "json"),
            ("https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv", "csv")]),
    ("TWO", [("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", "json"),
             ("https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv", "csv")]),
]

def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).replace("臺", "台")

def _fetch_company_list(suffix, candidates):
    for url, kind in candidates:
        try:
            r = requests.get(url, headers=HEADERS, timeout=6)
            r.raise_for_status()
            if kind == "json": rows = r.json()
            else:
                df = pd.read_csv(io.StringIO(r.content.decode("utf-8-sig")), dtype=str)
                rows = df.to_dict("records")
            out = []
            for row in rows:
                row = {str(k).lstrip("\ufeff").strip(): v for k, v in row.items()}
                code = str(row.get("公司代號", "")).strip()
                short = _norm(row.get("公司簡稱"))
                full = _norm(row.get("公司名稱"))
                if code and (short or full):
                    out.append({"code": code, "short": short, "full": full, "suffix": suffix})
            if out: return out
        except Exception: continue
    return []

@st.cache_data(ttl=86400, show_spinner=False)
def load_company_table():
    table = []
    with ThreadPoolExecutor(max_workers=len(COMPANY_SOURCES)) as ex:
        futures = [ex.submit(_fetch_company_list, sfx, cands) for sfx, cands in COMPANY_SOURCES]
        for f in futures:
            try: table.extend(f.result(timeout=12))
            except Exception: pass
    return table

def _lookup_company(query: str, table):
    q = _norm(query)
    if not q or not table: return None
    rules = [lambda c: c["short"] == q, lambda c: c["full"] == q, lambda c: c["short"].startswith(q), lambda c: q in c["short"] or q in c["full"]]
    for rule in rules:
        hits = [c for c in table if rule(c)]
        if hits: return min(hits, key=lambda c: len(c["short"] or c["full"]))
    return None

def _suffix_for_code(code: str, table) -> Optional[str]:
    for c in table:
        if c["code"] == code: return c["suffix"]
    return None

def _isin_lookup(clean_query: str) -> Optional[str]:
    sources = ["https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "https://isin.tpex.org.tw/isin/C_public.jsp?strMode=4"]
    for url in sources:
        try:
            res = requests.get(url, headers=HEADERS, timeout=5)
            res.encoding = "big5"
            soup = BeautifulSoup(res.text, "html.parser")
            for row in soup.find_all("tr"):
                tds = row.find_all("td")
                if not tds: continue
                parts = tds[0].get_text().strip().split()
                if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) in (4, 5):
                    if _norm("".join(parts[1:])) == clean_query: return parts[0]
        except Exception: continue
    return None

@st.cache_data(ttl=3600)
def resolve_symbol(user_input):
    text = user_input.strip()
    upper_text = text.upper()
    if upper_text == "0000" or upper_text == "^TWII" or text == "大盤": return "^TWII"
    match_fix = re.match(r"^(\d{4,5})(TW|TWO)$", upper_text)
    if match_fix: return f"{match_fix.group(1)}.{match_fix.group(2)}"
    if upper_text.endswith((".TW", ".TWO", ".US", "=F")) or upper_text.startswith("^"): return upper_text
    if _is_us_ticker(upper_text): return upper_text

    table = load_company_table()
    if not table: load_company_table.clear()
    clean_query = _norm(text)

    code = LOCAL_NAME_MAP.get(clean_query) or (upper_text if upper_text.isdigit() else None)
    if code:
        suffix = _suffix_for_code(code, table)
        if suffix: return f"{code}.{suffix}"
        for sfx in (".TW", ".TWO"):
            if _has_price(code + sfx): return code + sfx
        return f"{code}.TW"

    hit = _lookup_company(text, table)
    if hit: return f"{hit['code']}.{hit['suffix']}"

    try:
        search_url = f"https://query1.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(text)}&quotesCount=5&newsCount=0"
        data = requests.get(search_url, headers=HEADERS, timeout=5).json()
        for q in data.get("quotes", []):
            sym = q.get("symbol", "")
            if sym.endswith((".TW", ".TWO")):
                m = re.match(r"^(\d{4,5})(TW|TWO)$", sym.upper())
                if m: return f"{m.group(1)}.{m.group(2)}"
                return sym
    except Exception: pass

    code = _isin_lookup(clean_query)
    if code:
        for sfx in (".TW", ".TWO"):
            if _has_price(code + sfx): return code + sfx
        return f"{code}.TW"
    return text

@st.cache_data(ttl=3600)
def get_company_name(symbol):
    if symbol == "^TWII": return "大盤加權指數 (^TWII)"
    cn_mapping = {
        "NVDA": "輝達 (NVIDIA)", "AAPL": "蘋果 (Apple)", "TSLA": "特斯拉 (Tesla)",
        "MSFT": "微軟 (Microsoft)", "GOOGL": "谷歌 (Alphabet)", "AMZN": "亞馬遜 (Amazon)",
        "META": "Meta (臉書)", "AMD": "超微 (AMD)", "TSM": "台積電 ADR (TSMC)"
    }
    clean_sym = symbol.upper().strip()
    if clean_sym in cn_mapping: return cn_mapping[clean_sym]
    if clean_sym.endswith((".TW", ".TWO")):
        stock_id = clean_sym.split(".")[0]
        for name, code in LOCAL_NAME_MAP.items():
            if code == stock_id: return f"{name} ({symbol})"
    try:
        if ".TW" in symbol or ".TWO" in symbol:
            stock_id = symbol.split('.')[0]
            tw_yahoo_url = f"https://tw.stock.yahoo.com/quote/{stock_id}"
            res = requests.get(tw_yahoo_url, headers=HEADERS, timeout=5)
            match = re.search(r'<title>(.*?)\(', res.text)
            if match:
                extracted_name = match.group(1).strip()
                if extracted_name and "Yahoo" not in extracted_name and "找不到" not in extracted_name:
                    return f"{extracted_name} ({symbol})"
        stock = yf.Ticker(symbol)
        name = stock.info.get("longName") or stock.info.get("shortName")
        if name: return f"{name} ({symbol})"
    except Exception: pass
    return symbol

# ==========================================
# 2. 爬蟲與 FinBERT 評分引擎（含嚴格雜訊過濾）
# ==========================================
@st.cache_resource(show_spinner=False)
def load_finbert_model():
    if not HAS_FINBERT: return None, None
    try:
        model_name = "ProsusAI/finbert"
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSequenceClassification.from_pretrained(model_name)
        model.eval()
        return tokenizer, model
    except Exception:
        return None, None

def analyze_sentiment_finbert_nonlinear(titles, hours=168):
    if not titles: return 5.0, 0, 0
    tokenizer, model = load_finbert_model()
    if not tokenizer or not model:
        return None, 0, 0
    try:
        batch_titles = titles[:40]
        inputs = tokenizer(batch_titles, padding=True, truncation=True, max_length=64, return_tensors="pt")
        with torch.no_grad():
            outputs = model(**inputs)
            probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
        pos_scores = probs[:, 0].numpy()
        neg_scores = probs[:, 1].numpy()
        b_count = int(sum(1 for p, n in zip(pos_scores, neg_scores) if p > n))
        r_count = int(sum(1 for p, n in zip(pos_scores, neg_scores) if n > p))
        net_diff = float(np.mean(pos_scores) - np.mean(neg_scores))
        days_span = hours / 24.0
        time_weight_bias = math.log(days_span + 1.0) * 0.15
        nonlinear_factor = float(np.tanh(net_diff * 2.5 + time_weight_bias * 0.2))
        final_score = round(float(np.clip(5.0 + nonlinear_factor * 3.5, 1.5, 9.0)), 1)
        return final_score, b_count, r_count
    except Exception:
        return None, 0, 0

def _is_relevant_news(title: str, clean_name: str, clean_code: str) -> bool:
    """嚴格過濾與該標的無關之社會、影劇、政治等雜訊新聞"""
    t_lower = title.lower()
    
    # 1. 必須包含標的名稱或代號，或者是大盤指數相關
    has_target = (clean_code in t_lower) or (clean_name in t_lower) or ("台股" in t_lower) or ("大盤" in t_lower)
    if not has_target and clean_code != "^TWII":
        return False
        
    # 2. 定義絕對要排除的非財經雜訊關鍵字
    noise_keywords = [
        "車禍", "撞擊", "身亡", "死亡", "骨折", "送醫", "命案", "凶殺", "鬼臉", "影星", 
        "抗癌", "剃光頭", "抗癌歷程", "福利政見", "里長", "總統", "立委", "選戰", "連假悲劇"
    ]
    for noise in noise_keywords:
        if noise in t_lower:
            return False
            
    return True

def fetch_rss_feed_timed(rss_url, keyword, hours=168, clean_name="", clean_code=""):
    titles = []
    items_with_time = []
    time_threshold = datetime.now() - timedelta(hours=hours)
    try:
        res = requests.get(rss_url, headers=HEADERS, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            for item in root.findall('.//item'):
                t_elem = item.find('title')
                d_elem = item.find('pubDate')
                title_text = t_elem.text.strip() if t_elem is not None and t_elem.text else ""
                pub_dt = datetime.now()
                if d_elem is not None and d_elem.text:
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(d_elem.text).replace(tzinfo=None)
                    except Exception: pass
                
                if pub_dt >= time_threshold and title_text:
                    if clean_name and clean_code:
                        if not _is_relevant_news(title_text, clean_name, clean_code):
                            continue
                    titles.append(title_text)
                    items_with_time.append((title_text, pub_dt))
    except Exception: pass
    return titles, items_with_time

def fetch_moneydj_rss_timed(stock_code, company_name, hours=168):
    clean_code = stock_code.split('.')[0]
    clean_name = company_name.split('(')[0].strip()
    rss_url = f"https://www.moneydj.com/KMDJ/rss/rss.aspx?svc=NW&a={clean_code}"
    titles, items = fetch_rss_feed_timed(rss_url, clean_code, hours, clean_name, clean_code)
    if not titles:
        g_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(clean_name)}+site:moneydj.com&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        titles, items = fetch_rss_feed_timed(g_url, clean_name, hours, clean_name, clean_code)
    return titles, items

def fetch_chinatimes_rss_timed(company_name, stock_code, hours=168):
    clean_name = company_name.split('(')[0].strip()
    clean_code = stock_code.split('.')[0]
    rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(clean_name)}+site:chinatimes.com+財經&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    return fetch_rss_feed_timed(rss_url, clean_code, hours, clean_name, clean_code)

def fetch_google_news_rss_timed(company_name, stock_code, hours=168):
    clean_code = stock_code.split('.')[0]
    clean_name = company_name.split('(')[0].strip()
    days_total = max(1, int(hours / 24))
    now = datetime.now()
    date_after = (now - timedelta(days=days_total)).strftime("%Y-%m-%d")
    date_before = now.strftime("%Y-%m-%d")
    search_query = f"{clean_name} {clean_code} 股市 財經 after:{date_after} before:{date_before}"
    rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(search_query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    return fetch_rss_feed_timed(rss_url, clean_code, hours, clean_name, clean_code)

@st.cache_data(ttl=1800)
def comprehensive_quant_evaluation(stock_code, company_name, hours=168):
    g_titles, g_items = fetch_google_news_rss_timed(company_name, stock_code, hours)
    m_titles, m_items = fetch_moneydj_rss_timed(stock_code, company_name, hours)
    c_titles, c_items = fetch_chinatimes_rss_timed(company_name, stock_code, hours)

    sources_count = {"Google News": len(g_titles), "MoneyDJ": len(m_titles), "中時新聞網": len(c_titles)}
    seen = set()
    unique_items = []
    for title, dt in (g_items + m_items + c_items):
        clean_t = re.sub(r"\s+", "", title)
        if clean_t not in seen:
            seen.add(clean_t)
            unique_items.append((title, dt))

    all_titles = [item[0] for item in unique_items]
    finbert_sent, fb_bull, fb_bear = analyze_sentiment_finbert_nonlinear(all_titles, hours)
    
    bullish = ["漲", "高", "強", "買超", "創高", "突破", "擴產", "營收揚升", "暢旺", "多方", "利多", "成長", "大賺", "雙增"]
    bearish = ["跌", "殺", "跌停", "衰退", "利空", "縮減", "賣超", "低迷", "修正", "震盪", "壓力"]
    growth_pos = ["展望佳", "成長", "擴產", "訂單滿", "創高", "突破", "上修", "看好", "強勁", "增溫", "新單"]
    growth_neg = ["下修", "衰退", "保守", "庫存", "壓力", "疲弱", "下滑", "淡季"]
    hotspot_keywords = ["突破", "爆發", "大漲", "創高", "急單", "跌停", "崩", "震撼", "重訊"]

    if not all_titles:
        return 5.0, 5.0, 4.0, 2, 1, 2, 1, max(3, int(hours / 24) * 2), all_titles, sources_count, unique_items

    now = datetime.now()
    b_cnt, r_cnt, gp_cnt, gn_cnt = 0, 0, 0, 0
    weighted_hotspot_sum = 0.0

    for title, dt in unique_items:
        age_hours = max(0.0, (now - dt).total_seconds() / 3600.0)
        decay_lambda = 0.6 if hours <= 48 else (0.25 if hours <= 336 else 0.06)
        time_decay = math.exp(-decay_lambda * (age_hours / max(8.0, hours)))
        b_hits = sum(1 for w in bullish if w in title)
        r_hits = sum(1 for w in bearish if w in title)
        gp_hits = sum(1 for w in growth_pos if w in title)
        gn_hits = sum(1 for w in growth_neg if w in title)
        h_hits = sum(1 for w in hotspot_keywords if w in title)
        b_cnt += b_hits; r_cnt += r_hits; gp_cnt += gp_hits; gn_cnt += gn_hits
        weighted_hotspot_sum += (h_hits + 0.5) * time_decay

    total_count = max(1, len(all_titles))
    days_span = hours / 24.0

    if finbert_sent is not None:
        time_sentiment_boost = math.log(days_span + 1.0) * 0.25
        s_score = round(float(np.clip(finbert_sent + time_sentiment_boost * (1.0 if b_cnt >= r_cnt else -1.0), 1.5, 9.2)), 1)
        b_cnt = max(b_cnt, fb_bull); r_cnt = max(r_cnt, fb_bear)
    else:
        s_ratio = (b_cnt - r_cnt) / (total_count + 5.0)
        s_score = round(float(np.clip(5.0 + math.tanh(s_ratio * 3.0) * 3.5 + math.log(days_span + 1) * 0.15, 1.5, 9.2)), 1)

    growth_net = gp_cnt - gn_cnt
    growth_intensity = growth_net / math.pow(total_count, 0.75)
    temporal_curve = 1.0 + 0.35 * math.atan(days_span / 15.0)
    g_score = round(float(np.clip(5.0 + (math.tanh(growth_intensity * 2.8) * 3.2) * temporal_curve, 1.5, 9.2)), 1)

    burst_density = weighted_hotspot_sum / math.pow(days_span, 0.65)
    h_score = round(float(np.clip(2.0 + (2.0 / math.pi) * math.atan(burst_density * 0.4) * 6.5, 1.0, 9.5)), 1)

    return s_score, g_score, h_score, max(1, b_cnt), max(0, r_cnt), max(1, gp_cnt), max(0, gn_cnt), total_count, all_titles, sources_count, unique_items

# ==========================================
# 3. 行情財報擷取與機器學習特徵
# ==========================================
def _eps_series(df):
    if df is None or getattr(df, "empty", True): return None
    for key in ("Diluted EPS", "Basic EPS"):
        if key in df.index:
            s = df.loc[key].dropna()
            if not s.empty: return s.sort_index(ascending=False)
    return None

def download_us_daily(years: int = 5) -> pd.DataFrame:
    raw = yf.download(["NVDA", "^SOX", "^DJI", "^IRX"], period=f"{years}y", interval="1d", progress=False)["Close"]
    if isinstance(raw, pd.Series): raw = raw.to_frame()
    raw = raw.loc[:, ~raw.columns.duplicated()]
    raw.index = pd.DatetimeIndex(raw.index).tz_localize(None).normalize()
    return raw

def _map_daily_to_bars(daily: pd.Series, bar_index: pd.DatetimeIndex) -> np.ndarray:
    s = daily.dropna().copy()
    if s.empty: return np.full(len(bar_index), 0.0)
    s.index = s.index + pd.Timedelta(days=1)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    idx_naive = pd.DatetimeIndex(bar_index)
    if idx_naive.tz is not None: idx_naive = idx_naive.tz_localize(None)
    union_idx = s.index.union(idx_naive).sort_values()
    return s.reindex(union_idx).ffill().reindex(idx_naive).values

def build_feature_frame(symbol: str, market_data: pd.DataFrame, us_daily: pd.DataFrame, current_nlp: dict, rf_tw_daily: float = 0.017 / 365):
    stock = market_data[symbol].dropna()
    twii = market_data["^TWII"].reindex(stock.index).ffill() if "^TWII" in market_data.columns else pd.Series(stock.values, index=stock.index)
    
    us_cols = [c for c in ["NVDA", "^SOX", "^DJI"] if c in us_daily.columns]
    us_ret = us_daily[us_cols].pct_change() if us_cols else pd.DataFrame(index=us_daily.index)
    rf_us = (us_daily["^IRX"] / 100 / 365).ffill() if "^IRX" in us_daily.columns else pd.Series(0.01 / 365, index=us_daily.index)

    shock = pd.Series(0.0, index=us_daily.index)
    if symbol.upper() != "NVDA" and {"NVDA", "^SOX", "^DJI"}.issubset(us_ret.columns):
        sub = us_ret.assign(RF=rf_us.reindex(us_ret.index).ffill()).dropna()
        if len(sub) > 30:
            y, X_reg = sub["NVDA"] - sub["RF"], sm.add_constant(pd.DataFrame({"DJI_Excess": sub["^DJI"] - sub["RF"], "SOX_Excess": sub["^SOX"] - sub["RF"]}, index=sub.index))
            shock = sm.OLS(y, X_reg).fit().resid.reindex(us_daily.index).fillna(0.0)

    df = pd.DataFrame(index=stock.index)
    df[symbol] = stock.pct_change()
    if symbol != "^TWII": df["^TWII"] = twii.pct_change()
    
    df["NVDA_Pure_Shock"] = _map_daily_to_bars(shock, stock.index)
    df["^SOX"] = _map_daily_to_bars(us_ret["^SOX"], stock.index) if "^SOX" in us_ret.columns else 0.0
    
    df["RF_TW"] = rf_tw_daily
    df["Price_Mom_30D"] = (stock.pct_change(30) - twii.pct_change(30)).shift(1)
    df["Price_Mom_5D"] = (stock.pct_change(5) - twii.pct_change(5)).shift(1)
    df["Vol_10D"] = stock.pct_change().rolling(10).std().shift(1)

    delta = stock.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df["RSI_14"] = (100 - 100 / (1 + gain / loss)).shift(1)
    df["Interaction_Term"] = df["NVDA_Pure_Shock"] * df["Price_Mom_30D"]

    df['NLP_Sent'] = (df["Price_Mom_5D"] * 20 + 5.0).clip(0, 10)
    df['NLP_Growth'] = (df["Price_Mom_30D"] * 10 + 5.0).clip(0, 10)
    df['NLP_Hotspot'] = (df["Vol_10D"] * 100 + 3.0).clip(0, 10)
    if not df.empty:
        df.loc[df.index[-1], 'NLP_Sent'] = current_nlp['sent']
        df.loc[df.index[-1], 'NLP_Growth'] = current_nlp['growth']
        df.loc[df.index[-1], 'NLP_Hotspot'] = current_nlp['hotspot']

    fwd_excess = stock.pct_change(5).shift(-5) - twii.pct_change(5).shift(-5)
    return df.replace([np.inf, -np.inf], np.nan).dropna(), fwd_excess

def calculate_target_price_for_rsi(close_prices, target_rsi, mode='drop'):
    last_close = close_prices.iloc[-1]
    step = last_close * 0.005
    sim_price = last_close
    def calc_single_rsi(p_series):
        delta = p_series.diff()
        rs = (delta.where(delta > 0, 0).rolling(14).mean() / (-delta.where(delta < 0, 0)).rolling(14).mean())
        return (100 - (100 / (1 + rs))).iloc[-1]
    current_rsi = calc_single_rsi(close_prices)
    if mode == 'drop':
        if current_rsi <= target_rsi: return last_close, current_rsi
        for _ in range(100):
            sim_price -= step
            sim_rsi = calc_single_rsi(pd.concat([close_prices, pd.Series([sim_price])]).reset_index(drop=True))
            if pd.notna(sim_rsi) and sim_rsi <= target_rsi: return sim_price, sim_rsi
    elif mode == 'rise':
        if current_rsi >= target_rsi: return last_close, current_rsi
        for _ in range(100):
            sim_price += step
            sim_rsi = calc_single_rsi(pd.concat([close_prices, pd.Series([sim_price])]).reset_index(drop=True))
            if pd.notna(sim_rsi) and sim_rsi >= target_rsi: return sim_price, sim_rsi
    return sim_price, current_rsi

# ==========================================
# 4. Word 報告生成 (完整收錄主畫面所有量化與分析結果)
# ==========================================
def generate_word_report(ctx):
    doc = Document()
    t = doc.add_heading(f"{ctx['name']} 跨領域 AI 投資與量化分析報告", 0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(f"報告生成時間：{get_taiwan_time_str('%Y 年 %m 月 %d 日 %H:%M (CST)')}")
    doc.add_paragraph(f"資料頻率設定：{ctx['interval_label']} ｜ 最新即時成交價：NT$ {ctx['price']:,.2f}（當日漲跌 {ctx['change_txt']}）")

    # 一、核心總結與綜合評等
    doc.add_heading("一、核心總結與綜合評等", level=1)
    doc.add_paragraph(f"• AI 綜合評等：{ctx['rec']}（未來 5 天正報酬機率：{ctx['latest_proba']:.2%}）")
    doc.add_paragraph(f"• 預期轉折時間點：模型預測最有可能發生價格反轉的時點為【{ctx['turning_bar']}】（機率 {ctx['turning_prob']:.1f}% ｜ 趨勢方向：{ctx['turning_direction']}）")
    doc.add_paragraph(f"• 潛在空間與目標價：預估基準目標價為 NT$ {ctx['tp_base']:,.2f} 元，潛在空間 {ctx['upside']:.1f}%。")
    doc.add_paragraph(f"• SHAP AI 動態反推價位：SHAP AI 支撐價估計為 NT$ {ctx['shap_support']:,.2f} 元 ｜ SHAP AI 壓力價估計為 NT$ {ctx['shap_resistance']:,.2f} 元。")
    doc.add_paragraph(f"• AI 特徵含金量與資金簇擁：AI 含金量 (Beta_3) 數值為 {ctx['beta_3_val']:.3f} ｜ 資金簇擁度 (Gamma) 數值為 {ctx['gamma_val']:.3f}。")

    # 二、未來 5 根 K 棒走勢預測與價格區間
    doc.add_heading("二、未來 5 根 K 棒走勢預測與價格區間", level=1)
    doc.add_paragraph(f"• 預測正報酬勝率：{ctx['latest_proba']:.1%}（基於 LightGBM 機器學習模型）")
    doc.add_paragraph(f"• 預估 5 根 K 預期高價：NT$ {ctx['f5_high']:,.2f} 元（潛在漲幅 +{ctx['f5_high_pct']:.2f}%）")
    doc.add_paragraph(f"• 預估 5 根 K 預期低價：NT$ {ctx['f5_low']:,.2f} 元（潛在跌幅 {ctx['f5_low_pct']:.2f}%）")
    doc.add_paragraph(f"• 波動區間寬度：NT$ {ctx['f5_range_width']:,.2f} 元（區間變異數比例：{ctx['f5_ret_std']*100:.2f}%）")

    # 三、多期報酬率表現與區間價格
    doc.add_heading("三、多期報酬率表現與區間價格", level=1)
    ret_table = doc.add_table(rows=1, cols=4)
    ret_table.style = "Table Grid"
    ret_table.rows[0].cells[0].text, ret_table.rows[0].cells[1].text, ret_table.rows[0].cells[2].text, ret_table.rows[0].cells[3].text = "期間", "報酬率 (%)", "區間最低價", "區間最高價"
    for p_name, val, l_p, h_p in [
        ("近 1 期", ctx['ret_1w'], ctx['low_1w'], ctx['high_1w']),
        ("近 5 期", ctx['ret_2w'], ctx['low_2w'], ctx['high_2w']),
        ("近 20 期", ctx['ret_1m'], ctx['low_1m'], ctx['high_1m']),
        ("近 40 期", ctx['ret_2m'], ctx['low_2m'], ctx['high_2m']),
        ("近 60 期", ctx['ret_3m'], ctx['low_3m'], ctx['high_3m'])
    ]:
        r = ret_table.add_row().cells
        r[0].text = p_name
        r[1].text = ("資料不足" if val is None else f"{val:+.2f}%")
        r[2].text = ("-" if l_p is None else f"${l_p:,.2f}")
        r[3].text = ("-" if h_p is None else f"${h_p:,.2f}")

    # 四、新聞輿情與 FinBERT 跨時間維度量化評分
    doc.add_heading("四、新聞輿情與 FinBERT 跨時間維度量化評分", level=1)
    src_table = doc.add_table(rows=1, cols=9)
    src_table.style = "Table Grid"
    sch = src_table.rows[0].cells
    headers_list = ["時間", "Google", "MoneyDJ", "中時", "去重篇數", "情緒多/空", "展望多/空", "熱點(分)", "綜合評估"]
    for idx, h_text in enumerate(headers_list): sch[idx].text = h_text

    for p_label, src_dict, merged_c, b_cnt, r_cnt, gp_cnt, gn_cnt, h_val, sent_val in [
        ("近 48H", ctx['s_48h'], ctx['c48h'], ctx['b48h'], ctx['r48h'], ctx['gp_48h'], ctx['gn_48h'], ctx['h_48h'], ctx['sent_48h']),
        ("近 1W", ctx['s_1w'], ctx['c1w'], ctx['b1w'], ctx['r1w'], ctx['gp_1w'], ctx['gn_1w'], ctx['h_1w'], ctx['sent_1w']),
        ("近 2W", ctx['s_2w'], ctx['c2w'], ctx['b2w'], ctx['r2w'], ctx['gp_2w'], ctx['gn_2w'], ctx['h_2w'], ctx['sent_2w']),
        ("近 1M", ctx['s_1m'], ctx['c1m'], ctx['b1m'], ctx['r1m'], ctx['gp_1m'], ctx['gn_1m'], ctx['h_1m'], ctx['sent_1m']),
        ("近 2M", ctx['s_2m'], ctx['c2m'], ctx['b2m'], ctx['r2m'], ctx['gp_2m'], ctx['gn_2m'], ctx['h_2m'], ctx['sent_2m'])
    ]:
        r = src_table.add_row().cells
        r[0].text = p_label
        r[1].text = str(src_dict.get('Google News', 0))
        r[2].text = str(src_dict.get('MoneyDJ', 0))
        r[3].text = str(src_dict.get('中時新聞網', 0))
        r[4].text = str(merged_c)
        r[5].text = f"{b_cnt}/{r_cnt}"
        r[6].text = f"{gp_cnt}/{gn_cnt}"
        r[7].text = f"{h_val:.1f}"
        r[8].text = f"FinBERT:{sent_val:.1f}"

    # 五、本益比評價子項拆解與情境目標價
    doc.add_heading("五、本益比評價子項拆解與情境目標價", level=1)
    doc.add_paragraph(f"• 評價因子拆解：產業中樞本益比 (PE_base) {ctx['pe_base']:.1f}x ｜ FinBERT 輿情權重 {ctx['sentiment_exp']:+.2f}x ｜ 展望成長權重 {ctx['growth_exp']:+.2f}x ｜ 下行風險折價 -{ctx['risk_val']:.1f}x")
    doc.add_paragraph(f"• 情境目標價分佈：15倍地板 NT$ {ctx['tp_15x']:,.2f} 元 ｜ 悲觀(-0.5σ) NT$ {ctx['tp_lower']:,.2f} 元 ｜ 基準(Base) NT$ {ctx['tp_base']:,.2f} 元 ｜ 樂觀(+1σ) NT$ {ctx['tp_upper_1']:,.2f} 元 ｜ 樂觀(+2σ) NT$ {ctx['tp_upper_2']:,.2f} 元")

    # 六、AI 決策動能區間與動態買賣建議
    doc.add_heading("六、AI 決策動能區間與動態買賣建議", level=1)
    doc.add_paragraph(f"• 🟦 藍色動能區（逢低試單點）：預估跌至 NT$ {ctx['blue_price']:.2f} 元時，RSI 降至超賣區，為具備歷史勝率的支撐點。")
    doc.add_paragraph(f"• 🟥 紅色動能區（逢高賣出價）：預估漲至 NT$ {ctx['red_price']:.2f} 元時，RSI 飆至過熱區，容易遭遇主力調節。")
    doc.add_paragraph(f"• 🎯 模型具體建議區間：建議買進區間【${ctx['buy_low']:.2f} ~ ${ctx['buy_high']:.2f}】 ｜ 建議賣出區間【${ctx['sell_low']:.2f} ~ ${ctx['sell_high']:.2f}】")

    # 七、實質風險與波動率動態量化模組
    doc.add_heading("七、實質風險與波動率動態量化模組", level=1)
    doc.add_paragraph(f"• 匯率風險 (USDTWD=X)：最新匯率 {ctx['fx_latest']:.2f} ｜ 年化波動率 {ctx['fx_annual_vol']:.2f}%（68% 常態區間：{ctx['fx_low']:.2f} ~ {ctx['fx_high']:.2f}）")
    doc.add_paragraph(f"• 市場波動與安全邊際：過去一年個股年化波動 {ctx['stock_vol_1y']:.2f}% ｜ PE 標準差 {ctx['pe_std']:.2f} ｜ 最悲觀防守價 NT$ {ctx['real_safety_price']:.2f} 元")
    doc.add_paragraph(f"• 短長期波動比值：{ctx['vol_ratio']:.4f} → 狀態判定：{ctx['vol_signal']}")

    # 八、財務檢核與機器學習效能指標
    doc.add_heading("八、財務檢核與機器學習效能指標", level=1)
    doc.add_paragraph(f"• 近 4 季 EPS (TTM)：{ctx['ttm']:.2f} 元 ｜ 最近年度 EPS：{ctx['annual_eps_val']:.2f} 元")
    doc.add_paragraph(f"• 歷史本益比：{ctx['hist_pe']:.1f} 倍 ｜ 遠期本益比：{ctx['fwd_pe']:.1f} 倍")
    doc.add_paragraph(f"• 時序交叉驗證表現：平均準確率 {ctx['cv_acc']:.3f} ｜ 平均 AUC {ctx['cv_auc']:.3f}")

    # 九、歷史波段回測與 SHAP AI 決策邏輯解釋
    doc.add_heading("九、歷史波段回測與 SHAP AI 決策邏輯解釋", level=1)
    doc.add_paragraph(ctx['shap_explain_text'])

    doc.add_paragraph("")
    doc.add_paragraph(DISCLAIMER)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()

# ==========================================
# 5. 主程式設定與參數介面
# ==========================================
st.sidebar.title("⚙️ 標的與參數設定")
with st.sidebar.form(key="search_form"):
    user_query = st.text_input("輸入公司名稱或代號（如 2330）", value="2330").strip()
    interval_options = {"日線 (1d)": "1d", "60分鐘 (60m)": "60m", "30分鐘 (30m)": "30m", "15分鐘 (15m)": "15m", "5分鐘 (5m)": "5m"}
    interval_label = st.selectbox("選擇 K 線頻率", list(interval_options.keys()), index=0)
    interval = interval_options[interval_label]
    st.form_submit_button("📊 執行分析")

if not user_query: st.stop()
symbol = resolve_symbol(user_query)
company_name = get_company_name(symbol)
stock_code = symbol.split('.')[0]

(sent_48h, g_48h, h_48h, b48h, r48h, gp48h, gn48h, c48h, titles_48h, s_48h, items_48h) = comprehensive_quant_evaluation(symbol, company_name, 48)
(sent_1w, g_1w, h_1w, b1w, r1w, gp1w, gn1w, c1w, titles_1w, s_1w, items_1w) = comprehensive_quant_evaluation(symbol, company_name, 168)
(sent_2w, g_2w, h_2w, b2w, r2w, gp2w, gn2w, c2w, titles_2w, s_2w, items_2w) = comprehensive_quant_evaluation(symbol, company_name, 336)
(sent_1m, g_1m, h_1m, b1m, r1m, gp1m, gn1m, c1m, titles_1m, s_1m, items_1m) = comprehensive_quant_evaluation(symbol, company_name, 720)
(sent_2m, g_2m, h_2m, b2m, r2m, gp2m, gn2m, c2m, titles_2m, s_2m, items_2m) = comprehensive_quant_evaluation(symbol, company_name, 1440)

with st.spinner(f'正在分析 {company_name} [{interval_label}]...'):
    fetch_period = "59d" if interval in ["15m", "30m", "5m"] else ("730d" if interval != "1d" else None)
    fetch_start = (datetime.today() - pd.DateOffset(years=4)).strftime('%Y-%m-%d') if interval == "1d" else None
    end_date = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    tickers = list(dict.fromkeys([symbol, '^TWII']))

    try:
        raw_market_data = yf.download(tickers, period=fetch_period, start=fetch_start, end=end_date, interval=interval, progress=False)['Close']
        market_data = raw_market_data.to_frame(symbol) if isinstance(raw_market_data, pd.Series) else raw_market_data.loc[:, ~raw_market_data.columns.duplicated()]
    except Exception:
        market_data = yf.download(symbol, period="59d", interval=interval, progress=False)['Close'].to_frame(symbol)

    valid_stock = market_data[symbol].dropna()
    if valid_stock.empty:
        st.error(f"❌ 找不到 {symbol} 在 [{interval_label}] 下的股價資料。")
        st.stop()

    try: price = float(yf.Ticker(symbol).fast_info['last_price'])
    except Exception: price = float(valid_stock.iloc[-1])
    trade_date = str(valid_stock.index[-1])
    change = (price / float(valid_stock.iloc[-2]) - 1) * 100 if len(valid_stock) >= 2 else 0.0

    def get_ret_and_range(n):
        if len(valid_stock) <= n: return None, None, None
        sub_series = valid_stock.tail(n + 1)
        ret = (price / float(sub_series.iloc[0]) - 1) * 100
        low_val = float(sub_series.min())
        high_val = float(sub_series.max())
        return ret, low_val, high_val

    ret_1w, low_1w, high_1w = get_ret_and_range(1)
    ret_2w, low_2w, high_2w = get_ret_and_range(5)
    ret_1m, low_1m, high_1m = get_ret_and_range(20)
    ret_2m, low_2m, high_2m = get_ret_and_range(40)
    ret_3m, low_3m, high_3m = get_ret_and_range(60)

    tkr_fin = yf.Ticker(symbol)
    q_eps_list = []
    ttm_eps, annual_eps, annual_year_str = None, None, ""
    try:
        s_q = _eps_series(tkr_fin.quarterly_income_stmt)
        if s_q is not None:
            q_eps_list = [(f"{d.year}Q{(d.month - 1) // 3 + 1}", float(v)) for d, v in list(s_q.items())[:4]]
            if len(q_eps_list) == 4: ttm_eps = round(sum(v for _, v in q_eps_list), 2)
    except Exception: pass
    try:
        s_a = _eps_series(tkr_fin.income_stmt)
        if s_a is not None and not s_a.empty:
            annual_eps = round(float(s_a.iloc[0]), 2)
            if hasattr(s_a.index[0], 'year'): annual_year_str = f" ({s_a.index[0].year})"
    except Exception: pass

    auto_pe_base, pe_std = 15.0, 4.0
    try:
        if ttm_eps and ttm_eps > 0:
            s_full_for_pe = yf.download(symbol, period="1y", interval="1d", progress=False)['Close']
            if isinstance(s_full_for_pe, pd.DataFrame): s_full_for_pe = s_full_for_pe.iloc[:, 0]
            auto_pe_base = max(8.0, min(float(s_full_for_pe.median()) / ttm_eps, 40.0))
            hist_pe_series = (s_full_for_pe / ttm_eps).dropna()
            hist_pe_filtered = hist_pe_series[(hist_pe_series > 0) & (hist_pe_series < 200)]
            if len(hist_pe_filtered) > 10: pe_std = float(hist_pe_filtered.std())
    except Exception: pass

    fx_latest, fx_annual_vol, fx_low, fx_high = 32.0, 4.5, 30.5, 33.5
    try:
        fx_data = yf.download("USDTWD=X", period="1y", interval="1d", progress=False)['Close']
        if isinstance(fx_data, pd.DataFrame): fx_data = fx_data.iloc[:, 0]
        fx_latest = float(fx_data.iloc[-1])
        fx_annual_vol = float(fx_data.pct_change().std() * math.sqrt(252) * 100)
        fx_low = fx_latest * (1 - fx_annual_vol / 100)
        fx_high = fx_latest * (1 + fx_annual_vol / 100)
    except Exception: pass

    stock_vol_1y = 25.0
    actual_max_pe, actual_min_pe, real_safety_price = 25.0, 10.0, 10.0
    try:
        s_full = yf.download(symbol, period="1y", interval="1d", progress=False)
        if not s_full.empty:
            s_close = s_full['Close'].iloc[:, 0] if isinstance(s_full['Close'], pd.DataFrame) else s_full['Close']
            stock_vol_1y = float(s_close.pct_change().std() * math.sqrt(252) * 100)
            high_p = float(s_full['High'].max().iloc[0] if isinstance(s_full['High'].max(), pd.Series) else s_full['High'].max())
            low_p = float(s_full['Low'].min().iloc[0] if isinstance(s_full['Low'].min(), pd.Series) else s_full['Low'].min())
            base_eps_for_risk = ttm_eps if ttm_eps and ttm_eps > 0 else 1.0
            actual_max_pe, actual_min_pe = high_p / base_eps_for_risk, low_p / base_eps_for_risk
            real_safety_price = base_eps_for_risk * actual_min_pe
    except Exception: pass

    vol_5d, vol_20d, vol_ratio = 20.0, 20.0, 1.0
    try:
        recent_rets = valid_stock.pct_change().dropna()
        if len(recent_rets) >= 20:
            vol_5d = float(recent_rets.tail(5).std() * math.sqrt(252) * 100)
            vol_20d = float(recent_rets.tail(20).std() * math.sqrt(252) * 100)
            vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0
    except Exception: pass
    vol_signal = "🚨 [減碼/防守] 短期波動放大" if vol_ratio > 1.2 else ("🎯 [加碼/佈局] 短期波動壓縮" if vol_ratio < 0.8 else "⚖️ [觀望/中性] 多空平衡")

    current_nlp = {'sent': sent_1w, 'growth': g_1w, 'hotspot': h_1w}
    us_daily = download_us_daily(years=5) 
    df, fwd_excess = build_feature_frame(symbol, market_data, us_daily, current_nlp)

    Y_rolling = df[symbol] - df['RF_TW']
    roll_cols = [c for c in ['^TWII', '^SOX', 'NVDA_Pure_Shock', 'Price_Mom_30D', 'Interaction_Term'] if c in df.columns and c != symbol and float(df[c].std()) > 0]
    
    if len(df) > 30:
        rolling_res = RollingOLS(Y_rolling, sm.add_constant(df[roll_cols]), window=min(252, max(30, len(df) // 3))).fit()
        df['Beta_3_Rolling'] = rolling_res.params['NVDA_Pure_Shock'] if 'NVDA_Pure_Shock' in rolling_res.params else 0.0
        df['Gamma_Rolling'] = rolling_res.params['Interaction_Term'] if 'Interaction_Term' in rolling_res.params else 0.0
    else:
        df['Beta_3_Rolling'] = 0.0
        df['Gamma_Rolling'] = 0.0
        
    df['Beta_3_Trend_5D'] = df['Beta_3_Rolling'].diff(5)
    df['Gamma_Trend_5D'] = df['Gamma_Rolling'].diff(5)
    
    plot_gamma = df['Gamma_Rolling'].dropna()
    plot_beta3 = df['Beta_3_Rolling'].dropna()

    features = ['Beta_3_Rolling', 'Gamma_Rolling', 'Price_Mom_30D', 'Price_Mom_5D', 'RSI_14', 'Vol_10D', 'NLP_Sent', 'NLP_Growth', 'NLP_Hotspot']
    features = [c for c in features if c in df.columns]
    df_ai = df.dropna(subset=features)
    X, y = df_ai[features], (fwd_excess.reindex(df_ai.index) > 0.005).astype(int)

    # 升級版：精細化超參數配置的 LightGBM 模型
    model = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.015,
        max_depth=3,
        num_leaves=7,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=0.5,
        random_state=42,
        verbose=-1
    )
    
    tscv = TimeSeriesSplit(n_splits=max(2, len(X) // 10 if len(X) < 30 else 5))
    cv_test_acc, cv_test_auc, test_index = [], [], np.array([], dtype=int)
    try:
        for train_index, t_idx in tscv.split(X):
            safe_train_index = train_index[:-3] if len(train_index) > 3 else train_index
            if len(safe_train_index) < 3 or len(t_idx) < 1 or y.iloc[safe_train_index].nunique() < 2: continue
            model.fit(X.iloc[safe_train_index], y.iloc[safe_train_index])
            test_index = t_idx
            cv_test_acc.append(accuracy_score(y.iloc[t_idx], model.predict(X.iloc[t_idx])))
            if y.iloc[t_idx].nunique() > 1: cv_test_auc.append(roc_auc_score(y.iloc[t_idx], model.predict_proba(X.iloc[t_idx])[:, 1]))
    except Exception: pass

    model.fit(X, y)
    latest_features = df[features].iloc[[-1]] if not df.empty else pd.DataFrame(columns=features)
    latest_proba = float(model.predict_proba(latest_features)[:, 1][0]) if not latest_features.empty else 0.5
    beta3_trend_val = df['Beta_3_Trend_5D'].dropna().iloc[-1] if df['Beta_3_Trend_5D'].notna().any() else 0.0
    beta_3_val = float(df['Beta_3_Rolling'].dropna().iloc[-1]) if not df.empty and 'Beta_3_Rolling' in df.columns else 0.0
    gamma_val = float(df['Gamma_Rolling'].dropna().iloc[-1]) if not df.empty and 'Gamma_Rolling' in df.columns else 0.0

    # -------------------------------------------------------------------
    # 核心：SHAP 反推支撐與壓力價格計算模組
    # -------------------------------------------------------------------
    shap_support, shap_resistance = price * 0.95, price * 1.05
    try:
        explainer = shap.TreeExplainer(model)
        shap_vals_latest = explainer.shap_values(latest_features)
        s_vals = shap_vals_latest[1][0] if isinstance(shap_vals_latest, list) else (shap_vals_latest[0, :, 1] if getattr(shap_vals_latest, "ndim", 3) == 3 else shap_vals_latest[0])
        feat_shap_map = dict(zip(features, s_vals))
        
        support_offset = max(0.01, 0.03 + (feat_shap_map.get('Price_Mom_30D', 0.0) * 0.05))
        resistance_offset = max(0.01, 0.03 - (feat_shap_map.get('RSI_14', 0.0) * 0.05))
        
        shap_support = round(price * (1.0 - abs(support_offset)), 2)
        shap_resistance = round(price * (1.0 + abs(resistance_offset)), 2)
    except Exception:
        pass
    # -------------------------------------------------------------------

    # -------------------------------------------------------------------
    # 動態特徵驅動轉折點預測演算法
    # -------------------------------------------------------------------
    current_rsi_series = df['RSI_14'].dropna()
    current_rsi = float(current_rsi_series.iloc[-1]) if not current_rsi_series.empty else 50.0
    current_vol_series = df['Vol_10D'].dropna()
    current_vol = float(current_vol_series.iloc[-1]) if not current_vol_series.empty else 0.02
    current_gamma_series = df['Gamma_Trend_5D'].dropna()
    current_gamma_trend = float(current_gamma_series.iloc[-1]) if not current_gamma_series.empty else 0.0

    if latest_proba > 0.55: turning_direction = "向上突破 ↗"
    elif latest_proba < 0.45: turning_direction = "向下回檔 ↘"
    else: turning_direction = "震盪整理 ↔"

    rsi_extreme_dist = max(0.0, abs(current_rsi - 50.0) - 15.0) / 35.0
    vol_factor = min(1.0, current_vol * 20.0)
    urgency = float(np.clip(rsi_extreme_dist * 0.5 + vol_factor * 0.3 + abs(current_gamma_trend) * 10.0, 0, 1))

    if urgency > 0.7: w = np.array([0.4, 0.3, 0.15, 0.1, 0.05])
    elif urgency > 0.4: w = np.array([0.1, 0.25, 0.4, 0.15, 0.1])
    else: w = np.array([0.05, 0.15, 0.3, 0.3, 0.2])

    model_conf = abs(latest_proba - 0.5) * 2.0
    w[0] += model_conf * 0.1
    w[1] += model_conf * 0.05
    w = w / np.sum(w)

    turning_bar_idx = int(np.argmax(w)) + 1
    turning_bar_name = f"第 {turning_bar_idx} 根 K"
    turning_bar_prob = float(np.clip(35.0 + (model_conf * 45.0) + (current_vol * 150.0) + (w[turning_bar_idx-1] * 20.0), 25.0, 92.5))
    # -------------------------------------------------------------------

    base_rsi_oversold, base_rsi_overbought = 40.0, 70.0
    if latest_proba > 0.6: base_rsi_oversold, base_rsi_overbought = 45.0, 75.0
    elif latest_proba < 0.4: base_rsi_oversold, base_rsi_overbought = 35.0, 65.0
    blue_price, blue_rsi = calculate_target_price_for_rsi(valid_stock, target_rsi=base_rsi_oversold, mode='drop')
    red_price, red_rsi = calculate_target_price_for_rsi(valid_stock, target_rsi=base_rsi_overbought, mode='rise')

# ==========================================
# 6. 側邊欄 EPS 覆寫與估值核心計算
# ==========================================
st.sidebar.markdown("---")
st.sidebar.subheader("財報 EPS 設定")
use_manual = st.sidebar.checkbox("手動輸入 / 覆寫 EPS", value=not (ttm_eps is not None and annual_eps is not None))
if use_manual:
    ttm_eps_val = st.sidebar.number_input("近 4 季 EPS (TTM)", value=float(ttm_eps or 3.0), step=0.1, format="%.2f")
    annual_eps_val = st.sidebar.number_input("最近年度 EPS", value=float(annual_eps or ttm_eps_val), step=0.1, format="%.2f")
    ttm_src = annual_src = "手動輸入"
    annual_year_display = ""
else:
    ttm_eps_val, annual_eps_val = ttm_eps or 3.0, annual_eps or 3.0
    ttm_src = annual_src = "Yahoo Finance"
    annual_year_display = annual_year_str

st.sidebar.markdown("---")
st.sidebar.subheader("估值模型變數")
eps_fwd_base = st.sidebar.number_input("預估 Forward EPS", min_value=0.01, value=float(max(0.5, round(ttm_eps_val * 1.1, 2))), step=0.1)
pe_base = st.sidebar.number_input("產業中樞本益比 (PE_base)", min_value=1.0, value=float(round(auto_pe_base, 1)))
sentiment = st.sidebar.slider("新聞聲量情緒", 0.0, 10.0, float(sent_1w), 0.1)
growth_score = st.sidebar.slider("展望成長評分", 0.0, 10.0, float(g_1w), 0.1)

level_penalty = max(0, 5.0 - sent_1w) * 0.5 + max(0, 5.0 - g_1w) * 1.2
trend_penalty = max(0, sent_1m - sent_1w) * 0.5 + max(0, g_1m - g_1w) * 1.0
risk_val = min(10.0, level_penalty + trend_penalty)

amp_factor = 1.0 + 0.30 * max(0, beta3_trend_val) if beta3_trend_val > 0 else 1.0
eps_adj = eps_fwd_base * (math.pow(ttm_eps_val / annual_eps_val, 0.35) if ttm_eps_val > annual_eps_val > 0 else 1.0)
growth_exp = 0.8 * amp_factor * (math.exp(0.22 * growth_score) - 1)
sentiment_exp = 0.5 * amp_factor * math.copysign(1, sentiment - 5.0) * (math.exp(0.35 * abs(sentiment - 5.0)) - 1)

pe_target_raw = pe_base + sentiment_exp + growth_exp - risk_val
pe_target = min(max(pe_target_raw, pe_base * 0.5), pe_base * 2.0)
tp_base = eps_adj * pe_target
upside = (tp_base / price - 1) * 100

tp_15x, tp_lower = eps_adj * 15.0, eps_adj * max(15.0, pe_target - 0.5 * pe_std)
tp_upper_1, tp_upper_2 = eps_adj * (pe_target + 1.0 * pe_std), eps_adj * (pe_target + 2.0 * pe_std)

rec_title = "強烈作多" if latest_proba > 0.55 and beta3_trend_val > 0 else ("保守觀望" if latest_proba < 0.45 else "中性震盪")
rec_desc = "建議買進" if "多" in rec_title else ("建議賣出" if "觀望" in rec_title else "建議持有")

f5_ret_std = float(valid_stock.pct_change().tail(20).std() * math.sqrt(5)) if len(valid_stock) >= 20 else 0.02
f5_high = price * (1 + f5_ret_std * (1.2 if latest_proba > 0.5 else 0.5))
f5_low = price * (1 - f5_ret_std * (0.8 if latest_proba > 0.5 else 1.3))
f5_high_pct = ((f5_high / price) - 1) * 100
f5_low_pct = ((f5_low / price) - 1) * 100
f5_range_width = f5_high - f5_low

buy_low = min(blue_price, real_safety_price)
buy_high = max(blue_price, real_safety_price)
sell_low = min(red_price, tp_upper_1)
sell_high = max(red_price, tp_upper_2)

hist_pe = price / ttm_eps_val if ttm_eps_val > 0 else 0
fwd_pe = price / eps_adj if eps_adj > 0 else 0
cv_acc_val = float(np.mean(cv_test_acc)) if cv_test_acc else 0.0
cv_auc_val = float(np.mean(cv_test_auc)) if cv_test_auc else 0.0

# ==========================================
# 7. 最終 UI 呈現 
# ==========================================
st.title("📈 跨領域專家 AI 投資分析與量化預測")
st.subheader(f"🏢 {company_name} — 【{interval_label}】")
st.caption(f"報告生成時間：{get_taiwan_time_str()} (CST) ｜ ⚡ 系統運算完成耗時：{int((time.time() - start_time_perf)//60)}分{int((time.time() - start_time_perf)%60)}秒")
st.warning(DISCLAIMER)

def fmt_pct(v): return "資料不足" if v is None else f"{v:+.2f}%"
def fmt_price(v): return "-" if v is None else f"${v:,.2f}"

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("即時成交價", f"${price:,.2f}", f"{trade_date} ({fmt_pct(change)})")
c2.metric("AI 目標價與機率", f"${tp_base:,.0f} ({latest_proba:.1%})", f"{upside:.1f}% 潛在空間")
c3.metric("AI 綜合評等", rec_title, f"{'🟢' if '買' in rec_desc else ('🔴' if '賣' in rec_desc else '🟡')} {rec_desc}")
c4.metric("SHAP AI 支撐/壓力", f"支撐 ${shap_support:,.1f}", f"壓力 ${shap_resistance:,.1f}")
c5.metric("AI含金量 (Beta_3)", f"{beta_3_val:.3f}", f"資金簇擁: {gamma_val:.3f}")

st.markdown("---")
st.markdown("### 🔮 未來 5 根 K 棒走勢預測與價格區間")
fc1, fc2, fc3, fc4, fc5 = st.columns(5)
fc1.metric("預測正報酬勝率", f"{latest_proba:.1%}", "基於 LightGBM 模型")
fc2.metric("預估轉折時間點", turning_bar_name, f"機率: {turning_bar_prob:.1f}% ({turning_direction})")
fc3.metric("預估 5 根 K 預期高價", f"${f5_high:,.2f}", f"+{f5_high_pct:.2f}%")
fc4.metric("預估 5 根 K 預期低價", f"${f5_low:,.2f}", f"{f5_low_pct:.2f}%")
fc5.metric("波動區間寬度", f"${f5_range_width:,.2f}", f"區間變異: {f5_ret_std*100:.2f}%")

st.markdown("---")
st.markdown(f"### ⏱ 多期報酬率表現與區間價格 ({interval_label} 視角)")
r_col1, r_col2, r_col3, r_col4, r_col5 = st.columns(5)
r_col1.metric("近 1 期", fmt_pct(ret_1w), f"低 {fmt_price(low_1w)} / 高 {fmt_price(high_1w)}")
r_col2.metric("近 5 期", fmt_pct(ret_2w), f"低 {fmt_price(low_2w)} / 高 {fmt_price(high_2w)}")
r_col3.metric("近 20 期", fmt_pct(ret_1m), f"低 {fmt_price(low_1m)} / 高 {fmt_price(high_1m)}")
r_col4.metric("近 40 期", fmt_pct(ret_2m), f"低 {fmt_price(low_2m)} / 高 {fmt_price(high_2m)}")
r_col5.metric("近 60 期", fmt_pct(ret_3m), f"低 {fmt_price(low_3m)} / 高 {fmt_price(high_3m)}")

st.markdown("---")
st.markdown("### 📰 多來源真實新聞爬取與 FinBERT 量化評分 (Google News + MoneyDJ + 中時)")
src_df_data = [
    {"時間": "近 48H", "Google News": s_48h.get('Google News',0), "MoneyDJ": s_48h.get('MoneyDJ',0), "中時": s_48h.get('中時新聞網',0), "去重篇數": c48h, "情緒多/空": f"{b48h}/{r48h}", "展望多/空": f"{gp48h}/{gn48h}"},
    {"時間": "近 1W (168H)", "Google News": s_1w.get('Google News',0), "MoneyDJ": s_1w.get('MoneyDJ',0), "中時": s_1w.get('中時新聞網',0), "去重篇數": c1w, "情緒多/空": f"{b1w}/{r1w}", "展望多/空": f"{gp1w}/{gn1w}"},
    {"時間": "近 2W (336H)", "Google News": s_2w.get('Google News',0), "MoneyDJ": s_2w.get('MoneyDJ',0), "中時": s_2w.get('中時新聞網',0), "去重篇數": c2w, "情緒多/空": f"{b2w}/{r2w}", "展望多/空": f"{gp2w}/{gn2w}"},
    {"時間": "近 1M (720H)", "Google News": s_1m.get('Google News',0), "MoneyDJ": s_1m.get('MoneyDJ',0), "中時": s_1m.get('中時新聞網',0), "去重篇數": c1m, "情緒多/空": f"{b1m}/{r1m}", "展望多/空": f"{gp1m}/{gn1m}"},
    {"時間": "近 2M (1440H)", "Google News": s_2m.get('Google News',0), "MoneyDJ": s_2m.get('MoneyDJ',0), "中時": s_2m.get('中時新聞網',0), "去重篇數": c2m, "情緒多/空": f"{b2m}/{r2m}", "展望多/空": f"{gp2m}/{gn2m}"},
]
st.dataframe(pd.DataFrame(src_df_data), hide_index=True, use_container_width=True)

# 帶有按鈕可顯示/隱藏的近 48H 即時新聞明細區塊（已加入嚴格標的關聯過濾）
with st.expander("📰 點擊展開/收合：近 48H 即時新聞標題與明細清單 (已過濾與標的無關雜訊)", expanded=False):
    if items_48h:
        for idx, (t_title, t_dt) in enumerate(items_48h[:15], 1):
            st.markdown(f"<small><b>{idx}.</b> [{t_dt.strftime('%m-%d %H:%M')}] {t_title}</small>", unsafe_allow_html=True)
    else:
        st.info("近 48 小時內尚無符合條件的即時新聞。")

st.markdown("#### 📊 各時間維度 FinBERT 情緒、展望與熱點(炒作度)評分 (已啟用時間序列 RSS 過濾)")
h_col1, h_col2, h_col3, h_col4, h_col5 = st.columns(5)
with h_col1:
    st.metric("近 48H 熱點", f"{h_48h:.1f} 分", f"FinBERT情緒:{sent_48h:.1f} (多:{b48h}/空:{r48h})")
    st.markdown(f"<span style='background-color: #d1fae5; color: #065f46; padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: 600;'>⬆ 展望分數:{g_48h:.1f} (多:{gp48h}/空:{gn48h})</span>", unsafe_allow_html=True)
with h_col2:
    st.metric("近 1W 熱點", f"{h_1w:.1f} 分", f"FinBERT情緒:{sent_1w:.1f} (多:{b1w}/空:{r1w})")
    st.markdown(f"<span style='background-color: #d1fae5; color: #065f46; padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: 600;'>⬆ 展望分數:{g_1w:.1f} (多:{gp1w}/空:{gn1w})</span>", unsafe_allow_html=True)
with h_col3:
    st.metric("近 2W 熱點", f"{h_2w:.1f} 分", f"FinBERT情緒:{sent_2w:.1f} (多:{b2w}/空:{r2w})")
    st.markdown(f"<span style='background-color: #d1fae5; color: #065f46; padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: 600;'>⬆ 展望分數:{g_2w:.1f} (多:{gp2w}/空:{gn2w})</span>", unsafe_allow_html=True)
with h_col4:
    st.metric("近 1M 熱點", f"{h_1m:.1f} 分", f"FinBERT情緒:{sent_1m:.1f} (多:{b1m}/空:{r1m})")
    st.markdown(f"<span style='background-color: #d1fae5; color: #065f46; padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: 600;'>⬆ 展望分數:{g_1m:.1f} (多:{gp1m}/空:{gn1m})</span>", unsafe_allow_html=True)
with h_col5:
    st.metric("近 2M 熱點", f"{h_2m:.1f} 分", f"FinBERT情緒:{sent_2m:.1f} (多:{b2m}/空:{r2m})")
    st.markdown(f"<span style='background-color: #d1fae5; color: #065f46; padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: 600;'>⬆ 展望分數:{g_2m:.1f} (多:{gp2m}/空:{gn2m})</span>", unsafe_allow_html=True)

st.markdown("---")
st.subheader("🎯 本益比評價子項拆解與情境目標價")
p_col1, p_col2, p_col3, p_col4 = st.columns(4)
p_col1.metric("產業中樞本益比 (PE_base)", f"{pe_base:.1f}x", "歷史中位數定錨")
p_col2.metric("FinBERT 輿情權重", f"{sentiment_exp:+.2f}x", "AI 語意情緒氣氛")
p_col3.metric("展望成長權重 (Growth)", f"{growth_exp:+.2f}x", "基本面動能增幅")
p_col4.metric("下行風險折價 (Risk)", f"-{risk_val:.1f}x", "防守防護傘扣減")

sc1, sc2, sc3, sc4, sc5 = st.columns(5)
sc1.metric("15倍地板", f"${tp_15x:,.0f}", "(15.0x)"); sc2.metric("悲觀(-0.5σ)", f"${tp_lower:,.0f}", f"({max(15.0, pe_target - 0.5 * pe_std):.1f}x)")
sc3.metric("基準(Base)", f"${tp_base:,.0f}", f"({pe_target:.1f}x)"); sc4.metric("樂觀(+1σ)", f"${tp_upper_1:,.0f}", f"({pe_target + 1.0 * pe_std:.1f}x)")
sc5.metric("樂觀(+2σ)", f"${tp_upper_2:,.0f}", f"({pe_target + 2.0 * pe_std:.1f}x)")

st.divider()
left, right = st.columns(2)

with left:
    st.subheader("一、AI 決策動能區間 (機率動態調整)")
    st.info(f"**🟦 藍色動能區 (建議逢低試單點)**\n預估跌至 **{blue_price:.2f} 元** 時，RSI 降至 {blue_rsi:.1f} (超賣區)。歷史勝率支撐點。")
    st.warning(f"**🟥 紅色動能區 (建議逢高賣出價)**\n預估漲至 **{red_price:.2f} 元** 時，RSI 飆至 {red_rsi:.1f} (過熱區)。容易遭遇主力倒貨。")
    
    st.markdown("---")
    st.subheader("二、實質風險與波動率動態量化")
    st.info(f"**匯率風險 (USDTWD=X)：** 最新 {fx_latest:.2f}，年化波動 {fx_annual_vol:.2f}% (68%區間: {fx_low:.2f} ~ {fx_high:.2f})")
    st.warning(f"**市場競爭與歷史波動：** 過去一年個股波動 {stock_vol_1y:.2f}% (PE 標準差: {pe_std:.2f})")
    st.success(f"**模型安全邊際與建議區間：** 歷史最高 PE {actual_max_pe:.1f}x / 最低 {actual_min_pe:.1f}x，最悲觀防守價 **{real_safety_price:.2f} 元**\n\n🎯 **[模型建議區間]** 建議買進區間：**${buy_low:.2f} ~ ${buy_high:.2f}** ｜ 建議賣出區間：**${sell_low:.2f} ~ ${sell_high:.2f}**")
    st.error(f"**短長期波動比值：** {vol_ratio:.4f} → {vol_signal}")

with right:
    st.subheader("三、財務檢核與 AI 預測指標")
    fin_data = [{"指標": f"單季 EPS ({label})", "數值": f"{val:.2f}", "資料來源": "Yahoo Finance"} for label, val in q_eps_list]
    fin_data.extend([
        {"指標": "近 4 季 EPS (TTM)", "數值": f"{ttm_eps_val:.2f}", "資料來源": ttm_src},
        {"指標": f"最近年度 EPS{annual_year_display}", "數值": f"{annual_eps_val:.2f}", "資料來源": annual_src},
        {"指標": "歷史本益比", "數值": f"{hist_pe:.1f} 倍", "資料來源": "即時股價 / TTM"},
        {"指標": "遠期本益比", "數值": f"{fwd_pe:.1f} 倍", "資料來源": "即時股價 / 調整後 EPS"}
    ])
    st.dataframe(pd.DataFrame(fin_data), hide_index=True, use_container_width=True)
    if cv_test_acc: st.caption(f"時序交叉驗證：平均準確率 {cv_acc_val:.3f}｜平均 AUC {cv_auc_val:.3f} ({len(cv_test_acc)} 折)")

st.markdown("---")
st.markdown("<h3 style='color: #2e8b57;'>📊 歷史波段回測與 SHAP AI 決策邏輯</h3>", unsafe_allow_html=True)

shap_explain_text_plain = (
    f"💡 模型圖表綜合解釋說明：\n"
    f"• 歷史回測圖解析：紫線 Gamma 代表市場資金簇擁與推擠度，大於 0 表示強勢追價；綠線 Beta_3 代表個股相對於輝達 (NVDA) 的獨立超額衝擊。當兩者轉強並進入黃色標示之「動能爆發推升期 (Surge)」時，紅色的累積報酬曲線呈現明確的主升段噴發。\n"
    f"• SHAP 特徵歸因解析：模型以 Price_Mom_30D（30日動能差）與 Beta_3 具備最高決策影響力。右側紅點代表特徵值偏高時會顯著推升未來正報酬機率。\n"
    f"• 🎯 SHAP 動態反推價位：結合當前特徵對模型的邊際貢獻，機器學習反推之 **AI 支撐價為 ${shap_support:,.2f} 元**，**AI 壓力價為 ${shap_resistance:,.2f} 元**。"
)

shap_explain_html = f"""
<div style='color: #006400; background-color: #f0fdf4; padding: 15px; border-radius: 8px; border-left: 5px solid #2e8b57; margin-bottom: 15px;'>
{shap_explain_text_plain.replace(chr(10), '<br>')}
</div>
"""
st.markdown(shap_explain_html, unsafe_allow_html=True)

fig_col1, fig_col2 = st.columns(2)

with fig_col1:
    min_beta3_date = plot_beta3.idxmin() if not plot_beta3.empty else None
    period_returns = market_data[symbol].dropna().loc[min_beta3_date:plot_beta3.loc[min_beta3_date:].idxmax()].pct_change().dropna() if min_beta3_date else pd.Series(dtype=float)
    fig1, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 10), sharex=True)
    if not plot_gamma.empty: ax1.plot(plot_gamma.index, plot_gamma, color='purple', label='Gamma (資金簇擁度 / Crowding)')
    ax1.axhline(0, color='red', linestyle='--'); ax1.legend(loc='upper left'); ax1.grid(True, alpha=0.3)
    if not plot_beta3.empty:
        ax2.plot(plot_beta3.index, plot_beta3, color='forestgreen', label='Beta_3 (AI 含金量 / NVDA Pure Shock)')
        if not period_returns.empty: ax2.axvspan(period_returns.index[0], period_returns.index[-1], color='yellow', alpha=0.2, label='Surge')
    ax2.axhline(0, color='red', linestyle='--'); ax2.legend(loc='upper left'); ax2.grid(True, alpha=0.3)
    if not period_returns.empty:
        cum_ret = (1 + period_returns).cumprod() - 1
        ax3.plot(cum_ret.index, cum_ret, color='darkred', label='Cumulative Return')
        ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: '{:.0%}'.format(y)))
    ax3.legend(loc='upper left'); ax3.grid(True, alpha=0.3)
    fig1.suptitle(f'[{symbol}] {interval_label} Surge Backtest', fontsize=14)
    plt.tight_layout(); st.pyplot(fig1)
    
    st.markdown("""
    <div style='background-color: #1e1e1e; padding: 10px; border-radius: 6px; color: #d4d4d4; font-size: 13px;'>
    <b>📉 左圖 (Surge Backtest) 解釋：</b><br>
    • <b>上圖 (Gamma)</b>：衡量市場資金的擁擠與投機熱度。數值大於 0（紅虛線上方）代表買盤集體湧入；小於 0 代表資金退潮、盤勢陷入整理。<br>
    • <b>中圖 (Beta_3)</b>：量化個股對 AI 龍頭（如輝達）的敏感度。黃色陰影區（Surge 期間）代表動能爆發期，此時股價往往展開波段主升段。<br>
    • <b>下圖 (Cumulative Return)</b>：驗證模型在捕捉到主力資金簇擁時進場的累積報酬表現。
    </div>
    """, unsafe_allow_html=True)

with fig_col2:
    try:
        X_shap = X.iloc[test_index] if len(test_index) > 0 else X
        shap_values = shap.TreeExplainer(model).shap_values(X_shap)
        shap_values_to_plot = shap_values[1] if isinstance(shap_values, list) else (shap_values[:, :, 1] if getattr(shap_values, "ndim", 2) == 3 else shap_values)
        fig2 = plt.figure(figsize=(10, 8))
        shap.summary_plot(shap_values_to_plot, X_shap, feature_names=features, show=False)
        ax = plt.gca()
        ax.tick_params(axis='y', colors='white'); ax.tick_params(axis='x', colors='white')
        ax.xaxis.label.set_color('white')
        plt.title(f"[{symbol}] SHAP AI Decision Logic", fontsize=14, color='white')
        plt.tight_layout(); st.pyplot(fig2)
    except Exception as e: st.info(f"SHAP 渲染失敗：{e}")

    st.markdown(f"""
    <div style='background-color: #1e1e1e; padding: 10px; border-radius: 6px; color: #d4d4d4; font-size: 13px;'>
    <b>🤖 右圖 (SHAP AI Decision Logic) 解釋：</b><br>
    • <b>特徵影響力排序</b>：由上至下依序為對模型預測勝率最具決定性的指標（如 <code>Price_Mom_30D</code> 與 <code>Beta_3_Rolling</code>）。<br>
    • <b>紅藍點邏輯</b>：紅色代表特徵值偏高，藍色代表特徵值偏低。若紅點落在 SHAP 值 > 0 側，代表該特徵高檔時會強力推升上漲勝率。<br>
    • <b>💡 SHAP 動態反推價位</b>：依據當前特徵權重邊際推導，當前 AI 機器學習反推之 **支撐價為 ${shap_support:,.2f} 元**、**壓力價為 ${shap_resistance:,.2f} 元**。
    </div>
    """, unsafe_allow_html=True)

# ==========================================
# 8. 完整打包所有主畫面數據至 Word 報告 Context
# ==========================================
ctx = {
    "name": company_name, "interval_label": interval_label, "price": price, "change_txt": fmt_pct(change),
    "latest_proba": latest_proba, "rec": rec_title, "blue_price": blue_price, "red_price": red_price,
    "tp_base": tp_base, "pe_target": pe_target, "tp_15x": tp_15x, "tp_lower": tp_lower, 
    "tp_upper_1": tp_upper_1, "tp_upper_2": tp_upper_2,
    "ret_1w": ret_1w, "ret_2w": ret_2w, "ret_1m": ret_1m, "ret_2m": ret_2m, "ret_3m": ret_3m,
    "low_1w": low_1w, "high_1w": high_1w, "low_2w": low_2w, "high_2w": high_2w,
    "low_1m": low_1m, "high_1m": high_1m, "low_2m": low_2m, "high_2m": high_2m,
    "low_3m": low_3m, "high_3m": high_3m,
    "turning_bar": turning_bar_name, "turning_prob": turning_bar_prob, "turning_direction": turning_direction,
    "s_48h": s_48h, "c48h": c48h, "b48h": b48h, "r48h": r48h, "gp_48h": gp48h, "gn_48h": gn_48h, "h_48h": h_48h, "sent_48h": sent_48h,
    "s_1w": s_1w, "c1w": c1w, "b1w": b1w, "r1w": r1w, "gp_1w": gp1w, "gn_1w": gn1w, "h_1w": h_1w, "sent_1w": sent_1w,
    "s_2w": s_2w, "c2w": c2w, "b2w": b2w, "r2w": r2w, "gp_2w": gp2w, "gn_2w": gn2w, "h_2w": h_2w, "sent_2w": sent_2w,
    "s_1m": s_1m, "c1m": c1m, "b1m": b1m, "r1m": r1m, "gp_1m": gp1m, "gn_1m": gn1m, "h_1m": h_1m, "sent_1m": sent_1m,
    "s_2m": s_2m, "c2m": c2m, "b2m": b2m, "r2m": r2m, "gp_2m": gp2m, "gn_2m": gn2m, "h_2m": h_2m, "sent_2m": sent_2m,
    "pe_base": pe_base, "sentiment_exp": sentiment_exp, "growth_exp": growth_exp, "risk_val": risk_val,
    "fx_latest": fx_latest, "fx_annual_vol": fx_annual_vol, "fx_low": fx_low, "fx_high": fx_high,
    "stock_vol_1y": stock_vol_1y, "ttm": ttm_eps_val, "annual_eps_val": annual_eps_val, "pe_std": pe_std, 
    "real_safety_price": real_safety_price, "shap_explain_text": shap_explain_text_plain, 
    "f5_high": f5_high, "f5_low": f5_low, "f5_high_pct": f5_high_pct, "f5_low_pct": f5_low_pct, 
    "f5_range_width": f5_range_width, "f5_ret_std": f5_ret_std, "upside": upside,
    "buy_low": buy_low, "buy_high": buy_high, "sell_low": sell_low, "sell_high": sell_high,
    "shap_support": shap_support, "shap_resistance": shap_resistance,
    "beta_3_val": beta_3_val, "gamma_val": gamma_val, "vol_ratio": vol_ratio, "vol_signal": vol_signal,
    "hist_pe": hist_pe, "fwd_pe": fwd_pe, "cv_acc": cv_acc_val, "cv_auc": cv_auc_val
}

st.download_button("📝 下載 Word 完整分析報告", data=generate_word_report(ctx), file_name=f"{stock_code}_AI_Report.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", type="primary")
