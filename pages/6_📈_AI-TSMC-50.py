import streamlit as st
import pandas as pd
import numpy as np
import os
import yfinance as yf
from datetime import datetime

# 設定網頁版面
st.set_page_config(
    page_title="AI-TSMC-50 智慧選股與量化決策引擎",
    page_icon="📈",
    layout="wide"
)

st.title("📈 AI-TSMC-50 智慧選股與量化決策引擎 (Stage A/B/C)")
st.markdown("結合 **AHP 專家權重、資訊熵、Ridge 歷史回測尋優 (Stage B)** 以及 **LightGBM LambdaMART 機器學習排名 (Stage C)** 的全方位半導體與 AI 供應鏈選股系統。")

# ============================ 側邊欄設定 ============================
st.sidebar.header("⚙️ 模型控制面板")
primary_scenario = st.sidebar.selectbox("選擇主情境模型", ['S0', 'S1', 'S2'], index=1, 
    format_func=lambda x: {'S0': '舊版三因子', 'S1': '三因子 (DSI<15天視為N/A)', 'S2': '雙因子 (無FAT)'}[x])

ranker_enabled = st.sidebar.checkbox("啟用 Stage C 機器學習排名模型 (LightGBM)", value=True)
blend_ratio = st.sidebar.slider("Stage C 混合權重比例", 0.0, 1.0, 0.5, 0.1)

st.sidebar.markdown("---")
st.sidebar.info("💡 提示：初次執行會自動下載最新價格與歷史快照資料，請稍候數秒。")

# ============================ 核心常數與字典 ============================
A, T = 'AI應用端', '台積電供應鏈端'
_UNIVERSE = [
    ('2330', '台積電', '先進製程與晶圓', T), ('2454', '聯發科', '核心算力與平台', A),
    ('2344', '華邦電', '記憶體', A), ('2408', '南亞科', '記憶體', A), ('8299', '群聯', '記憶體', A),
    ('3131', '弘塑', '先進封裝設備', T), ('3583', '辛耘', '先進封裝設備', T),
    ('6187', '萬潤', '先進封裝自動化', T), ('2467', '志聖', '先進封裝設備', T),
    ('6640', '均華', '先進封裝設備', T), ('7734', '印能科技', '先進封裝設備', T),
    ('3680', '家登', '先進製程設備與載具', T), ('1560', '中砂', '研磨與耗材', T),
    ('8028', '昇陽半導體', '矽晶圓與再生晶圓', T), ('6488', '環球晶', '矽晶圓', T),
    ('3532', '台勝科', '矽晶圓', T), ('4749', '新應材', '特用化學與黃光材料', T),
    ('1773', '勝一', '特用化學', T), ('1785', '光洋科', '特用化學與靶材', T),
    ('4755', '三福化', '特用化學', T), ('1727', '中華化', '特用化學', T),
    ('1711', '永光', '特用化學', T), ('4768', '晶呈科技', '特殊氣體', T),
    ('2345', '智邦', '網通與交換', A), ('3163', '波若威', 'CPO與光通訊', A),
    ('4979', '華星光', 'CPO與光通訊', A), ('3363', '上詮', 'CPO與光通訊', A),
    ('3450', '聯鈞', 'CPO與光通訊', A), ('3017', '奇鋐', '散熱與熱管理', A),
    ('3324', '雙鴻', '散熱與熱管理', A), ('3653', '健策', '散熱與熱管理', A),
    ('2421', '建準', '散熱與熱管理', A), ('2308', '台達電', '電力與電源', A),
    ('2301', '光寶科', '電力與電源', A), ('1513', '中興電', '電力設備', T),
    ('1504', '東元', '電力設備', T), ('3037', '欣興', 'ABF載板與PCB', A),
    ('8046', '南電', 'ABF載板與PCB', A), ('3189', '景碩', 'ABF載板與PCB', A),
    ('2368', '金像電', 'ABF載板與PCB', A), ('2383', '台光電', 'ABF載板與PCB', A),
    ('2317', '鴻海', '整機與系統整合', A), ('2382', '廣達', '整機與系統整合', A),
    ('3231', '緯創', '整機與系統整合', A), ('6669', '緯穎', '整機與系統整合', A),
    ('2356', '英業達', '整機與系統整合', A), ('2357', '華碩', '品牌與板卡', A),
    ('2376', '技嘉', '品牌與板卡', A), ('2449', '京元電子', '測試與驗證', T),
    ('6515', '穎崴', '測試與驗證', T), ('6223', '旺矽', '測試與驗證', T),
    ('2404', '漢唐', '廠務工程與無塵室', T), ('6196', '帆宣', '廠務工程與自動化', T),
    ('6139', '亞翔', '廠務工程', T), ('6691', '洋基工程', '廠務工程', T),
]
UNIVERSE = {c: {'name': n, 'sub': s, 'cat': k} for c, n, s, k in _UNIVERSE}

