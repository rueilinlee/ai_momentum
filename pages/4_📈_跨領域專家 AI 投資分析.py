import urllib.request
import os
from fpdf import FPDF
import tempfile
from datetime import datetime

# ==========================================
# 新增：自動下載 Google 開源中文字型函數
# ==========================================
def get_chinese_font():
    font_path = "NotoSansTC-Regular.ttf"
    # 若雲端環境沒有字型，就自動從 Google Fonts 下載
    if not os.path.exists(font_path):
        font_url = "https://github.com/google/fonts/raw/main/ofl/notosanstc/NotoSansTC-Regular.ttf"
        urllib.request.urlretrieve(font_url, font_path)
    return font_path

# ==========================================
# 2. PDF 完整報告生成函數 (已修正字型載入)
# ==========================================
def generate_pdf_report(data_dict, valuation_dict):
    pdf = FPDF()
    pdf.add_page()
    
    # 獲取並註冊中文字型
    font_path = get_chinese_font()
    pdf.add_font("ChineseFont", "", font_path)
    
    # 設定大標題字型
    pdf.set_font("ChineseFont", size=16)
    
    # 標題與基本資料
    pdf.cell(200, 10, txt=f"跨領域專家 AI 投資分析報告 - {data_dict['symbol']}", ln=True, align='C')
    pdf.set_font("ChineseFont", size=10)
    pdf.cell(200, 8, txt=f"報告生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ln=True, align='C')
    pdf.ln(5)
    
    # 核心數據區塊
    pdf.set_font("ChineseFont", size=12)
    pdf.cell(200, 8, txt=f"【最新收盤價】 ${data_dict['price']:,.2f} (漲跌幅 {data_dict['change']:.2f}%)", ln=True)
    pdf.cell(200, 8, txt=f"【綜合投資評等】 {valuation_dict['rec']} (目標價區間: ${valuation_dict['tp_lower']:,.0f} ~ ${valuation_dict['tp_upper']:,.0f})", ln=True)
    pdf.cell(200, 8, txt=f"【基準目標價】 ${valuation_dict['tp_base']:,.0f} (潛在空間 {valuation_dict['upside_base']:.1f}%)", ln=True)
    pdf.ln(5)
    
    # 四大專家視角
    sections = [
        ("一、 產業專家視角", f"技術動態：{data_dict['ind_cowos']}\n市場護城河：{data_dict['ind_nodes']}"),
        ("二、 數學家視角 (量化模型)", f"PE目標 = 基準({data_dict['pe_base']}) + 聲量({valuation_dict['delta_sentiment']:.1f}) + 成長({data_dict['growth']}) - 風險({data_dict['risk']}) = {valuation_dict['pe_target']:.1f}x\n目標價推算 = 遠期EPS({data_dict['eps_fwd']}) x 目標PE = ${valuation_dict['tp_base']:,.0f}"),
        ("三、 財金專家視角", f"近四季 TTM EPS：${data_dict['fin_ttm']}\n歷史 P/E：{valuation_dict['historical_pe']:.1f}x | 遠期 P/E：{valuation_dict['forward_pe']:.1f}x"),
        ("四、 經濟專家與風險陣列", f"宏觀動能：{data_dict['macro']}")
    ]
    
    for title, content in sections:
        pdf.set_font("ChineseFont", size=14)
        pdf.cell(200, 10, txt=title, ln=True)
        pdf.set_font("ChineseFont", size=11)
        pdf.multi_cell(0, 8, txt=content)
        pdf.ln(3)

    # 匯出暫存檔
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp_file.name)
    return tmp_file.name
