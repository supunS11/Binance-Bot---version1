from logger import log_info, log_error
from ai_model import ai_confidence_boost
from indicators import calculate_atr
from filters import (
    has_liquidity_sweep,
    has_displacement,
    entry_confirmation_5m,
    is_fresh_move
)


# =========================================================
# MARKET BIAS (LIGHT WEIGHT CONTEXT ONLY)
# =========================================================
def get_market_bias(trend_df):

    try:
        ema50 = trend_df['ema50'].iloc[-2]
        ema200 = trend_df['ema200'].iloc[-2]

        if ema50 > ema200:
            return "BULLISH"
        elif ema50 < ema200:
            return "BEARISH"

        return "RANGE"

    except Exception:
        return "RANGE"


# =========================================================
# STRUCTURE (UNCHANGED)
# =========================================================
def detect_structure(df):

    try:
        swing_high = df['high'].rolling(10).max().iloc[-6]
        swing_low = df['low'].rolling(10).min().iloc[-6]

        hh = df['high'].iloc[-2] > swing_high
        ll = df['low'].iloc[-2] < swing_low

        bos_up = df['close'].iloc[-1] > swing_high
        bos_down = df['close'].iloc[-1] < swing_low

        choch_up = (
            df['low'].iloc[-2] < swing_low and
            df['close'].iloc[-1] > swing_low
        )

        choch_down = (
            df['high'].iloc[-2] > swing_high and
            df['close'].iloc[-1] < swing_high
        )

        return {
            "bos_up": bos_up,
            "bos_down": bos_down,
            "hh": hh,
            "ll": ll,
            "choch_up": choch_up,
            "choch_down": choch_down,
            "swing_high": swing_high,
            "swing_low": swing_low
        }

    except Exception as e:
        log_error(f"STRUCTURE ERROR: {e}")
        return {"bos_up": False, "bos_down": False, "hh": False, "ll": False}

def detect_retest(entry_df, structure):

    try:

        close = entry_df['close'].iloc[-1]

        retest_buy = (
            structure["bos_up"]
            and close > structure["swing_high"]
        )

        retest_sell = (
            structure["bos_down"]
            and close < structure["swing_low"]
        )

        return retest_buy, retest_sell

    except Exception:
        return False, False

# =========================================================
# LIQUIDITY (UNCHANGED)
# =========================================================
def detect_liquidity(df):

    try:
        eq_highs = abs(df['high'].iloc[-1] - df['high'].iloc[-3]) / df['high'].iloc[-1] < 0.004
        eq_lows = abs(df['low'].iloc[-1] - df['low'].iloc[-3]) / df['low'].iloc[-1] < 0.004

        return eq_highs, eq_lows

    except Exception:
        return False, False


# =========================================================
# DISPLACEMENT (NOW OPTIONAL CONFIRMATION)
# =========================================================
def detect_displacement(df):

    try:
        body = abs(df['close'].iloc[-1] - df['open'].iloc[-1])
        atr = df['atr'].iloc[-1]

        return body > atr * 0.35

    except Exception:
        return False


# =========================================================
# ORDER BLOCK (UNCHANGED)
# =========================================================
def get_order_block_v2(df):

    try:
        for i in range(-2, -10, -1):

            body = abs(df['close'].iloc[i] - df['open'].iloc[i])
            prev_body = abs(df['close'].iloc[i-1] - df['open'].iloc[i-1])

            if body > prev_body * 1.5:

                ob_idx = i - 1

                ob_high = df['high'].iloc[ob_idx]
                ob_low = df['low'].iloc[ob_idx]

                ob_type = "BULLISH" if df['close'].iloc[ob_idx] > df['open'].iloc[ob_idx] else "BEARISH"

                return ob_high, ob_low, ob_type

        return None, None, None

    except Exception as e:
        log_error(f"OB ERROR: {e}")
        return None, None, None

# =========================================================
# MAIN SIGNAL ENGINE (SMC v3 - LEADING FIRST)
# =========================================================
def check_signal(trend_df, confirm_df, entry_df, btc_trend, btc_corr, rs):

    try:

        price = entry_df['close'].iloc[-1]

        structure = detect_structure(trend_df)
        retest_buy, retest_sell = detect_retest(entry_df, structure)

        eq_highs, eq_lows = detect_liquidity(confirm_df)

        displacement = detect_displacement(confirm_df)

        ob_high, ob_low, ob_type = get_order_block_v2(confirm_df)

        in_ob = ob_low is not None and ob_low <= price <= ob_high

        # =====================================================
        # LIQUIDITY SWEEP
        # =====================================================
        sweep_low = confirm_df['low'].iloc[-3] < confirm_df['low'].iloc[-10:-3].min()
        sweep_high = confirm_df['high'].iloc[-3] > confirm_df['high'].iloc[-10:-3].max()

        # =====================================================
        # SCORE SYSTEM (REPLACED LOGIC)
        # =====================================================
        buy_score = 0
        sell_score = 0

        # STRUCTURE
        if structure["choch_up"] or structure["bos_up"]:
            buy_score += 2

        if structure["choch_down"] or structure["bos_down"]:
            sell_score += 2

        # LIQUIDITY
        if eq_lows or sweep_low:
            buy_score += 1

        if eq_highs or sweep_high:
            sell_score += 1

        # RETEST
        if retest_buy:
            buy_score += 2

        if retest_sell:
            sell_score += 2

        # ORDER BLOCK
        if ob_type == "BULLISH" and in_ob:
            buy_score += 1

        if ob_type == "BEARISH" and in_ob:
            sell_score += 1

        # DISPLACEMENT (SOFT BOOST, NOT REQUIRED)
        if displacement:
            buy_score += 1
            sell_score += 1

        # =====================================================
        # FINAL SIGNAL DECISION
        # =====================================================
        signal = None

        if buy_score >= 3 and buy_score > sell_score:
            signal = "BUY"

        elif sell_score >= 3 and sell_score > buy_score:
            signal = "SELL"

        if signal is None:
            return None

        # =====================================================
        # ANTI-CHASE FILTER (UNCHANGED)
        # =====================================================
        recent_range_high = trend_df['high'].iloc[-20:].max()
        recent_range_low = trend_df['low'].iloc[-20:].min()

        distance_to_high = abs(recent_range_high - price)
        distance_to_low = abs(price - recent_range_low)

        atr = confirm_df['atr'].iloc[-1]

        if atr == 0:
            return None

        if signal == "BUY" and distance_to_high < atr * 0.8:
            log_info("BUY BLOCKED - CHASING RESISTANCE")
            return None

        if signal == "SELL" and distance_to_low < atr * 0.8:
            log_info("SELL BLOCKED - CHASING SUPPORT")
            return None

        # =====================================================
        # AI FILTER (UNCHANGED)
        # =====================================================
        ai_score = ai_confidence_boost(
            trend_df,
            confirm_df,
            entry_df,
            signal,
            btc_trend,
            btc_corr,
            rs
        )

        if ai_score < -6:
            log_info(f"AI REJECTED | score={ai_score}")
            return None

        log_info(f"SMC SIGNAL | {signal} | AI={ai_score}")

        return signal

    except Exception as e:
        log_error(f"SMC ERROR: {e}")
        return None