import warnings
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import lightgbm as lgb
import shap
import statsmodels.api as sm
import yfinance as yf
from sklearn.base import clone
from sklearn.metrics import accuracy_score, classification_report, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from statsmodels.regression.rolling import RollingOLS

warnings.filterwarnings('ignore')

# 台股交易成本：手續費 0.1425%（買賣各一次）＋ 證交稅 0.3%（僅賣出）
FEE = 0.001425
TAX = 0.003
HORIZON = 5          # 預測天數
THRESHOLD = 0.005    # 超額報酬門檻


def AI_動能與機器學習預測系統(stock_code, exchange="TW", window=252, n_splits=5):
    """
    Rolling OLS 因子 + LightGBM 預測（修正版）
    特徵規則：所有特徵只使用「T 日收盤前已知」的資訊，標籤為 T 收盤 -> T+5 收盤，
    因此特徵一律不再額外 shift；美股資料則統一 shift(1) 對齊台股隔天。
    """
    print("\n" + "=" * 55)
    print(f" 🚀 啟動【{stock_code}】AI 動能檢驗與機器學習預測系統")
    print("=" * 55)

    # ==========================================
    # 1. 資料下載與時間對齊（以台股交易日為基準）
    # ==========================================
    end_date = (datetime.today() + timedelta(days=1)).strftime('%Y-%m-%d')  # end 為開區間
    fetch_start = (datetime.today() - pd.DateOffset(years=4)).strftime('%Y-%m-%d')
    stock_yf = f"{stock_code}.{exchange}"
    tickers = [stock_yf, 'NVDA', '^SOX', '^DJI', '^IRX', '^TWII']

    print(f"📥 [階段 1] 正在下載 {stock_yf} 與市場數據...")
    raw = yf.download(tickers, start=fetch_start, end=end_date,
                      progress=False, auto_adjust=True)['Close']

    if stock_yf not in raw.columns or raw[stock_yf].dropna().empty:
        print(f"❌ 找不到 {stock_code} 的股價資料，請確認代碼（上櫃股票請用 exchange='TWO'）。")
        return

    # 以台股交易日為主，美股/利率用 ffill 補假日缺值
    px = raw.dropna(subset=[stock_yf, '^TWII']).ffill().dropna()

    rets = px[[stock_yf, 'NVDA', '^SOX', '^DJI', '^TWII']].pct_change()
    us_cols = ['NVDA', '^SOX', '^DJI']
    rets[us_cols] = rets[us_cols].shift(1)  # 美股前一晚 -> 台股今天

    df = rets.copy()
    df['RF_US'] = ((px['^IRX'] / 100) / 365).shift(1)
    df['RF_TW'] = 0.017 / 365

    # ==========================================
    # 2. 微觀特徵（皆為 T 日收盤已知）
    # ==========================================
    print("⚙️ [階段 2] 構建微觀特徵 (價格動能、RSI與波動率)...")
    s = px[stock_yf]
    m = px['^TWII']
    df['Price_Mom_30D'] = s.pct_change(30) - m.pct_change(30)
    df['Price_Mom_5D'] = s.pct_change(5) - m.pct_change(5)
    df['Vol_10D'] = s.pct_change().rolling(10).std()

    delta = s.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df['RSI_14'] = 100 - 100 / (1 + gain / loss)

    df = df.dropna(subset=['NVDA', '^SOX', '^DJI', 'RF_US', 'Price_Mom_30D',
                           'Price_Mom_5D', 'Vol_10D', 'RSI_14'])

    # ==========================================
    # 3. Rolling OLS 因子（無前視偏誤）
    # ==========================================
    print(f"⚙️ [階段 3] 執行 {window} 天 Rolling OLS 萃取計量因子與動能加速度...")

    # 3a. NVDA 純衝擊：用「前一日」的滾動係數預測今天，殘差即純衝擊
    Y_ortho = df['NVDA'] - df['RF_US']
    X_ortho = sm.add_constant(pd.DataFrame({
        'DJI_Excess': df['^DJI'] - df['RF_US'],
        'SOX_Excess': df['^SOX'] - df['RF_US'],
    }))
    r_ortho = RollingOLS(Y_ortho, X_ortho, window=window).fit()
    pred = (r_ortho.params.shift(1) * X_ortho).sum(axis=1, min_count=X_ortho.shape[1])
    df['NVDA_Pure_Shock'] = Y_ortho - pred
    df = df.dropna(subset=['NVDA_Pure_Shock'])

    # 3b. 主回歸（X 也使用超額報酬，與 Y 定義一致）
    df['Interaction_Term'] = df['NVDA_Pure_Shock'] * df['Price_Mom_30D']
    Y_roll = df[stock_yf] - df['RF_TW']
    X_roll = sm.add_constant(pd.DataFrame({
        'TWII_Excess': df['^TWII'] - df['RF_TW'],
        'SOX_Excess': df['^SOX'] - df['RF_US'],
        'NVDA_Pure_Shock': df['NVDA_Pure_Shock'],
        'Price_Mom_30D': df['Price_Mom_30D'],
        'Interaction_Term': df['Interaction_Term'],
    }))

    if len(df) <= window + 60:
        print("❌ 資料長度不足以執行回測與機器學習。")
        return

    params_df = RollingOLS(Y_roll, X_roll, window=window).fit().params
    df['Beta_3_Rolling'] = params_df['NVDA_Pure_Shock']
    df['Gamma_Rolling'] = params_df['Interaction_Term']
    df['Beta_3_Trend_5D'] = df['Beta_3_Rolling'].diff(5)
    df['Gamma_Trend_5D'] = df['Gamma_Rolling'].diff(5)

    plot_gamma = df['Gamma_Rolling'].dropna()
    plot_beta3 = df['Beta_3_Rolling'].dropna()

    # ==========================================
    # 4. 標籤
    # ==========================================
    print(f"🎯 [階段 4] 標籤建構 (未來 {HORIZON} 日擊敗大盤 > {THRESHOLD:.1%})...")
    df['Future_Excess_Ret'] = (s.pct_change(HORIZON).shift(-HORIZON)
                               - m.pct_change(HORIZON).shift(-HORIZON))

    features = ['Beta_3_Rolling', 'Beta_3_Trend_5D', 'Gamma_Rolling', 'Gamma_Trend_5D',
                'NVDA_Pure_Shock', 'Price_Mom_30D', 'Price_Mom_5D', 'RSI_14', 'Vol_10D']
    base_features = ['Price_Mom_30D', 'Price_Mom_5D', 'RSI_14', 'Vol_10D']

    # 「最新一列」不需要標籤，先取出供預測使用
    df_feat = df.dropna(subset=features)
    latest_row = df_feat.iloc[[-1]]

    df_ai = df_feat.dropna(subset=['Future_Excess_Ret']).copy()
    df_ai['Target_Label'] = (df_ai['Future_Excess_Ret'] > THRESHOLD).astype(int)
    X, y = df_ai[features], df_ai['Target_Label']
    base_rate = y.mean()
    print(f"   樣本數: {len(X)} | 正類基準率 (擊敗大盤 > {THRESHOLD:.1%}): {base_rate:.2%}")

    # ==========================================
    # 5. 交叉驗證（gap 防重疊標籤洩漏）＋簡單基準模型
    # ==========================================
    print("🧠 [階段 5] 啟動防洩漏交叉驗證 (gap) 與正則化 LightGBM...")
    model = lgb.LGBMClassifier(
        n_estimators=80, learning_rate=0.03, max_depth=3, min_child_samples=40,
        subsample=0.7, subsample_freq=1, colsample_bytree=0.7,
        reg_alpha=0.5, reg_lambda=0.5, random_state=42, verbose=-1
    )

    tscv = TimeSeriesSplit(n_splits=n_splits, gap=HORIZON)
    tr_acc, tr_auc, te_acc, te_auc, base_auc = [], [], [], [], []
    oos_proba = pd.Series(np.nan, index=X.index)
    last_fold = None

    print("-" * 65)
    for fold, (tr_idx, te_idx) in enumerate(tscv.split(X), 1):
        X_tr, X_te = X.iloc[tr_idx], X.iloc[te_idx]
        y_tr, y_te = y.iloc[tr_idx], y.iloc[te_idx]

        if y_tr.nunique() < 2 or y_te.nunique() < 2:
            print(f"Fold {fold} | 訓練或測試集只有單一類別，略過")
            continue

        fold_model = clone(model).fit(X_tr, y_tr)
        p_tr = fold_model.predict_proba(X_tr)[:, 1]
        p_te = fold_model.predict_proba(X_te)[:, 1]
        oos_proba.iloc[te_idx] = p_te

        tr_acc.append(accuracy_score(y_tr, p_tr > 0.5))
        tr_auc.append(roc_auc_score(y_tr, p_tr))
        te_acc.append(accuracy_score(y_te, p_te > 0.5))
        te_auc.append(roc_auc_score(y_te, p_te))

        base_model = clone(model).fit(X_tr[base_features], y_tr)
        base_auc.append(roc_auc_score(y_te, base_model.predict_proba(X_te[base_features])[:, 1]))

        last_fold = (fold_model, X_te, y_te, (p_te > 0.5).astype(int))

        print(f"Fold {fold} | 樣本 (Train/Test): {len(X_tr):4d} / {len(X_te):4d}")
        print(f"  👉 Train | ACC: {tr_acc[-1]:.4f} | AUC: {tr_auc[-1]:.4f}")
        print(f"  👉 Test  | ACC: {te_acc[-1]:.4f} | AUC: {te_auc[-1]:.4f} "
              f"| 基準模型 AUC: {base_auc[-1]:.4f}")

    print("-" * 65)
    if not te_auc:
        print("❌ 所有 fold 皆無法評估，請拉長資料期間。")
        return
    mean_auc = float(np.mean(te_auc))
    print(f"✅ 訓練完成！平均 Test ACC: {np.mean(te_acc):.4f} | 平均 Test AUC: {mean_auc:.4f} "
          f"| 基準模型平均 AUC: {np.mean(base_auc):.4f}")
    if mean_auc < 0.55:
        print("⚠️ 平均 AUC < 0.55，訊號與雜訊難以區分，下方預測請勿過度解讀。")

    # ==========================================
    # 6. 可交易的樣本外回測（walk-forward 預測 + 交易成本）
    # ==========================================
    edge = 0.05
    oos = oos_proba.dropna()
    position = (oos > base_rate + edge).astype(int)          # T 收盤決策
    next_ret = df[stock_yf].shift(-1).reindex(oos.index)     # T+1 報酬
    valid = next_ret.notna()
    position, next_ret = position[valid], next_ret[valid]

    change = position.diff().fillna(position.iloc[0])
    cost = change.clip(lower=0) * FEE + (-change).clip(lower=0) * (FEE + TAX)
    strat_ret = position * next_ret - cost
    bh_ret = next_ret
    strat_cum = (1 + strat_ret).cumprod() - 1
    bh_cum = (1 + bh_ret).cumprod() - 1
    n_trades = int((change > 0).sum())

    print("\n" + "=" * 75)
    print(f" 💰 【{stock_code}】樣本外 walk-forward 回測 (訊號: 勝率 > 基準率 + {edge:.0%}, 含成本)")
    print("=" * 75)
    print(f"區間: {position.index[0].date()} ~ {position.index[-1].date()} ({len(position)} 天)")
    print(f"策略累積報酬: {strat_cum.iloc[-1]:8.2%} | 買進持有: {bh_cum.iloc[-1]:8.2%} "
          f"| 進場次數: {n_trades} | 持倉天數占比: {position.mean():.1%}")

    if last_fold is not None:
        f_model, X_te, y_te, pred_te = last_fold
        print("\n🔍 最後一個 fold 的分類報告 (門檻 0.5，僅供參考):")
        print(classification_report(y_te, pred_te, labels=[0, 1], zero_division=0,
                                    target_names=['落後或微漲 (0)', '實質擊敗大盤 (1)']))

    # ==========================================
    # 7. 最新預測（用全部資料重訓 + 最新一列特徵）
    # ==========================================
    final_model = clone(model).fit(X, y)
    latest_features = latest_row[features]
    latest_proba = final_model.predict_proba(latest_features)[:, 1][0]
    latest_date = latest_row.index[0].date()

    current_beta3 = latest_row['Beta_3_Rolling'].iloc[0]
    beta3_trend_val = latest_row['Beta_3_Trend_5D'].iloc[0]
    current_gamma = latest_row['Gamma_Rolling'].iloc[0]
    gamma_trend_val = latest_row['Gamma_Trend_5D'].iloc[0]

    beta3_trend_str = "上升 ↗" if beta3_trend_val > 0 else "下降 ↘"
    gamma_trend_str = "加速湧入 ↗" if gamma_trend_val > 0 else "動能衰退 ↘"
    gamma_status = (f"過熱追高區 ({gamma_trend_str})" if current_gamma > 0
                    else f"冷卻/均值回歸區 ({gamma_trend_str})")

    diff = latest_proba - base_rate
    if diff > 0.08 and beta3_trend_val > 0:
        action_plan = "🔥 強烈作多訊號：預測勝率明顯高於基準率，且 Beta_3 趨勢向上。"
    elif diff > 0.04:
        action_plan = "📈 偏多觀察：預測略高於基準率，建議分批、嚴設停損。"
    elif diff < -0.08 and (beta3_trend_val < 0 or gamma_trend_val < 0):
        action_plan = "❄️ 保守觀望：預測明顯低於基準率，且因子趨勢轉弱。"
    else:
        action_plan = "⚖️ 中性：與基準率差距不大，訊號不明確。"

    print("\n" + "=" * 75)
    print(f" 🔮 【{stock_code}】AI 未來 {HORIZON} 日預測 (特徵日期: {latest_date})")
    print("=" * 75)
    print(f"🎯 預測勝率 (未來 {HORIZON} 日擊敗大盤 > {THRESHOLD:.1%}): {latest_proba:8.2%} "
          f"(基準率 {base_rate:.2%}, 差距 {diff:+.2%})")
    print(f"📊 Beta_3 近五日 【{beta3_trend_str}】 (當前值: {current_beta3:.4f})")
    print(f"📊 Gamma 位處 【{gamma_status}】 (當前值: {current_gamma:.4f})")
    print("-" * 75)
    print(f"💡 系統策略建議:\n👉 {action_plan}")
    print("   (僅為統計模型輸出，非投資建議)")
    print("=" * 75 + "\n")

    # ==========================================
    # 8. 圖表
    # ==========================================
    fig1, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(14, 12), sharex=False)
    ax1.plot(plot_gamma.index, plot_gamma, color='purple', label='Gamma (Crowding)')
    ax1.axhline(0, color='red', linestyle='--'); ax1.legend(loc='upper left'); ax1.grid(True, alpha=0.3)

    ax2.plot(plot_beta3.index, plot_beta3, color='forestgreen', label='Beta_3 (Pure AI Shock)')
    ax2.axhline(0, color='red', linestyle='--'); ax2.legend(loc='upper left'); ax2.grid(True, alpha=0.3)

    ax3.plot(strat_cum.index, strat_cum, color='darkred', label='Strategy (OOS, after costs)')
    ax3.plot(bh_cum.index, bh_cum, color='gray', label='Buy & Hold')
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: '{:.0%}'.format(v)))
    ax3.legend(loc='upper left'); ax3.grid(True, alpha=0.3)
    fig1.suptitle(f'[{stock_code}] Rolling Factors & Out-of-Sample Walk-Forward Backtest', fontsize=16)
    plt.tight_layout()
    plt.show()

    if last_fold is not None:
        f_model, X_te, _, _ = last_fold
        shap_values = shap.TreeExplainer(f_model).shap_values(X_te)
        if isinstance(shap_values, list):
            shap_plot = shap_values[1]
        elif getattr(shap_values, 'ndim', 2) == 3:
            shap_plot = shap_values[:, :, 1]
        else:
            shap_plot = shap_values
        plt.figure(figsize=(10, 6))
        shap.summary_plot(shap_plot, X_te, feature_names=features, show=False)
        plt.title(f"[{stock_code}] SHAP Decision Logic ({HORIZON}-Day Outperformance)", fontsize=14)
        plt.tight_layout()
        plt.show()


if __name__ == "__main__":
    AI_動能與機器學習預測系統('3231', exchange='TW')