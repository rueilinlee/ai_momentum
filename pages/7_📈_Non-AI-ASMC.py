import requests
from bs4 import BeautifulSoup
import re
import streamlit as st

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_name_from_yahoo(code):
    """
    透過 Yahoo 股市網頁標題動態解析台股公司中文名稱
    """
    for suffix in ['.TW', '.TWO']:
        url = f"https://tw.stock.yahoo.com/quote/{code}{suffix}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            res = requests.get(url, headers=headers, timeout=3)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                # Yahoo 股市通常會把公司名稱放在 <title> 標籤中，格式如: "億而得(6423) 股票走勢圖..."
                title_tag = soup.find('title')
                if title_tag:
                    title_text = title_tag.text
                    # 使用正規表達式抓取括號前的中文公司名稱
                    match = re.search(r'^([^(]+)\(' + str(code), title_text)
                    if match:
                        chinese_name = match.group(1).strip()
                        if chinese_name:
                            return chinese_name
        except Exception:
            continue
            
    return None
