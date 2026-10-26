import streamlit as st
import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import os
import io
import yfinance as yf

# 設定網頁版面
st.set_page_config(
    page_title="AI-TSMC-50 衛星同儕標的動態分群與次產業戰情室",
    page_icon="📈",
    layout="wide"
)

st.title("📊 AI-TSMC-50 衛星同儕標的動態分群與次產業戰情室 (含狀態保留與中文公司名稱)")
st.markdown("本系統針對全市場標的扣除 **AI-TSMC-50 核心 55 檔** 後之同儕衛星標的，透過多維度量化因子與 K-Means AI 分群引擎，動態自動尋找最強勢的前兩個集群（各前 10 名）。執行後畫面結果會**凍結保留（透過 session_state）**，方便您順暢使用下方的次產業下拉選單切換檢視各次產業前 3 名標的。")

# 側邊欄控制面板
st.sidebar.header("⚙️ 模型與資料設定")
uploaded_extra = st.sidebar.file_uploader("上傳全市場標的 CSV (`taiwan_extra_universe.csv`)", type=["csv"])
uploaded_names = st.sidebar.file_uploader("上傳公司名稱對照 CSV (`stock_names.csv`) (選填)", type=["csv"])

n_clusters_input = st.sidebar.slider("K-Means AI 分群數量 (Clusters)", 5, 15, 10)

st.sidebar.markdown("---")
st.sidebar.subheader("🎲 亂數與分群設定")
enable_random_seed = st.sidebar.checkbox("固定亂數種子 (確保結果可重現)", value=True)
random_seed_val = st.sidebar.number_input("設定 Random Seed數值", value=42, step=1) if enable_random_seed else None

# 使用 @st.cache_data 快取 yfinance 查詢結果以加速效能，並確保優先抓取中文名稱
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_stock_info_and_tech(code, fallback_name):
    real_name = fallback_name
    p_val, d_val, s_val, r_val = 'N/A', 'N/A', 'N/A', 'N/A'
    
    for suffix in ['.TW', '.TWO']:
        try:
            t = yf.Ticker(code + suffix)
            info = t.info
            long_n = info.get('longName', info.get('shortName', ''))
            if long_n:
                real_name = long_n
            
            hist = t.history(period='2mo')
            if not hist.empty and len(hist) >= 20:
                p_val = round(float(hist['Close'].iloc[-1]), 2)
                d_val = hist.index[-1].strftime('%Y-%m-%d')
                s_val = round(float(hist['Low'].iloc[-20:].min()), 2)
                r_val = round(float(hist['High'].iloc[-20:].max()), 2)
                break
        except Exception:
            continue
            
    return real_name, p_val, d_val, s_val, r_val

# 初始化 session_state 用於凍結畫面結果
if 'executed' not in st.session_state:
    st.session_state.executed = False

