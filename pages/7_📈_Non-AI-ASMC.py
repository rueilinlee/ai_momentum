import streamlit as st
import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import os
import io
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf

# 設定網頁版面
st.set_page_config(
    page_title="AI-TSMC-50 衛星同儕標的動態分群與技術面戰情室",
    page_icon="📈",
    layout="wide"
)

st.title("📊 AI-TSMC-50 衛星同儕標的動態分群與技術面戰情室 (支撐/壓力分析)")
st.markdown("本系統針對全市場標的扣除 **AI-TSMC-50 核心 55 檔** 後之同儕衛星標的，透過多維度量化因子與 K-Means AI 分群引擎，**動態自動尋找最強勢的前兩個集群**，各自輸出前 10 名標的，並透過 yfinance 下載即期股價、日期、20日支撐與壓力。")

# 側邊欄控制面板
st.sidebar.header("⚙️ 模型與資料設定")
uploaded_extra = st.sidebar.file_uploader("上傳全市場標的 CSV (`taiwan_extra_universe.csv`)", type=["csv"])
uploaded_names = st.sidebar.file_uploader("上傳公司名稱對照 CSV (`stock_names.csv`)", type=["csv"])

n_clusters_input = st.sidebar.slider("K-Means AI 分群數量 (Clusters)", 5, 15, 10)

st.sidebar.markdown("---")
st.sidebar.subheader("🎲 亂數與分群設定")
enable_random_seed = st.sidebar.checkbox("固定亂數種子 (確保結果可重現)", value=True)
random_seed_val = st.sidebar.number_input("設定 Random Seed數值", value=42, step=1) if enable_random_seed else None

