import math
from datetime import datetime, timezone, timedelta
from io import BytesIO

import pandas as pd
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
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

# ==========================================
# 0. 頁面設定
# ==========================================
st.set_page_config(page_title="跨領域專家 AI 投資分析與量化預測系統", layout="wide", page_icon="📈")

DISCLAIMER = (
    "免責聲明：本報告由程式依公開資料與使用者設定之參數自動試算，僅供研究與學習參考，"
    "不構成任何投資建議。資料來源為 Yahoo Finance，可能有延遲或缺漏；標示「手動」者為使用者自行輸入。"
)

def get_taiwan_time_str(fmt="%Y-%m-%d %H:%M:%S"):
    return datetime.now(timezone(timedelta(hours=8))).strftime(fmt)

# ==========================================
# 1. 動態聯網搜尋與「先測 .TW、再測 .TWO」代號解析機制
# ==========================================
def _has_price(symbol):
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
        })
        return not yf.Ticker(symbol, session=session).history(period="5d").empty
    except Exception:
        return False

@st.cache_data(ttl=3600)
def resolve_symbol(user_input):
    text = user_input.strip()
    upper_text = text.upper()
    
    # 1. 若已經是標準代號格式
    if upper_text.endswith(".TW") or upper_text.endswith(".TWO"):
        return upper_text
    if upper_text.isdigit() and len(upper_text) in (4, 5, 6):
        for suffix in (".TW", ".TWO"):
            test_sym = upper_text + suffix
            if _has_price(test_sym):
                return test_sym
        return upper_text + ".TW"

    # 2. 透過 Yahoo Finance 搜尋 API 動態聯網查詢名稱（如「今國光」、「台積電」）
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
        })
        search_url = f"https://query1.finance.yahoo.com/v1/finance/search?q={text}&quotesCount=10&newsCount=0"
        res = session.get(search_url, timeout=5)
        data = res.json()
        
        if "quotes" in data and len(data["quotes"]) > 0:
            # 優先檢查搜尋結果中已經自帶 .TW 或 .TWO 的標的
            for q in data["quotes"]:
                sym = q.get("symbol", "")
                if ".TW" in sym or ".TWO" in sym:
                    if _has_price(sym):
                        return sym
            
            # 從搜尋結果中萃取出數字代號，嚴格執行「先測 .TW，再測 .TWO」
            for q in data["quotes"]:
                sym = q.get("symbol", "")
                clean_digits = ''.join(filter(str.isdigit, sym))
                if len(clean_digits) in (4, 5):
                    for suffix in (".TW", ".TWO"):
                        test_sym = clean_digits + suffix
                        if _has_price(test_sym):
                            return test_sym
                    return clean_digits + ".TW"
                    
            # 檢查第一筆搜尋結果
            first_sym = data["quotes"][0].get("symbol", "")
            if first_sym:
                clean_digits = ''.join(filter(str.isdigit, first_sym))
                if len(clean_digits) in (4, 5):
                    for suffix in (".TW", ".TWO"):
                        test_sym = clean_digits + suffix
                        if _has_price(test_sym):
                            return test_sym
                    return clean_digits + ".TW"
                if _has_price(first_sym):
                    return first_sym
    except Exception:
        pass
        
    # 3. 最後防線：若輸入含有數字，自動萃取並依序測試 .TW 與 .TWO
    clean_input_digits = ''.join(filter(str.isdigit, text))
    if clean_input_digits:
        for suffix in (".TW", ".TWO"):
            test_sym = clean_input_digits + suffix
            if _has_price(test_sym):
                return test_sym
        return clean_input_digits + ".TW"
        
    return text

@st.cache_data(ttl=3600)
def get_company_name(symbol):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
    })
    try:
        tw_yahoo_url = f"https://tw.stock.yahoo.com/quote/{symbol.split('.')[0]}"
        res = session.get(tw_yahoo_url, timeout=5)
        match = re.search(r'<title>(.*?)\(', res.text)
        if match:
            extracted_name = match.group(1).strip()
            if extracted_name and "Yahoo" not in extracted_name and "找不到" not in extracted_name:
                return f"{extracted_name} ({symbol})"
    except Exception:
        pass
        
    try:
        info = yf.Ticker(symbol, session=session).info
        name = info.get("longName") or info.get("shortName")
        if name:
            return f"{name} ({symbol})"
    except Exception:
        pass
    return f"{symbol}"

