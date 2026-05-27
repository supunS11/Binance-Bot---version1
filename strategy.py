from logger import (
    log_info,
    log_error
)


def check_signal(
    trend_df,
    confirm_df,
    entry_df
):

    try:

        trend = trend_df.iloc[-2]

        confirm = confirm_df.iloc[-2]

        entry = entry_df.iloc[-2]

        # =========================
        # BUY SIGNAL
        # =========================
        buy_signal = (

            # TREND
            trend['ema50'] > trend['ema200']
            and trend['close'] > trend['ema50']
            and trend['adx'] > 20

            # CONFIRMATION
            and confirm['macd'] >
            confirm['macd_signal']

            and confirm['rsi'] > 52

            and confirm['volume'] >
            confirm['volume_sma']

            # ENTRY
            and entry['close'] >
            entry['ema20']

            and entry['rsi'] > 50

            and entry['volume'] >
            entry['volume_sma']
        )

        # =========================
        # SELL SIGNAL
        # =========================
        sell_signal = (

            # TREND
            trend['ema50'] <
            trend['ema200']

            and trend['close'] <
            trend['ema50']

            and trend['adx'] > 20

            # CONFIRMATION
            and confirm['macd'] <
            confirm['macd_signal']

            and confirm['rsi'] < 48

            and confirm['volume'] >
            confirm['volume_sma']

            # ENTRY
            and entry['close'] <
            entry['ema20']

            and entry['rsi'] < 50

            and entry['volume'] >
            entry['volume_sma']
        )

        # =========================
        # RETURN SIGNAL
        # =========================
        if buy_signal:

            log_info(
                "BUY SIGNAL FOUND"
            )

            return "BUY"

        elif sell_signal:

            log_info(
                "SELL SIGNAL FOUND"
            )

            return "SELL"

        return None

    except Exception as e:

        log_error(
            f"STRATEGY ERROR: {e}"
        )

        return None