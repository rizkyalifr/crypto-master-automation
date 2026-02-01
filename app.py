import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
from datetime import datetime
import pytz

# --- KONFIGURASI HALAMAN ---
st.set_page_config(page_title="Market Sniper Automation", page_icon="🦅", layout="wide")

# --- CSS FIX ---
st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 24px; }
    .report-text { 
        font-family: 'Courier New', monospace; 
        white-space: pre-wrap; 
        background-color: #f0f2f6; 
        padding: 15px; 
        border-radius: 10px; 
        color: #000000; 
        border: 1px solid #ccc;
    }
</style>
""", unsafe_allow_html=True)

# --- KONFIGURASI ASET (MENU PILIHAN) ---
ASSETS = {
    "GOLD (PAXG)": "PAXG-USD",
    "BITCOIN (BTC)": "BTC-USD",
    "ETHEREUM (ETH)": "ETH-USD",
    "SOLANA (SOL)": "SOL-USD",
    "RIPPLE (XRP)": "XRP-USD"
}

# --- KONFIGURASI ENGINE ---
INTERVAL = "1h"
PERIOD = "1mo"
SPREAD_AJAIB = 1.015 # Estimasi Spread Exchange Lokal

# --- HELPER FORMATTING ---
def fmt_idr(val): return f"Rp {val:,.0f}".replace(",", ".")
def fmt_usd(val): return f"${val:,.2f}"

# --- FUNGSI INDIKATOR MANUAL ---
def add_manual_indicators(df):
    df = df.copy()
    
    # 1. MACD (12, 26, 9)
    k = df['Close'].ewm(span=12, adjust=False, min_periods=12).mean()
    d = df['Close'].ewm(span=26, adjust=False, min_periods=26).mean()
    df['MACD'] = k - d
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False, min_periods=9).mean()
    
    # 2. Bollinger Bands (20, 2)
    df['SMA20'] = df['Close'].rolling(window=20).mean()
    df['STD20'] = df['Close'].rolling(window=20).std()
    df['BBU'] = df['SMA20'] + (df['STD20'] * 2) # Upper
    df['BBL'] = df['SMA20'] - (df['STD20'] * 2) # Lower
    
    # 3. Stochastic RSI (14, 14, 3, 3)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # Hitung Stoch
    min_rsi = df['RSI'].rolling(window=14).min()
    max_rsi = df['RSI'].rolling(window=14).max()
    stoch_rsi = (df['RSI'] - min_rsi) / (max_rsi - min_rsi)
    df['STOCHRSIk'] = stoch_rsi.rolling(window=3).mean() * 100
    df['STOCHRSId'] = df['STOCHRSIk'].rolling(window=3).mean()
    
    return df

# --- FUNGSI GET DATA (DYNAMIC) ---
@st.cache_data(ttl=300)
def get_data_engine(ticker_code):
    # Download Ticker Pilihan + IDR
    tickers_to_fetch = [ticker_code, "IDR=X"]
    df = yf.download(tickers_to_fetch, period=PERIOD, interval=INTERVAL, group_by='ticker', progress=False, threads=False)
    
    try:
        # 1. Ambil Data Aset Utama
        if isinstance(df.columns, pd.MultiIndex):
            main_data = df[ticker_code].dropna()
        else:
            main_data = df # Fallback
            
        # 🔥 LOGIKA KALIBRASI KHUSUS 🔥
        # Kalau PAXG, pake multiplier. Kalau Crypto lain, RAW PRICE.
        if ticker_code == "PAXG-USD":
            main_data = main_data * 0.99048968
        
        # 2. Ambil Kurs IDR
        if isinstance(df.columns, pd.MultiIndex):
            kurs_raw = df['IDR=X']['Close'].dropna()
        else:
             kurs_raw = pd.Series([16800])

        kurs = kurs_raw.iloc[-1] if not kurs_raw.empty else 16800

    except Exception as e:
        st.error(f"Error Data Fetching: {e}")
        return pd.DataFrame(), 16800
        
    if kurs < 10000: kurs = 16800
    return main_data, kurs

def calculate_fibonacci_levels(df):
    if df.empty: return {}
    high = df['High'].max()
    low = df['Low'].min()
    diff = high - low
    levels = {
        "MOONBAG (1.618)": high + (diff * 0.618),
        "RESISTANCE (High)": high,
        "GOLDEN POCKET (0.618)": high - (diff * 0.618),
        "FLOOR (Low)": low
    }
    return levels

def send_telegram_alert(token, chat_id, message):
    if not token or not chat_id: return False, "Token/ID Kosong"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    params = {"chat_id": chat_id, "text": message}
    try:
        r = requests.get(url, params=params)
        return (True, "Sukses") if r.status_code == 200 else (False, r.text)
    except Exception as e:
        return False, str(e)

# --- LOGIC ANALYSIS & REPORT ---
def generate_analysis_report(df, kurs, asset_name):
    if df.empty: return "Data Kosong/Error", df, {}, df.iloc[-1] if not df.empty else None

    # Analisa Indikator
    df = add_manual_indicators(df)
    
    # VPVR Logic
    price_bins = pd.cut(df['Close'], bins=50)
    vpvr = df.groupby(price_bins, observed=True)['Volume'].sum()
    poc = vpvr.idxmax().mid
    
    fib_levels = calculate_fibonacci_levels(df)
    last_row = df.iloc[-1]
    
    # Indikator Status
    stoch_k = last_row['STOCHRSIk']
    stoch_d = last_row['STOCHRSId']
    
    if stoch_k < 20 and stoch_k > stoch_d: res_stoch = ("🟢 BULLISH", "Golden Cross")
    elif stoch_k > 80 and stoch_k < stoch_d: res_stoch = ("🔴 BEARISH", "Death Cross")
    elif stoch_k < 20: res_stoch = ("⚪ WAIT", "Oversold")
    else: res_stoch = ("⚪ NEUTRAL", f"{stoch_k:.1f}")
    
    # MACD
    if last_row['MACD'] > last_row['MACD_Signal']: res_macd = ("🟢 BULLISH", "Trend Naik")
    else: res_macd = ("🔴 BEARISH", "Trend Turun")
    
    # VPVR
    if last_row['Close'] > poc: res_vpvr = ("🟢 STRONG", "Above POC")
    else: res_vpvr = ("🔴 WEAK", "Below POC")
    
    # Bollinger
    if last_row['Close'] <= last_row['BBL']: res_bb = ("🟢 BUY ZONE", "Lower Band")
    elif last_row['Close'] >= last_row['BBU']: res_bb = ("🔴 SELL ZONE", "Upper Band")
    else: res_bb = ("⚪ INSIDE", "Normal")

    # >>> 🔥 RESTORE FIBONACCI LOGIC 🔥 <<<
    current_price = last_row['Close']
    target_buy = fib_levels["GOLDEN POCKET (0.618)"]
    target_sell = fib_levels["RESISTANCE (High)"]
    
    # Toleransi dinamis (0.3% dari harga) biar cocok buat BTC/XRP/Gold
    fib_tolerance = current_price * 0.003 
    dist_to_gold = current_price - target_buy

    if abs(dist_to_gold) < fib_tolerance: 
        res_fib = ("⚠️ ALERT", "Testing Golden Pocket")
    elif dist_to_gold > 0: 
        res_fib = ("🔴 ABOVE", "Above Support")
    else: 
        res_fib = ("🟢 BELOW", "Discount Area")
    # >>> END RESTORE <<<
    
    decision = "WAIT / HOLD"
    validation = "Market sideways."
    
    # LOGIKA PENGAMBILAN KEPUTUSAN (Toleransi 0.2%)
    decision_tolerance = current_price * 0.002 

    if (res_stoch[0] == "🟢 BULLISH") and (current_price <= target_buy + decision_tolerance):
        decision = "🔵 BUY / LONG"
        validation = "✅ VALIDATED: Rebound Golden Pocket + Stoch Cross Up."
    elif (res_bb[0] == "🟢 BUY ZONE") and (res_stoch[0] == "🟢 BULLISH"):
        decision = "🔵 BUY / SCALP"
        validation = "✅ VALIDATED: Pantulan Lower BB + Momentum."
    elif (res_stoch[0] == "🔴 BEARISH") and (current_price >= target_sell - decision_tolerance):
        decision = "🟠 SELL / TAKE PROFIT"
        validation = "✅ VALIDATED: Rejection Resistance + Stoch Cross Down."
    elif current_price < (target_buy - (decision_tolerance * 2)):
        decision = "🛑 CUT LOSS / STOP BUY"
        validation = "⚠️ INVALID: Jebol Support Kuat."

    now = datetime.now(pytz.timezone('Asia/Jakarta'))
    
    # REPORT GENERATOR
    report = f"""🦅 {asset_name} SNIPER AUTOMATION
