import config


def ai_confidence_boost(trend_df, confirm_df, entry_df, signal, btc_trend=None, btc_corr=None, rs=None):

    try:

        score = 0

        trend = trend_df.iloc[-2]
        confirm = confirm_df.iloc[-2]
        entry = entry_df.iloc[-2]

        price = entry_df['close'].iloc[-1]

        # =========================================================
        # LIQUIDITY CONTEXT (SMC ZONE LOGIC - FIXED)
        # =========================================================
        recent_high = trend_df['high'].iloc[-10:].max()
        recent_low = trend_df['low'].iloc[-10:].min()

        range_size = recent_high - recent_low
        midpoint = (recent_high + recent_low) / 2

        bull_zone = price < midpoint
        bear_zone = price > midpoint

        if signal == "BUY":
            if bull_zone:
                score += 2
            else:
                score -= 2

        if signal == "SELL":
            if bear_zone:
                score += 2
            else:
                score -= 2

        # =========================================================
        # DISPLACEMENT QUALITY (IMPROVED)
        # =========================================================
        body = abs(entry['close'] - entry['open'])
        prev_body = abs(entry_df['close'].iloc[-2] - entry_df['open'].iloc[-2])

        if body > prev_body * 1.5:
            score += 3
        else:
            score -= 2

        # =========================================================
        # VOLUME CONFIRMATION (SMC SUPPORT ONLY)
        # =========================================================
        if 'volume_sma' in entry_df.columns:
            if entry['volume'] > entry_df['volume_sma'].iloc[-1]:
                score += 2
            else:
                score -= 1

        # =========================================================
        # BTC CONTEXT (CONFLICT FILTER ONLY)
        # =========================================================
        if btc_corr is not None and btc_corr >= 0.7:

            if signal == "BUY" and btc_trend == "BEARISH":
                score -= 4

            if signal == "SELL" and btc_trend == "BULLISH":
                score -= 4

            if signal == "BUY" and btc_trend == "BULLISH":
                score += 2

            if signal == "SELL" and btc_trend == "BEARISH":
                score += 2

        # =========================================================
        # RELATIVE STRENGTH FILTER
        # =========================================================
        if rs is not None:

            if signal == "BUY" and rs > 2:
                score += 2

            if signal == "SELL" and rs < -2:
                score += 2

        # =========================================================
        # VOLATILITY REGIME (NORMALIZED ATR)
        # =========================================================
        atr = entry.get('atr', None)

        if atr is not None and 'atr' in trend_df.columns:

            atr_mean = trend_df['atr'].rolling(20).mean().iloc[-1]

            if atr_mean and atr_mean > 0:

                if atr < atr_mean * 0.6:
                    score -= 2  # dead market

                elif atr > atr_mean * 2:
                    score -= 2  # unstable market

        # =========================================================
        # FINAL OUTPUT (SMC GATE ONLY)
        # =========================================================
        return max(-10, min(10, score))

    except Exception:
        return 0