@st.cache_data(ttl=1800)
def fetch_news_sentiment_recent(symbol, company_full_name, hours=48):
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
        })
        clean_sym = symbol.split('.')[0]
        query_kw = company_full_name.split('(')[0].strip() or clean_sym
        
        news_url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query_kw}&quotesCount=0&newsCount=15"
        res = session.get(news_url, timeout=5)
        data = res.json()
        
        news_items = data.get("news", [])
        if not news_items:
            return 5.0, "無近期新聞，給予中性分"
            
        now_timestamp = datetime.now().timestamp()
        time_threshold = now_timestamp - (hours * 3600)
        
        filtered_items = []
        for item in news_items:
            pub_time = item.get("providerPublishTime", 0)
            if pub_time >= time_threshold:
                filtered_items.append(item)
                
        if not filtered_items:
            return 5.0, f"近 {hours}H 內無相關新聞，給予中性分"
            
        bullish_words = ["漲", "高", "強", "買超", "創高", "突破", "擴產", "營收揚升", "暢旺", "多方", "利多", "成長"]
        bearish_words = ["跌", "殺", "跌停", "衰退", "利空", "縮減", "賣超", "低迷", "修正", "震盪", "壓力"]
        
        score_sum = 5.0
        count = 0
        for item in filtered_items:
            title = item.get("title", "")
            b_hits = sum(1 for w in bullish_words if w in title)
            r_hits = sum(1 for w in bearish_words if w in title)
            
            if b_hits > r_hits:
                score_sum += 1.5 * b_hits
            elif r_hits > b_hits:
                score_sum -= 1.5 * r_hits
            count += 1
            
        final_score = max(0.0, min(10.0, round(score_sum / max(1, count) + 3.0, 1)))
        return final_score, f"成功篩選近 {hours}H 內 {count} 篇新聞計算情緒"
    except Exception:
        return 5.0, f"聯網抓取近 {hours}H 情緒異常，採用預設值"

@st.cache_data(ttl=1800)
def fetch_growth_score_recent(symbol, company_full_name, hours=48):
    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
        })
        clean_sym = symbol.split('.')[0]
        query_kw = company_full_name.split('(')[0].strip() or clean_sym
        
        news_url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query_kw}&quotesCount=0&newsCount=15"
        res = session.get(news_url, timeout=5)
        data = res.json()
        
        news_items = data.get("news", [])
        if not news_items:
            return 5.0, "無近期新聞，給予中性成長評分"
            
        now_timestamp = datetime.now().timestamp()
        time_threshold = now_timestamp - (hours * 3600)
        
        filtered_items = []
        for item in news_items:
            pub_time = item.get("providerPublishTime", 0)
            if pub_time >= time_threshold:
                filtered_items.append(item)
                
        if not filtered_items:
            return 5.0, f"近 {hours}H 內無相關新聞，給予中性成長評分"
            
        growth_positive_words = ["展望佳", "成長", "擴產", "訂單滿", "創高", "突破", "上修", "法人看好", "強勁", "增溫"]
        growth_negative_words = ["下修", "衰退", "保守", "庫存調整", "壓力", "疲弱", "下滑"]
        
        score_sum = 5.0
        count = 0
        for item in filtered_items:
            title = item.get("title", "")
            p_hits = sum(1 for w in growth_positive_words if w in title)
            n_hits = sum(1 for w in growth_negative_words if w in title)
            
            if p_hits > n_hits:
                score_sum += 2.0 * p_hits
            elif n_hits > p_hits:
                score_sum -= 2.0 * n_hits
            count += 1
            
        final_score = max(0.0, min(10.0, round(score_sum / max(1, count) + 2.0, 1)))
        return final_score, f"成功篩選近 {hours}H 內 {count} 篇新聞展望"
    except Exception:
        return 5.0, f"聯網抓取近 {hours}H 展望異常，採用預設值"

# ==========================================
# 2. 行情與財報數據擷取
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

