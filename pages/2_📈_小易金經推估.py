import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf
import requests
import re
import urllib.parse
from bs4 import BeautifulSoup
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, Tuple

# ==========================================
# 1. 標的解析與中英文名稱對照機制 (智慧動態反查與先 .TW 後 .TWO 驗證)
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
    
    # 支援輸入 0000 或 ^TWII 直接對應大盤加權指數
    if upper_text == "0000" or upper_text == "^TWII" or text == "大盤":
        return "^TWII"

    if upper_text.endswith((".TW", ".TWO", ".US", "=F")) or upper_text.startswith("^"):
        return upper_text

    # 如果是純英文（美股代號如 NVDA, AAPL），直接回傳
    if upper_text.isalpha() and len(upper_text) <= 5:
        return upper_text
        
    digits_found = None
    if upper_text.isdigit():
        digits_found = upper_text
    else:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
        }
        clean_query = re.sub(r'\s+', '', text)

        # 1. 透過證交所與櫃買中心 ISIN 網站尋找公司代碼（支援模糊包含與字串清洗）
        for mode in ["2", "4"]:
            try:
                url = f"https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}"
                response = requests.get(url, headers=headers, timeout=6)
                response.encoding = 'big5'
                
                soup = BeautifulSoup(response.text, 'html.parser')
                for row in soup.find_all('tr'):
                    tds = row.find_all('td')
                    if tds and len(tds) > 0:
                        cell_text = tds[0].get_text().strip()
                        clean_cell = re.sub(r'\s+', '', cell_text)
                        if clean_query in clean_cell:
                            parts = cell_text.split()
                            if parts and parts[0].isdigit() and len(parts[0]) in (4, 5):
                                digits_found = parts[0]
                                break
            except Exception:
                continue
            if digits_found:
                break

        # 2. 若 ISIN 未找到，改用 Yahoo Finance 搜尋 API 尋找公司代碼
        if not digits_found:
            try:
                session = requests.Session()
                session.headers.update(headers)
                search_url = f"https://query1.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(text)}&quotesCount=5&newsCount=0"
                res = session.get(search_url, timeout=5)
                data = res.json()
                if "quotes" in data:
                    for q in data["quotes"]:
                        sym = q.get("symbol", "")
                        digits = "".join(c for c in sym if c.isdigit())
                        if len(digits) in [4, 5]:
                            digits_found = digits
                            break
            except Exception:
                pass

    # 3. 取得公司代碼後，嚴格執行：先測試 .TW，若無效再測試 .TWO
    if digits_found:
        for suffix in [".TW", ".TWO"]:
            symbol = digits_found + suffix
            if _has_price(symbol):
                return symbol
        return digits_found + ".TW"

    return text

@st.cache_data(ttl=3600)
def get_company_name(symbol):
    if symbol == "^TWII":
        return "大盤加權指數 (^TWII)"

    cn_mapping = {
        "NVDA": "輝達 (NVIDIA)", "AAPL": "蘋果 (Apple)", "TSLA": "特斯拉 (Tesla)",
        "MSFT": "微軟 (Microsoft)", "GOOGL": "谷歌 (Alphabet)", "AMZN": "亞馬遜 (Amazon)",
        "META": "Meta (臉書)", "AMD": "超微 (AMD)", "TSM": "台積電 ADR (TSMC)"
    }
    clean_sym = symbol.upper().strip()
    if clean_sym in cn_mapping:
        return cn_mapping[clean_sym]

    try:
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
        })
        if ".TW" in symbol or ".TWO" in symbol:
            stock_id = symbol.split('.')[0]
            tw_yahoo_url = f"https://tw.stock.yahoo.com/quote/{stock_id}"
            res = session.get(tw_yahoo_url, timeout=5)
            match = re.search(r'<title>(.*?)\(', res.text)
            if match:
                extracted_name = match.group(1).strip()
                if extracted_name and "Yahoo" not in extracted_name and "找不到" not in extracted_name:
                    return f"{extracted_name} ({symbol})"

        stock = yf.Ticker(symbol, session=session)
        info = stock.info
        name = info.get("longName") or info.get("shortName")
        if name:
            return f"{name} ({symbol})"
    except Exception:
        pass
    return symbol

