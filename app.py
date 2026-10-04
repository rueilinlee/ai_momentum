import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import statsmodels.api as sm
from statsmodels.regression.rolling import RollingOLS
import lightgbm as lgb
import shap
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, roc_auc_score
import matplotlib.pyplot as plt
from datetime import datetime
import warnings
import requests  # 新增 requests 模組來建立偽裝連線

warnings.filterwarnings('ignore')

# 設定網頁標題與寬度
st.set_page_config(page_title="台股 AI 量化預測系統", layout="wide")

def run_quant_system(stock_code, exchange="TW", window=252):
    stock_yf = f"{stock_code}.{exchange}"
    tickers = [stock_yf, 'NVDA', '^SOX', '^DJI', '^IRX', '^TWII']
    
    # 1. 資料下載 (使用 st.spinner 顯示載入中)
    with st.spinner(f'正在下載 {stock_yf} 與市場數據並萃取高階因子，這可能需要幾十秒...'):
        end_date = datetime.today().strftime('%Y-%m-%d')
        fetch_start = (datetime.today() - pd.DateOffset(years=4)).strftime('%Y-%m-%d')
        
        # --- 建立偽裝 Session，避免被 Yahoo 封鎖 (HTTP 429) ---
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
        })
        
        # 將 session 參數加入 download 中，繞過阻擋機制
        market_data = yf.download(tickers, start=fetch_start, end=end_date, progress=False, session=session)['Close']
        
        if stock_yf not in market_data.columns or market_data[stock_yf].dropna().empty:
            st.error(f"❌ 找不到 {stock_code} 的股價資料，或遭遇 Yahoo Finance 暫時封鎖，請稍後再試。")
            return

        returns = market_data[[stock_yf, 'NVDA', '^SOX', '^DJI', '^TWII']].pct_change().dropna()
        rf_us_daily = (market_data['^IRX'].dropna() / 100) / 365
        df = returns.join(rf_us_daily, how='inner').rename(columns={'^IRX': 'RF_US'})
        df['RF_TW'] = 0.017 / 365 

        # 2. 特徵工程
        df['Price_Mom_30D'] = (market_data[stock_yf].pct_change(30) - market_data['^TWII'].pct_change(30)).shift(1)
        df['Price_Mom_5D'] = (market_data[stock_yf].pct_change(5) - market_data['^TWII'].pct_change(5)).shift(1)
        df['Vol_10D'] = market_data[stock_yf].pct_change().rolling(10).std().shift(1)

        delta = market_data[stock_yf].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI_14'] = (100 - (100 / (1 + rs))).shift(1)
        df = df.dropna()

        # 3. 計量因子
        Y_ortho = df['NVDA'] - df['RF_US']
        X_ortho = pd.DataFrame({'DJI_Excess': df['^DJI'] - df['RF_US'], 'SOX_Excess': df['^SOX'] - df['RF_US']})
        X_ortho = sm.add_constant(X_ortho)
        df['NVDA_Pure_Shock'] = sm.OLS(Y_ortho, X_ortho).fit().resid 

        df['Interaction_Term'] = df['NVDA_Pure_Shock'] * df['Price_Mom_30D']
        Y_rolling = df[stock_yf] - df['RF_TW']
        X_rolling = df[['^TWII', '^SOX', 'NVDA_Pure_Shock', 'Price_Mom_30D', 'Interaction_Term']]
        X_rolling = sm.add_constant(X_rolling)

        rolling_res = RollingOLS(Y_rolling, X_rolling, window=window).fit()
        params_df = rolling_res.params
        
        df['Beta_3_Rolling'] = params_df['NVDA_Pure_Shock']
        df['Gamma_Rolling'] = params_df['Interaction_Term']
        df['Beta_3_Trend_5D'] = df['Beta_3_Rolling'].diff(5)
        df['Gamma_Trend_5D'] = df['Gamma_Rolling'].diff(5)
        
        plot_gamma = params_df['Interaction_Term'].dropna()
        plot_beta3 = params_df['NVDA_Pure_Shock'].dropna()

        # 4. 機器學習標籤與模型
        threshold = 0.005 
        df['Target_Label'] = ((market_data[stock_yf].pct_change(5).shift(-5) - market_data['^TWII'].pct_change(5).shift(-5)) > threshold).astype(int)
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

        # --- 輸出到 Web UI ---
        st.success(f"✅ AI 模型訓練完成！平均 Test ACC: {sum(cv_test_acc)/5:.2%} | 平均 Test AUC: {sum(cv_test_auc)/5:.4f}")

        # 5. 預測與策略邏輯
        latest_features = X.iloc[[-1]]
        latest_proba = model.predict_proba(latest_features)[:, 1][0]
        
        current_beta3 = plot_beta3.iloc[-1]
        beta3_trend_val = df_ai['Beta_3_Trend_5D'].iloc[-1]
        beta3_trend_str = "上升 ↗" if beta3_trend_val > 0 else "下降 ↘"
        
        current_gamma = plot_gamma.iloc[-1]
        gamma_trend_val = df_ai['Gamma_Trend_5D'].iloc[-1]
        gamma_trend_str = "加速湧入 ↗" if gamma_trend_val > 0 else "動能衰退 ↘"
        gamma_status = f"過熱追高區 ({gamma_trend_str})" if current_gamma > 0 else f"冷卻/均值回歸區 ({gamma_trend_str})"

        if latest_proba > 0.55 and beta3_trend_val > 0:
            action_plan = "🔥 強烈作多訊號：AI 預測勝率高，純度擴張且動能配合，可能為新波段起漲點。"
        elif latest_proba > 0.52:
            action_plan = "📈 偏多觀察：AI 預測略佔優勢，建議逢低分批佈局，嚴設停損。"
        elif latest_proba < 0.45 and (beta3_trend_val < 0 or gamma_trend_val < 0):
            action_plan = "❄️ 強烈保守觀望：AI 不看好且純度或資金動能衰退，極高機率落後大盤，建議避開。"
        else:
            action_plan = "⚖️ 中性震盪：多空訊號分歧 (可能正在築底或盤頭)，等待右側趨勢明朗。"

        # 顯示指標卡片
        st.markdown("### 🔮 未來 5 日預測與位階狀態")
        col1, col2, col3 = st.columns(3)
        col1.metric("AI 預測擊敗大盤勝率", f"{latest_proba:.2%}")
        col2.metric("Beta_3 純度趨勢", beta3_trend_str, f"{current_beta3:.4f}")
        col3.metric("Gamma 資金擁擠度", gamma_trend_str, f"{current_gamma:.4f}", delta_color="inverse")
        
        st.info(f"**💡 系統策略建議：** {action_plan}")

        # 6. 視覺化圖表繪製
        st.markdown("---")
        st.markdown("### 📊 歷史波段回測與 AI 決策邏輯")
        
        # 波段計算
        min_beta3_date = plot_beta3.idxmin()
        period_returns = df.loc[min_beta3_date:plot_beta3.loc[min_beta3_date:].idxmax(), stock_yf]
        
        # 建立兩個欄位並排放置圖表
        fig_col1, fig_col2 = st.columns(2)
        
        with fig_col1:
            fig1, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 10), sharex=True)
            ax1.plot(plot_gamma.index, plot_gamma, color='purple', label='Gamma (Crowding)')
            ax1.axhline(0, color='red', linestyle='--'); ax1.legend(loc='upper left'); ax1.grid(True, alpha=0.3)
            
            ax2.plot(plot_beta3.index, plot_beta3, color='forestgreen', label='Beta_3 (Pure AI Shock)')
            ax2.axvspan(period_returns.index[0], period_returns.index[-1], color='yellow', alpha=0.2, label='Surge Period')
            ax2.axhline(0, color='red', linestyle='--'); ax2.legend(loc='upper left'); ax2.grid(True, alpha=0.3)

            tsmc_cum_returns = (1 + period_returns).cumprod() - 1
            ax3.plot(tsmc_cum_returns.index, tsmc_cum_returns, color='darkred', label='Cumulative Return')
            ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: '{:.0%}'.format(y)))
            ax3.legend(loc='upper left'); ax3.grid(True, alpha=0.3)
            fig1.suptitle(f'[{stock_code}] Econometric Surge Backtest', fontsize=14)
            plt.tight_layout()
            st.pyplot(fig1)

        with fig_col2:
            explainer = shap.TreeExplainer(model)
            shap_values = explainer.shap_values(X.iloc[test_index])
            shap_values_to_plot = shap_values[1] if isinstance(shap_values, list) else shap_values

            fig2 = plt.figure(figsize=(10, 8))
            shap.summary_plot(shap_values_to_plot, X.iloc[test_index], feature_names=features, show=False)
            plt.title(f"[{stock_code}] SHAP AI Decision Logic", fontsize=14)
            plt.tight_layout()
            st.pyplot(fig2)

# ==========================================
# UI 介面設計 (側邊欄輸入區)
# ==========================================
st.sidebar.title("🤖 AI 資金動能檢驗系統")
st.sidebar.markdown("請輸入欲分析的台股代號：")

stock_input = st.sidebar.text_input("股票代號 (如: 3231, 2330)", value="3231")
exchange_input = st.sidebar.selectbox("市場類別", options=["上市 (TW)", "上櫃 (TWO)"])

# 解析上市櫃參數
exch_val = "TW" if "上市" in exchange_input else "TWO"

# 執行按鈕
if st.sidebar.button("🚀 執行量化分析"):
    run_quant_system(stock_input, exch_val)