# ==========================================
# 3. 藍紅動能區建議價格模擬器
# ==========================================
def calculate_target_price_for_rsi(close_prices, target_rsi, mode='drop'):
    last_close = close_prices.iloc[-1]
    step = last_close * 0.005 
    sim_price = last_close
    
    def calc_single_rsi(prices_series):
        delta = prices_series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        return (100 - (100 / (1 + rs))).iloc[-1]
        
    current_rsi = calc_single_rsi(close_prices)
    
    if mode == 'drop':
        if current_rsi <= target_rsi:
            return last_close, current_rsi
        for _ in range(100):
            sim_price -= step
            sim_series = pd.concat([close_prices, pd.Series([sim_price])]).reset_index(drop=True)
            sim_rsi = calc_single_rsi(sim_series)
            if pd.notna(sim_rsi) and sim_rsi <= target_rsi:
                return sim_price, sim_rsi
        return sim_price, sim_rsi
        
    elif mode == 'rise':
        if current_rsi >= target_rsi:
            return last_close, current_rsi
        for _ in range(100):
            sim_price += step
            sim_series = pd.concat([close_prices, pd.Series([sim_price])]).reset_index(drop=True)
            sim_rsi = calc_single_rsi(sim_series)
            if pd.notna(sim_rsi) and sim_rsi >= target_rsi:
                return sim_price, sim_rsi
        return sim_price, sim_rsi

# ==========================================
# 4. Word 報告生成
# ==========================================
def generate_word_report(ctx):
    doc = Document()
    t = doc.add_heading(f"{ctx['name']} 跨領域 AI 投資與量化分析報告", 0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"報告生成時間：{get_taiwan_time_str('%Y 年 %m 月 %d 日 %H:%M (CST)')}")
    doc.add_paragraph(f"最新即時成交價：{ctx['price']:,.2f}（成交時間 {ctx['trade_date']}，當日漲跌 {ctx['change_txt']}）")
    doc.add_paragraph(f"AI 動態非線性模型目標價：{ctx['tp_base']:,.2f}（{ctx['rec']}）")
    doc.add_paragraph(f"藍色動能區（建議買點）：{ctx['blue_price']:,.2f} 元 | 紅色動能區（建議賣價）：{ctx['red_price']:,.2f} 元")
    doc.add_paragraph(f"目標價區間：[{ctx['tp_lower']:,.0f}, {ctx['tp_upper']:,.0f}]")

    doc.add_heading("一、AI 模型預測與動能區間", level=1)
    doc.add_paragraph(f"未來 5 日擊敗大盤勝率預測：{ctx['latest_proba']:.2%}")
    doc.add_paragraph(f"AI 建議逢低買點：{ctx['blue_price']:,.2f} 元（預估 RSI 降至 {ctx['blue_rsi']:.1f}）")
    doc.add_paragraph(f"AI 建議逢高賣出價：{ctx['red_price']:,.2f} 元（預估 RSI 升至 {ctx['red_rsi']:.1f}）")

    doc.add_heading("二、基本面估值模型", level=1)
    doc.add_paragraph(f"動態非線性 PE = {ctx['pe_target']:.1f}x，目標價 {ctx['tp_base']:,.2f}")
    doc.add_paragraph(f"線性基準 PE = {ctx['pe_linear']:.1f}x，目標價 {ctx['tp_linear']:,.2f}")
    doc.add_paragraph(f"調整後預估 EPS：{ctx['eps_adj']:.2f}")

    doc.add_heading("三、財務檢核數據", level=1)
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
        ("歷史本益比", f"{ctx['hist_pe']:.1f} 倍", "即時股價 / TTM EPS"),
        ("遠期本益比", f"{ctx['fwd_pe']:.1f} 倍", "即時股價 / 調整後預估 EPS"),
    ]
    for a, b, c in rows:
        r = table.add_row().cells
        r[0].text, r[1].text, r[2].text = a, b, c

    doc.add_heading("四、即時新聞情緒與展望成長評分 (聯網真實數據)", level=1)
    doc.add_paragraph(f"近 48H 聯網新聞聲量情緒分數：{ctx['sentiment']:.1f} / 10（狀態說明：{ctx['news_status']}）", style="List Bullet")
    doc.add_paragraph(f"近 48H 聯網展望成長評分：{ctx['growth_score']:.1f} / 10（狀態說明：{ctx['growth_status']}）", style="List Bullet")

    doc.add_heading("五、風險提示", level=1)
    for r in ctx["risks"]:
        doc.add_paragraph(f"{r[0]}：{r[1]} — {r[2]}", style="List Bullet")

    doc.add_paragraph("")
    doc.add_paragraph(DISCLAIMER)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()

