import os
import time
import yfinance as yf
import pandas as pd
import requests
from datetime import datetime
import pytz

# --- KONFIGURASI ASET ---
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
SPREAD_AJAIB = 1.015 

# --- HELPER FORMATTING ---
def fmt_idr(val): return f"Rp {val:,.0f}".replace(",", ".")
def fmt_usd(val): return f"${val:,.2f}"

# --- FUNGSI INDIKATOR MANUAL (COPY DARI APP.PY) ---
def add_manual_indicators(df):
    df = df.copy()
    
    # 1. MACD
    k = df['Close'].ewm(span=12, adjust=False, min_periods=12).mean()
    d = df['Close'].ewm(span=26, adjust=False, min_periods=26).mean()
    df['MACD'] = k - d
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False, min_periods=9).mean()
    
    # 2. Bollinger Bands
    df['SMA20'] = df['Close'].rolling(window=20).mean()
    df['STD20'] = df['Close'].rolling(window=20).std()
    df['BBU'] = df['SMA20'] + (df['STD20'] * 2)
    df['BBL'] = df['SMA20'] - (df['STD20'] * 2)
    
    # 3. Stochastic RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    min_rsi = df['RSI'].rolling(window=14).min()
    max_rsi = df['RSI'].rolling(window=14).max()
    stoch_rsi = (df['RSI'] - min_rsi) / (max_rsi - min_rsi)
    df['STOCHRSIk'] = stoch_rsi.rolling(window=3).mean() * 100
    df['STOCHRSId'] = df['STOCHRSIk'].rolling(window=3).mean()
    
    return df

# --- GET DATA ENGINE (DYNAMIC & CALIBRATED) ---
def get_data_engine(ticker_code):
    try:
        tickers_to_fetch = [ticker_code, "IDR=X"]
        df = yf.download(tickers_to_fetch, period=PERIOD, interval=INTERVAL, group_by='ticker', progress=False, threads=False)
        
        # 1. Ambil Data Aset Utama
        if isinstance(df.columns, pd.MultiIndex):
            main_data = df[ticker_code].dropna()
        else:
            main_data = df # Fallback

        # 🔥 LOGIKA KALIBRASI KHUSUS 🔥
        if ticker_code == "PAXG-USD":
            main_data = main_data * 0.99048968
        
        # 2. Ambil Kurs IDR
        if isinstance(df.columns, pd.MultiIndex):
            kurs_raw = df['IDR=X']['Close'].dropna()
        else:
            kurs_raw = pd.Series([16800])
        
        kurs = kurs_raw.iloc[-1] if not kurs_raw.empty else 16800
        
        return main_data, kurs

    except Exception as e:
        print(f"❌ Error fetching {ticker_code}: {e}")
        return pd.DataFrame(), 16800

def calculate_fibonacci_levels(df):
    if df.empty: return {}
    high = df['High'].max()
    low = df['Low'].min()
    diff = high - low
    return {
        "MOONBAG (1.618)": high + (diff * 0.618),
        "RESISTANCE (High)": high,
        "GOLDEN POCKET (0.618)": high - (diff * 0.618),
        "FLOOR (Low)": low
    }

def send_telegram(token, chat_id, message):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.get(url, params={"chat_id": chat_id, "text": message})
        print("✅ Pesan Terkirim!")
    except Exception as e:
        print(f"❌ Gagal Kirim: {e}")

# --- ANALISA & GENERATE REPORT (LOGIC DARI APP.PY) ---
def generate_analysis_report(df, kurs, asset_name):
    if df.empty: return None, "WAIT / HOLD"

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
    
    # REPORT GENERATOR (FORMAT SAMA PERSIS APP.PY)
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

    return report, decision

# --- MAIN LOOP ---
if __name__ == "__main__":
    print("🤖 MARKET SNIPER STARTED...")
    try:
        TOKEN = os.environ["TELEGRAM_TOKEN"]
        CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
    except:
        print("❌ Secret Token Hilang!")
        exit()

    # Loop semua aset di daftar
    for name, ticker in ASSETS.items():
        try:
            print(f"🔍 Analyzing {name}...")
            main_df, kurs_val = get_data_engine(ticker)
            
            if main_df.empty:
                print(f"❌ Data {name} Kosong.")
                continue

            report_text, decision = generate_analysis_report(main_df, kurs_val, name)
            
            print(f"   👉 Result: {decision}")

            # Filter Kirim Telegram (Cuma Kirim Jika Penting)
            # Kalau mau debug kirim semua, hapus if-nya.
            # if "BUY" in decision or "SELL" in decision or "CUT LOSS" in decision:
            print(f"🚀 MENGIRIM ALERT {name}...")
            send_telegram(TOKEN, CHAT_ID, report_text)
            time.sleep(2) # Jeda biar ga spamming
            # else:
            #     pass # Sideways, diem aja
                
        except Exception as e:
            print(f"❌ Error pada {name}: {e}")
            
    print("✅ Selesai Scan Semua Aset.")
