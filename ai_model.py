def ai_confidence_boost(trend_df, confirm_df, entry_df, signal):

    try:

        boost = 0

        confirm = confirm_df.iloc[-2]
        entry = entry_df.iloc[-2]
        trend = trend_df.iloc[-2]

        support = trend_df['low'].rolling(50).min().iloc[-1]
        resistance = trend_df['high'].rolling(50).max().iloc[-1]
        price = trend_df['close'].iloc[-1]

        resistance_distance = ((resistance - price) / price) * 100
        support_distance = ((price - support) / price) * 100

        # ======================
        # ADX FILTER
        # ======================
        if confirm['adx'] < 18:
            boost -= 5

        if confirm['adx'] > 25:
            boost += 5

        # ======================
        # RSI EXTREMES
        # ======================
        if signal == "BUY" and confirm['rsi'] > 75:
            boost -= 8

        if signal == "SELL" and confirm['rsi'] < 25:
            boost -= 8

        # ======================
        # VOLUME CONFIRMATION
        # ======================
        if entry['volume'] > entry['volume_sma'] * 1.2:
            boost += 5

        # ======================
        # TREND ALIGNMENT
        # ======================
        if signal == "BUY" and trend['ema50'] > trend['ema200'] and resistance_distance > 2:
            boost += 5

        if signal == "SELL" and trend['ema50'] < trend['ema200'] and support_distance > 2:
            boost += 5

        # ======================
        # FINAL CLAMP
        # ======================
        return max(-15, min(15, boost))

    except Exception:
        return 0