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

# --- FUNGSI INDIKATOR MANUAL (SAMA PERSIS DENGAN APP.PY) ---
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

def calculate_fib_levels(df):
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

# --- ANALISA PER ASET ---
def analyze_one_asset(asset_name, ticker):
    print(f"🔍 Analyzing {asset_name}...")
    
    df, kurs = get_data_engine(ticker)
    if df.empty: return None, "ERROR"

    # Hitung Indikator
    df = add_manual_indicators(df)
    fib_levels = calculate_fib_levels(df)
    
    # Ambil Data Terakhir
    last = df.iloc[-1]
    
    # --- LOGIKA INDIKATOR ---
    # Stoch RSI
    stoch_k = last['STOCHRSIk']
    stoch_d = last['STOCHRSId']
    if stoch_k < 20 and stoch_k > stoch_d: res_stoch = ("🟢 BULLISH", "Golden Cross")
    elif stoch_k > 80 and stoch_k < stoch_d: res_stoch = ("🔴 BEARISH", "Death Cross")
    elif stoch_k < 20: res_stoch = ("⚪ WAIT", "Oversold")
    else: res_stoch = ("⚪ NEUTRAL", f"{stoch_k:.1f}")
    
    # Bollinger
    if last['Close'] <= last['BBL']: res_bb = ("🟢 BUY ZONE", "Lower Band")
    elif last['Close'] >= last['BBU']: res_bb = ("🔴 SELL ZONE", "Upper Band")
    else: res_bb = ("⚪ INSIDE", "Normal")
    
    # VPVR (Simple POC Estimation)
    price_bins = pd.cut(df['Close'], bins=50)
    vpvr = df.groupby(price_bins, observed=True)['Volume'].sum()
    poc = vpvr.idxmax().mid
    
    # --- LOGIKA FIBONACCI (RESTORED) ---
    current_price = last['Close']
    target_buy = fib_levels["GOLDEN POCKET (0.618)"]
    target_sell = fib_levels["RESISTANCE (High)"]
    
    fib_tolerance = current_price * 0.003 # 0.3% Toleransi visual
    dist_to_gold = current_price - target_buy

    if abs(dist_to_gold) < fib_tolerance: res_fib = ("⚠️ ALERT", "Testing Golden Pocket")
    elif dist_to_gold > 0: res_fib = ("🔴 ABOVE", "Above Support")
    else: res_fib = ("🟢 BELOW", "Discount Area")

    # --- LOGIKA DECISION ---
    decision = "WAIT / HOLD"
    decision_tolerance = current_price * 0.002 # 0.2% Toleransi Sinyal

    if (res_stoch[0] == "🟢 BULLISH") and (current_price <= target_buy + decision_tolerance):
        decision = "🔵 BUY / LONG"
    elif (res_bb[0] == "🟢 BUY ZONE") and (res_stoch[0] == "🟢 BULLISH"):
        decision = "🔵 BUY / SCALP"
    elif (res_stoch[0] == "🔴 BEARISH") and (current_price >= target_sell - decision_tolerance):
        decision = "🟠 SELL / TAKE PROFIT"
    elif current_price < (target_buy - (decision_tolerance * 2)):
        decision = "🛑 CUT LOSS / STOP BUY"

    # --- BUILD REPORT (Hanya jika sinyal penting) ---
    now = datetime.now(pytz.timezone('Asia/Jakarta'))
    
    report = f"""🦅 {asset_name} ALERT
📅 {now.strftime('%H:%M WIB')}
========================
🎯 DECISION: {decision}
========================
💎 USD: {fmt_usd(current_price)}
🇮🇩 IDR: {fmt_idr(current_price * kurs * SPREAD_AJAIB)}

📊 Indikator:
1. Stoch: {res_stoch[0]} ({res_stoch[1]})
2. BB: {res_bb[0]}
3. Fib Status: {res_fib[0]} ({res_fib[1]})
4. POC Area: ${poc:.2f}

📍 Mapping Area:
• Sell: {fmt_usd(target_sell)}
• Buy: {fmt_usd(target_buy)}
"""
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
            report_text, status = analyze_one_asset(name, ticker)
            
            if status == "ERROR":
                continue
                
            print(f"   👉 {name}: {status}")

            # Filter Kirim Telegram
            # Cuma kirim kalau BUY, SELL, CUT LOSS, atau ALERT Fibonacci
            if "BUY" in status or "SELL" in status or "CUT LOSS" in status or "ALERT" in report_text:
                print(f"🚀 MENGIRIM ALERT {name}...")
                send_telegram(TOKEN, CHAT_ID, report_text)
                time.sleep(2) # Jeda biar ga spamming
            else:
                pass # Sideways, diem aja
                
        except Exception as e:
            print(f"❌ Error pada {name}: {e}")
            
    print("✅ Selesai Scan Semua Aset.")
