import config
from logger import log_info, log_error
from ai_model import ai_confidence_boost
from exchange import get_support_resistance


def score_to_confidence(score, max_score=20):

    if score <= 0:
        return 0

    confidence = (score / max_score) * 100
    confidence = confidence ** 1.15

    return round(min(confidence, 100), 2)


# =========================================================
# STRUCTURE-BASED STOP LOSS (NEW - SAFE ADDITION)
# =========================================================
def get_structure_stop_loss(df, side):

    try:
        atr = df['atr'].iloc[-2]

        if side == "BUY":

            swing_low_10 = df['low'].iloc[-10:-1].min()
            swing_low_20 = df['low'].iloc[-20:-1].min()

            swing_low = min(swing_low_10, swing_low_20)

            return swing_low - (atr * 0.8)

        else:

            swing_high_10 = df['high'].iloc[-10:-1].max()
            swing_high_20 = df['high'].iloc[-20:-1].max()

            swing_high = max(swing_high_10, swing_high_20)

            return swing_high + (atr * 0.8)

    except Exception as e:
        log_error(f"STRUCTURE SL ERROR: {e}")
        return None


# =========================================================
# LIQUIDITY SWEEP DETECTION (UNCHANGED)
# =========================================================
def detect_liquidity_sweep(df):

    try:

        prev_high = df['high'].iloc[-3]
        prev_low = df['low'].iloc[-3]

        last_high = df['high'].iloc[-1]
        last_low = df['low'].iloc[-1]
        close = df['close'].iloc[-1]

        bullish_sweep = (
            last_low < prev_low and
            close > prev_low
        )

        bearish_sweep = (
            last_high > prev_high and
            close < prev_high
        )

        return bullish_sweep, bearish_sweep

    except Exception:
        return False, False


# =========================================================
# ORDER BLOCK DETECTION (UNCHANGED)
# =========================================================
def detect_order_block(df):

    try:

        body = abs(df['close'] - df['open'])

        idx = body.iloc[-20:].idxmax()

        ob_high = df['high'].loc[idx]
        ob_low = df['low'].loc[idx]

        ob_type = (
            "BULLISH"
            if df['close'].loc[idx] > df['open'].loc[idx]
            else "BEARISH"
        )

        return ob_high, ob_low, ob_type

    except Exception:
        return None, None, None