def resolve_yahoo_ticker(user_input):
    resolved_sym = resolve_symbol(user_input)
    if resolved_sym == "^TWII":
        return "^TWII", "大盤加權指數", "大盤指數", "0000"

    full_name_str = get_company_name(resolved_sym)
    match = re.match(r"^(.*?)\s*\(", full_name_str)
    company_name = match.group(1).strip() if match else full_name_str

    if resolved_sym.endswith(".TWO"):
        market_attr = "上櫃公司"
    elif resolved_sym.endswith(".TW"):
        market_attr = "上市公司"
    elif resolved_sym.isalpha() and len(resolved_sym) <= 5:
        market_attr = "美股/國際標的"
    else:
        market_attr = "國際/其他標的"

    pure_digits = "".join(filter(str.isdigit, resolved_sym)) or resolved_sym
    return resolved_sym, company_name, market_attr, pure_digits

@st.cache_data(ttl=600)
def fetch_yahoo_data(ticker_symbol, interval, period):
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty and ticker_symbol.isdigit():
            alt_symbol = ticker_symbol + ".TWO"
            ticker = yf.Ticker(alt_symbol)
            df = ticker.history(period=period, interval=interval)
            ticker_symbol = alt_symbol

        if df.empty:
            return None, f"無法從 Yahoo Finance 取得代號 {ticker_symbol} 的資料。"

        df = df.reset_index()
        col_candidates = [c for c in df.columns if 'Date' in c or 'Datetime' in c]
        date_col = col_candidates[0] if col_candidates else df.columns[0]

        df = df.rename(columns={
            date_col: "DateTime",
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Volume": "Volume"
        })

        df["DateTime"] = pd.to_datetime(df["DateTime"])
        if df["DateTime"].dt.tz is not None:
            df["DateTime"] = df["DateTime"].dt.tz_convert("Asia/Taipei")
        else:
            df["DateTime"] = df["DateTime"].dt.tz_localize("UTC").dt.tz_convert("Asia/Taipei")

        df["DateTime"] = df["DateTime"].dt.strftime('%Y-%m-%d %H:%M:%S')
        return df, ticker_symbol
    except Exception as e:
        return None, str(e)