# ==========================================
# 5. 側邊欄參數設定
# ==========================================
st.sidebar.title("⚙️ 標的與參數設定")

with st.sidebar.form(key="search_form"):
    user_query = st.text_input(
        "輸入公司名稱或代號（如 今國光, 6209, 聯電, 2303）", value="今國光"
    ).strip()
    st.form_submit_button("📊 執行 AI 與基本面綜合分析")

if not user_query:
    st.info("請在左側輸入公司名稱或代號。")
    st.stop()

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
})

symbol = resolve_symbol(user_query)
company_name = get_company_name(symbol)

# 🌟 自動聯網抓取近 48 小時內新聞並計算情緒與展望成長分數
auto_sentiment_score, news_status_msg = fetch_news_sentiment_recent(symbol, company_name, hours=48)
auto_growth_score, growth_status_msg = fetch_growth_score_recent(symbol, company_name, hours=48)

# ==========================================
# 6. 主程式執行與即時行情、計量模型運算
# ==========================================
with st.spinner(f'正在取得 {company_name} 即時報價與美股市場資料（NVDA、SOX 等），並進行機器學習訓練與價格模擬...'):
    stock_code = symbol.split('.')[0]
    exchange = symbol.split('.')[1] if '.' in symbol else "TW"
    tickers = [symbol, 'NVDA', '^SOX', '^DJI', '^IRX', '^TWII']
    
    end_date = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')
    fetch_start = (datetime.today() - pd.DateOffset(years=4)).strftime('%Y-%m-%d')
    
    market_data = yf.download(tickers, start=fetch_start, end=end_date, progress=False, session=session)['Close']
    
    if symbol not in market_data.columns or market_data[symbol].dropna().empty:
        st.error(f"❌ 找不到 {symbol} 的股價資料，或遭遇 Yahoo Finance 暫時封鎖，請稍後再試。")
        st.stop()

    valid_stock_data = market_data[symbol].dropna()
    
    try:
        tkr = yf.Ticker(symbol, session=session)
        price = float(tkr.fast_info['last_price'])
        trade_date = get_taiwan_time_str('%Y-%m-%d %H:%M:%S')
    except Exception:
        price = float(valid_stock_data.iloc[-1])
        trade_date = valid_stock_data.index[-1].strftime('%Y-%m-%d')

    change = (price / float(valid_stock_data.iloc[-2]) - 1) * 100 if len(valid_stock_data) >= 2 else 0.0
    ret_05m = (price / float(valid_stock_data.iloc[-11]) - 1) * 100 if len(valid_stock_data) > 10 else None
    ret_3m = (price / float(valid_stock_data.iloc[-61]) - 1) * 100 if len(valid_stock_data) > 60 else None

    # 財報數據抓取
    tkr_fin = yf.Ticker(symbol, session=session)
    q_eps_list = []
    ttm_eps, annual_eps = None, None
    try:
        s_q = _eps_series(tkr_fin.quarterly_income_stmt)
        if s_q is not None:
            q_eps_list = [(f"{d.year}Q{(d.month - 1) // 3 + 1}", float(v)) for d, v in list(s_q.items())[:4]]
            if len(q_eps_list) == 4:
                ttm_eps = round(sum(v for _, v in q_eps_list), 2)
    except Exception:
        pass

    try:
        s_a = _eps_series(tkr_fin.income_stmt)
        if s_a is not None:
            annual_eps = round(float(s_a.iloc[0]), 2)
    except Exception:
        pass

    # 計算藍紅動能區價格
    blue_price_target, blue_rsi = calculate_target_price_for_rsi(valid_stock_data, target_rsi=40, mode='drop')
    red_price_target, red_rsi = calculate_target_price_for_rsi(valid_stock_data, target_rsi=70, mode='rise')

    # 計量模型特徵工程
    returns = market_data[[symbol, 'NVDA', '^SOX', '^DJI', '^TWII']].pct_change().dropna()
    rf_us_daily = (market_data['^IRX'].dropna() / 100) / 365
    df = returns.join(rf_us_daily, how='inner').rename(columns={'^IRX': 'RF_US'})
    df['RF_TW'] = 0.017 / 365 

    df['Price_Mom_30D'] = (market_data[symbol].pct_change(30) - market_data['^TWII'].pct_change(30)).shift(1)
    df['Price_Mom_5D'] = (market_data[symbol].pct_change(5) - market_data['^TWII'].pct_change(5)).shift(1)
    df['Vol_10D'] = market_data[symbol].pct_change().rolling(10).std().shift(1)

    delta = market_data[symbol].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI_14'] = (100 - (100 / (1 + rs))).shift(1)
    df = df.dropna()

    # 迴歸模型
    Y_ortho = df['NVDA'] - df['RF_US']
    X_ortho = pd.DataFrame({'DJI_Excess': df['^DJI'] - df['RF_US'], 'SOX_Excess': df['^SOX'] - df['RF_US']})
    X_ortho = sm.add_constant(X_ortho)
    df['NVDA_Pure_Shock'] = sm.OLS(Y_ortho, X_ortho).fit().resid 

    df['Interaction_Term'] = df['NVDA_Pure_Shock'] * df['Price_Mom_30D']
    Y_rolling = df[symbol] - df['RF_TW']
    X_rolling = df[['^TWII', '^SOX', 'NVDA_Pure_Shock', 'Price_Mom_30D', 'Interaction_Term']]
    X_rolling = sm.add_constant(X_rolling)

    rolling_res = RollingOLS(Y_rolling, X_rolling, window=252).fit()
    params_df = rolling_res.params
    
    df['Beta_3_Rolling'] = params_df['NVDA_Pure_Shock']
    df['Gamma_Rolling'] = params_df['Interaction_Term']
    df['Beta_3_Trend_5D'] = df['Beta_3_Rolling'].diff(5)
    df['Gamma_Trend_5D'] = df['Gamma_Rolling'].diff(5)
    
    plot_gamma = params_df['Interaction_Term'].dropna()
    plot_beta3 = params_df['NVDA_Pure_Shock'].dropna()

    # AI 模型訓練
    threshold = 0.005 
    df['Target_Label'] = ((market_data[symbol].pct_change(5).shift(-5) - market_data['^TWII'].pct_change(5).shift(-5)) > threshold).astype(int)
    df_ai = df.dropna()

    features = ['Beta_3_Rolling', 'Beta_3_Trend_5D', 'Gamma_Rolling', 'Gamma_Trend_5D', 'NVDA_Pure_Shock', 'Price_Mom_30D', 'Price_Mom_5D', 'RSI_14', 'Vol_10D']
    X = df_ai[features]
    y = df_ai['Target_Label']

    model = lgb.LGBMClassifier(n_estimators=80, learning_rate=0.03, max_depth=3, min_child_samples=40, subsample=0.7, colsample_bytree=0.7, reg_alpha=0.5, reg_lambda=0.5, random_state=42, verbose=-1)
    tscv = TimeSeriesSplit(n_splits=5)
    gap = 5 
    cv_test_acc, cv_test_auc = [], []

    for train_index, test_index in tscv.split(X):
        safe_train_index = train_index[:-gap] if len(train_index) > gap else train_index
        model.fit(X.iloc[safe_train_index], y.iloc[safe_train_index])
        cv_test_acc.append(accuracy_score(y.iloc[test_index], model.predict(X.iloc[test_index])))
        cv_test_auc.append(roc_auc_score(y.iloc[test_index], model.predict_proba(X.iloc[test_index])[:, 1]))

    latest_features = X.iloc[[-1]]
    latest_proba = model.predict_proba(latest_features)[:, 1][0]
    current_beta3 = plot_beta3.iloc[-1]
    beta3_trend_val = df_ai['Beta_3_Trend_5D'].iloc[-1]
    beta3_trend_str = "上升 ↗" if beta3_trend_val > 0 else "下降 ↘"
    current_gamma = plot_gamma.iloc[-1]
    gamma_trend_val = df_ai['Gamma_Trend_5D'].iloc[-1]
    gamma_trend_str = "加速湧入 ↗" if gamma_trend_val > 0 else "動能衰退 ↘"