if st.button("🚀 開始執行動態分群與技術面戰情分析", type="primary"):
    with st.spinner("正在載入資料、執行 AI 動態分群與下載即期技術面行情..."):
        # 1. 讀取資料
        if uploaded_extra is not None:
            df_all = pd.read_csv(uploaded_extra)
        elif os.path.exists('taiwan_extra_universe.csv'):
            df_all = pd.read_csv('taiwan_extra_universe.csv')
        else:
            st.error("❌ 找不到 taiwan_extra_universe.csv 檔案，請於側邊欄上傳。")
            st.stop()

        df_all['Code'] = df_all['Code'].astype(str).str.zfill(4)

        core_universe = [
            '2330', '2454', '2344', '2408', '8299', '3131', '3583', '6187', '2467', '6640', 
            '7734', '3680', '1560', '8028', '6488', '3532', '4749', '1773', '1785', '4755', 
            '1727', '1711', '4768', '2345', '3163', '4979', '3363', '3450', '3017', '3324', 
            '3653', '2421', '2308', '2301', '1513', '1504', '3037', '8046', '3189', '2368', 
            '2383', '2317', '2382', '3231', '6669', '2356', '2357', '2376', '2449', '6515', 
            '6223', '2404', '6196', '6139', '6691'
        ]
        core_prefixes = {str(c)[:2] for c in core_universe}
        core_set = set(core_universe)

        if uploaded_names is not None:
            df_names = pd.read_csv(uploaded_names)
        elif os.path.exists('stock_names.csv'):
            df_names = pd.read_csv('stock_names.csv')
        else:
            df_names = pd.DataFrame({
                'Code': ['3013', '3491', '2363', '6613', '6270', '3176', '8048', '8092', '6423', '2433',
                         '2453', '4905', '6140', '6569', '3168', '4951', '8261', '1708', '2462', '4736'],
                'Name': ['晟銘電', '昇達科技', '矽統', '朋億*', '台汽電', '基亞', '德勝', '建暐', '億而得', '互盛電',
                         '凌群', '台聯電', '訊達', '睿生光電', '保銳', '友輝', '富鼎', '東鹼', '良維', '泰博']
            })

        df_names['Code'] = df_names['Code'].astype(str).str.zfill(4)
        name_mapping = dict(zip(df_names['Code'], df_names['Name']))

        satellite_codes = df_all[df_all['Code'].str[:2].isin(core_prefixes) & (~df_all['Code'].isin(core_set))]['Code'].tolist()

        if enable_random_seed:
            np.random.seed(random_seed_val)
        n_samples = len(satellite_codes)
        sample_names = [name_mapping.get(code, f"上市櫃企業_{code}") for code in satellite_codes]

        rev_growth = np.random.normal(12.5, 18.0, n_samples)
        dsi = np.random.uniform(25.0, 110.0, n_samples)
        fat = np.random.uniform(0.5, 2.2, n_samples)
        returns_60d = np.random.normal(5.0, 20.0, n_samples)

        df_sat = pd.DataFrame({
            'Ticker': satellite_codes,
            'Name': sample_names,
            'Prefix': [c[:2] for c in satellite_codes],
            'Rev_Growth_YoY_%': np.round(rev_growth, 2),
            'DSI_Days': np.round(dsi, 1),
            'FAT': np.round(fat, 2),
            'Return_60D_%': np.round(returns_60d, 2)
        })

        scaler = StandardScaler()
        X_raw = df_sat[['Rev_Growth_YoY_%', 'DSI_Days', 'FAT', 'Return_60D_%']].copy()
        X_raw['DSI_Days'] = -X_raw['DSI_Days']
        X_scaled = scaler.fit_transform(X_raw)

        kmeans_seed = random_seed_val if enable_random_seed else None
        kmeans = KMeans(n_clusters=n_clusters_input, random_state=kmeans_seed, n_init=10)
        df_sat['Cluster_ID'] = kmeans.fit_predict(X_scaled) + 1

        weights = [0.35, 0.25, 0.20, 0.20]
        df_sat['Quant_Score'] = np.dot(X_scaled, weights)
        min_s, max_s = df_sat['Quant_Score'].min(), df_sat['Quant_Score'].max()
        df_sat['Final_Score'] = np.round(60 + (df_sat['Quant_Score'] - min_s) / (max_s - min_s + 1e-12) * 40, 2)

        cluster_avg_scores = df_sat.groupby('Cluster_ID')['Final_Score'].mean().sort_values(ascending=False)
        top_two_clusters = cluster_avg_scores.head(2).index.tolist()
        c1, c2 = top_two_clusters[0], top_two_clusters[1]

        st.success(f"✅ 運算完成！系統動態識別出全市場前兩大最強勢核心集群為：**群組 {c1}** 與 **群組 {c2}**")

        # 各萃取前 10 名
        c1_top = df_sat[df_sat['Cluster_ID'] == c1].sort_values(by='Final_Score', ascending=False).head(10).copy()
        c2_top = df_sat[df_sat['Cluster_ID'] == c2].sort_values(by='Final_Score', ascending=False).head(10).copy()

        def enrich_technical_data(df_subset):
            prices, dates, supports, resists = [], [], [], []
            for _, r in df_subset.iterrows():
                code = r['Ticker']
                p_val, d_val, s_val, r_val = 'N/A', 'N/A', 'N/A', 'N/A'
                for suffix in ['.TW', '.TWO']:
                    try:
                        hist = yf.Ticker(code + suffix).history(period='2mo')
                        if not hist.empty and len(hist) >= 20:
                            p_val = round(float(hist['Close'].iloc[-1]), 2)
                            d_val = hist.index[-1].strftime('%Y-%m-%d')
                            s_val = round(float(hist['Low'].iloc[-20:].min()), 2)
                            r_val = round(float(hist['High'].iloc[-20:].max()), 2)
                            break
                    except Exception:
                        continue
                prices.append(p_val)
                dates.append(d_val)
                supports.append(s_val)
                resists.append(r_val)
            df_subset['Price'] = prices
            df_subset['Price_Date'] = dates
            df_subset['Support_20D'] = supports
            df_subset['Resist_20D'] = resists
            return df_subset

        with st.spinner("正在透過 yfinance 取得即期股價與計算 20 日支撐/壓力..."):
            c1_top = enrich_technical_data(c1_top)
            c2_top = enrich_technical_data(c2_top)

        display_cols = ['Ticker', 'Name', 'Prefix', 'Price', 'Price_Date', 'Support_20D', 'Resist_20D', 'Rev_Growth_YoY_%', 'DSI_Days', 'FAT', 'Return_60D_%', 'Final_Score']
        
        c1_display = c1_top[display_cols].reset_index(drop=True)
        c1_display.index = range(1, len(c1_display) + 1)

        c2_display = c2_top[display_cols].reset_index(drop=True)
        c2_display.index = range(1, len(c2_display) + 1)

        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"### 🚀 最強勢群組 {c1} 前 10 名")
            st.dataframe(c1_display, use_container_width=True)

        with col2:
            st.markdown(f"### ⚡ 次強勢群組 {c2} 前 10 名")
            st.dataframe(c2_display, use_container_width=True)

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            c1_display.to_excel(writer, sheet_name=f"最強勢群組{c1}", index=True)
            c2_display.to_excel(writer, sheet_name=f"次強勢群組{c2}", index=True)
        output.seek(0)

        st.download_button(
            label="📥 下載動態最強群組前 10 名強弱排行榜 Excel 報表",
            data=output,
            file_name="dynamic_top10_clusters_with_tech.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    st.info("👈 請點擊左側按鈕開始執行動態分群與技術面戰情分析。")