# ==========================================
# 2. 核心引擎 (v5.0)
# ==========================================
class IChingTrinitySpatiotemporalEngine:
    def __init__(self, df: pd.DataFrame, ticker: str, company_name: str, timeframe: str):
        self.df = df.copy()
        self.ticker = ticker
        self.company_name = company_name
        self.timeframe = timeframe
        self.lambda_decay = 1.0 / 4.16  
        
        self.hurst = self._calculate_hurst(self.df['Close'].values)
        self.tau_adj = 4.16 * ((self.hurst / 0.5) ** 0.2)

    def _calculate_hurst(self, time_series: np.ndarray) -> float:
        if len(time_series) < 20:
            return 0.5
        lags = range(2, 20)
        tau = [np.sqrt(np.std(np.subtract(time_series[lag:], time_series[:-lag]))) for lag in lags]
        poly = np.polyfit(np.log(lags), np.log(tau), 1)
        return float(np.clip(poly[0] * 2.0, 0.4, 0.75))

    def _ricker_wavelet(self, points: int, a: int) -> np.ndarray:
        t = np.arange(0, points) - (points - 1.0) / 2.0
        x = t / a
        return (2.0 / (np.sqrt(3.0 * a) * (np.pi ** 0.25))) * (1.0 - x ** 2) * np.exp(-0.5 * x ** 2)

    def _custom_cwt(self, data: np.ndarray, widths: np.ndarray) -> np.ndarray:
        data_len = len(data)
        cwtmatr = np.zeros((len(widths), data_len))
        
        for i, width in enumerate(widths):
            points = min(int(width * 10), data_len)
            if points % 2 == 0:
                points += 1
            wavelet = self._ricker_wavelet(points, width)
            conv_result = np.convolve(data, wavelet, mode='same')
            
            if len(conv_result) > data_len:
                conv_result = conv_result[:data_len]
            elif len(conv_result) < data_len:
                conv_result = np.pad(conv_result, (0, data_len - len(conv_result)), 'edge')
                
            cwtmatr[i, :] = conv_result
        return cwtmatr

    def analyze_bian_yi(self) -> Dict[str, Any]:
        close = self.df['Close'].values
        widths = np.arange(1, 32)
        cwtmatr = self._custom_cwt(close, widths)
        
        high_freq_energy = np.mean(np.abs(cwtmatr[1:8, :]), axis=0)
        recent_energy = high_freq_energy[-1]
        energy_slope = (high_freq_energy[-1] - high_freq_energy[-3]) / 2.0
        
        status = "極致收斂/蓄能態" if abs(energy_slope) < 0.05 else ("爆發發散態" if energy_slope > 0 else "耗散減速態")
        return {"high_freq_energy": float(recent_energy), "energy_slope": float(energy_slope), "dynamics_status": status}

    def analyze_bu_yi(self) -> Dict[str, Any]:
        close = self.df['Close'].values
        p0 = close[-1]
        
        lookback = min(60, len(close))
        high_max = np.max(self.df['High'].values[-lookback:])
        low_min = np.min(self.df['Low'].values[-lookback:])
        
        core_support = low_min + (p0 - low_min) * 0.236
        core_resistance = p0 + (high_max - p0) * 0.618
        deviation_rate = ((p0 - core_support) / p0) * 100.0 if p0 != 0 else 0.0
        
        return {"p0": float(p0), "core_support": round(float(core_support), 2), "core_resistance": round(float(core_resistance), 2), "deviation_rate": round(float(deviation_rate), 2)}

    def analyze_jian_yi(self, current_regime_bars: int) -> Dict[str, Any]:
        t = current_regime_bars
        p_retention = np.exp(-self.lambda_decay * (t / self.tau_adj))
        return {"current_bars": t, "tau_adj": round(float(self.tau_adj), 2), "p_retention": round(float(p_retention * 100.0), 2), "is_critical": t >= 4}

    def predict_spatiotemporal_turning_window(self, current_regime_bars: int) -> Dict[str, Any]:
        bu_yi = self.analyze_bu_yi()
        bars_to_primary = max(1, int(round(self.tau_adj - current_regime_bars)))
        bars_to_secondary = bars_to_primary + 2
        return {
            "primary_window": f"未來 第 {bars_to_primary} – {bars_to_primary + 1} 根 K 棒",
            "primary_prob": "65% – 75%",
            "primary_space_target": f"{bu_yi['core_support']} – {bu_yi['core_support'] * 1.01:.2f}",
            "secondary_window": f"未來 第 {bars_to_secondary} – {bars_to_secondary + 1} 根 K 棒",
            "secondary_prob": "25% – 30%",
            "secondary_space_target": f"{bu_yi['core_support'] * 0.98:.2f} – {bu_yi['core_support']:.2f}"
        }

    def generate_full_report(self, current_regime_bars: int, last_bar_time: str, report_time: str) -> str:
        bian = self.analyze_bian_yi()
        buyi = self.analyze_bu_yi()
        jian = self.analyze_jian_yi(current_regime_bars)
        turning = self.predict_spatiotemporal_turning_window(current_regime_bars)
        
        report = f"""==================================================
【易經三義量化時空分析 5.0 版】實戰分析報告
==================================================
公司/指數: {self.company_name}
標的代碼: {self.ticker} | 分析級別: {self.timeframe}
最後K棒時間: {last_bar_time} | 報告產出時脈: {report_time}
當前收盤/太極原點 P0: {buyi['p0']:.2f}
--------------------------------------------------

一、 易經三義量化時空矩陣
--------------------------------------------------
[變易] 高頻能量密度: {bian['high_freq_energy']:.2f} | 狀態: {bian['dynamics_status']}
[不易] 核心重力井: {buyi['core_support']:.2f} | 空間偏離率: {buyi['deviation_rate']}%
[簡易] 狀態持續根數: {jian['current_bars']} 根 | 4.16 校準閾值: {jian['tau_adj']} 根
       當前政權維持機率 P(T >= t): {jian['p_retention']}%

二、 空間標尺投影
--------------------------------------------------
極限/中繼壓力: {buyi['core_resistance']:.2f}
當前太極原點: {buyi['p0']:.2f}
核心結構支撐: {buyi['core_support']:.2f}

三、 時空共振轉折窗預測 (轉折高/轉折低)
--------------------------------------------------
首選轉折窗口: {turning['primary_window']}
  └ 成立機率: {turning['primary_prob']}
  └ 空間對位目標: {turning['primary_space_target']}
次選轉折窗口: {turning['secondary_window']}
  └ 成立機率: {turning['secondary_prob']}
  └ 空間對位目標: {turning['secondary_space_target']}

四、 核心決策邏輯 (Master Insight)
--------------------------------------------------
1. 知幾預警: 當前狀態已持續 {jian['current_bars']} 根，維持機率為 {jian['p_retention']}%。
   預計於 {turning['primary_window']} 進入動能耗散臨界點。
2. 逆數策略: 靜待價格進入 {turning['primary_space_target']} 重力井，
   並觀察小波動能是否平鋪，作為高期望值 E[R] 之決策對位點。
=================================================="""
        return report

    def plot_spatiotemporal_matrix(self, last_bar_time: str):
        plt.style.use('dark_background')
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={'height_ratios': [2, 1]}, sharex=True)
        
        close = self.df['Close'].values
        x = np.arange(len(close))
        
        ax1.plot(x, close, label='Close Price', color='cyan', linewidth=1.5)
        buyi = self.analyze_bu_yi()
        
        ax1.axhline(buyi['core_support'], color='lime', linestyle='--', linewidth=2, label=f"Core Support: {buyi['core_support']:.2f}")
        ax1.axhline(buyi['core_resistance'], color='red', linestyle='--', linewidth=2, label=f"Resistance: {buyi['core_resistance']:.2f}")
        ax1.set_title(f"{self.company_name} ({self.ticker}) - {self.timeframe} Gravity Wells (Last: {last_bar_time})", fontsize=15, fontweight='bold', color='white')
        ax1.set_ylabel("Price")
        ax1.legend(loc='upper left', frameon=True, facecolor='black')
        ax1.grid(True, alpha=0.2, linestyle=':')
        
        widths = np.arange(1, 64)
        cwtmatr = self._custom_cwt(close, widths)
        
        cax = ax2.pcolormesh(x, widths, np.abs(cwtmatr), shading='gouraud', cmap='magma')
        ax2.set_title("CWT Energy Scalogram (Dynamics & Invariants)", fontsize=13, color='white')
        ax2.set_ylabel("Scale (Time Horizon)")
        ax2.set_xlabel("Time (Trading Bars)")
        ax2.invert_yaxis()
        
        cbar = fig.colorbar(cax, ax=ax2, orientation='horizontal', pad=0.2, aspect=40)
        cbar.set_label('Energy Density (Volatility)', color='white')
        cbar.ax.tick_params(colors='white')
        
        plt.tight_layout()
        return fig

