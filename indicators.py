import pandas as pd
import numpy as np


def apply_indicators(df):

    try:

        df = df.copy()

        # =====================================================
        # EMA (required by Market Bias Filter)
        # =====================================================
        df['ema50'] = df['close'].ewm(
            span=50,
            adjust=False
        ).mean()

        df['ema200'] = df['close'].ewm(
            span=200,
            adjust=False
        ).mean()

        # =====================================================
        # ATR (risk / volatility)
        # =====================================================
        tr = pd.concat([
            df['high'] - df['low'],
            abs(df['high'] - df['close'].shift()),
            abs(df['low'] - df['close'].shift())
        ], axis=1).max(axis=1)

        df['atr'] = tr.rolling(14).mean()

        # =====================================================
        # Volume SMA
        # =====================================================
        df['volume_sma'] = df['volume'].rolling(20).mean()

        # =====================================================
        # Clean
        # =====================================================
        df.dropna(inplace=True)

        return df

    except Exception as e:
        print(f"INDICATORS ERROR: {e}")
        return None