import pandas as pd

from ta.trend import EMAIndicator
from ta.trend import MACD
from ta.trend import ADXIndicator

from ta.momentum import RSIIndicator


def apply_indicators(df):

    try:

        # EMA 20
        ema20 = EMAIndicator(
        close=df['close'],
        window=20
        )

        df['ema20'] = ema20.ema_indicator()

        # EMA 50
        ema50 = EMAIndicator(
            close=df['close'],
            window=50
        )

        df['ema50'] = ema50.ema_indicator()

        # EMA 200
        ema200 = EMAIndicator(
            close=df['close'],
            window=200
        )

        df['ema200'] = ema200.ema_indicator()

        # RSI
        rsi = RSIIndicator(
            close=df['close'],
            window=14
        )

        df['rsi'] = rsi.rsi()

        # MACD
        macd = MACD(
            close=df['close']
        )

        df['macd'] = macd.macd()

        df['macd_signal'] = macd.macd_signal()

        # ADX
        adx = ADXIndicator(
            high=df['high'],
            low=df['low'],
            close=df['close'],
            window=14
        )

        df['adx'] = adx.adx()

        # VOLUME SMA
        df['volume_sma'] = (
            df['volume']
            .rolling(20)
            .mean()
        )

        # REMOVE NaN
        df.dropna(inplace=True)

        df.reset_index(
            drop=True,
            inplace=True
        )

        return df

    except Exception as e:

        print(
            f"INDICATOR ERROR: {e}"
        )

        return pd.DataFrame()