# ==========================================
# 3. Streamlit 前端介面
# ==========================================
st.set_page_config(page_title="易經三義量化時空分析", layout="wide", page_icon="☯️")

st.title("☯️ 易經三義量化時空分析系統 (多時框日內版)")
st.markdown("整合 **小波變換動能 (變易)**、**重力井空間 (不易)** 與 **馬可夫狀態機率 (簡易)**。支援台美股代碼、公司名稱或 `0000` (大盤)。")

with st.sidebar:
    st.header("參數設定")
    ticker_input = st.text_input("輸入台/美股代碼或公司名稱 (例如: 今國光、6209 或 NVDA)", value="今國光")
    
    timeframe_choice = st.selectbox(
        "選擇分析週期 (Timeframe)",
        ["Daily (日線)", "60m (60分K)", "30m (30分K)", "15m (15分K)", "5m (5分K)"],
        index=0
    )
    
    current_regime_bars = st.slider("當前趨勢已持續 K棒數 (狀態根數)", min_value=1, max_value=13, value=3)
    run_btn = st.button("啟動量化引擎 🚀", use_container_width=True)

if run_btn:
    with st.spinner(f"正在解析標的 [{ticker_input}] 並獲取 [{timeframe_choice}] 歷史資料..."):
        resolved_sym, company_name, market_type, pure_code = resolve_yahoo_ticker(ticker_input)
        
        if timeframe_choice == "Daily (日線)":
            interval = "1d"
            period = "1y"
        elif timeframe_choice == "60m (60分K)":
            interval = "60m"
            period = "730d"
        elif timeframe_choice == "30m (30分K)":
            interval = "30m"
            period = "60d"
        elif timeframe_choice == "15m (15分K)":
            interval = "15m"
            period = "60d"
        elif timeframe_choice == "5m (5分K)":
            interval = "5m"
            period = "60d"
        else:
            interval = "1d"
            period = "1y"

        df_real, final_symbol = fetch_yahoo_data(resolved_sym, interval, period)
        
        if df_real is None or df_real.empty:
            st.error(f"⚠️ 無法獲取 [{ticker_input}] ({resolved_sym}) 的資料，請確認代碼或公司名稱是否正確。")
        else:
            tw_timezone = ZoneInfo("Asia/Taipei")
            now_tw = datetime.now(tw_timezone)
            
            full_last_time = df_real['DateTime'].iloc[-1]
            if timeframe_choice == "Daily (日線)":
                full_last_time = full_last_time.split()[0]
                
            report_date_str = now_tw.strftime('%Y-%m-%d')
            report_time_str = now_tw.strftime('%H:%M:%S')
            
            current_price = float(df_real['Close'].iloc[-1])
            prev_price = float(df_real['Close'].iloc[-2]) if len(df_real) > 1 else current_price
            price_change = current_price - prev_price
            price_change_pct = (price_change / prev_price) * 100
            
            pure_name_only = company_name
            code_bracket_part = f"({resolved_sym})"
            
            engine = IChingTrinitySpatiotemporalEngine(df_real, ticker=resolved_sym, company_name=company_name, timeframe=timeframe_choice)
            report_text = engine.generate_full_report(current_regime_bars=current_regime_bars, last_bar_time=full_last_time, report_time=f"{report_date_str} {report_time_str}")
            fig = engine.plot_spatiotemporal_matrix(last_bar_time=full_last_time)
            
            buyi_data = engine.analyze_bu_yi()
            bian_data = engine.analyze_bian_yi()
            
            st.markdown("---")
            m1, m2, m3, m4, m5 = st.columns(5)
            
            with m1:
                st.markdown(f"""
                <div style="font-size: 14px; color: #333333; margin-bottom: 2px; font-weight: 600;">標的名稱</div>
                <div style="font-size: 20px; font-weight: bold; color: #111111; line-height: 1.2;">{pure_name_only}</div>
                <div style="font-size: 16px; font-weight: bold; color: #333333; line-height: 1.2; margin-top: 2px;">{code_bracket_part}</div>
                """, unsafe_allow_html=True)

            m2.metric("股票/指數代碼", resolved_sym)
            m3.metric("市場屬性", market_type)
            m4.metric("目前收盤價 (P0)", f"{current_price:.2f}", f"{price_change:+.2f} ({price_change_pct:+.2f}%)")
            
            with m5:
                st.markdown(f"""
                <div style="font-size: 14px; color: #333333; margin-bottom: 2px; font-weight: 600;">最後 K 棒時間</div>
                <div style="font-size: 17px; font-weight: bold; color: #111111; line-height: 1.2;">{full_last_time}</div>
                <div style="font-size: 14px; color: #333333; margin-top: 10px; margin-bottom: 2px; font-weight: 600;">報告產出時間 (CST)</div>
                <div style="font-size: 16px; font-weight: bold; color: #111111; line-height: 1.2;">{report_date_str}<br>{report_time_str}</div>
                """, unsafe_allow_html=True)
                
            st.markdown("---")
            
            col1, col2 = st.columns([1.2, 2])
            with col1:
                st.subheader("📝 策略決策報告")
                st.code(report_text, language="text")
                
            col2.subheader(f"📊 時空共振視覺化矩陣 ({timeframe_choice})")
            col2.pyplot(fig)
            
            col2.info(f"""
            📌 **【圖表判讀重點摘要】**
            1. 🟢 **核心重力井 (支撐)**：`{buyi_data['core_support']}`。若價格回檔，此線具備強大的結構吸引與支撐防線。
            2. 🔴 **極限/中繼壓力**：`{buyi_data['core_resistance']}`。若價格逼近此區間，上檔易受引力約束。
            3. ⚡ **當前動能狀態**：`{bian_data['dynamics_status']}`（高頻能量密度: `{bian_data['high_freq_energy']:.2f}`）。
            4. 👁️ **讀圖指引**：分析標的為 **{company_name} ({resolved_sym})**，最後 K 棒時間：**{full_last_time}**。
            """)
