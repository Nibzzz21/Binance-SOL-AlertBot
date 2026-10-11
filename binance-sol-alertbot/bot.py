import os
import json
import ccxt
import numpy as np
import pandas as pd
import requests

# Load credentials securely from GitHub Environment Secrets
BINANCE_API_KEY = os.getenv('BINANCE_API_KEY')
BINANCE_SECRET_KEY = os.getenv('BINANCE_SECRET_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOLS = ['SOL/USDT:USDT', 'BTC/USDT:USDT', 'ETH/USDT:USDT']
STATE_FILE = 'bot_state.json'

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram tokens missing!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    response = requests.post(url, json=payload)
    print("Telegram response:", response.status_code)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=4)

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    return true_range.rolling(window=period).mean()

def find_pivots(df, window=4):
    df['swing_high'] = df['high'][(df['high'] == df['high'].rolling(window*2+1, center=True).max())]
    df['swing_low'] = df['low'][(df['low'] == df['low'].rolling(window*2+1, center=True).min())]
    return df

def get_linear_regression_trendline(df, pivot_type='low', num_pivots=3):
    col = 'swing_low' if pivot_type == 'low' else 'swing_high'
    pivots = df[df[col].notna()].copy()
    
    if len(pivots) < num_pivots:
        return None
        
    recent_pivots = pivots.tail(num_pivots)
    x = recent_pivots.index.values.astype(float)
    y = recent_pivots[col].values.astype(float)
    
    slope, intercept = np.polyfit(x, y, 1)
    current_idx = float(len(df) - 1)
    projected_value = (slope * current_idx) + intercept
    
    return projected_value

