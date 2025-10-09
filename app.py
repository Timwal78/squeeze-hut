from flask import Flask, jsonify, make_response
from flask_cors import CORS
import pandas as pd
import requests
from datetime import datetime
import os
from threading import Thread, Lock
from apscheduler.schedulers.background import BackgroundScheduler
import time

app = Flask(__name__)
CORS(app)

# Configuration
TICKERS = os.getenv('TICKERS', 'TSLA,AMD,GME,NVDA,IONQ').split(',')
DISCORD_WEBHOOK = os.getenv('DISCORD_WEBHOOK', '')

# Global state
squeeze_data = {}
data_lock = Lock()

def fetch_yahoo_data(symbol):
    """Fetch 1-minute data from Yahoo Finance"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {'interval': '1m', 'range': '1d'}
        headers = {'User-Agent': 'Mozilla/5.0'}
        
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        if 'chart' not in data or not data['chart']['result']:
            return None
            
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quote = result['indicators']['quote'][0]
        
        df = pd.DataFrame({
            'close': quote['close'],
            'high': quote['high'],
            'low': quote['low'],
            'volume': quote['volume']
        })
        
        return df.dropna()
    except Exception as e:
        print(f"Error fetching {symbol}: {e}")
        return None

def calculate_squeeze(df, L=20, bb=2.0, kc=1.5, vol=1.5):
    """Calculate TTM Squeeze indicator"""
    try:
        if len(df) < L + 26:
            return False, {}
        
        # Keltner Channels
        ema = df['close'].ewm(span=L, adjust=False).mean()
        atr = (df['high'] - df['low']).rolling(L).mean()
        kc_upper = ema + kc * atr
        kc_lower = ema - kc * atr
        
        # Bollinger Bands
        sma = df['close'].rolling(L).mean()
        std = df['close'].rolling(L).std()
        bb_upper = sma + bb * std
        bb_lower = sma - bb * std
        
        # Squeeze: BB inside KC
        in_squeeze = (bb_lower > kc_lower) & (bb_upper < kc_upper)
        
        # Volume surge
        vol_avg = df['volume'].rolling(L).mean()
        high_vol = df['volume'] > vol * vol_avg
        
        # MACD
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        macd_bull = macd > signal
        
        # Final signal
        squeeze_signal = in_squeeze & high_vol & macd_bull
        
        idx = -1
        return squeeze_signal.iloc[idx], {
            'price': float(df['close'].iloc[idx]),
            'volume': int(df['volume'].iloc[idx]),
            'in_squeeze': bool(in_squeeze.iloc[idx]),
            'high_volume': bool(high_vol.iloc[idx]),
            'macd_bullish': bool(macd_bull.iloc[idx]),
            'signal': bool(squeeze_signal.iloc[idx]),
            'error': False
        }
    except Exception as e:
        print(f"Calculation error: {e}")
        return False, {'error': True, 'message': str(e)}

def send_discord_alert(sym, price):
    """Send Discord webhook alert"""
    if not DISCORD_WEBHOOK:
        return
    try:
        requests.post(DISCORD_WEBHOOK, json={
            "embeds": [{
                "title": f"🔥 {sym} SQUEEZE DETECTED",
                "description": f"Price: ${price:.2f}",
                "color": 15158332
            }]
        }, timeout=5)
    except:
        pass

def scan_markets():
    """Scan all tickers for squeeze signals"""
    global squeeze_data
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scanning...")
    temp_data = {}
    
    for ticker in TICKERS:
        ticker = ticker.strip().upper()
        df = fetch_yahoo_data(ticker)
        
        if df is None or len(df) < 50:
            temp_data[ticker] = {
                'error': True,
                'message': 'No data available',
                'signal': False
            }
            continue
        
        is_squeeze, details = calculate_squeeze(df)
        details['timestamp'] = datetime.now().isoformat()
        temp_data[ticker] = details
        
        if is_squeeze:
            send_discord_alert(ticker, details['price'])
            print(f"  🔥 SQUEEZE: {ticker} @ ${details['price']:.2f}")
    
    with data_lock:
        squeeze_data = temp_data

@app.route('/')
def index():
    """Serve the dashboard"""
    return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>⚡ Squeeze Radar</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, sans-serif;
            background: linear-gradient(135deg, #0f172a, #1e293b);
            color: #e2e8f0;
            min-height: 100vh;
            padding: 2rem;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 {
            font-size: 2.5rem;
            margin-bottom: 1rem;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .controls {
            background: #1e293b;
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 2rem;
        }
        button {
            padding: 0.75rem 1.5rem;
            background: #3b82f6;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
        }
        button:hover { background: #2563eb; }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 1.5rem;
        }
        .card {
            background: #1e293b;
            padding: 1.5rem;
            border-radius: 12px;
            border: 1px solid #334155;
        }
        .card.squeeze {
            border-color: #ef4444;
            box-shadow: 0 0 30px rgba(239, 68, 68, 0.5);
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { box-shadow: 0 0 30px rgba(239, 68, 68, 0.5); }
            50% { box-shadow: 0 0 40px rgba(239, 68, 68, 0.8); }
        }
        .ticker { font-size: 1.8rem; font-weight: bold; margin-bottom: 0.5rem; }
        .status {
            display: inline-block;
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-weight: 600;
            margin-bottom: 1rem;
        }
        .status.active { background: #dc2626; color: white; }
        .status.inactive { background: #334155; color: #94a3b8; }
        .details {
            font-size: 0.85rem;
            color: #94a3b8;
            margin-top: 1rem;
            padding-top: 1rem;
            border-top: 1px solid #334155;
        }
        .row { display: flex; justify-content: space-between; margin: 0.3rem 0; }
        .indicator {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
        }
        .indicator.on { background: #10b981; }
        .indicator.off { background: #64748b; }
        .footer { text-align: center; margin-top: 2rem; color: #64748b; }
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ Squeeze Radar</h1>
        <div class="controls">
            <button onclick="load()">🔄 Refresh</button>
        </div>
        <div id="content" class="grid"></div>
        <div id="footer" class="footer"></div>
    </div>
    <script>
        async function load() {
            try {
                const r = await fetch('/api/signals');
                const data = await r.json();
                
                const items = Object.entries(data).sort((a,b) => 
                    (b[1].signal?1:0) - (a[1].signal?1:0)
                );
                
                document.getElementById('content').innerHTML = items.map(([t, d]) => {
                    if (d.error) {
                        return `<div class="card">
                            <div class="ticker">${t}</div>
                            <div class="status inactive">⚠️ Error</div>
                        </div>`;
                    }
                    
                    return `<div class="card ${d.signal?'squeeze':''}">
                        <div class="ticker">${t}</div>
                        <div class="status ${d.signal?'active':'inactive'}">
                            ${d.signal ? '🔥 SQUEEZE' : '— No Signal'}
                        </div>
                        <div class="details">
                            <div class="row"><span>Price:</span><strong>$${d.price.toFixed(2)}</strong></div>
                            <div class="row"><span>Volume:</span><span>${(d.volume/1e6).toFixed(2)}M</span></div>
                            <div style="margin-top:1rem;padding-top:1rem;border-top:1px solid #334155;">
                                <div class="row"><span>BB/KC Squeeze</span><span class="indicator ${d.in_squeeze?'on':'off'}"></span></div>
                                <div class="row"><span>Volume Surge</span><span class="indicator ${d.high_volume?'on':'off'}"></span></div>
                                <div class="row"><span>MACD Bullish</span><span class="indicator ${d.macd_bullish?'on':'off'}"></span></div>
                            </div>
                        </div>
                    </div>`;
                }).join('');
                
                document.getElementById('footer').textContent = 'Updated: ' + new Date().toLocaleTimeString();
            } catch (e) {
                console.error(e);
                document.getElementById('content').innerHTML = '<div style="text-align:center;color:#ef4444;">Error loading data</div>';
            }
        }
        
        load();
        setInterval(load, 120000);
    </script>
</body>
</html>"""

@app.route('/api/signals')
def get_signals():
    """API endpoint - returns JSON data"""
    with data_lock:
        data = dict(squeeze_data)
    
    response = make_response(jsonify(data))
    response.headers['Content-Type'] = 'application/json'
    return response

# Start background scanner
scheduler = BackgroundScheduler()
scheduler.add_job(scan_markets, 'interval', minutes=2, id='market_scan')
scheduler.start()

# Initial scan
print("Starting initial scan...")
scan_markets()
print("Initial scan complete. Scheduler running every 2 minutes.")

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
