from logger import log_info, log_error
from ai_model import ai_confidence_boost


# =========================================================
# MARKET BIAS (NEW CORE FILTER)
# =========================================================
def get_market_bias(trend_df):

    try:
        close = trend_df['close'].iloc[-2]

        ema50 = trend_df['ema50'].iloc[-2]
        ema200 = trend_df['ema200'].iloc[-2]

        if ema50 > ema200 and close > ema50:
            return "BULLISH"

        if ema50 < ema200 and close < ema50:
            return "BEARISH"

        return "RANGE"

    except Exception as e:
        log_error(f"BIAS ERROR: {e}")
        return "RANGE"


# =========================================================
# STRUCTURE
# =========================================================
def detect_structure(df):

    try:
        swing_high = df['high'].rolling(10).max().iloc[-6]
        swing_low = df['low'].rolling(10).min().iloc[-6]

        hh = df['high'].iloc[-2] > swing_high
        ll = df['low'].iloc[-2] < swing_low

        bos_up = df['close'].iloc[-1] > swing_high
        bos_down = df['close'].iloc[-1] < swing_low

        return {
            "bos_up": bos_up,
            "bos_down": bos_down,
            "hh": hh,
            "ll": ll
        }

    except Exception as e:
        log_error(f"STRUCTURE ERROR: {e}")
        return {"bos_up": False, "bos_down": False, "hh": False, "ll": False}


# =========================================================
# LIQUIDITY (EQ HIGH / LOW)
# =========================================================
def detect_liquidity(df):

    try:
        eq_highs = abs(df['high'].iloc[-1] - df['high'].iloc[-3]) / df['high'].iloc[-1] < 0.002
        eq_lows = abs(df['low'].iloc[-1] - df['low'].iloc[-3]) / df['low'].iloc[-1] < 0.002

        return eq_highs, eq_lows

    except Exception:
        return False, False


# =========================================================
# DISPLACEMENT
# =========================================================
def detect_displacement(df):

    try:
        body = abs(df['close'].iloc[-1] - df['open'].iloc[-1])
        prev_body = abs(df['close'].iloc[-2] - df['open'].iloc[-2])

        return body > prev_body * 1.5

    except Exception:
        return False


# =========================================================
# ORDER BLOCK
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
# MAIN SIGNAL ENGINE (SMC v2 + BIAS FILTER)
# =========================================================
def check_signal(trend_df, confirm_df, entry_df, btc_trend, btc_corr, rs):

    try:

        price = entry_df['close'].iloc[-1]

        # ======================
        # MARKET BIAS (NEW GATE)
        # ======================
        bias = get_market_bias(trend_df)

        # ======================
        # SMC CORE
        # ======================
        structure = detect_structure(trend_df)
        eq_highs, eq_lows = detect_liquidity(confirm_df)
        displacement = detect_displacement(confirm_df)
        ob_high, ob_low, ob_type = get_order_block_v2(confirm_df)

        in_ob = ob_low is not None and ob_low <= price <= ob_high

        # ======================
        # BUY CONDITION
        # ======================
        buy_condition = (
            structure["bos_up"]
            #and eq_lows
            and displacement
            and ob_type == "BULLISH"
            and in_ob
        )

        # ======================
        # SELL CONDITION
        # ======================
        sell_condition = (
            structure["bos_down"]
            #and eq_highs
            and displacement
            and ob_type == "BEARISH"
            and in_ob
        )

        signal = None

        if buy_condition:
            signal = "BUY"

        elif sell_condition:
            signal = "SELL"

        if signal is None:
            return None

        # =========================================================
        # 🚨 MARKET BIAS FILTER (MAIN FIX FOR YOUR PROBLEM)
        # =========================================================

        if bias == "BULLISH" and signal == "SELL":
            log_info("SELL BLOCKED - AGAINST BULLISH BIAS")
            return None

        if bias == "BEARISH" and signal == "BUY":
            log_info("BUY BLOCKED - AGAINST BEARISH BIAS")
            return None

        # =========================================================
        # AI FILTER (UNCHANGED ROLE)
        # =========================================================
        ai_score = ai_confidence_boost(
            trend_df,
            confirm_df,
            entry_df,
            signal,
            btc_trend,
            btc_corr,
            rs
        )

        if ai_score < -5:
            log_info(f"AI REJECTED SIGNAL | score={ai_score}")
            return None

        log_info(f"SMC SIGNAL CONFIRMED | bias={bias} AI={ai_score}")

        return signal

    except Exception as e:
        log_error(f"SMC ERROR: {e}")
        return None