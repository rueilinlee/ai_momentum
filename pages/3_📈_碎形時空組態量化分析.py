from datetime import datetime
import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

# ==========================================
# 0. 頁面配置與自定義 CSS 樣式
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
        font-size: 22px;
        font-weight: bold;
        color: #212529;
    }
    .metric-sub {
        font-size: 13px;
        margin-top: 5px;
    }
    .up { color: #28a745; font-weight: bold; }
    .down { color: #dc3545; font-weight: bold; }
    .sr-box {
        background-color: #e9ecef;
        padding: 10px 15px;
        border-radius: 8px;
        text-align: center;
        font-weight: bold;
    }
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

user_input_code = st.sidebar.text_input(
    "輸入公司/指數代號",
    value="3122",
    help="例如: 3122, 2330, 6213, 0000(大盤)",
)

interval_map = {
    "日線 (Daily)": {"interval": "1d", "period": "1y"},
    "60分鐘 (60m)": {"interval": "60m", "period": "60d"},
    "30分鐘 (30m)": {"interval": "30m", "period": "60d"},
    "15分鐘 (15m)": {"interval": "15m", "period": "60d"},
    "5分鐘 (5m)": {"interval": "5m", "period": "30d"},
}
selected_freq = st.sidebar.selectbox("選擇 K 棒頻率", list(interval_map.keys()))


# ==========================================
# 2. 輔助函數：台股代號解析與中文對應字典
# ==========================================
TW_STOCK_NAMES = {
    "3122": "笙泉",
    "2330": "台積電",
    "2317": "鴻海",
    "2454": "聯發科",
    "6213": "聯茂",
    "6147": "頎邦",
    "2376": "技嘉",
    "3017": "奇鋐",
    "2308": "台達電",
    "2881": "富邦金",
    "2882": "國泰金",
    "0050": "元大台灣50",
    "0056": "元大高股息",
}


def resolve_yahoo_ticker(code):
  code = code.strip()
  if code == "0000" or code.upper() == "^TWII":
    return "^TWII", "大盤加權指數", "台灣市場指數"

  company_name = TW_STOCK_NAMES.get(code, f"台股代號 {code}")

  if code.isdigit():
    return f"{code}.TW", company_name, "台灣上市公司"
  else:
    return code.upper(), f"標的 {code.upper()}", "國際/美股標的"


@st.cache_data(ttl=600)
def fetch_yahoo_data(ticker_symbol, interval, period):
  try:
    ticker = yf.Ticker(ticker_symbol)
    df = ticker.history(period=period, interval=interval)
    if df.empty and ".TW" in ticker_symbol:
      alt_symbol = ticker_symbol.replace(".TW", ".TWO")
      ticker = yf.Ticker(alt_symbol)
      df = ticker.history(period=period, interval=interval)
      ticker_symbol = alt_symbol

    if df.empty:
      return None, f"無法從 Yahoo Finance 取得代號 {ticker_symbol} 的資料。"

    df = df.reset_index()
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

    df["DateTime"] = pd.to_datetime(df["DateTime"])
    if df["DateTime"].dt.tz is not None:
      df["DateTime"] = df["DateTime"].dt.tz_convert("Asia/Taipei")
    else:
      df["DateTime"] = df["DateTime"].dt.tz_localize("UTC").dt.tz_convert(
          "Asia/Taipei"
      )

    df["DateTime"] = df["DateTime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return df, ticker_symbol
  except Exception as e:
    return None, str(e)


# ==========================================
# 3. 核心量化引擎運算函數
# ==========================================
def run_quant_engine(df):
  for col in ["Open", "High", "Low", "Close"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")

  df = df.dropna(subset=["Close", "High", "Low"]).reset_index(drop=True)
  df["Return"] = np.log(df["Close"] / df["Close"].shift(1))

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

  def get_slope(ts):
    if np.isnan(ts).any():
      return np.nan
    return np.polyfit(np.arange(5), ts, 1)[0]

  df["Slope_5"] = df["Close"].rolling(5).apply(get_slope, raw=True)
  df["Slope_Acc"] = df["Slope_5"].diff()

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
# 4. 數學動態支撐與壓力計算模組
# ==========================================
def calculate_support_resistance(latest):
  p0 = latest["Close"]
  garch_v = latest["GARCH_V"] if not pd.isna(latest["GARCH_V"]) else 0.01
  slope_5 = latest["Slope_5"] if not pd.isna(latest["Slope_5"]) else 0.0
  slope_acc = latest["Slope_Acc"] if not pd.isna(latest["Slope_Acc"]) else 0.0
  delta_net_di = (
      latest["Delta_Net_DI"] if not pd.isna(latest["Delta_Net_DI"]) else 0.0
  )

  v_safe = max(garch_v, 0.001)
  garch_band = p0 * v_safe * 1.5
  mom_direction = 1 if delta_net_di >= 0 else -1
  momentum_adj = slope_5 + (mom_direction * abs(slope_acc))

  r1 = p0 + garch_band + max(0, momentum_adj)
  r2 = p0 + (2 * garch_band) + abs(slope_5)
  s1 = p0 - garch_band - max(0, -momentum_adj)
  s2 = p0 - (2 * garch_band) - abs(slope_5)

  return {
      "R2": round(r2, 2),
      "R1": round(r1, 2),
      "P0": round(p0, 2),
      "S1": round(s1, 2),
      "S2": round(s2, 2),
  }


# ==========================================
# 5. 智慧深度量化解析模組
# ==========================================
def generate_deep_insights(latest, market_state):
  hurst = latest["Hurst"]
  gadx = latest["G_ADX"]
  net_di = latest["Net_DI"]
  delta_net_di = latest["Delta_Net_DI"]
  slope_acc = latest["Slope_Acc"]
  insights = []

  if hurst > 0.7:
    insights.append(
        f"**強效記憶性主導 ($Hurst = {hurst:.4f}$)：**"
        " 目前走勢具備極強的單向持續性記憶,趨勢慣性不易輕易扭轉。"
    )
  elif 0.5 <= hurst <= 0.6:
    insights.append(
        f"**記憶剛形成 ($Hurst = {hurst:.4f}$)：**"
        " 處於趨勢初升段或變盤轉折邊緣,正向記憶正剛開始萌芽。"
    )
  elif hurst < 0.4:
    insights.append(
        f"**均值回歸盤整 ($Hurst = {hurst:.4f}$)：**"
        " 走勢呈現反持續性與鋸齒狀震盪,缺乏單向續航力,應避免盲目追價。"
    )
  else:
    insights.append(
        f"**過渡記憶區 ($Hurst = {hurst:.4f}$)：**"
        f" 市場多空雜訊交織,正處於 {market_state}。"
    )

  if gadx > 25:
    trend_desc = (
        "多方" if net_di > 0 else ("空方" if net_di < 0 else "多空拉鋸")
    )
    insights.append(
        f"**真實趨勢強度 ($G\_ADX = {gadx:.2f}$)：**"
        f" 數值大於 25 門檻,代表當前動能已有效擊穿背景雜訊,由 **{trend_desc}**"
        " 主導盤勢。"
    )
  else:
    insights.append(
        f"**動能引擎熄火 ($G\_ADX = {gadx:.2f}$)：**"
        " 趨勢強度偏低,盤勢缺乏足夠的實質資金推力,容易出現假突破或頻繁拉回。"
    )

  if delta_net_di > 0 and slope_acc > 0:
    insights.append(
        "**動能與價格共振擴張：** 靈魂指標 $\\Delta Net\_DI$ 與價格加速度"
        " 雙雙為正,買盤力道正在加速擴大,上攻動能扎實。"
    )
  elif delta_net_di < 0 and slope_acc > 0:
    insights.append(
        "**⚠️ 高檔多頭背離警訊：** 價格雖然維持慣性（加速度轉正/平緩）,但 $\\Delta"
        " Net\_DI$ 動能變化量轉負,顯示高檔追價力道開始收斂,須防範短線過熱拉回。"
    )
  elif delta_net_di > 0 and slope_acc < 0:
    insights.append(
        "**🔥 低檔空頭背離/強烈抵抗：** 價格雖然處於回檔,但 $\\Delta Net\_DI$"
        " 出現顯著正向跳升（賣壓竭盡、買盤回補）,具備潛在 V 型轉折契機。"
    )
  else:
    insights.append(
        f"**微觀動能交替：** $\\Delta Net\_DI$ 變動值為 {delta_net_di:,.2f},"
        " 顯示短線資金攻防處於過渡交替期。"
    )

  if "多頭" in market_state:
    strategy = (
        "**💡 策略建議：** 大格局維持多頭主升段,強勢慣性有利順勢操作。"
        " 若伴隨多頭背離則可適度居高思危,續抱核心部位並嚴守移動停利。"
    )
  elif "空頭" in market_state:
    strategy = (
        "**💡 策略建議：** 大格局受空方控盤。雖偶有低檔背離抵抗（短線回補）,但在"
        " $Net\_DI$ 真正翻正前,反彈仍視為技術性修繕,不宜過度積極摸底。"
    )
  elif "剛起漲" in market_state:
    strategy = (
        "**💡 策略建議：** 🎯 **黃金起漲點訊號**！Hurst 剛跨越 0.5 且加速度與"
        " Delta Net_DI 同步放大,為勝率與期望值極佳的切入點。"
    )
  else:
    strategy = (
        "**💡 策略建議：** 當前盤勢落於區間或過渡轉換期,建議降低部位或採高出低進策略,靜待下一個具備"
        " G_ADX 突破 25 的明確訊號。"
    )

  return insights, strategy


# ==========================================
# 6. 主畫面執行與互動呈現
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
    if ".TWO" in used_ticker:
      market_attr = "櫃買中心 (上櫃公司)"
    elif ".TW" in used_ticker:
      market_attr = "證交所 (上市公司)"

    df_res = run_quant_engine(df_raw)
    latest = df_res.iloc[-1]
    prev = df_res.iloc[-2] if len(df_res) > 1 else latest

    p0 = latest["Close"]
    p_prev = prev["Close"]
    chg = p0 - p_prev
    chg_pct = (chg / p_prev) * 100 if p_prev > 0 else 0.0
    chg_class = "up" if chg >= 0 else "down"
    chg_str = f"↓ {chg:.2f} ({chg_pct:.2f}%)" if chg < 0 else f"↑ +{chg:.2f} (+{chg_pct:.2f}%)"

    # --- 輸出個股資訊卡片 ---
    st.markdown("### 📋 標的即時資訊摘要")
    c1, c2, c3, c4, c5 = st.columns(5)

    with c1:
      st.markdown(
          f"""
            <div class="metric-card">
                <div class="metric-title">標的名稱</div>
                <div class="metric-value">{default_name} ({user_input_code})</div>
                <div class="metric-sub">中文對應名稱</div>
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
                <div class="metric-value" style="font-size: 18px;">{market_attr}</div>
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
      now_time_tw = (
          pd.Timestamp.now(tz="Asia/Taipei").strftime("%Y-%m-%d %H:%M:%S")
      )
      last_k_time = str(latest["DateTime"])
      st.markdown(
          f"""
            <div class="metric-card">
                <div class="metric-title">最後 K 棒時間 (CST)</div>
                <div class="metric-value" style="font-size: 14px;">{last_k_time}</div>
                <div class="metric-sub">報告產出: {now_time_tw}</div>
            </div>
            """,
          unsafe_allow_html=True,
      )

    # --- 透過標題與括號註記尺度頻率呈現動態數學支撐與壓力模型 ---
    sr = calculate_support_resistance(latest)
    st.markdown("---")
    st.markdown(
        f"### 🎯 GARCH & 碎形動態數學支撐與壓力模型 `({selected_freq})`"
    )

    sc1, sc2, sc3, sc4, sc5 = st.columns(5)
    with sc1:
      st.markdown(
          f"""<div class="sr-box" style="color: #dc3545;">極限強壓 (R2)<br><span"
          f" style="font-size: 20px;">{sr['R2']}</span></div>""",
          unsafe_allow_html=True,
      )
    with sc2:
      st.markdown(
          f"""<div class="sr-box" style="color: #fd7e14;">短線壓力 (R1)<br><span"
          f" style="font-size: 20px;">{sr['R1']}</span></div>""",
          unsafe_allow_html=True,
      )
    with sc3:
      st.markdown(
          f"""<div class="sr-box" style="color: #007bff;">目前價格 (P0)<br><span"
          f" style="font-size: 22px;">{sr['P0']}</span></div>""",
          unsafe_allow_html=True,
      )
    with sc4:
      st.markdown(
          f"""<div class="sr-box" style="color: #20c997;">短線支撐 (S1)<br><span"
          f" style="font-size: 20px;">{sr['S1']}</span></div>""",
          unsafe_allow_html=True,
      )
    with sc5:
      st.markdown(
          f"""<div class="sr-box" style="color: #28a745;">極限強支撐 (S2)<br><span"
          f" style="font-size: 20px;">{sr['S2']}</span></div>""",
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
    display_df = df_res[output_cols].tail(15).iloc[::-1]
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # --- 輸出智慧推論與實證解析模組 ---
    st.markdown("---")
    st.markdown("### 🧠 碎形推論引擎：深度量化推論與實證解析")

    market_state_val = latest["市場狀態分類"]
    insights, strategy_advice = generate_deep_insights(latest, market_state_val)

    with st.container():
      st.markdown(
          f"**📊 分析標的：** `{default_name} ({user_input_code})` ｜"
          f" **目前狀態判定：** `{latest['Hurst']:.4f}` 記憶性主導下的"
          f" **【{market_state_val}】**"
      )

      for ins in insights:
        st.markdown(f"- {ins}")

      st.markdown("---")
      st.info(strategy_advice)

    # --- Excel 下載按鈕 ---
    excel_file = f"{user_input_code}_{default_name}_碎形推論完整報告.xlsx"
    try:
      df_res[output_cols].to_excel(excel_file, index=False)
      with open(excel_file, "rb") as f:
        st.download_button(
            label="📥 下載完整 Excel 矩陣分析報告",
            data=f,
            file_name=excel_file,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
      st.warning(
          "⚠️ 無法自動生成 Excel 檔案，請確認環境是否已安裝 `openpyxl` 套件。"
          f"（錯誤訊息: {e}）"
      )
else:
  st.info(
      "👈 請在左側側邊欄輸入公司代碼（例如 3122、2330 或 0000 大盤），選擇 K"
      " 棒頻率，然後點擊「開始執行碎形推論」按鈕。"
  )
