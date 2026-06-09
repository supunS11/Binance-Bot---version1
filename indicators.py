import pandas as pd
import numpy as np


def apply_indicators(df):

    try:

        df = df.copy()

        # =========================
        # EMA
        # =========================
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

        # =========================
        # RSI (UNCHANGED LOGIC)
        # =========================
        delta = df['close'].diff()

        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()

        rs = gain / (loss + 1e-10)
        df['rsi'] = 100 - (100 / (1 + rs))

        # =========================
        # MACD
        # =========================
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()

        df['macd'] = ema12 - ema26
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()

        # =========================
        # TRUE RANGE (FIXED)
        # =========================
        high = df['high']
        low = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # =========================
        # ATR (STABLE VERSION)
        # =========================
        df['atr'] = tr.rolling(14).mean()

        # =========================
        # ADX (FIXED - REAL VERSION)
        # =========================
        plus_dm = high.diff()
        minus_dm = low.diff().abs()

        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        atr = df['atr']

        plus_di = 100 * (plus_dm.rolling(14).mean() / (atr + 1e-10))
        minus_di = 100 * (minus_dm.rolling(14).mean() / (atr + 1e-10))

        dx = (
            abs(plus_di - minus_di) /
            (plus_di + minus_di + 1e-10)
        ) * 100

        df['adx'] = dx.rolling(14).mean()

        # =========================
        # VOLUME SMA
        # =========================
        df['volume_sma'] = df['volume'].rolling(20).mean()

        # =========================
        # CLEAN
        # =========================
        df.dropna(inplace=True)

        return df

    except Exception as e:
        print(f"INDICATORS ERROR: {e}")
        return None
    
def calculate_atr(candles, period=14):
    if not candles or len(candles) < period + 1:
        return 0  # safety fallback

    tr_values = []

    for i in range(1, len(candles)):
        high = candles[i]['high']
        low = candles[i]['low']
        prev_close = candles[i - 1]['close']

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        tr_values.append(tr)

    return sum(tr_values[-period:]) / period