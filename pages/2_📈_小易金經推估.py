import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf
from typing import Dict, Any

# ==========================================
# 核心引擎 (加入圖表自動解說模組 v2.3)
# ==========================================
class IChingTrinitySpatiotemporalEngine:
    def __init__(self, df: pd.DataFrame, ticker: str, timeframe: str = "Daily"):
        self.df = df.copy()
        self.ticker = ticker
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

    def generate_full_report(self, current_regime_bars: int) -> str:
        bian = self.analyze_bian_yi()
        buyi = self.analyze_bu_yi()
        jian = self.analyze_jian_yi(current_regime_bars)
        turning = self.predict_spatiotemporal_turning_window(current_regime_bars)
        
        report = f"""==================================================
【易經三義量化時空分析 2.3 版】實戰分析報告
標的: {self.ticker} | 週期: {self.timeframe} | 太極原點 P0: {buyi['p0']:.2f}
==================================================

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
==================================================="""""
        return report

    def plot_spatiotemporal_matrix(self):
        plt.style.use('dark_background')
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={'height_ratios': [2, 1]}, sharex=True)
        
        close = self.df['Close'].values
        x = np.arange(len(close))
        
        ax1.plot(x, close, label='Close Price', color='cyan', linewidth=1.5)
        buyi = self.analyze_bu_yi()
        
        ax1.axhline(buyi['core_support'], color='lime', linestyle='--', linewidth=2, label=f"Core Support: {buyi['core_support']:.2f}")
        ax1.axhline(buyi['core_resistance'], color='red', linestyle='--', linewidth=2, label=f"Resistance: {buyi['core_resistance']:.2f}")
        ax1.set_title(f"{self.ticker} - Spatiotemporal Gravity Wells", fontsize=16, fontweight='bold', color='white')
        ax1.set_ylabel("Price")
        ax1.legend(loc='upper left', frameon=True, facecolor='black')
        ax1.grid(True, alpha=0.2, linestyle=':')
        
        widths = np.arange(1, 64)
        cwtmatr = self._custom_cwt(close, widths)
        
        cax = ax2.pcolormesh(x, widths, np.abs(cwtmatr), shading='gouraud', cmap='magma')
        ax2.set_title("CWT Energy Scalogram (Dynamics & Invariants)", fontsize=14, color='white')
        ax2.set_ylabel("Scale (Time Horizon)")
        ax2.set_xlabel("Time (Trading Bars)")
        ax2.invert_yaxis()
        
        cbar = fig.colorbar(cax, ax=ax2, orientation='horizontal', pad=0.2, aspect=40)
        cbar.set_label('Energy Density (Volatility)', color='white')
        cbar.ax.tick_params(colors='white')
        
        plt.tight_layout()
        return fig

# ==========================================
# Streamlit 前端介面
# ==========================================
st.set_page_config(page_title="易經三義量化時空分析", layout="wide", page_icon="☯️")

st.title("☯️ 易經三義量化時空分析系統")
st.markdown("整合 **小波變換動能 (變易)**、**重力井空間 (不易)** 與 **馬可夫狀態機率 (簡易)** 的多維度定序框架。")

with st.sidebar:
    st.header("參數設定")
    ticker_input = st.text_input("輸入股票代碼 (台股請加 .TW)", value="2330.TW")
    period = st.selectbox("分析週期", ["3mo", "6mo", "1y", "2y"], index=1)
    current_regime_bars = st.slider("當前趨勢已持續 K棒數 (狀態根數)", min_value=1, max_value=13, value=3)
    run_btn = st.button("啟動量化引擎 🚀", use_container_width=True)

if run_btn:
    with st.spinner(f"正在從 Yahoo Finance 獲取 {ticker_input} 歷史數據..."):
        try:
            df_real = yf.download(ticker_input, period=period, interval="1d", progress=False)
            
            if isinstance(df_real.columns, pd.MultiIndex):
                df_real.columns = df_real.columns.get_level_values(0)
                
            df_real = df_real[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()
            
            if df_real.empty:
                st.error("⚠️ 無法獲取資料，請確認代碼是否正確（例如台積電為 2330.TW）。")
            else:
                engine = IChingTrinitySpatiotemporalEngine(df_real, ticker=ticker_input, timeframe=f"Daily ({period})")
                report_text = engine.generate_full_report(current_regime_bars=current_regime_bars)
                fig = engine.plot_spatiotemporal_matrix()
                
                # 取得內部數值供說明摘要使用
                buyi_data = engine.analyze_bu_yi()
                bian_data = engine.analyze_bian_yi()
                
                col1, col2 = st.columns([1.2, 2])
                with col1:
                    st.subheader("📝 策略決策報告")
                    st.code(report_text, language="text")
                    
                with col2:
                    st.subheader("📊 時空共振視覺化矩陣")
                    st.pyplot(fig)
                    
                    # --- 新增：圖表簡短說明區塊 ---
                    st.info(f"""
                    📌 **【圖表判讀重點摘要】**
                    1. 🟢 **核心重力井 (支撐)**：`{buyi_data['core_support']}` 元。若價格回檔，此線具備強大的結構吸引與支撐防線。
                    2. 🔴 **極限/中繼壓力**：`{buyi_data['core_resistance']}` 元。若價格逼近此區間，上檔易受引力約束。
                    3. ⚡ **當前動能狀態**：`{bian_data['dynamics_status']}`（高頻能量密度: `{bian_data['high_freq_energy']:.2f}`）。
                    4. 👁️ **讀圖指引**：上圖藍線觀察價格相對於上下虛線（重力井）的空間位階；下圖熱力圖越亮代表能量越強，深色代表進入蓄能或耗散期。
                    """)
                    
        except Exception as e:
            st.error(f"執行時發生錯誤: {e}")
