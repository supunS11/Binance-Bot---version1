import config
from logger import log_info, log_error, log_warning
from ai_model import ai_confidence_boost
from exchange import get_support_resistance


def score_to_confidence(score, max_score=20):

    if score <= 0:
        return 0

    confidence = (score / max_score) * 100

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
# MARKET STRUCTURE DETECTION (BOS / CHOCH CONTEXT)
# =========================================================
def detect_market_structure(df):

    try:

        recent_high = df['high'].iloc[-20:-5].max()
        recent_low = df['low'].iloc[-20:-5].min()
        prev_high = df['high'].iloc[-35:-20].max()
        prev_low = df['low'].iloc[-35:-20].min()
        last_close = df['close'].iloc[-2]

        bullish_structure = (
            recent_high > prev_high
            and recent_low > prev_low
        )

        bearish_structure = (
            recent_high < prev_high
            and recent_low < prev_low
        )

        bullish_bos = last_close > recent_high
        bearish_bos = last_close < recent_low

        bullish_choch = bearish_structure and last_close > recent_high
        bearish_choch = bullish_structure and last_close < recent_low

        return {
            "bullish_structure": bullish_structure,
            "bearish_structure": bearish_structure,
            "bullish_bos": bullish_bos,
            "bearish_bos": bearish_bos,
            "bullish_choch": bullish_choch,
            "bearish_choch": bearish_choch
        }

    except Exception:
        return {
            "bullish_structure": False,
            "bearish_structure": False,
            "bullish_bos": False,
            "bearish_bos": False,
            "bullish_choch": False,
            "bearish_choch": False
        }


