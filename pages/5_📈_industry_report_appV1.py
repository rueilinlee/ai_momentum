"""
news_classifier.py
第一階段新聞 → 標的辨識 → 次產業 / 供應鏈位置歸類

供應鏈位置定義：
  上游 = IC 設計、IP、半導體設備
  中游 = 晶圓製造、封測、載板、散熱與零組件
  下游 = 伺服器與系統整合 / 組裝
  終端需求 = 國際雲端與晶片客戶（不在追蹤名單，但影響需求面）

使用方式見 render_news_classification()。
名單只是起點，請依你的追蹤範圍持續擴充（可看「未歸類新聞」分頁來補名單）。
"""

import pandas as pd

# ------------------------------------------------------------------
# 公司字典：名稱別名 → 次產業 / 供應鏈位置 / 細分角色
# ------------------------------------------------------------------
COMPANIES = [
    # 先進製程與晶圓代工
    {"name": "台積電", "ticker": "2330.TW", "aliases": ["台積電", "TSMC"],
     "subsector": "先進製程與晶圓代工", "chain": "中游", "role": "晶圓代工・先進製程"},
    {"name": "聯電", "ticker": "2303.TW", "aliases": ["聯電"],
     "subsector": "先進製程與晶圓代工", "chain": "中游", "role": "晶圓代工・成熟製程"},
    {"name": "世界先進", "ticker": "5347.TWO", "aliases": ["世界先進"],
     "subsector": "先進製程與晶圓代工", "chain": "中游", "role": "晶圓代工・特殊製程"},
    # AI伺服器與系統整合
    {"name": "緯穎", "ticker": "6669.TW", "aliases": ["緯穎"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "雲端資料中心伺服器"},
    {"name": "廣達", "ticker": "2382.TW", "aliases": ["廣達"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "伺服器 ODM 組裝"},
    {"name": "技嘉", "ticker": "2376.TW", "aliases": ["技嘉"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "伺服器・板卡與系統"},
    {"name": "鴻海", "ticker": "2317.TW", "aliases": ["鴻海", "鴻海精密"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "伺服器 / 機櫃組裝"},
    {"name": "緯創", "ticker": "3231.TW", "aliases": ["緯創"],
     "subsector": "AI伺服器與系統整合", "chain": "下游", "role": "伺服器 ODM 組裝"},
    # 先進封裝與設備
    {"name": "弘塑", "ticker": "3131.TWO", "aliases": ["弘塑"],
     "subsector": "先進封裝與設備", "chain": "上游", "role": "封裝設備・濕製程"},
    {"name": "均華", "ticker": "6640.TWO", "aliases": ["均華"],
     "subsector": "先進封裝與設備", "chain": "上游", "role": "封裝設備・黏晶 / 挑揀"},
    {"name": "萬潤", "ticker": "6187.TWO", "aliases": ["萬潤"],
     "subsector": "先進封裝與設備", "chain": "上游", "role": "封裝設備・自動化"},
    {"name": "日月光投控", "ticker": "3711.TW", "aliases": ["日月光"],
     "subsector": "先進封裝與設備", "chain": "中游", "role": "封裝測試代工"},
    {"name": "欣興", "ticker": "3037.TW", "aliases": ["欣興"],
     "subsector": "先進封裝與設備", "chain": "中游", "role": "載板 / PCB"},
    # 散熱與水冷模組
    {"name": "健策", "ticker": "3653.TW", "aliases": ["健策"],
     "subsector": "散熱與水冷模組", "chain": "中游", "role": "均熱片・散熱零組件"},
    {"name": "奇鋐", "ticker": "3017.TW", "aliases": ["奇鋐"],
     "subsector": "散熱與水冷模組", "chain": "中游", "role": "風扇與散熱模組"},
    {"name": "雙鴻", "ticker": "3324.TWO", "aliases": ["雙鴻"],
     "subsector": "散熱與水冷模組", "chain": "中游", "role": "水冷 / 散熱模組"},
    # 高速傳輸與 IC 設計
    {"name": "信驊", "ticker": "5274.TWO", "aliases": ["信驊"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・BMC 伺服器管理晶片"},
    {"name": "世芯-KY", "ticker": "3661.TW", "aliases": ["世芯"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・ASIC 設計服務"},
    {"name": "祥碩", "ticker": "5269.TW", "aliases": ["祥碩"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・高速傳輸介面"},
    {"name": "創意", "ticker": "3443.TW", "aliases": ["創意電子"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・ASIC 設計服務"},
    {"name": "聯發科", "ticker": "2454.TW", "aliases": ["聯發科"],
     "subsector": "高速傳輸與 IC 設計", "chain": "上游", "role": "IC 設計・終端 SoC"},
]

# 不在追蹤名單、但會影響需求面的國際公司
FOREIGN = [
    {"name": "輝達", "aliases": ["輝達", "NVIDIA", "Nvidia"], "role": "AI 晶片客戶 / 需求領頭羊"},
    {"name": "超微", "aliases": ["超微", "AMD"], "role": "AI 晶片客戶"},
    {"name": "博通", "aliases": ["博通", "Broadcom"], "role": "ASIC / 網通晶片客戶"},
    {"name": "蘋果", "aliases": ["蘋果", "Apple"], "role": "終端消費性客戶"},
    {"name": "微軟", "aliases": ["微軟", "Microsoft"], "role": "雲端資本支出"},
    {"name": "Google", "aliases": ["Google", "谷歌"], "role": "雲端資本支出"},
    {"name": "亞馬遜", "aliases": ["亞馬遜", "AWS", "Amazon"], "role": "雲端資本支出"},
    {"name": "Meta", "aliases": ["Meta"], "role": "雲端資本支出"},
]

CHAIN_ORDER = ["上游", "中游", "下游", "終端需求"]


def _all_entries():
    for c in COMPANIES:
        yield {
            "公司": c["name"], "代號": c["ticker"], "次產業": c["subsector"],
            "供應鏈位置": c["chain"], "細分角色": c["role"], "aliases": c["aliases"],
        }
    for f in FOREIGN:
        yield {
            "公司": f["name"], "代號": "", "次產業": "（國際客戶 / 終端需求）",
            "供應鏈位置": "終端需求", "細分角色": f["role"], "aliases": f["aliases"],
        }


ENTRIES = list(_all_entries())


def match_companies(title):
    """回傳標題中出現的名單公司（同一公司只算一次）。"""
    hits = []
    for e in ENTRIES:
        if any(alias in title for alias in e["aliases"]):
            hits.append(e)
    return hits


def sentiment_label(score, bull=60, bear=40):
    if score >= bull:
        return "利多"
    if score <= bear:
        return "利空"
    return "中性"


def classify_news(df_news, bull=60, bear=40):
    """
    輸入含「新聞標題」「分數」欄位的 DataFrame。
    回傳 (tagged 明細, by_company 公司彙總, by_sector 次產業×供應鏈位置淨多空矩陣, untagged 未歸類新聞)。
    """
    rows, untagged_rows = [], []
    for _, r in df_news.iterrows():
        title = r["新聞標題"]
        hits = match_companies(title)
        if not hits:
            untagged_rows.append(r.to_dict())
            continue
        label = sentiment_label(r["分數"], bull, bear)
        value = {"利多": 1, "利空": -1, "中性": 0}[label]
        for h in hits:
            rows.append({
                "新聞標題": title,
                "來源與日期": r.get("來源與日期", ""),
                "榜單": r.get("榜單", ""),
                "分數": r["分數"],
                "情緒": label,
                "多空值": value,
                "公司": h["公司"],
                "代號": h["代號"],
                "次產業": h["次產業"],
                "供應鏈位置": h["供應鏈位置"],
                "細分角色": h["細分角色"],
            })

    tagged = pd.DataFrame(rows)
    untagged = pd.DataFrame(untagged_rows)
    if tagged.empty:
        return tagged, pd.DataFrame(), pd.DataFrame(), untagged

    by_company = (
        tagged.groupby(["次產業", "供應鏈位置", "細分角色", "公司", "代號"])
        .agg(
            新聞數=("新聞標題", "count"),
            利多=("情緒", lambda s: int((s == "利多").sum())),
            利空=("情緒", lambda s: int((s == "利空").sum())),
            平均分數=("分數", "mean"),
        )
        .reset_index()
    )
    by_company["淨多空"] = by_company["利多"] - by_company["利空"]
    by_company["平均分數"] = by_company["平均分數"].round(1)
    by_company = by_company.sort_values(["次產業", "供應鏈位置", "淨多空"], ascending=[True, True, False])

    by_sector = tagged.pivot_table(
        index="次產業", columns="供應鏈位置", values="多空值", aggfunc="sum", fill_value=0
    )
    by_sector = by_sector.reindex(columns=[c for c in CHAIN_ORDER if c in by_sector.columns])
    by_sector["合計"] = by_sector.sum(axis=1)
    return tagged, by_company.reset_index(drop=True), by_sector, untagged


def render_news_classification(st, top_10, bottom_10, news_all):
    """在 Streamlit 第一階段頁面呼叫此函式即可顯示歸類結果。"""
    st.subheader("🏭 新聞標的與產業位置歸類")
    scope = st.radio(
        "分析範圍", ["僅篩選出的前 / 後 10 則", "全部抓取新聞"],
        horizontal=True, key="news_class_scope",
    )
    if scope.startswith("僅"):
        src = pd.concat(
            [top_10.assign(榜單="利多榜"), bottom_10.assign(榜單="利空榜")], ignore_index=True
        ).drop_duplicates(subset="新聞標題")
    else:
        src = news_all

    c1, c2 = st.columns(2)
    bull = c1.slider("利多門檻（分數 ≥）", 50, 90, 60)
    bear = c2.slider("利空門檻（分數 ≤）", 10, 50, 40)

    tagged, by_company, by_sector, untagged = classify_news(src, bull, bear)
    covered = src["新聞標題"].nunique() - (untagged["新聞標題"].nunique() if not untagged.empty else 0)
    st.caption(f"{covered} / {src['新聞標題'].nunique()} 則新聞對應到名單內公司")

    if tagged.empty:
        st.info("這批新聞沒有出現名單內的公司。")
        return

    t1, t2, t3, t4 = st.tabs(["公司歸類", "次產業 × 供應鏈位置", "新聞明細", "未歸類新聞"])
    with t1:
        st.dataframe(by_company, use_container_width=True, hide_index=True)
    with t2:
        st.caption("數字為利多則數減利空則數（淨多空），可看出熱度集中在供應鏈哪一段。")
        st.dataframe(by_sector, use_container_width=True)
    with t3:
        st.dataframe(tagged, use_container_width=True, hide_index=True)
        st.download_button(
            "下載歸類明細 CSV",
            tagged.to_csv(index=False).encode("utf-8-sig"),
            file_name="news_tagged.csv",
            mime="text/csv",
        )
    with t4:
        st.caption("這些新聞沒有命中名單，可從中找出需要補進字典的公司。")
        st.dataframe(untagged, use_container_width=True, hide_index=True)
