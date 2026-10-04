from datetime import datetime
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

# ==========================================
# 0. 頁面配置與 CSS 樣式
# ==========================================
st.set_page_config(
    page_title="碎形推論核心量化引擎 1.0",
    page_icon="📈",
    layout="wide",
)

st.markdown("""
    <style>
    .metric-card {
        background-color: #f8f9fa;
        border: 1px solid #e9ecef;
        padding: 15px 20px;
        border-radius: 10px;
        margin-bottom: 10px;
    }
    .metric-title {
        font-size: 14px;
        color: #6c757d;
        margin-bottom: 5px;
    }
    .metric-value {
        font-size: 24px;
        font-weight: bold;
        color: #212529;
    }
    .metric-sub {
        font-size: 13px;
        margin-top: 5px;
    }
    .up { color: #28a745; }
    .down { color: #dc3545; }
    </style>
""", unsafe_allow_html=True)

st.title("🔮 碎形推論核心量化引擎 (Fractal Inference Core Engine v1.0)")
st.markdown(
    "結合 **Hurst 記憶性**、**GARCH 條件波動度**、**時變 G_ADX** 與 **Delta Net_DI 動能變化量**"
    " 的多維度量化分析平台。"
)

# ==========================================
# 1. 側邊欄：使用者互動控制項
# ==========================================
st.sidebar.header("⚙️ 參數設定面板")

# 輸入代碼
user_input_code = st.sidebar.text_input(
    "輸入公司/指數代號", value="3122", help="例如: 3122, 2330, 0000(大盤)"
)

# 頻率選擇
interval_map = {
    "日線 (Daily)": {"interval": "1d", "period": "1y"},
    "60分鐘 (60m)": {"interval": "60m", "period": "60d"},
    "30分鐘 (30m)": {"interval": "30m", "period": "60d"},
    "15分鐘 (15m)": {"interval": "15m", "period": "60d"},
    "5分鐘 (5m)": {"interval": "5m", "period": "30d"},
}
selected_freq = st.sidebar.selectbox("選擇 K 棒頻率", list(interval_map.keys()))


# ==========================================
# 2. 輔助函數：台股代號解析與資料抓取
# ==========================================
def resolve_yahoo_ticker(code):
  code = code.strip()
  if code == "0000":
    return "^TWII", "大盤加權指數", "台灣市場指數"

  # 簡單啟發式判斷上市櫃 (實務上可串接上市櫃完整清單)
  # 4碼多數為台股，若為美股英文代碼則直接回傳
  if code.isdigit():
    # 這裡預設上市優先，若失敗可在後面 fallback 上櫃
    return f"{code}.TW", f"台股代號 {code}", "台灣上市公司"
  else:
    return code.upper(), f"標的 {code.upper()}", "國際/美股標的"


@st.cache_data(ttl=600)
def fetch_yahoo_data(ticker_symbol, interval, period):
  try:
    ticker = yf.Ticker(ticker_symbol)
    df = ticker.history(period=period, interval=interval)
    if df.empty and ".TW" in ticker_symbol:
      # Try OTC (.TWO)
      alt_symbol = ticker_symbol.replace(".TW", ".TWO")
      ticker = yf.Ticker(alt_symbol)
      df = ticker.history(period=period, interval=interval)
      ticker_symbol = alt_symbol

    if df.empty:
      return None, f"無法從 Yahoo Finance 取得代號 {ticker_symbol} 的資料。"

    df = df.reset_index()
    # 統一欄位名稱
    col_candidates = [c for c in df.columns if "Date" in c or "Datetime" in c]
    date_col = col_candidates[0] if col_candidates else df.columns[0]

    df = df.rename(
        columns={
            date_col: "DateTime",
            "Open": "Open",
            "High": "High",
            "Low": "Low",
            "Close": "Close",
            "Volume": "Volume",
        }
    )
    return df, ticker_symbol
  except Exception as e:
    return None, str(e)


