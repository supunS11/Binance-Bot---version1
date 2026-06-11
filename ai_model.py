import config

def ai_confidence_boost(
    trend_df,
    confirm_df,
    entry_df,
    signal,
    btc_trend=None,
    btc_corr=None,
    rs=None
):

    try:

        boost = 0

        confirm = confirm_df.iloc[-2]
        entry = entry_df.iloc[-2]
        trend = trend_df.iloc[-2]

        support = trend_df['low'].rolling(50).min().iloc[-1]
        resistance = trend_df['high'].rolling(50).max().iloc[-1]
        price = trend_df['close'].iloc[-1]

        resistance_distance = (
            (resistance - price) / price
        ) * 100

        support_distance = (
            (price - support) / price
        ) * 100

        required_distance = (
            config.ROI_PERCENT_TP /
            config.LEVERAGE
        ) + 0.7

        # ======================
        # ADX FILTER
        # ======================
        if confirm['adx'] < 18:
            boost -= 5

        elif confirm['adx'] > 25:
            boost += 5

        # ======================
        # RSI EXTREMES
        # ======================
        if signal == "BUY":

            if confirm['rsi'] > 75:
                boost -= 8

            elif confirm['rsi'] > 60:
                boost += 2

        else:

            if confirm['rsi'] < 25:
                boost -= 8

            elif confirm['rsi'] < 40:
                boost += 2

        # ======================
        # VOLUME CONFIRMATION
        # ======================
        if entry['volume'] > entry['volume_sma'] * 1.2:
            boost += 5

        # ======================
        # EMA50 TREND ALIGNMENT
        # (EMA200 REMOVED)
        # ======================
        if signal == "BUY":

            if (
                trend['close'] > trend['ema50']
                and resistance_distance > required_distance
            ):
                boost += 5

        else:

            if (
                trend['close'] < trend['ema50']
                and support_distance > required_distance
            ):
                boost += 5

        # ======================
        # EMA20 PULLBACK BONUS
        # ======================
        buy_pullback = (
            entry['low'] <= entry['ema20']
            and entry['close'] > entry['ema20']
        )

        sell_pullback = (
            entry['high'] >= entry['ema20']
            and entry['close'] < entry['ema20']
        )

        if signal == "BUY" and buy_pullback:
            boost += 3

        elif signal == "SELL" and sell_pullback:
            boost += 3

        # ======================
        # BTC CONTEXT
        # ======================
        if btc_corr is not None and btc_corr >= 0.75:

            if signal == "BUY" and btc_trend == "BULLISH":
                boost += 4

            elif signal == "SELL" and btc_trend == "BEARISH":
                boost += 4

            elif signal == "BUY" and btc_trend == "BEARISH":
                boost -= 6

            elif signal == "SELL" and btc_trend == "BULLISH":
                boost -= 6

        # ======================
        # RELATIVE STRENGTH
        # ======================
        if rs is not None:

            if signal == "BUY" and rs > 2:
                boost += 3

            elif signal == "SELL" and rs < -2:
                boost += 3

        # ======================
        # VOLATILITY CHECK
        # ======================
        recent_range = (
            trend_df['high'].iloc[-10:].max()
            - trend_df['low'].iloc[-10:].min()
        )

        avg_range = trend_df['atr'].iloc[-1] * 2

        if recent_range < avg_range:
            boost -= 2

        # ======================
        # MOMENTUM BONUS
        # ======================
        if signal == "BUY":

            if (
                confirm['macd'] > confirm['macd_signal']
                and confirm['rsi'] > 50
            ):
                boost += 2

        else:

            if (
                confirm['macd'] < confirm['macd_signal']
                and confirm['rsi'] < 50
            ):
                boost += 2

        # ======================
        # FINAL CLAMP
        # ======================
        return max(-15, min(15, boost))

    except Exception:
        return 0