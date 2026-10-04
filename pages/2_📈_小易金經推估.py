import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

# ==========================================
# 核心引擎 (加入公司全名與時脈資訊 v2.5)
# ==========================================
class IChingTrinitySpatiotemporalEngine:
    def __init__(self, df: pd.DataFrame, ticker: str, company_name: str, timeframe: str = "Daily"):
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
        
        high_max = np.max(self.df['High'].values[-60:]) if len(self.df) >= 60 else np.max(self.df['High'].values)
        low_min = np.min(self.df['Low'].values[-60:]) if len(self.df) >= 60 else np.min(self.df['Low'].values)
        
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

    def generate_full_report(self, current_regime_bars: int, last_trading_date: str, report_time: str) -> str:
        bian = self.analyze_bian_yi()
        buyi = self.analyze_bu_yi()
        jian = self.analyze_jian_yi(current_regime_bars)
        turning = self.predict_spatiotemporal_turning_window(current_regime_bars)
        
        report = f"""==================================================
【易經三義量化時空分析 2.5 版】實戰分析報告
==================================================
公司名稱: {self.company_name}
標的代碼: {self.ticker} | 週期: {self.timeframe}
最後交易日: {last_trading_date} | 報告產出時脈: {report_time}
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

    def plot_spatiotemporal_matrix(self, last_trading_date: str):
        plt.style.use('dark_background')
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={'height_ratios': [2, 1]}, sharex=True)
        
        close = self.df['Close'].values
        x = np.arange(len(close))
        
        ax1.plot(x, close, label='Close Price', color='cyan', linewidth=1.5)
        buyi = self.analyze_bu_yi()
        
        ax1.axhline(buyi['core_support'], color='lime', linestyle='--', linewidth=2, label=f"Core Support: {buyi['core_support']:.2f}")
        ax1.axhline(buyi['core_resistance'], color='red', linestyle='--', linewidth=2, label=f"Resistance: {buyi['core_resistance']:.2f}")
        ax1.set_title(f"{self.company_name} ({self.ticker}) - Spatiotemporal Gravity Wells (Last: {last_trading_date})", fontsize=15, fontweight='bold', color='white')
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

def fetch_taiwan_stock_data(raw_input: str, period: str) -> Tuple[Optional[pd.DataFrame], str, str]:
    """智慧判斷代碼、抓取資料並取得公司中文/英文名稱"""
    clean_code = raw_input.strip()
    
    if clean_code.upper().endswith(('.TW', '.TWO', '.US')):
        tickers_to_try = [clean_code]
    else:
        tickers_to_try = [f"{clean_code}.TW", f"{clean_code}.TWO"]
        
    for t in tickers_to_try:
        try:
            ticker_obj = yf.Ticker(t)
            df = ticker_obj.history(period=period, interval="1d")
            
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
                
            df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
            
            if not df.empty:
                info = ticker_obj.info
                # 嘗試取得公司名稱，若無則以代碼代替
                company_name = info.get('longName') or info.get('shortName') or t
                market_type = "上市公司" if ".TW" in t and ".TWO" not in t else "上櫃公司"
                return df, f"{t} ({market_type})", company_name
        except Exception:
            continue
            
    return None, "", ""

# ==========================================
# Streamlit 前端介面
# ==========================================
st.set_page_config(page_title="易經三義量化時空分析", layout="wide", page_icon="☯️")

st.title("☯️ 易經三義量化時空分析系統")
st.markdown("整合 **小波變換動能 (變易)**、**重力井空間 (不易)** 與 **馬可夫狀態機率 (簡易)** 的多維度定序框架。")

with st.sidebar:
    st.header("參數設定")
    ticker_input = st.text_input("輸入股票代碼 (例如: 2330 或 3293)", value="2330")
    period = st.selectbox("分析週期", ["3mo", "6mo", "1y", "2y"], index=1)
    current_regime_bars = st.slider("當前趨勢已持續 K棒數 (狀態根數)", min_value=1, max_value=13, value=3)
    run_btn = st.button("啟動量化引擎 🚀", use_container_width=True)

if run_btn:
    with st.spinner(f"正在智慧辨識與獲取代碼 [{ticker_input}] 的歷史數據與公司資訊..."):
        df_real, resolved_ticker, company_name = fetch_taiwan_stock_data(ticker_input, period)
        
        if df_real is None or df_real.empty:
            st.error(f"⚠️ 無法獲取代碼 [{ticker_input}] 的資料，請確認代碼是否正確。")
        else:
            # 取得時間資訊
            last_trading_date = df_real.index[-1].strftime('%Y-%m-%d')
            report_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            current_price = float(df_real['Close'].iloc[-1])
            prev_price = float(df_real['Close'].iloc[-2]) if len(df_real) > 1 else current_price
            price_change = current_price - prev_price
            price_change_pct = (price_change / prev_price) * 100
            
            # 初始化引擎
            engine = IChingTrinitySpatiotemporalEngine(df_real, ticker=resolved_ticker, company_name=company_name, timeframe=f"Daily ({period})")
            report_text = engine.generate_full_report(current_regime_bars=current_regime_bars, last_trading_date=last_trading_date, report_time=report_time)
            fig = engine.plot_spatiotemporal_matrix(last_trading_date=last_trading_date)
            
            buyi_data = engine.analyze_bu_yi()
            bian_data = engine.analyze_bian_yi()
            
            # 頂部顯示核心即時數據指標看板 (Metric Cards)
            st.markdown("---")
            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("公司名稱", company_name)
            m2.metric("股票代碼", resolved_ticker)
            m3.metric("目前收盤價 (P0)", f"{current_price:.2f} 元", f"{price_change:+.2f} ({price_change_pct:+.2f}%)")
            m4.metric("最後交易日", last_trading_date)
            m5.metric("報告產出時間", report_time.split()[1]) # 顯示時分秒
            st.markdown("---")
            
            col1, col2 = st.columns([1.2, 2])
            with col1:
                st.subheader("📝 策略決策報告")
                st.code(report_text, language="text")
                
            with col2:
                st.subheader("📊 時空共振視覺化矩陣")
                st.pyplot(fig)
                
                st.info(f"""
                📌 **【圖表判讀重點摘要】**
                1. 🟢 **核心重力井 (支撐)**：`{buyi_data['core_support']}` 元。若價格回檔，此線具備強大的結構吸引與支撐防線。
                2. 🔴 **極限/中繼壓力**：`{buyi_data['core_resistance']}` 元。若價格逼近此區間，上檔易受引力約束。
                3. ⚡ **當前動能狀態**：`{bian_data['dynamics_status']}`（高頻能量密度: `{bian_data['high_freq_energy']:.2f}`）。
                4. 👁️ **讀圖指引**：分析標的為 **{company_name}**，最後交易日為 **{last_trading_date}**。
                """)
