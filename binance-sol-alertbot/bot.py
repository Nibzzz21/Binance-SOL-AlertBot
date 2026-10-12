import os
import json
import ccxt
import numpy as np
import pandas as pd
import requests

# Load credentials securely from GitHub Environment Secrets
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

SYMBOLS = [
    'BTC/USDT:USDT', 
    'ETH/USDT:USDT', 
    'SOL/USDT:USDT', 
    'ZEC/USDT:USDT', 
    'ONDO/USDT:USDT', 
    'BNB/USDT:USDT'
]
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
    return (slope * current_idx) + intercept

def check_hidden_divergences(df):
    pivots = df[df['swing_low'].notna() | df['swing_high'].notna()].tail(4)
    if len(pivots) < 2:
        return "None"
    
    last_piv = pivots.iloc[-1]
    prev_piv = pivots.iloc[-2]
    
    if not np.isnan(last_piv['swing_low']) and not np.isnan(prev_piv['swing_low']):
        if last_piv['close'] > prev_piv['close'] and last_piv['rsi_14'] < prev_piv['rsi_14']:
            return "Hidden Bullish Divergence 🟢"
            
    if not np.isnan(last_piv['swing_high']) and not np.isnan(prev_piv['swing_high']):
        if last_piv['close'] < prev_piv['close'] and last_piv['rsi_14'] > prev_piv['rsi_14']:
            return "Hidden Bearish Divergence 🔴"
            
    return "None"