📅 Waktu: {now.strftime('%d %b %Y | %H:%M WIB')}
============================================================

💰 UPDATE HARGA ({asset_name})
💵 KURS USD/IDR : {fmt_idr(kurs)}
------------------------------------------------------------
💎 PRICE USD     : {fmt_usd(current_price)}
💎 PRICE IDR     : {fmt_idr(current_price * kurs)}
   *(Est. Exchange Lokal: {fmt_idr(current_price * kurs * SPREAD_AJAIB)})*
------------------------------------------------------------

📊 HASIL ANALISIS (5 METODE)
1. Stoch RSI   [{res_stoch[0]}] : {res_stoch[1]}
2. MACD        [{res_macd[0]}] : {res_macd[1]}
3. VPVR POC    [{res_vpvr[0]}] : {res_vpvr[1]} (Area ${poc:.2f})
4. Bollinger   [{res_bb[0]}] : {res_bb[1]}
5. Fibonacci   [{res_fib[0]}] : {res_fib[1]}

============================================================
🧠 ENSEMBLE DECISION : [ {decision} ]
🔐 VALIDATED BY      : {validation}
============================================================

🎯 MAPPING AREA TERDEKAT
"""
    levels_sorted = ["MOONBAG (1.618)", "RESISTANCE (High)", "GOLDEN POCKET (0.618)", "FLOOR (Low)"]
    for name in levels_sorted:
        val_usd = fib_levels[name]
        val_idr = val_usd * kurs * SPREAD_AJAIB
        report += f"\n📍 LEVEL: {name}"
        report += f"\n   • USD : {fmt_usd(val_usd)}"
        report += f"\n   • IDR : {fmt_idr(val_idr)}"
        
        if "MOONBAG" in name: report += "\n   👉 [TARGET] TP 2 / Jual Semua."
        elif "RESISTANCE" in name: report += "\n   👉 [UJI NYALI] Breakout=Moonbag."
        elif "GOLDEN POCKET" in name: report += "\n   👉 [BUY ZONE] Mantul=Buy."
        elif "FLOOR" in name: report += "\n   👉 [BAHAYA] Pertahanan Terakhir."
        report += "\n"

    return report, df, fib_levels, last_row

# --- SIDEBAR (INPUT & MENU) ---
st.sidebar.title("🦅 Market Sniper")

# MENU PILIH ASET
selected_asset_name = st.sidebar.selectbox(
    "Pilih Aset Analisa:",
    list(ASSETS.keys())
)
selected_ticker = ASSETS[selected_asset_name]

st.sidebar.markdown("---")
st.sidebar.header("⚙️ Konfigurasi Bot")

if "TELEGRAM_TOKEN" in st.secrets:
    bot_token = st.secrets["TELEGRAM_TOKEN"]
    chat_id = st.secrets["TELEGRAM_CHAT_ID"]
    st.sidebar.success("✅ Login via Secrets")
else:
    bot_token = st.sidebar.text_input("Bot Token", type="password")
    chat_id = st.sidebar.text_input("Chat ID")

# --- MAIN APP LOGIC ---
st.title(f"🦅 {selected_asset_name} Automation")

with st.spinner(f"Sedang Menganalisis {selected_asset_name}..."):
    # Panggil Engine dengan Ticker Pilihan
    main_df, kurs_val = get_data_engine(selected_ticker)
    
    if main_df.empty:
        st.error(f"Gagal mengambil data {selected_asset_name}. Coba refresh.")
    else:
        # Generate Report dengan parameter dinamis
        final_report, df_processed, fib_levels, last_row = generate_analysis_report(main_df, kurs_val, selected_asset_name)

        # --- SIDEBAR METRICS ---
        st.sidebar.markdown("---")
        st.sidebar.header("💰 Live Price")
        st.sidebar.metric("Kurs USD/IDR", fmt_idr(kurs_val))
        
        est_local = last_row['Close'] * kurs_val * SPREAD_AJAIB
        st.sidebar.metric(f"{selected_asset_name.split()[0]}/IDR (Est)", fmt_idr(est_local))
        st.sidebar.metric(f"{selected_asset_name.split()[0]}/USD", fmt_usd(last_row['Close']))

        # --- CHART ---
        st.subheader(f"📊 Chart {selected_asset_name} + Fibonacci")
        fig = go.Figure(data=[go.Candlestick(x=df_processed.index,
                        open=df_processed['Open'], high=df_processed['High'],
                        low=df_processed['Low'], close=df_processed['Close'],
                        name=selected_asset_name)])
        
        colors = {"MOONBAG": "lime", "RESISTANCE": "red", "GOLDEN POCKET": "gold", "FLOOR": "white"}
        for label, val in fib_levels.items():
            c = "gray"
            for k, v in colors.items():
                if k in label: c = v
            fig.add_hline(y=val, line_dash="dash", line_color=c, 
                          annotation_text=f"{label} : ${val:.2f}", 
                          annotation_position="top right")
        
        fig.update_layout(template="plotly_dark", height=600, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

        # --- REPORT SECTION ---
        st.subheader("📋 Laporan Analisis Lengkap")
        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button(f"📩 Kirim {selected_asset_name} ke Tele"):
                success, msg = send_telegram_alert(bot_token, chat_id, final_report)
                if success: st.success("Terkirim!")
                else: st.error(f"Gagal: {msg}")

        st.text_area("Output Logika:", value=final_report, height=600, label_visibility="collapsed")