# ==========================================
# 7. 側邊欄財報與估值覆寫設定
# ==========================================
st.sidebar.markdown("---")
st.sidebar.subheader("財報 EPS 設定")
fetched_ok = ttm_eps is not None and annual_eps is not None
if fetched_ok:
    st.sidebar.success("已自動取得 TTM 與年度 EPS")
else:
    st.sidebar.warning("財報資料不完整，請手動輸入 EPS")

use_manual = st.sidebar.checkbox("手動輸入 / 覆寫 EPS", value=not fetched_ok)
if use_manual:
    ttm_eps_val = st.sidebar.number_input("近 4 季 EPS (TTM)", value=float(ttm_eps or 3.0), step=0.1, format="%.2f")
    annual_eps_val = st.sidebar.number_input("最近年度 EPS", value=float(annual_eps or ttm_eps_val), step=0.1, format="%.2f")
    ttm_src = annual_src = "手動輸入"
else:
    ttm_eps_val, annual_eps_val = ttm_eps or 3.0, annual_eps or 3.0
    ttm_src = annual_src = "Yahoo Finance"

st.sidebar.markdown("---")
st.sidebar.subheader("估值模型變數")
eps_fwd_base = st.sidebar.number_input("基礎預估 Forward EPS (模擬範例數據)", min_value=0.01, value=float(max(0.5, round(ttm_eps_val * 1.1, 2))), step=0.1, format="%.2f")
pe_base = st.sidebar.number_input("產業中樞本益比 (PE) (模擬範例數據)", min_value=1.0, value=22.0)