def analyze_15m_structure(df):
    df_piv = df[df['swing_high'].notna() | df['swing_low'].notna()].copy()
    highs = df_piv[df_piv['swing_high'].notna()]['swing_high'].tolist()
    lows = df_piv[df_piv['swing_low'].notna()]['swing_low'].tolist()
    
    trend = "NEUTRAL"
    key_level = 0.0
    level_name = "N/A"
    
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            trend = "BULLISH"
            key_level = lows[-1]
            level_name = "Higher Low (HL)"
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            trend = "BEARISH"
            key_level = highs[-1]
            level_name = "Lower High (LH)"
            
    return trend, key_level, level_name

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
    exchange = ccxt.okx({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'}
    })
    
    state = load_state()

    for symbol in SYMBOLS:
        try:
            sym_key = symbol.replace('/', '_').replace(':', '_')
            if sym_key not in state:
                state[sym_key] = {'status': 'TRACKING', 'last_trend': 'NEUTRAL', 'key_level': 0.0}

            s_data = state[sym_key]

            htf_bias = get_daily_structure_bias(exchange, symbol)
            ltf_data = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=70)
            df_ltf = pd.DataFrame(ltf_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            df_closed = df_ltf.iloc[:-1].copy()
            df_closed['ema_16'] = df_closed['close'].ewm(span=16).mean()
            df_closed['ema_50'] = df_closed['close'].ewm(span=50).mean()
            df_closed['rsi_14'] = calculate_rsi(df_closed['close'], period=14)
            df_closed['atr'] = calculate_atr(df_closed, period=14)

            df_closed = find_pivots(df_closed, window=4)
            proj_support = get_linear_regression_trendline(df_closed, pivot_type='low', num_pivots=3)
            proj_resistance = get_linear_regression_trendline(df_closed, pivot_type='high', num_pivots=3)

            last_closed = df_closed.iloc[-1]
            prev_candle = df_closed.iloc[-2]

            close = last_closed['close']
            ema16 = last_closed['ema_16']
            ema50 = last_closed['ema_50']
            rsi = last_closed['rsi_14']
            rsi_prev = df_closed.iloc[-2]['rsi_14']
            rsi_slope = "Upward 📈" if rsi > rsi_prev else "Downward 📉"
            hidden_div = check_hidden_divergences(df_closed)
            atr = last_closed['atr']
            vol = last_closed['volume']
            avg_vol_prev3 = df_closed['volume'].iloc[-4:-1].mean()

            prev_high = prev_candle['high']
            prev_low = prev_candle['low']

            if close > prev_high:
                candle_pos_msg = "Candle close is **Higher** than previous candle 🟢"
            elif close < prev_low:
                candle_pos_msg = "Candle close is **Lower** than previous candle 🔴"
            else:
                candle_pos_msg = "Candle close is **Within** previous candle range ⚖️"

            ema_status = ""
            ema_status += "• **Price vs 50 EMA:** Above 🟢\n" if close > ema50 else "• **Price vs 50 EMA:** Below 🔴\n"
            ema_status += "• **Price vs 16 EMA:** Above 🟢" if close > ema16 else "• **Price vs 16 EMA:** Below 🔴"

            poi_msg = "No immediate POI test"
            in_trendline_zone = False
            if proj_support is not None and abs(close - proj_support) <= (atr * 0.5):
                in_trendline_zone = True
                poi_msg = f"Approached Support Trendline POI (~${proj_support:.2f}) 🎯"
            elif proj_resistance is not None and abs(close - proj_resistance) <= (atr * 0.5):
                poi_msg = f"Approached Resistance Trendline POI (~${proj_resistance:.2f}) 🎯"

            current_15m_trend, key_lvl, lvl_name = analyze_15m_structure(df_closed)
            choch_alert = ""
            if s_data['last_trend'] != 'NEUTRAL' and current_15m_trend != s_data['last_trend']:
                choch_alert = f"\n🚨 **CHoCH / TREND SHIFT!** Shifted from `{s_data['last_trend']}` to `{current_15m_trend}`."
            s_data['last_trend'] = current_15m_trend
            s_data['key_level'] = key_lvl

            pattern_confirmed = check_candlestick_patterns(df_closed)
            volume_confirmed = vol > avg_vol_prev3
            is_applus_setup = in_trendline_zone and pattern_confirmed and volume_confirmed

            breakout_high = close > prev_high
            breakdown_low = close < prev_low

            signal_title = ""
            signal_action = ""
            is_actionable = False

            if s_data['status'] == 'TRACKING':
                if is_applus_setup or (breakout_high and htf_bias == "BULLISH"):
                    signal_title = "🟢 **INITIAL BUY SIGNAL**"
                    signal_action = "• **Action:** Enter Initial Spot Position."
                    s_data['status'] = 'IN_TRADE'
                    is_actionable = True
            elif s_data['status'] == 'IN_TRADE':
                if breakdown_low:
                    signal_title = "🔴 **EXIT SIGNAL**"
                    signal_action = "• **Action:** Sell All / Exit Position. Previous low broken."
                    s_data['status'] = 'TRACKING'
                    is_actionable = True
                elif breakout_high:
                    signal_title = "🟢 **SCALE-IN BUY SIGNAL**"
                    signal_action = "• **Action:** Scale-In (Add More Spot Position). Previous high broken."
                    is_actionable = True

            # ONLY send Telegram message if an actionable signal or structural shift occurs
            if is_actionable or choch_alert != "":
                msg = (
                    f"📊 *15m Bot Alert: {symbol}*\n"
                    f"• Status: `{s_data['status']}` | Bias: `{htf_bias}`\n"
                    f"• Close: `${close:.2f}`\n"
                    f"{candle_pos_msg}\n"
                    f"{ema_status}\n"
                    f"• **RSI (14):** `{rsi:.1f}` (Slope: {rsi_slope})\n"
                    f"• **Divergence:** `{hidden_div}`\n"
                    f"• **POI Status:** `{poi_msg}`\n"
                    f"• **15m Structure:** `{current_15m_trend}` | Critical {lvl_name}: `${key_lvl:.2f}`{choch_alert}\n\n"
                    f"{signal_title}\n{signal_action}"
                )
                send_telegram(msg)
            else:
                print(f"Skipping Telegram notification for {symbol}: Currently tracking with no trigger event.")

        except Exception as e:
            send_telegram(f"⚠️ Error on {symbol}: {str(e)}")

        save_state(state)

if __name__ == '__main__':
    run_bot()
