import numpy as np

from binance.enums import SIDE_BUY, SIDE_SELL


def has_liquidity_sweep(df, side):
    try:
        if side == "BUY":
            return df['low'].iloc[-2] < df['low'].iloc[-8:-2].min()
        else:
            return df['high'].iloc[-2] > df['high'].iloc[-8:-2].max()
    except:
        return False


def has_displacement(df):
    try:
        body = abs(df['close'].iloc[-1] - df['open'].iloc[-1])
        atr = df['atr'].iloc[-1]

        # soft threshold instead of strict comparison
        return body > atr * 0.35
    except:
        return False


def entry_confirmation_5m(df, signal):

    try:
        body = abs(df['close'].iloc[-1] - df['open'].iloc[-1])
        atr = df['atr'].iloc[-1]

        momentum_ok = body > atr * 0.25   # softer than strict 0.5+

        trend_ok = True

        if signal == "BUY":
            trend_ok = df['close'].iloc[-1] > df['ema50'].iloc[-1]

        if signal == "SELL":
            trend_ok = df['close'].iloc[-1] < df['ema50'].iloc[-1]

        # FINAL SCORE STYLE (not strict rejection)
        score = 0

        if momentum_ok:
            score += 1
        if trend_ok:
            score += 1

        return score >= 1   # allow partial confirmation

    except:
        return False


def is_fresh_move(df):
    try:
        return abs(df['close'].iloc[-1] - df['close'].iloc[-5]) < df['atr'].iloc[-1] * 1.5
    except:
        return False
    
def get_entry_filter_mode(trend_tf):

    if trend_tf == "1h" or trend_tf == "1H":
        return "MOMENTUM_ONLY"

    if trend_tf == "30m" or trend_tf == "30M":
        return "STRICT_ENTRY"

    return "MOMENTUM_ONLY"

def entry_momentum_only(df):

    try:
        body = abs(df['close'].iloc[-1] - df['open'].iloc[-1])
        atr = df['atr'].iloc[-1]

        return body > atr * 0.15

    except:
        return False
    
def entry_strict_filter(df, signal):

    try:
        body = abs(df['close'].iloc[-1] - df['open'].iloc[-1])
        atr = df['atr'].iloc[-1]

        momentum_ok = body > atr * 0.20

        if signal == "BUY":
            trend_ok = df['close'].iloc[-1] > df['ema50'].iloc[-1]
        else:
            trend_ok = df['close'].iloc[-1] < df['ema50'].iloc[-1]

        return momentum_ok and trend_ok

    except:
        return False