st.sidebar.info(f"📰 新聞情緒狀態：{news_status_msg}")
sentiment = st.sidebar.slider("新聞聲量情緒 (0~10) (近 48H 聯網真實新聞情緒)", 0.0, 10.0, float(auto_sentiment_score), 0.1)

st.sidebar.info(f"📈 展望成長狀態：{growth_status_msg}")
growth_score = st.sidebar.slider("展望成長評分 (0~10) (近 48H 聯網真實展望評分)", 0.0, 10.0, float(auto_growth_score), 0.1)

risk_val = st.sidebar.slider("下行風險折價 (-PE) (模擬範例數據)", 0.0, 10.0, 1.0, 0.1)

# ==========================================
# 8. 估值核心計算
# ==========================================
hot_triggered = beta3_trend_val > 0
eps_triggered = ttm_eps_val > annual_eps_val > 0
amp_factor = 1.0 + 0.30 * max(0, beta3_trend_val) if hot_triggered else 1.0
eps_multiplier = math.pow(ttm_eps_val / annual_eps_val, 0.35) if eps_triggered else 1.0
eps_adj = eps_fwd_base * eps_multiplier

alpha_g, beta_g, alpha_s, beta_s = 0.8, 0.22, 0.5, 0.35
growth_exp = alpha_g * amp_factor * (math.exp(beta_g * growth_score) - 1)
sent_diff = sentiment - 5.0
sentiment_exp = alpha_s * amp_factor * math.copysign(1, sent_diff) * (math.exp(beta_s * abs(sent_diff)) - 1)

pe_target_raw = pe_base + sentiment_exp + growth_exp - risk_val
pe_target = min(max(pe_target_raw, pe_base * 0.5), pe_base * 2.0)
pe_capped = pe_target != pe_target_raw

pe_linear = pe_base + (sentiment - 5.0) * 0.4 + max(growth_score - 5.0, 0.0) * 0.6 - risk_val
pe_linear = max(pe_linear, 1.0)
tp_linear = eps_fwd_base * pe_linear

pe_upper = pe_target + 4.0
pe_lower = max(min(pe_base - 3.0, pe_target - 3.0), 1.0)

tp_base = eps_adj * pe_target
tp_upper = eps_adj * pe_upper
tp_lower = eps_adj * pe_lower

upside = (tp_base / price - 1) * 100
fwd_pe = price / eps_adj if eps_adj > 0 else 0.0
hist_pe = price / ttm_eps_val if ttm_eps_val > 0 else 0.0