# =========================================================
# MAIN SIGNAL ENGINE (UPDATED INTEGRATION + CONFIRMATIONS)
# =========================================================
def check_signal(trend_df, confirm_df, entry_df, btc_trend, btc_corr, rs, return_confidence=False):

    try:

        trend = trend_df.iloc[-3]
        confirm = confirm_df.iloc[-2]
        entry = entry_df.iloc[-2]

        support, resistance = get_support_resistance(trend_df)
        ema_gap_pct = abs(entry['ema20'] - entry['ema50']) / entry['ema50'] * 100

        if support is None or resistance is None:
            log_info("INVALID SUPPORT/RESISTANCE")
            return None
        
        price = trend_df['close'].iloc[-1]

        bullish_sweep, bearish_sweep = detect_liquidity_sweep(confirm_df)
        ob_high, ob_low, ob_type = detect_order_block(confirm_df)
        structure = detect_market_structure(trend_df)

        # ======================
        # REVERSAL GUARD
        # ======================
        trend_latest = trend_df.iloc[-2]
        confirm_latest = confirm_df.iloc[-2]
        entry_latest = entry_df.iloc[-2]

        bullish_reversal_guard = (
            structure['bullish_choch']
            or structure['bullish_bos']
            or (
                'vwap' in entry_latest.index
                and entry_latest['close'] > entry_latest['vwap']
                and confirm_latest['close'] > confirm_latest['ema20']
                and confirm_latest['macd'] > confirm_latest['macd_signal']
            )
        )

        bearish_reversal_guard = (
            structure['bearish_choch']
            or structure['bearish_bos']
            or (
                'vwap' in entry_latest.index
                and entry_latest['close'] < entry_latest['vwap']
                and confirm_latest['close'] < confirm_latest['ema20']
                and confirm_latest['macd'] < confirm_latest['macd_signal']
            )
        )

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

        # ======================
        # BUY SCORE
        # ======================
        buy_score = 0

        if ema_gap_pct < 0.2 or ema_gap_pct > 4.5:
            log_warning(f"INVALID EMA GAP: {round(ema_gap_pct, 2)}%")
            return None
        
        ema20_distance = (
            abs(entry['close'] - entry['ema20'])
            / entry['ema20']
        ) * 100

        vwap_buy_ok = (
            'vwap' in entry.index
            and entry['close'] > entry['vwap']
        )

        vwap_sell_ok = (
            'vwap' in entry.index
            and entry['close'] < entry['vwap']
        )

        if ema20_distance > 0.8:
            log_warning(
                f"EMA20 TOO FAR: {round(ema20_distance,2)}%"
            )
            return None

        bullish_ema_rejection = all(
            trend_df['low'].iloc[-i] > trend_df['ema50'].iloc[-i]
            for i in range(1, 3)   # relaxed 4 → 3 candles
        )

        buy_pullback_zone = (
            abs(entry['close'] - entry['ema20']) / entry['ema20'] < 0.006
            or (
                entry['low'] <= entry['ema20']
                and entry['close'] > entry['ema20']
            )
        )

        buy_trend_ok = (
            trend['ema20'] > trend['ema50']
            and trend['close'] > trend['ema50']
        )

        if bearish_reversal_guard:
            buy_score -= 5
            log_info("BUY REVERSAL GUARD ACTIVE")

        buy_momentum_ok = (
            confirm['macd'] > confirm['macd_signal']
            or confirm['rsi'] > 52
        )

        buy_valid = (
            buy_trend_ok
            and buy_pullback_zone
            and buy_momentum_ok
        )

        if buy_valid:
            buy_score += 5

        if structure['bullish_structure']:
            buy_score += 2

        if structure['bullish_bos']:
            buy_score += 1

        if structure['bearish_choch']:
            buy_score -= 2

        if vwap_buy_ok:
            buy_score += 1

        elif 'vwap' in entry.index and entry['close'] < entry['vwap']:
            buy_score -= 1

        if bullish_ema_rejection:
            buy_score += 1

        if confirm['macd'] > confirm['macd_signal']:
            buy_score += 1

        if confirm['rsi'] > 52:
            buy_score += 1

        if confirm['adx'] > 18:   # relaxed
            buy_score += 1

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
        if rs > 1:
            buy_score += 2

        elif rs < -1:
            buy_score -= 1

        # Liquidity / OB
        if bullish_sweep:
            buy_score += 1

        if ob_type == "BULLISH" and ob_low <= price <= ob_high:
            buy_score += 1

        # Breakout
        recent_high = trend_df['high'].iloc[-20:-5].max()

        if trend_df['close'].iloc[-1] > recent_high:
            buy_score += 1

        bullish_rsi_cross = (
            confirm_df['rsi'].iloc[-3] < 50
            and confirm_df['rsi'].iloc[-2] > 50
        )

        if bullish_rsi_cross:
            buy_score += 1

        if regime == "TRENDING":
            buy_score += 1

        if not buy_valid:
            buy_score -= 3

        # ======================
        # SELL SCORE
        # ======================
        sell_score = 0

        if ema_gap_pct < 0.2 or ema_gap_pct > 4.5:
            log_warning(f"INVALID EMA GAP: {round(ema_gap_pct, 2)}%")
            return None
        
        if ema20_distance > 0.8:
            log_warning(
                f"EMA20 TOO FAR: {round(ema20_distance,2)}%"
            )
            return None

        bearish_ema_rejection = all(
            trend_df['high'].iloc[-i] < trend_df['ema50'].iloc[-i]
            for i in range(1, 3)
        )

        sell_pullback_zone = (
            abs(entry['close'] - entry['ema20']) / entry['ema20'] < 0.006
            or (
                entry['high'] >= entry['ema20']
                and entry['close'] < entry['ema20']
            )
        )

        sell_trend_ok = (
            trend['ema20'] < trend['ema50']
            and trend['close'] < trend['ema50']
        )

        if bullish_reversal_guard:
            sell_score -= 5
            log_info("SELL REVERSAL GUARD ACTIVE")

        sell_momentum_ok = (
            confirm['macd'] < confirm['macd_signal']
            or confirm['rsi'] < 48
        )

        sell_valid = (
            sell_trend_ok
            and sell_pullback_zone
            and sell_momentum_ok
        )

        if sell_valid:
            sell_score += 5

        if structure['bearish_structure']:
            sell_score += 2

        if structure['bearish_bos']:
            sell_score += 1

        if structure['bullish_choch']:
            sell_score -= 2

        if vwap_sell_ok:
            sell_score += 1

        elif 'vwap' in entry.index and entry['close'] > entry['vwap']:
            sell_score -= 1

        if bearish_ema_rejection:
            sell_score += 1

        if confirm['macd'] < confirm['macd_signal']:
            sell_score += 1

        if confirm['rsi'] < 48:
            sell_score += 1

        if confirm['adx'] > 18:
            sell_score += 1

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
        if rs < -1:
            sell_score += 2

        elif rs > 1:
            sell_score -= 1

        # Liquidity / OB
        if bearish_sweep:
            sell_score += 1

        if ob_type == "BEARISH" and ob_low <= price <= ob_high:
            sell_score += 1

        # Breakdown
        recent_low = trend_df['low'].iloc[-20:-5].min()

        if trend_df['close'].iloc[-1] < recent_low:
            sell_score += 1

        bearish_rsi_cross = (
            confirm_df['rsi'].iloc[-3] > 50
            and confirm_df['rsi'].iloc[-2] < 50
        )

        if bearish_rsi_cross:
            sell_score += 1

        if regime == "TRENDING":
            sell_score += 1

        if not sell_valid:
            sell_score -= 3

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
            signal_guess,
            btc_trend,
            btc_corr,
            rs
        )

        if buy_conf > sell_conf:
            buy_conf = min(100, max(0, buy_conf + ai_boost))
        else:
            sell_conf = min(100, max(0, sell_conf + ai_boost))

        if abs(buy_conf - sell_conf) < 8:
            return None

        if buy_conf >= 75 and buy_conf > sell_conf:
            log_info(f"FINAL BUY CONFIDENCE: {buy_conf}")

            if return_confidence:
                return "BUY", buy_conf

            return "BUY"

        if sell_conf >= 75 and sell_conf > buy_conf:
            log_info(f"FINAL SELL CONFIDENCE: {sell_conf}")

            if return_confidence:
                return "SELL", sell_conf

            return "SELL"

        if return_confidence:
            return None, max(buy_conf, sell_conf)

        return None

    except Exception as e:
        log_error(f"STRATEGY ERROR: {e}")

        if return_confidence:
            return None, 0

        return None