def check_candlestick_patterns(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    body = abs(last['close'] - last['open'])
    candle_range = last['high'] - last['low']
    
    is_engulfing = (last['close'] > last['open']) and (prev['close'] < prev['open']) and \
                   (last['close'] >= prev['open']) and (last['open'] <= prev['close'])
                   
    lower_wick = min(last['open'], last['close']) - last['low']
    upper_wick = last['high'] - max(last['open'], last['close'])
    is_hammer = (lower_wick > (2 * body)) and (upper_wick < (0.5 * body)) and (candle_range > 0)
    
    higher_close = last['close'] > prev['high']
    
    return is_engulfing or is_hammer or higher_close

def get_daily_structure_bias(exchange, symbol):
    try:
        htf_data = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=35)
        df = pd.DataFrame(htf_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df = df.iloc[:-1].copy()
        
        df['sh'] = df['high'][(df['high'] > df['high'].shift(1)) & (df['high'] > df['high'].shift(-1))]
        df['sl'] = df['low'][(df['low'] < df['low'].shift(1)) & (df['low'] < df['low'].shift(-1))]
        
        highs = df['sh'].dropna().tolist()
        lows = df['sl'].dropna().tolist()
        
        if len(highs) >= 2 and len(lows) >= 2:
            if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
                return "BULLISH"
        return "BEARISH"
    except:
        return "NEUTRAL"

def run_bot():
    # Initialize Binance publicly without API keys to bypass cloud/datacenter IP blocks
    exchange = ccxt.okx({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'}
    })
    
    state = load_state()

    for symbol in SYMBOLS:
        try:
            sym_key = symbol.replace('/', '_')
            if sym_key not in state:
                state[sym_key] = {'status': 'TRACKING'}

            s_data = state[sym_key]

            # 1. Daily HTF Bias & 15m Data
            htf_bias = get_daily_structure_bias(exchange, symbol)
            ltf_data = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=70)
            df_ltf = pd.DataFrame(ltf_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # CRUCIAL: Examine ONLY the most recent fully closed candle
            df_closed = df_ltf.iloc[:-1].copy()
            df_closed['ema_16'] = df_closed['close'].ewm(span=16).mean()
            df_closed['ema_50'] = df_closed['close'].ewm(span=50).mean()
            df_closed['rsi_14'] = calculate_rsi(df_closed['close'], period=14)
            df_closed['atr'] = calculate_atr(df_closed, period=14)

            df_closed = find_pivots(df_closed, window=4)
            proj_support = get_linear_regression_trendline(df_closed, pivot_type='low', num_pivots=3)

            last_closed = df_closed.iloc[-1]
            prev_candle = df_closed.iloc[-2]

            close = last_closed['close']
            ema16 = last_closed['ema_16']
            ema50 = last_closed['ema_50']
            rsi = last_closed['rsi_14']
            atr = last_closed['atr']
            vol = last_closed['volume']
            avg_vol_prev3 = df_closed['volume'].iloc[-4:-1].mean()

            prev_high = prev_candle['high']
            prev_low = prev_candle['low']

            # Trendline POI Zone Check
            in_trendline_zone = False
            if proj_support is not None:
                tolerance = atr * 0.5
                if abs(close - proj_support) <= tolerance:
                    in_trendline_zone = True

            pattern_confirmed = check_candlestick_patterns(df_closed)
            volume_confirmed = vol > avg_vol_prev3
            is_applus_setup = in_trendline_zone and pattern_confirmed and volume_confirmed

            # Setup Quality Tier
            if close > ema50 and close > ema16:
                setup_quality = "A+ Setup 🟢 (Price above both EMAs)"
            elif (ema16 < close < ema50) or (ema50 < close < ema16):
                setup_quality = "Less Probable Setup ⚠️ (Price between EMAs)"
            else:
                setup_quality = "No Setup ❌ (Price below EMAs)"

            signal_msg = ""
            breakout_high = close > prev_high
            breakdown_low = close < prev_low

            # Build dynamic break details (Only shown if broken is True)
            break_info = ""
            if breakout_high:
                break_info += f"\n• **Prev High Broken:** `${prev_high:.2f}`"
            if breakdown_low:
                break_info += f"\n• **Prev Low Broken:** `${prev_low:.2f}`"

            if close < ema50 and close < ema16:
                signal_msg = "⚠️ *No Setup:* Price below both EMAs. No trade."
                s_data['status'] = 'TRACKING'
            else:
                if is_applus_setup:
                    signal_msg = f"🔥 **A++ TRENDLINE POI SETUP**\n• **Action:** Buy **$5 USDT** on spot at market price.\n• **Trigger:** Projected Support Trendline hit + Pattern + Volume Surge."
                    s_data['status'] = 'IN_TRADE'
                else:
                    if s_data['status'] == 'TRACKING':
                        if breakout_high and htf_bias == "BULLISH":
                            signal_msg = f"🟢 *INITIAL BUY SIGNAL*\n• **Action:** Buy **$5 USDT** on spot at market price.\n• **Setup:** {setup_quality}"
                            s_data['status'] = 'IN_TRADE'
                        else:
                            signal_msg = f"⏳ *Tracking Mode:*\n• Near Trendline POI: `{in_trendline_zone}`"

                    elif s_data['status'] == 'IN_TRADE':
                        # Explicit Exit Rule: Previous Low Broken
                        if breakdown_low:
                            signal_msg = f"🔴 *EXIT / SELL ALL SIGNAL*\n• **Action:** Sell all coins bought on the way up at market price.\n• **Reason:** Previous low broken. Reverting to tracking mode."
                            s_data['status'] = 'TRACKING'
                        # Scale-in Rule: Previous High Broken
                        elif breakout_high:
                            signal_msg = f"🟢 *SCALE-IN BUY SIGNAL*\n• **Action:** Buy **$5 USDT** more on spot at market price.\n• **Setup:** {setup_quality}"
                        else:
                            signal_msg = f"⚖️ *In Trade (Holding):* Price within range between previous high and low."

           # Only send Telegram notification if in trade, or if a signal/A++ setup triggered
            should_send = False
            if s_data['status'] == 'IN_TRADE':
                should_send = True
            elif is_applus_setup or (breakout_high and htf_bias == "BULLISH") or breakdown_low:
                should_send = True

            if should_send:
                msg = f"📊 *GitHub Alert: {symbol}*\n" \
                      f"• Status: `{s_data['status']}`\n" \
                      f"• Daily Bias: `{htf_bias}`\n" \
                      f"• Close: `${close:.2f}` | RSI: `{rsi:.1f}`{break_info}\n\n{signal_msg}"
                send_telegram(msg)
            else:
                print(f"Skipping notification for {symbol}: In tracking mode with no active signal.")

        except Exception as e:
            send_telegram(f"⚠️ Error on {symbol}: {str(e)}")

    save_state(state)

if __name__ == '__main__':
    run_bot()