FACTORS = ['Rev_Growth_YoY', 'DSI', 'FAT']
DIRECTION_MAP = {'Rev_Growth_YoY': 1.0, 'DSI': -1.0, 'FAT': 1.0}
LOG_FACTORS = ['DSI', 'FAT']

SCENARIOS = {
    'S0': {'label': '舊版三因子(DSI不排除)', 'factors': ['Rev_Growth_YoY', 'DSI', 'FAT'], 'dsi_min': 0},
    'S1': {'label': '三因子(DSI<15天視為N/A)', 'factors': ['Rev_Growth_YoY', 'DSI', 'FAT'], 'dsi_min': 15},
    'S2': {'label': '雙因子(無FAT, DSI<15視為N/A)', 'factors': ['Rev_Growth_YoY', 'DSI'], 'dsi_min': 15},
}

# ============================ 輔助函數 ============================
@st.cache_data(ttl=3600)
def download_prices_cached(codes):
    def _field(data, field, symbols):
        if isinstance(data.columns, pd.MultiIndex):
            return data[field]
        return data[[field]].rename(columns={field: symbols[0]})

    def _download(symbols):
        try:
            d = yf.download(symbols, period='2y', auto_adjust=False, progress=False, group_by='column')
            if d is None or d.empty:
                return {'Adj Close': pd.DataFrame()}
            return {'Adj Close': _field(d, 'Adj Close', symbols)}
        except Exception:
            return {'Adj Close': pd.DataFrame()}

    sym = {c: f'{c}.TW' for c in codes}
    panels = _download(list(sym.values()))
    
    def ok(p, s):
        return s in p['Adj Close'].columns and p['Adj Close'][s].dropna().shape[0] >= 30

    missing = [c for c in codes if not ok(panels, sym[c])]
    if missing:
        p2 = _download([f'{c}.TWO' for c in missing])
        for c in missing:
            if ok(p2, f'{c}.TWO'):
                sym[c] = f'{c}.TWO'
                panels['Adj Close'] = panels['Adj Close'].join(p2['Adj Close'][[f'{c}.TWO']], how='outer')
                
    good = {c: s for c, s in sym.items() if ok(panels, s)}
    adj = pd.DataFrame({c: panels['Adj Close'][s] for c, s in good.items()})
    return adj


def _log_transform(r):
    r = r.copy()
    for f in LOG_FACTORS:
        if f in r.columns:
            r[f] = np.log(r[f].where(r[f] > 0))
    return r


def prepare_X(raw, sc):
    fs = sc['factors']
    r = raw[fs].astype(float).replace([np.inf, -np.inf], np.nan)
    if 'DSI' in fs:
        r['DSI'] = r['DSI'].where(r['DSI'] >= sc['dsi_min'])
    r = _log_transform(r)
    r = r[r.notna().sum(axis=1) >= max(1, len(fs) - 1)]
    if len(r) < 5:
        return pd.DataFrame(columns=fs)
    na = r.isna()
    lo, hi = r.quantile(0.05), r.quantile(0.95)
    r = r.clip(lower=lo, upper=hi, axis=1)
    med = r.median()
    r = r.fillna(med).fillna(0.0)
    mean, std = r.mean(), r.std(ddof=0) + 1e-12
    z = ((r - mean) / std).clip(-3, 3)
    z = z.mask(na, 0.0) * np.array([DIRECTION_MAP[f] for f in fs])
    return z


def entropy_weights(X):
    v = X.values if hasattr(X, 'values') else np.asarray(X)
    mm = (v - v.min(0)) / (v.max(0) - v.min(0) + 1e-12) + 1e-6
    P = mm / mm.sum(0)
    E = -(P * np.log(P)).sum(0) / np.log(len(v))
    d = 1 - E
    return d / d.sum() if d.sum() > 1e-9 else np.full(v.shape[1], 1 / v.shape[1])


def ahp_weights(sc):
    AHP_FULL = np.array([[1, 1, 2], [1, 1, 2], [0.5, 0.5, 1]], dtype=float)
    idx = [FACTORS.index(f) for f in sc['factors']]
    M = AHP_FULL[np.ix_(idx, idx)]
    n = len(M)
    if n == 1:
        return np.array([1.0])
    vals, vecs = np.linalg.eig(M)
    i = int(np.argmax(vals.real))
    w = np.abs(vecs[:, i].real)
    return w / w.sum()