# ==========================================
# 3. 核心量化引擎運算函數
# ==========================================
def run_quant_engine(df):
  # 確保數值正確
  for col in ["Open", "High", "Low", "Close"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

  df = df.dropna(subset=["Close", "High", "Low"]).reset_index(drop=True)

  # 1. 報酬率
  df["Return"] = np.log(df["Close"] / df["Close"].shift(1))

  # 2. Hurst Exponent
  def get_hurst_rs(ts):
    ts = np.array(ts)
    ts = ts[~np.isnan(ts)]
    if len(ts) < 3:
      return 0.5
    n_vals = [3, 5, 10]
    rs_vals = []
    for n in n_vals:
      if n > len(ts):
        continue
      sub = ts[-n:]
      if len(sub) < 2:
        continue
      cum_sub = np.cumsum(sub - np.mean(sub))
      R = np.max(cum_sub) - np.min(cum_sub)
      S = np.std(sub)
      rs_vals.append((R / S) if S > 0 else 1.0)
    if len(rs_vals) >= 2:
      slope, _ = np.polyfit(
          np.log(n_vals[: len(rs_vals)]), np.log(rs_vals), 1
      )
      return slope
    return 0.5

  df["Hurst"] = (
      df["Return"].rolling(10, min_periods=3).apply(get_hurst_rs, raw=True)
  )
  df["GARCH_V"] = df["Return"].rolling(5).std() / 100.0

  # 3. G_ADX & Net_DI
  up_move = df["High"].diff()
  down_move = df["Low"].shift(1) - df["Low"]
  pos_DM = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
  neg_DM = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

  pos_DM_smooth = pd.Series(pos_DM).rolling(5).mean()
  neg_DM_smooth = pd.Series(neg_DM).rolling(5).mean()

  safe_v = df["GARCH_V"].replace(0, 1e-5).fillna(1e-5)
  pos_DI = (pos_DM_smooth / safe_v) * 100
  neg_DI = (neg_DM_smooth / safe_v) * 100

  DX = np.abs(pos_DI - neg_DI) / (pos_DI + neg_DI + 1e-9) * 100
  df["G_ADX"] = DX.rolling(5).mean()
  df["Net_DI"] = pos_DI - neg_DI
  df["Delta_Net_DI"] = df["Net_DI"].diff()

  # 4. Slope & Slope_Acc
  def get_slope(ts):
    if np.isnan(ts).any():
      return np.nan
    return np.polyfit(np.arange(5), ts, 1)[0]

  df["Slope_5"] = df["Close"].rolling(5).apply(get_slope, raw=True)
  df["Slope_Acc"] = df["Slope_5"].diff()

  # 5. fsQCA State Classification
  def map_state(row):
    if pd.isna(row["Hurst"]) or pd.isna(row["G_ADX"]):
      return "資料不足"
    if (
        0.5 <= row["Hurst"] <= 0.6
        and row["Slope_Acc"] > 0.05
        and row["G_ADX"] > 25
        and row["Net_DI"] > 0
        and row["Delta_Net_DI"] > 0
    ):
      return "剛起漲 (Early Breakout)"
    elif (
        0.5 <= row["Hurst"] <= 0.6
        and row["Slope_Acc"] < -0.05
        and row["G_ADX"] > 25
        and row["Net_DI"] < 0
        and row["Delta_Net_DI"] < 0
    ):
      return "剛起跌 (Early Breakdown)"
    elif row["Hurst"] > 0.5 and row["G_ADX"] > 25 and row["Net_DI"] > 0:
      return "多頭趨勢 (Bullish)"
    elif row["Hurst"] > 0.5 and row["G_ADX"] > 25 and row["Net_DI"] < 0:
      return "空頭趨勢 (Bearish)"
    elif row["Hurst"] < 0.4:
      return "均值回歸/窒息盤整 (Range)"
    else:
      return "動能轉換期 (Transition)"

  df["市場狀態分類"] = df.apply(map_state, axis=1)
  return df


# ==========================================
# 4. 主畫面執行與呈現
# ==========================================
if st.sidebar.button("🚀 開始執行碎形推論", type="primary"):
  raw_ticker, default_name, market_attr = resolve_yahoo_ticker(
      user_input_code
  )
  cfg = interval_map[selected_freq]

  with st.spinner(f"正在從 Yahoo Finance 抓取 {raw_ticker} ({selected_freq}) ..."):
    df_raw, used_ticker = fetch_yahoo_data(
        raw_ticker, cfg["interval"], cfg["period"]
    )

  if df_raw is None:
    st.error(f"資料取得失敗：{used_ticker}")
  else:
    # 自動判斷上市櫃
    if ".TWO" in used_ticker:
      market_attr = "櫃買中心 (上櫃公司)"
    elif ".TW" in used_ticker:
      market_attr = "證交所 (上市公司)"

    # 執行量化引擎
    df_res = run_quant_engine(df_raw)
    latest = df_res.iloc[-1]
    prev = df_res.iloc[-2] if len(df_res) > 1 else latest

    p0 = latest["Close"]
    p_prev = prev["Close"]
    chg = p0 - p_prev
    chg_pct = (chg / p_prev) * 100 if p_prev > 0 else 0.0
    chg_class = "up" if chg >= 0 else "down"
    chg_str = f"↑ +{chg:.2f} (+{chg_pct:.2f}%)" if chg >= 0 else f"↓ {chg:.2f} ({chg_pct:.2f}%)"

    # --- 輸出個股資訊卡片 (仿照您提供的圖片風格) ---
    st.markdown("### 📋 標的即時資訊摘要")
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
      st.markdown(
          f"""
            <div class="metric-card">
                <div class="metric-title">標的名稱</div>
                <div class="metric-value">{default_name}</div>
                <div class="metric-sub">({user_input_code})</div>
            </div>
            """,
          unsafe_allow_html=True,
      )

    with c2:
      st.markdown(
          f"""
            <div class="metric-card">
                <div class="metric-title">股票/指數代碼</div>
                <div class="metric-value">{user_input_code}</div>
                <div class="metric-sub">{used_ticker}</div>
            </div>
            """,
          unsafe_allow_html=True,
      )

    with c3:
      st.markdown(
          f"""
            <div class="metric-card">
                <div class="metric-title">市場屬性</div>
                <div class="metric-value" style="font-size: 20px;">{market_attr}</div>
                <div class="metric-sub">Yahoo Finance 串接</div>
            </div>
            """,
          unsafe_allow_html=True,
      )

    with c4:
      st.markdown(
          f"""
            <div class="metric-card">
                <div class="metric-title">目前收盤價 (P0)</div>
                <div class="metric-value">{p0:.2f}</div>
                <div class="metric-sub {chg_class}">{chg_str}</div>
            </div>
            """,
          unsafe_allow_html=True,
      )

    with c5:
      now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      last_k_time = str(latest["DateTime"])
      st.markdown(
          f"""
            <div class="metric-card">
                <div class="metric-title">最後 K 棒時間</div>
                <div class="metric-value" style="font-size: 16px;">{last_k_time}</div>
                <div class="metric-sub">報告產出: {now_time}</div>
            </div>
            """,
          unsafe_allow_html=True,
      )

    # --- 輸出量化模型分析結果表格 ---
    st.markdown("---")
    st.markdown("### 🔬 碎形推論時空組態矩陣分析結果")

    output_cols = [
        "DateTime",
        "Close",
        "Slope_5",
        "Slope_Acc",
        "Hurst",
        "GARCH_V",
        "G_ADX",
        "Net_DI",
        "Delta_Net_DI",
        "市場狀態分類",
    ]
    display_df = df_res[output_cols].tail(15).iloc[::-1]  # 顯示最近 15 筆，最新在上

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # 下載按鈕
    excel_file = f"{user_input_code}_碎形推論完整報告.xlsx"
    df_res[output_cols].to_excel(excel_file, index=False)

    with open(excel_file, "rb") as f:
      st.download_button(
          label="📥 下載完整 Excel 矩陣分析報告",
          data=f,
          file_name=excel_file,
          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      )
else:
  st.info(
      "👈 請在左側側邊欄輸入公司代碼（例如 3122 或 2330），選擇 K"
      " 棒頻率，然後點擊「開始執行碎形推論」按鈕。"
  )