# =========================================================
# MAIN SIGNAL ENGINE (UNCHANGED LOGIC)
# =========================================================
def check_signal(trend_df, confirm_df, entry_df, btc_trend, btc_corr, rs):

    try:

        trend = trend_df.iloc[-2]
        confirm = confirm_df.iloc[-2]
        entry = entry_df.iloc[-2]

        support, resistance = get_support_resistance(trend_df)

        if support is None or resistance is None:
            log_info("INVALID SUPPORT/RESISTANCE")
            return None
        
        price = trend_df['close'].iloc[-1]

        bullish_sweep, bearish_sweep = detect_liquidity_sweep(confirm_df)
        ob_high, ob_low, ob_type = detect_order_block(confirm_df)

        atr_pct = (entry['atr'] / entry['close']) * 100

        log_info(f"ATR%: {round(atr_pct, 2)}")

        # ======================
        # ATR FILTER (slightly relaxed)
        # ======================
        if atr_pct < 0.15 or atr_pct > 3.2:
            log_info(f"ATR FILTER BLOCKED | ATR%: {round(atr_pct, 2)}")
            return None

        # ======================
        # REGIME (less strict)
        # ======================
        regime = "NORMAL"

        if confirm['adx'] > 25:
            regime = "TRENDING"
        elif confirm['adx'] < 15:
            regime = "SIDEWAYS"

        log_info(f"MARKET REGIME: {regime}")

        if regime == "SIDEWAYS":
            return None

        required_distance = (
            config.ROI_PERCENT_TP /
            config.LEVERAGE
        ) + 0.7

        # ======================
        # BUY SCORE
        # ======================
        buy_score = 0

        resistance_distance = (
            (resistance - price) / price
        ) * 100

        if resistance_distance < required_distance:
            log_info(
                f"BUY BLOCKED | Resistance too close: "
                f"{round(resistance_distance,2)}%"
            )
            buy_score -= 2

        bullish_ema_rejection = all(
            trend_df['low'].iloc[-i] > trend_df['ema50'].iloc[-i]
            for i in range(1, 3)   # relaxed 4 → 3 candles
        )

        buy_pullback_zone = (
            abs(entry['close'] - entry['ema20']) / entry['ema20'] < 0.004
        )

        # Trend (core)
        if trend['close'] > trend['ema50']:
            buy_score += 2

        if bullish_ema_rejection:
            buy_score += 1

        # Momentum (soft)
        if confirm['macd'] > confirm['macd_signal']:
            buy_score += 1

        if confirm['rsi'] > 48:   # relaxed
            buy_score += 1

        if confirm['adx'] > 18:   # relaxed
            buy_score += 1

        # Entry trigger (broader)
        if buy_pullback_zone:
            buy_score += 2

        # Volume (soft)
        if entry['volume'] > entry['volume_sma'] * 1.05:
            buy_score += 1

        if entry['close'] > entry['open']:
            buy_score += 1

        # BTC influence (SOFT now)
        if btc_corr >= 0.75:

            if btc_trend == "BULLISH":
                buy_score += 1
            elif btc_trend == "BEARISH":
                buy_score -= 1

        # Relative strength
        if rs > 2:
            buy_score += 2

        # Liquidity / OB (bonus only)
        if bullish_sweep:
            buy_score += 1

        if ob_type == "BULLISH" and ob_low <= price <= ob_high:
            buy_score += 1

        # Breakout
        recent_high = trend_df['high'].iloc[-20:-5].max()

        if trend_df['close'].iloc[-1] > recent_high:
            buy_score += 2

        bullish_rsi_cross = (
            confirm_df['rsi'].iloc[-3] < 50
            and confirm_df['rsi'].iloc[-2] > 50
        )

        if bullish_rsi_cross:
            buy_score += 1

        if regime == "TRENDING":
            buy_score += 1

        # ======================
        # SELL SCORE
        # ======================
        sell_score = 0

        support_distance = (
            (price - support) / price
        ) * 100

        if support_distance < required_distance:
            log_info(
                f"SELL BLOCKED | Support too close: "
                f"{round(support_distance,2)}%"
            )
            sell_score -= 2

        bearish_ema_rejection = all(
            trend_df['high'].iloc[-i] < trend_df['ema50'].iloc[-i]
            for i in range(1, 3)
        )

        sell_pullback_zone = (
            abs(entry['close'] - entry['ema20']) / entry['ema20'] < 0.004
        )

        # Trend
        if trend['close'] < trend['ema50']:
            sell_score += 2

        if bearish_ema_rejection:
            sell_score += 1

        # Momentum
        if confirm['macd'] < confirm['macd_signal']:
            sell_score += 1

        if confirm['rsi'] < 52:
            sell_score += 1

        if confirm['adx'] > 18:
            sell_score += 1

        # Entry trigger
        if sell_pullback_zone:
            sell_score += 2

        # Volume
        if entry['volume'] > entry['volume_sma'] * 1.05:
            sell_score += 1

        if entry['close'] < entry['open']:
            sell_score += 1

        # BTC influence (SOFT)
        if btc_corr >= 0.75:

            if btc_trend == "BEARISH":
                sell_score += 1
            elif btc_trend == "BULLISH":
                sell_score -= 1

        # Relative strength
        if rs < -2:
            sell_score += 2

        # Liquidity / OB
        if bearish_sweep:
            sell_score += 1

        if ob_type == "BEARISH" and ob_low <= price <= ob_high:
            sell_score += 1

        # Breakdown
        recent_low = trend_df['low'].iloc[-20:-5].min()

        if trend_df['close'].iloc[-1] < recent_low:
            sell_score += 2

        bearish_rsi_cross = (
            confirm_df['rsi'].iloc[-3] > 50
            and confirm_df['rsi'].iloc[-2] < 50
        )

        if bearish_rsi_cross:
            sell_score += 1

        if regime == "TRENDING":
            sell_score += 1

        # ======================
        # FINAL
        # ======================
        buy_score = max(0, buy_score)
        sell_score = max(0, sell_score)

        buy_conf = score_to_confidence(buy_score)
        sell_conf = score_to_confidence(sell_score)

        log_info(f"BUY conf: {buy_conf}% | SELL conf: {sell_conf}%")

        signal_guess = "BUY" if buy_conf > sell_conf else "SELL"

        ai_boost = ai_confidence_boost(
            trend_df,
            confirm_df,
            entry_df,
            signal_guess
        )

        if buy_conf > sell_conf:
            buy_conf = min(100, max(0, buy_conf + ai_boost))
        else:
            sell_conf = min(100, max(0, sell_conf + ai_boost))

        if buy_conf >= 68 and buy_conf > sell_conf:
            log_info(f"FINAL BUY CONFIDENCE: {buy_conf}")
            return "BUY"

        if sell_conf >= 68 and sell_conf > buy_conf:
            log_info(f"FINAL SELL CONFIDENCE: {sell_conf}")
            return "SELL"

        return None

    except Exception as e:
        log_error(f"STRATEGY ERROR: {e}")
        return None