if latest_proba > 0.55 and beta3_trend_val > 0:
    rec, rec_icon = "強烈作多 (建議買進)", "🟢"
elif latest_proba < 0.45:
    rec, rec_icon = "保守觀望 (建議賣出)", "🔴"
else:
    rec, rec_icon = "中性震盪 (持有)", "🟡"

risks = [
    ("總體經濟風險", "利率與匯率波動", "可能造成毛利與評價短期波動"),
    ("市場競爭風險", "同業擴產與需求變化", "需持續追蹤訂單能見度"),
    ("模型風險", "參數多為主觀設定", "請以多組情境檢視，勿單一依賴目標價"),
]

def fmt_pct(v):
    return "資料不足" if v is None else f"{v:+.2f}%"

# ==========================================
# 9. 主畫面呈現
# ==========================================
st.title("📈 跨領域專家 AI 投資分析與量化預測")
st.subheader(f"🏢 {company_name}")
st.caption(f"報告生成時間：{get_taiwan_time_str()} (CST)")
st.warning(DISCLAIMER)

change_txt = fmt_pct(change)
c1, c2, c3, c4 = st.columns(4)
c1.metric("最新即時成交價", f"${price:,.2f}", f"{trade_date} ({change_txt})")
c2.metric("AI 動態目標價", f"${tp_base:,.0f}", f"{upside:.1f}% 潛在空間")
c3.metric("AI 綜合評等", rec, rec_icon)
c4.metric("目標價區間", f"[{tp_lower:,.0f}, {tp_upper:,.0f}]")