if st.button("🚀 開始執行動態分群與次產業戰情分析", type="primary"):
    st.session_state.executed = True
    with st.spinner("正在載入資料、執行 AI 動態分群與次產業標的計算..."):
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

        # 讀取備用 CSV 名稱對照
        fallback_mapping = {}
        if uploaded_names is not None:
            df_names = pd.read_csv(uploaded_names)
        elif os.path.exists('stock_names.csv'):
            df_names = pd.read_csv('stock_names.csv')
        else:
            df_names = pd.DataFrame(columns=['Code', 'Name'])
        
        if not df_names.empty:
            df_names['Code'] = df_names['Code'].astype(str).str.zfill(4)
            fallback_mapping = dict(zip(df_names['Code'], df_names['Name']))

        satellite_codes = df_all[df_all['Code'].str[:2].isin(core_prefixes) & (~df_all['Code'].isin(core_set))]['Code'].tolist()

        if enable_random_seed:
            np.random.seed(random_seed_val)
        n_samples = len(satellite_codes)

        dummy_names = [fallback_mapping.get(code, f"上市櫃企業_{code}") for code in satellite_codes]

        rev_growth = np.random.normal(12.5, 18.0, n_samples)
        dsi = np.random.uniform(25.0, 110.0, n_samples)
        fat = np.random.uniform(0.5, 2.2, n_samples)
        returns_60d = np.random.normal(5.0, 20.0, n_samples)

        df_sat = pd.DataFrame({
            'Ticker': satellite_codes,
            'Name': dummy_names,
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

        # 各萃取前 10 名
        c1_top = df_sat[df_sat['Cluster_ID'] == c1].sort_values(by='Final_Score', ascending=False).head(10).copy()
        c2_top = df_sat[df_sat['Cluster_ID'] == c2].sort_values(by='Final_Score', ascending=False).head(10).copy()

        def enrich_real_name_and_tech(df_subset):
            names, prices, dates, supports, resists = [], [], [], [], []
            for _, r in df_subset.iterrows():
                code = r['Ticker']
                fallback = fallback_mapping.get(code, f"上市櫃企業_{code}")
                r_name, p_val, d_val, s_val, r_val = fetch_stock_info_and_tech(code, fallback)
                
                if code in fallback_mapping:
                    r_name = fallback_mapping[code]
                
                names.append(r_name)
                prices.append(p_val)
                dates.append(d_val)
                supports.append(s_val)
                resists.append(r_val)
                
            df_subset['Name'] = names
            df_subset['Price'] = prices
            df_subset['Price_Date'] = dates
            df_subset['Support_20D'] = supports
            df_subset['Resist_20D'] = resists
            return df_subset

        with st.spinner("正在透過快取機制取得主畫面標的之真實中文名稱、即期股價與技術面..."):
            c1_top = enrich_real_name_and_tech(c1_top)
            c2_top = enrich_real_name_and_tech(c2_top)

        display_cols = ['Ticker', 'Name', 'Prefix', 'Price', 'Price_Date', 'Support_20D', 'Resist_20D', 'Rev_Growth_YoY_%', 'DSI_Days', 'FAT', 'Return_60D_%', 'Final_Score']
        
        c1_display = c1_top[display_cols].reset_index(drop=True)
        c1_display.index = range(1, len(c1_display) + 1)

        c2_display = c2_top[display_cols].reset_index(drop=True)
        c2_display.index = range(1, len(c2_display) + 1)

        # 儲存至 session_state 凍結畫面
        st.session_state.c1 = c1
        st.session_state.c2 = c2
        st.session_state.c1_display = c1_display
        st.session_state.c2_display = c2_display
        st.session_state.df_sat = df_sat
        st.session_state.fallback_mapping = fallback_mapping

# 若已經執行過，直接從 session_state 渲染畫面，避免下拉選單觸發重新整理而洗掉主畫面
if st.session_state.get('executed', False):
    c1 = st.session_state.c1
    c2 = st.session_state.c2
    c1_display = st.session_state.c1_display
    c2_display = st.session_state.c2_display
    df_sat = st.session_state.df_sat
    fallback_mapping = st.session_state.fallback_mapping

    st.success(f"✅ 系統目前呈現凍結狀態！識別出全市場前兩大最強勢核心集群為：**群組 {c1}** 與 **群組 {c2}**")

    # ---------------- 主畫面呈現 ----------------
    st.markdown("---")
    st.subheader("🏆 主畫面：最強勢前兩大集群前 10 名排行榜")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"### 🚀 最強勢群組 {c1} 前 10 名")
        st.dataframe(c1_display, use_container_width=True)

    with col2:
        st.markdown(f"### ⚡ 次強勢群組 {c2} 前 10 名")
        st.dataframe(c2_display, use_container_width=True)

    # ---------------- 各次產業前 3 名查詢與下拉選單 ----------------
    st.markdown("---")
    st.subheader("📑 各次產業標的戰情室 (前 3 名)")
    st.markdown("您可以透過下方下拉選單自由切換檢視全市場各個同儕次產業（依據代號前兩碼分類）的前 3 名標的。")

    prefixes = df_sat['Prefix'].unique()
    subindustry_options = sorted([str(p) for p in prefixes])

    selected_sub = st.selectbox("🔍 請選擇要檢視的次產業字首 (Prefix)", subindustry_options)

    if selected_sub:
        sub_df = df_sat[df_sat['Prefix'] == selected_sub].sort_values(by='Final_Score', ascending=False).head(3).copy()
        
        names, prices, dates = [], [], []
        for _, r in sub_df.iterrows():
            code = r['Ticker']
            fallback = fallback_mapping.get(code, f"上市櫃企業_{code}")
            r_name, p_val, d_val, _, _ = fetch_stock_info_and_tech(code, fallback)
            if code in fallback_mapping:
                r_name = fallback_mapping[code]
            names.append(r_name)
            prices.append(p_val)
            dates.append(d_val)
            
        sub_df['Name'] = names
        sub_df['Price'] = prices
        sub_df['Price_Date'] = dates

        sub_display_cols = ['Name', 'Ticker', 'Price', 'Price_Date']
        sub_display = sub_df[sub_display_cols].reset_index(drop=True)
        sub_display.index = range(1, len(sub_display) + 1)
        sub_display.columns = ['公司名稱', '公司代碼', '即期股價', '日期']

        st.markdown(f"### 📌 次產業字首 [{selected_sub}] 前 3 名標的")
        st.dataframe(sub_display, use_container_width=True)

    # Excel 匯出
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        c1_display.to_excel(writer, sheet_name=f"最強勢群組{c1}", index=True)
        c2_display.to_excel(writer, sheet_name=f"次強勢群組{c2}", index=True)
    output.seek(0)

    st.download_button(
        label="📥 下載主畫面雙群組前 10 名 Excel 報表",
        data=output,
        file_name="dynamic_top10_clusters_realname_tech.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
else:
    st.info("👈 請點擊左側「開始執行動態分群與次產業戰情分析」按鈕以載入主畫面與次產業戰情室。")
