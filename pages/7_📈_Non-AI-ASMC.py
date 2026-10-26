import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import os

# 1. 定義 AI-TSMC-50 核心 55 檔清單與字首
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

# 2. 讀取全市場標的 CSV
df_all = pd.read_csv('taiwan_extra_universe.csv')
df_all['Code'] = df_all['Code'].astype(str).str.zfill(4)

# 3. 讀取外部完整公司名稱對照表（stock_names.csv）
mapping_filename = 'stock_names.csv'
if not os.path.exists(mapping_filename):
    default_mapping = pd.DataFrame({
        'Code': ['3013', '3491', '2363', '6613', '6270', '3176', '8048', '8092', '6423', '2433',
                 '2453', '4905', '6140', '6569', '3168', '4951', '8261', '1708', '2462', '4736'],
        'Name': ['晟銘電', '昇達科技', '矽統', '朋億*', '台汽電', '基亞', '德勝', '建暐', '億而得', '互盛電',
                 '凌群', '台聯電', '訊達', '睿生光電', '保銳', '友輝', '富鼎', '東鹼', '良維', '泰博']
    })
    default_mapping.to_csv(mapping_filename, index=False, encoding='utf-8-sig')

df_names = pd.read_csv(mapping_filename)
df_names['Code'] = df_names['Code'].astype(str).str.zfill(4)
name_mapping = dict(zip(df_names['Code'], df_names['Name']))

# 4. 扣除核心 55 檔，篩選同儕衛星標的（前兩碼相同）
satellite_codes = df_all[df_all['Code'].str[:2].isin(core_prefixes) & (~df_all['Code'].isin(core_set))]['Code'].tolist()

# 5. 模擬真實財務與量化因子資料
np.random.seed(42)
n_samples = len(satellite_codes)

sample_names = [name_mapping.get(code, f"上市櫃企業_{code}") for code in satellite_codes]

rev_growth = np.random.normal(12.5, 18.0, n_samples)
dsi = np.random.uniform(25.0, 110.0, n_samples)
fat = np.random.uniform(0.5, 2.2, n_samples)
prices = np.random.uniform(15.0, 450.0, n_samples)
returns_60d = np.random.normal(5.0, 20.0, n_samples)

df_sat = pd.DataFrame({
    'Ticker': satellite_codes,
    'Name': sample_names,
    'Prefix': [c[:2] for c in satellite_codes],
    'Price': np.round(prices, 2),
    'Price_Date': '2026-10-26',
    'Rev_Growth_YoY_%': np.round(rev_growth, 2),
    'DSI_Days': np.round(dsi, 1),
    'FAT': np.round(fat, 2),
    'Return_60D_%': np.round(returns_60d, 2)
})

# 6. 特徵標準化與 K-Means AI 分群 (10 群)
scaler = StandardScaler()
X_raw = df_sat[['Rev_Growth_YoY_%', 'DSI_Days', 'FAT', 'Return_60D_%']].copy()
X_raw['DSI_Days'] = -X_raw['DSI_Days']
X_scaled = scaler.fit_transform(X_raw)

kmeans = KMeans(n_clusters=10, random_state=42, n_init=10)
df_sat['Cluster_ID'] = kmeans.fit_predict(X_scaled) + 1

# 7. 計算綜合量化得分
weights = [0.35, 0.25, 0.20, 0.20]
df_sat['Quant_Score'] = np.dot(X_scaled, weights)
min_s, max_s = df_sat['Quant_Score'].min(), df_sat['Quant_Score'].max()
df_sat['Final_Score'] = np.round(60 + (df_sat['Quant_Score'] - min_s) / (max_s - min_s + 1e-12) * 40, 2)

# 8. 動態自動尋找綜合量化平均得分最高的前兩個最強勢集群
cluster_avg_scores = df_sat.groupby('Cluster_ID')['Final_Score'].mean().sort_values(ascending=False)
top_two_clusters = cluster_avg_scores.head(2).index.tolist()
cluster_1, cluster_2 = top_two_clusters[0], top_two_clusters[1]

print(f"自動識別最強勢的兩個核心集群為: 群組 {cluster_1} 與 群組 {cluster_2}")

# 9. 萃取這兩個最強勢集群中得分最高的前 2 名標的
c1_top2 = df_sat[df_sat['Cluster_ID'] == cluster_1].sort_values(by='Final_Score', ascending=False).head(2)
c2_top2 = df_sat[df_sat['Cluster_ID'] == cluster_2].sort_values(by='Final_Score', ascending=False).head(2)

# 10. 輸出專業格式 Excel 報表
wb = openpyxl.Workbook()
default_sheet = wb.active

ws_c1 = wb.create_sheet(title=f"最強勢群組{cluster_1}_前2名")
ws_c2 = wb.create_sheet(title=f"次強勢群組{cluster_2}_前2名")

headers = ['排名', '股票代號', '企業名稱', '產業字首', '即期股價', '報價日期', '營收年增率YoY(%)', '存貨周轉天數DSI', '資產周轉率FAT', '60日報酬率(%)', '量化綜合得分']

ws_c1.append(headers)
for idx, row in c1_top2.reset_index(drop=True).iterrows():
    ws_c1.append([
        idx + 1, row['Ticker'], row['Name'], row['Prefix'], row['Price'], row['Price_Date'],
        row['Rev_Growth_YoY_%'], row['DSI_Days'], row['FAT'], row['Return_60D_%'], row['Final_Score']
    ])

ws_c2.append(headers)
for idx, row in c2_top2.reset_index(drop=True).iterrows():
    ws_c2.append([
        idx + 1, row['Ticker'], row['Name'], row['Prefix'], row['Price'], row['Price_Date'],
        row['Rev_Growth_YoY_%'], row['DSI_Days'], row['FAT'], row['Return_60D_%'], row['Final_Score']
    ])

if default_sheet in wb.worksheets:
    wb.remove(default_sheet)

# 樣式設定
header_fill = PatternFill(start_color='0F172A', end_color='0F172A', fill_type='solid')
header_font = Font(name='Segoe UI', size=11, bold=True, color='FFFFFF')
cell_font = Font(name='Segoe UI', size=10)
border_thin = Border(left=Side(style='thin', color='CBD5E1'), right=Side(style='thin', color='CBD5E1'), top=Side(style='thin', color='CBD5E1'), bottom=Side(style='thin', color='CBD5E1'))

for ws in [ws_c1, ws_c2]:
    ws.freeze_panes = 'A2'
    for col_num in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = border_thin
        
    for row_num in range(2, ws.max_row + 1):
        for col_num in range(1, ws.max_column + 1):
            cell = ws.cell(row=row_num, column=col_num)
            cell.font = cell_font
            cell.border = border_thin
            if col_num in [1, 2, 4, 6]:
                cell.alignment = Alignment(horizontal='center', vertical='center')
            elif col_num == 3:
                cell.alignment = Alignment(horizontal='left', vertical='center')
            else:
                cell.alignment = Alignment(horizontal='right', vertical='center')

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

output_filename = "dynamic_top_clusters_csv_mapping.xlsx"
wb.save(output_filename)
print(f"模組化 CSV 名稱對照程式執行完畢，Excel 報表已儲存為: {output_filename}")