ctx = {
    "name": company_name, "price": price, "trade_date": trade_date,
    "change_txt": change_txt, "tp_base": tp_base, "tp_linear": tp_linear,
    "tp_lower": tp_lower, "tp_upper": tp_upper, "rec": rec,
    "latest_proba": latest_proba, "blue_price": blue_price_target, "red_price": red_price_target,
    "blue_rsi": blue_rsi, "red_rsi": red_rsi,
    "q_eps": q_eps_list, "ttm": ttm_eps_val, "annual": annual_eps_val,
    "ttm_src": ttm_src, "annual_src": annual_src,
    "pe_target": pe_target, "pe_linear": pe_linear, "eps_adj": eps_adj,
    "hist_pe": hist_pe, "fwd_pe": fwd_pe, "risks": risks,
    "sentiment": sentiment, "growth_score": growth_score,
    "news_status": news_status_msg, "growth_status": growth_status_msg
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
    st.subheader("一、AI 決策動能區間 (買賣點建議)")
    st.info(f"**🟦 藍色動能區 (建議逢低試單點)**\n\n預估跌至 **{blue_price_target:.2f} 元** 時，RSI 將降至 {blue_rsi:.1f} (超賣區)。歷史數據顯示此時模型勝率最高，為極佳的防守反擊點。")
    st.warning(f"**🟥 紅色動能區 (建議逢高賣出價)**\n\n預估漲至 **{red_price_target:.2f} 元** 時，RSI 將飆至 {red_rsi:.1f} (過熱區)。系統判定此時追高勝率極差，容易遭遇主力倒貨，建議分批停利。")
    
    st.markdown("---")
    st.subheader("二、估值模型對照（動態非線性 vs 線性）")
    st.markdown(f"🚀 **動態非線性模型：** PE **{pe_target:.1f}x** → 目標價 **${tp_base:,.0f}**")
    if pe_capped:
        st.caption(f"⚠️ 原始 PE {pe_target_raw:.1f}x 超出範圍，已自動套用上下限保護。")
    st.markdown(f"📉 **線性基準模型：** PE **{pe_linear:.1f}x** → 目標價 **${tp_linear:,.0f}**")
    st.markdown(f"✨ **調整後 Forward EPS：** **{eps_adj:.2f}**（基礎 {eps_fwd_base}）")

    s1, s2, s3 = st.columns(3)
    s1.metric("悲觀 (Bear)", f"${tp_lower:,.0f}", f"PE: {pe_lower:.1f}x", delta_color="off")
    s2.metric("基準 (Base)", f"${tp_base:,.0f}", f"PE: {pe_target:.1f}x", delta_color="off")
    s3.metric("樂觀 (Bull)", f"${tp_upper:,.0f}", f"PE: {pe_upper:.1f}x (動能PE+4x)", delta_color="off")

with right:
    st.subheader("三、財務檢核與 AI 預測指標")
    f1, f2, f3 = st.columns(3)
    f1.metric("TTM EPS", f"{ttm_eps_val:.2f}", ttm_src, delta_color="off")
    f2.metric("年度 EPS", f"{annual_eps_val:.2f}", annual_src, delta_color="off")
    f3.metric("遠期 P/E", f"{fwd_pe:.1f}x")

    st.markdown("**AI 模型核心指標狀態：**")
    col_m1, col_m2, col_m3 = st.columns(3)
    col_m1.metric("未來 5 日勝率", f"{latest_proba:.2%}")
    col_m2.metric("AI 晶片純度趨勢", beta3_trend_str, f"{current_beta3:.4f}")
    col_m3.metric("資金擁擠度", gamma_trend_str, f"{current_gamma:.4f}", delta_color="inverse")

    st.markdown("---")
    st.markdown("**📰 近 48H 聯網即時情緒與展望評分 (真實聯網數據)：**")
    ns1, ns2 = st.columns(2)
    ns1.metric("新聞聲量情緒", f"{sentiment:.1f} / 10")
    ns2.metric("展望成長評分", f"{growth_score:.1f} / 10")
    st.caption(f"• 情緒狀態：{news_status_msg}\n• 展望狀態：{growth_status_msg}")

    st.markdown("**近 4 季單季 EPS：**")
    if q_eps_list:
        st.dataframe(pd.DataFrame(q_eps_list, columns=["財報季度", "單季 EPS (元)"]), hide_index=True)
    else:
        st.caption("Yahoo Finance 未提供單季 EPS 資料，可於側邊欄手動輸入。")

    st.subheader("四、風險提示")
    st.dataframe(pd.DataFrame(risks, columns=["風險維度", "關鍵影響因子", "影響評估"]), hide_index=True)

# ==========================================
# 10. 歷史回測與 SHAP 決策圖表
# ==========================================
st.markdown("---")
st.markdown("### 📊 歷史波段回測與 SHAP AI 決策邏輯")

min_beta3_date = plot_beta3.idxmin()
period_returns = market_data[symbol].loc[min_beta3_date:plot_beta3.loc[min_beta3_date:].idxmax()].pct_change().dropna()

fig_col1, fig_col2 = st.columns(2)

with fig_col1:
    fig1, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 10), sharex=True)
    ax1.plot(plot_gamma.index, plot_gamma, color='purple', label='Gamma (Crowding)')
    ax1.axhline(0, color='red', linestyle='--'); ax1.legend(loc='upper left'); ax1.grid(True, alpha=0.3)
    
    ax2.plot(plot_beta3.index, plot_beta3, color='forestgreen', label='Beta_3 (Pure AI Shock)')
    if not period_returns.empty:
        ax2.axvspan(period_returns.index[0], period_returns.index[-1], color='yellow', alpha=0.2, label='Surge Period')
    ax2.axhline(0, color='red', linestyle='--'); ax2.legend(loc='upper left'); ax2.grid(True, alpha=0.3)

    if not period_returns.empty:
        cum_returns = (1 + period_returns).cumprod() - 1
        ax3.plot(cum_returns.index, cum_returns, color='darkred', label='Cumulative Return')
        ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: '{:.0%}'.format(y)))
    ax3.legend(loc='upper left'); ax3.grid(True, alpha=0.3)
    fig1.suptitle(f'[{symbol}] Econometric Surge Backtest', fontsize=14)
    plt.tight_layout()
    st.pyplot(fig1)

with fig_col2:
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X.iloc[test_index])
    shap_values_to_plot = shap_values[1] if isinstance(shap_values, list) else shap_values

    fig2 = plt.figure(figsize=(10, 8))
    shap.summary_plot(shap_values_to_plot, X.iloc[test_index], feature_names=features, show=False)
    plt.title(f"[{symbol}] SHAP AI Decision Logic", fontsize=14)
    plt.tight_layout()
    st.pyplot(fig2)