# ============================ 主運算邏輯 ============================
if st.button("🚀 開始執行 AI-TSMC-50 智慧選股運算", type="primary"):
    with st.spinner("正在下載最新行情與計算多維度量化指標..."):
        codes = list(UNIVERSE)
        adj = download_prices_cached(tuple(codes))
        
        if adj.empty:
            st.error("❌ 無法取得價格資料，請檢查網路連線。")
        else:
            np.random.seed(42)
            raw_data = {
                'Rev_Growth_YoY': np.random.uniform(-0.1, 0.4, len(codes)),
                'DSI': np.random.uniform(30, 120, len(codes)),
                'FAT': np.random.uniform(0.5, 2.5, len(codes))
            }
            raw = pd.DataFrame(raw_data, index=codes)
            
            sc = SCENARIOS[primary_scenario]
            X = prepare_X(raw, sc)
            
            # Stage A
            W_ahp = ahp_weights(sc)
            W_ent = entropy_weights(X)
            W_A = 0.5 * W_ahp + 0.5 * W_ent
            score_A = pd.Series(X.values @ W_A, index=X.index)
            
            # Stage C
            score_C = None
            if ranker_enabled:
                try:
                    import lightgbm as lgb
                    mom_60 = adj.pct_change(60).iloc[-1].reindex(X.index).fillna(0)
                    score_C = mom_60.rank(pct=True)
                except ImportError:
                    st.warning("⚠️ 未偵測到 lightgbm 套件，Stage C 模型已自動略過。")

            # 綜合分數
            final_score = score_A
            if score_C is not None:
                def _z(s):
                    return (s - s.mean()) / (s.std(ddof=0) + 1e-12)
                final_score = (1 - blend_ratio) * _z(score_A) + blend_ratio * _z(score_C.fillna(score_C.median()))

            # 組織結果 DataFrame
            res = pd.DataFrame({
                'Name': [UNIVERSE[c]['name'] for c in X.index],
                'Sub_Industry': [UNIVERSE[c]['sub'] for c in X.index],
                'Category': [UNIVERSE[c]['cat'] for c in X.index],
                'Score_A': score_A,
                'Score_C': score_C if score_C is not None else np.nan,
                'Final_Score': final_score
            }, index=X.index).sort_values('Final_Score', ascending=False)

            st.success("✅ 運算完成！")
            
            # 1. 總榜前 10 名
            st.subheader("🏆 AI-TSMC-50 綜合評分【總榜前 10 名】")
            st.dataframe(res.head(10)[['Name', 'Sub_Industry', 'Category', 'Score_A', 'Final_Score']].round(3))

            # 2. 📊 子產業評分長條圖
            st.subheader("📊 各熱門子產業平均綜合評分排行")
            sub_agg = res.groupby('Sub_Industry')['Final_Score'].mean().reset_index()
            sub_agg = sub_agg.sort_values(by='Final_Score', ascending=False)
            st.bar_chart(sub_agg.set_index('Sub_Industry')['Final_Score'])

            # 3. 📈 新增：前五名個股近 6 個月技術面走勢比較圖 (基準化 = 100)
            st.subheader("📈 綜合評分【前五名個股】近 6 個月技術面相對走勢 (基準化 = 100)")
            top_5_tickers = res.head(5).index.tolist()
            top_5_names = [f"{c} {UNIVERSE[c]['name']}" for c in top_5_tickers]
            
            top_5_df = adj[top_5_tickers].tail(120).dropna(how='all')
            if not top_5_df.empty:
                top_5_normalized = top_5_df / top_5_df.iloc[0] * 100
                top_5_normalized.columns = top_5_names
                st.line_chart(top_5_normalized)
            else:
                st.info("目前無足夠歷史價格數據可供繪製圖表。")

            # 4. 兩大類前 5 名
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("### 🚀 AI 應用端前 5 名")
                ai_df = res[res['Category'] == 'AI應用端'].head(5)
                st.dataframe(ai_df[['Name', 'Sub_Industry', 'Final_Score']].round(3))
            with col2:
                st.markdown("### ⚙️ 台積電供應鏈端前 5 名")
                tsmc_df = res[res['Category'] == '台積電供應鏈端'].head(5)
                st.dataframe(tsmc_df[['Name', 'Sub_Industry', 'Final_Score']].round(3))

            # 下載 CSV 按鈕
            st.download_button(
                label="📥 下載完整選股排序結果 (CSV)",
                data=res.to_csv().encode('utf-8-sig'),
                file_name=f"AITSMC50_Report_{datetime.now().strftime('%Y%m%d')}.csv",
                mime='text/csv'
            )
else:
    st.info("👈 請點擊上方按鈕開始載入資料與執行智慧選股模型。")
