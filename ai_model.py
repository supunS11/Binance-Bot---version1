from logger import (
    log_info,
    log_error
)


def ai_confirmation(
    trend_df,
    confirm_df,
    entry_df,
    signal
):

    try:

        trend = trend_df.iloc[-2]

        confirm = confirm_df.iloc[-2]

        entry = entry_df.iloc[-2]

        confidence = 0

        # =========================
        # BUY CONFIRMATION
        # =========================
        if signal == "BUY":

            # TREND EMA
            if (
                trend['ema50'] >
                trend['ema200']
            ):
                confidence += 25

            # TREND STRENGTH
            if trend['adx'] > 20:
                confidence += 15

            # MACD MOMENTUM
            if (
                confirm['macd'] >
                confirm['macd_signal']
            ):
                confidence += 20

            # RSI STRENGTH
            if confirm['rsi'] > 52:
                confidence += 15

            # ENTRY EMA20
            if (
                entry['close'] >
                entry['ema20']
            ):
                confidence += 10

            # ENTRY VOLUME
            if (
                entry['volume'] >
                entry['volume_sma']
            ):
                confidence += 15

        # =========================
        # SELL CONFIRMATION
        # =========================
        elif signal == "SELL":

            # TREND EMA
            if (
                trend['ema50'] <
                trend['ema200']
            ):
                confidence += 25

            # TREND STRENGTH
            if trend['adx'] > 20:
                confidence += 15

            # MACD MOMENTUM
            if (
                confirm['macd'] <
                confirm['macd_signal']
            ):
                confidence += 20

            # RSI WEAKNESS
            if confirm['rsi'] < 48:
                confidence += 15

            # ENTRY EMA20
            if (
                entry['close'] <
                entry['ema20']
            ):
                confidence += 10

            # ENTRY VOLUME
            if (
                entry['volume'] >
                entry['volume_sma']
            ):
                confidence += 15

        return confidence

    except Exception as e:

        log_error(
            f"AI MODEL ERROR: {e}"
        )

        return 0