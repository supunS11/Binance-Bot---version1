import config
from logger import log_info, log_error, log_warning
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
        candle = df.iloc[-2]
        atr = candle["atr"]
        atr_mult = get_config_float("FLOW_SL_ATR_MULT", 1.2)

        if atr <= 0:
            return None

        if not getattr(config, "STRUCTURE_SL_ENABLED", True):
            if side == "BUY":
                return candle["close"] - (atr * atr_mult)

            return candle["close"] + (atr * atr_mult)

        lookback = get_config_int("STRUCTURE_SL_LOOKBACK", 8)
        buffer_mult = get_config_float("STRUCTURE_SL_ATR_BUFFER_MULT", 0.25)
        min_atr_mult = get_config_float("STRUCTURE_SL_MIN_ATR_MULT", 0.55)
        max_atr_mult = get_config_float("STRUCTURE_SL_MAX_ATR_MULT", atr_mult)
        buffer = atr * buffer_mult
        recent = df.iloc[-(lookback + 1):-1]

        if recent.empty:
            recent = df.iloc[-3:-1]

        if side == "BUY":
            raw_sl = recent["low"].min() - buffer
            min_distance_sl = candle["close"] - (atr * min_atr_mult)
            max_distance_sl = candle["close"] - (atr * max_atr_mult)

            sl_price = min(raw_sl, min_distance_sl)
            return max(sl_price, max_distance_sl)

        raw_sl = recent["high"].max() + buffer
        min_distance_sl = candle["close"] + (atr * min_atr_mult)
        max_distance_sl = candle["close"] + (atr * max_atr_mult)

        sl_price = max(raw_sl, min_distance_sl)
        return min(sl_price, max_distance_sl)

    except Exception as e:
        log_error(f"FLOW SL ERROR: {e}")
        return None


# =========================================================
# POST-SIGNAL ENTRY QUALITY FILTER
# =========================================================
def validate_entry_quality(signal, entry_df, trend_df, current_price, sl_price):

    try:

        entry = entry_df.iloc[-2]
        candle_range = entry["high"] - entry["low"]
        candle_body = abs(entry["close"] - entry["open"])
        atr = entry["atr"]

        if current_price <= 0 or sl_price <= 0 or atr <= 0:
            return False, "INVALID ENTRY QUALITY DATA"

        if candle_range <= 0:
            return False, "INVALID SIGNAL CANDLE RANGE"

        risk = abs(current_price - sl_price)

        if risk <= 0:
            return False, "INVALID ENTRY RISK"

        ema20_distance_pct = (
            abs(current_price - entry["ema20"]) / entry["ema20"]
        ) * 100

        max_ema_distance_pct = getattr(config, "MAX_ENTRY_EMA20_DISTANCE_PCT", 1.3)
        max_signal_candle_atr = getattr(config, "MAX_SIGNAL_CANDLE_ATR", 1.8)
        min_sr_room_r = getattr(config, "MIN_ENTRY_SR_ROOM_R", 0.8)
        ema20_tolerance_pct = get_config_float("ENTRY_EMA20_TOLERANCE_PCT", 0.12)
        min_body_ratio = get_config_float("MIN_SIGNAL_BODY_RATIO", 0.18)
        min_close_position = get_config_float("MIN_SIGNAL_CLOSE_POSITION", 0.45)
        max_close_position = get_config_float("MAX_SIGNAL_CLOSE_POSITION", 0.88)

        if ema20_distance_pct > max_ema_distance_pct:
            return (False, f"ENTRY TOO FAR FROM EMA20: {ema20_distance_pct:.2f}%")

        if candle_range > atr * max_signal_candle_atr:
            return (False, f"SIGNAL CANDLE TOO LARGE: {candle_range / atr:.2f} ATR")

        if candle_body / candle_range < min_body_ratio:
            return False, "SIGNAL CANDLE BODY TOO WEAK"

        if signal == "BUY":

            min_buy_price = entry["ema20"] * (1 - ema20_tolerance_pct / 100)

            if current_price < min_buy_price:
                return False, "BUY ENTRY BELOW EMA20"

            close_position = (entry["close"] - entry["low"]) / candle_range

            if close_position < min_close_position:
                return False, "BUY WEAK CANDLE CLOSE"

            if close_position > max_close_position:
                return False, f"BUY ENTRY TOO CLOSE TO CANDLE HIGH: {close_position:.2f}"

            if sl_price >= current_price:
                return False, "BUY SL ABOVE ENTRY"

        elif signal == "SELL":

            max_sell_price = entry["ema20"] * (1 + ema20_tolerance_pct / 100)

            if current_price > max_sell_price:
                return False, "SELL ENTRY ABOVE EMA20"

            close_position = (entry["high"] - entry["close"]) / candle_range

            if close_position < min_close_position:
                return False, "SELL WEAK CANDLE CLOSE"

            if close_position > max_close_position:
                return False, f"SELL ENTRY TOO CLOSE TO CANDLE LOW: {close_position:.2f}"

            if sl_price <= current_price:
                return False, "SELL SL BELOW ENTRY"

        else:
            return False, "UNKNOWN SIGNAL"

        support, resistance = get_support_resistance(trend_df)

        if signal == "BUY" and resistance is not None and resistance > current_price:
            room_r = (resistance - current_price) / risk

            if room_r < min_sr_room_r:
                return False, f"BUY RESISTANCE TOO CLOSE: {room_r:.2f}R"

        if signal == "SELL" and support is not None and support < current_price:
            room_r = (current_price - support) / risk

            if room_r < min_sr_room_r:
                return False, f"SELL SUPPORT TOO CLOSE: {room_r:.2f}R"

        return True, "ENTRY QUALITY OK"

    except Exception as e:
        log_error(f"ENTRY QUALITY ERROR: {e}")
        return False, "ENTRY QUALITY ERROR"


def validate_live_entry_timing(signal, entry_df, live_price):

    try:
        signal_candle = entry_df.iloc[-2]
        live_candle = entry_df.iloc[-1]
        atr = signal_candle["atr"]

        if live_price <= 0 or atr <= 0:
            return False, "INVALID LIVE ENTRY DATA"

        retrace_atr = get_config_float("MAX_LIVE_ENTRY_RETRACE_ATR", 0.25)
        chase_atr = get_config_float("MAX_LIVE_ENTRY_CHASE_ATR", 0.35)
        ema_tolerance_pct = get_config_float("LIVE_ENTRY_EMA_TOLERANCE_PCT", 0.08)

        signal_close = signal_candle["close"]
        ema20 = signal_candle["ema20"]
        vwap = signal_candle["vwap"] if "vwap" in signal_candle.index else None
        live_open = live_candle["open"]
        live_high = max(live_candle["high"], live_price)
        live_low = min(live_candle["low"], live_price)
        live_range = live_high - live_low
        max_live_position = get_config_float("MAX_LIVE_ENTRY_CLOSE_POSITION", 0.88)

        if live_range <= 0:
            return False, "INVALID LIVE CANDLE RANGE"

        if signal == "BUY":
            live_position = (live_price - live_low) / live_range

            if live_price < ema20 * (1 - ema_tolerance_pct / 100):
                return False, "LIVE BUY BELOW EMA20"

            if vwap is not None and live_price < vwap:
                return False, "LIVE BUY BELOW VWAP"

            if live_price < signal_close - (atr * retrace_atr):
                return False, "LIVE BUY RETRACED TOO MUCH"

            if live_price > signal_close + (atr * chase_atr):
                return False, "LIVE BUY CHASING TOO FAR"

            if live_position > max_live_position:
                return False, f"LIVE BUY TOO CLOSE TO CANDLE HIGH: {live_position:.2f}"

            if live_price < live_open:
                return False, "LIVE BUY CURRENT CANDLE WEAK"

        elif signal == "SELL":
            live_position = (live_high - live_price) / live_range

            if live_price > ema20 * (1 + ema_tolerance_pct / 100):
                return False, "LIVE SELL ABOVE EMA20"

            if vwap is not None and live_price > vwap:
                return False, "LIVE SELL ABOVE VWAP"

            if live_price > signal_close + (atr * retrace_atr):
                return False, "LIVE SELL RETRACED TOO MUCH"

            if live_price < signal_close - (atr * chase_atr):
                return False, "LIVE SELL CHASING TOO FAR"

            if live_position > max_live_position:
                return False, f"LIVE SELL TOO CLOSE TO CANDLE LOW: {live_position:.2f}"

            if live_price > live_open:
                return False, "LIVE SELL CURRENT CANDLE WEAK"

        else:
            return False, "UNKNOWN LIVE SIGNAL"

        return True, "LIVE ENTRY TIMING OK"

    except Exception as e:
        log_error(f"LIVE ENTRY TIMING ERROR: {e}")
        return False, "LIVE ENTRY TIMING ERROR"


def validate_open_trade_flow(side, entry_df, live_price):

    try:
        live_candle = entry_df.iloc[-1]

        if live_price <= 0:
            return False, "INVALID OPEN TRADE PRICE"

        ema20 = live_candle["ema20"]
        vwap = live_candle["vwap"] if "vwap" in live_candle.index else None
        live_open = live_candle["open"]
        ema_tolerance_pct = get_config_float(
            "EARLY_FLOW_EXIT_EMA_TOLERANCE_PCT",
            0.05
        )

        if side == "BUY":
            if live_price < ema20 * (1 - ema_tolerance_pct / 100):
                return False, "BUY FLOW BROKE EMA20"

            if vwap is not None and live_price < vwap:
                return False, "BUY FLOW BROKE VWAP"

            if live_price < live_open:
                return False, "BUY LIVE CANDLE TURNED RED"

        elif side == "SELL":
            if live_price > ema20 * (1 + ema_tolerance_pct / 100):
                return False, "SELL FLOW BROKE EMA20"

            if vwap is not None and live_price > vwap:
                return False, "SELL FLOW BROKE VWAP"

            if live_price > live_open:
                return False, "SELL LIVE CANDLE TURNED GREEN"

        else:
            return False, "UNKNOWN OPEN TRADE SIDE"

        return True, "OPEN TRADE FLOW OK"

    except Exception as e:
        log_error(f"OPEN TRADE FLOW ERROR: {e}")
        return True, "OPEN TRADE FLOW CHECK FAILED"


# =========================================================
# LIQUIDITY SWEEP DETECTION (UNCHANGED)
# =========================================================
def detect_liquidity_sweep(df):

    try:

        prev_high = df["high"].iloc[-12:-2].max()
        prev_low = df["low"].iloc[-12:-2].min()

        last_high = df["high"].iloc[-2]
        last_low = df["low"].iloc[-2]
        close = df["close"].iloc[-2]

        bullish_sweep = last_low < prev_low and close > prev_low

        bearish_sweep = last_high > prev_high and close < prev_high

        return bullish_sweep, bearish_sweep

    except Exception:
        return False, False


# =========================================================
# MARKET STRUCTURE DETECTION (BOS / CHOCH CONTEXT)
# =========================================================
def detect_market_structure(df):

    try:

        recent_high = df["high"].iloc[-20:-5].max()
        recent_low = df["low"].iloc[-20:-5].min()
        prev_high = df["high"].iloc[-35:-20].max()
        prev_low = df["low"].iloc[-35:-20].min()
        last_close = df["close"].iloc[-2]

        bullish_structure = recent_high > prev_high and recent_low > prev_low

        bearish_structure = recent_high < prev_high and recent_low < prev_low

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
            "bearish_choch": bearish_choch,
        }

    except Exception:
        return {
            "bullish_structure": False,
            "bearish_structure": False,
            "bullish_bos": False,
            "bearish_bos": False,
            "bullish_choch": False,
            "bearish_choch": False,
        }


def add_score(score, condition, points):
    return score + points if condition else score


def build_signal_result(signal, confidence, return_confidence):

    if return_confidence:
        return signal, confidence

    return signal


def get_config_float(name, default):
    try:
        return float(getattr(config, name, default))
    except (TypeError, ValueError):
        return default


def get_config_int(name, default):
    try:
        return int(getattr(config, name, default))
    except (TypeError, ValueError):
        return default


def pct_distance(a, b):
    if b == 0:
        return 0

    return abs(a - b) / b * 100


def count_direction_candles(df, side, lookback=6):
    count = 0

    for i in range(2, lookback + 2):
        candle = df.iloc[-i]

        if side == "BUY" and candle["close"] > candle["open"]:
            count += 1
        elif side == "SELL" and candle["close"] < candle["open"]:
            count += 1
        else:
            break

    return count


def is_late_entry(side, entry_df, trend_df, entry, atr_pct):
    atr = entry["atr"]

    if atr <= 0:
        return True, "INVALID ATR"

    max_late_atr = get_config_float("MAX_LATE_ENTRY_ATR", 1.8)
    max_direction_candles = get_config_int("MAX_DIRECTION_CANDLES", 4)
    max_chase_distance_pct = get_config_float("MAX_CHASE_DISTANCE_PCT", 0.45)

    ema_extension_atr = abs(entry["close"] - entry["ema20"]) / atr

    if ema_extension_atr > max_late_atr:
        return True, f"LATE ENTRY EMA EXTENSION: {ema_extension_atr:.2f} ATR"

    direction_candles = count_direction_candles(entry_df, side)

    if direction_candles >= max_direction_candles:
        return True, f"LATE ENTRY AFTER {direction_candles} CANDLES"

    recent_high = trend_df["high"].iloc[-20:-5].max()
    recent_low = trend_df["low"].iloc[-20:-5].min()

    if side == "BUY":
        chase_distance = (
            (entry["close"] - recent_high) / recent_high * 100
            if entry["close"] > recent_high
            else 0
        )

        if chase_distance > max_chase_distance_pct:
            return True, f"BUY CHASING BREAKOUT: {chase_distance:.2f}%"

        if entry["rsi"] > get_config_float("BUY_RSI_OVERHEAT", 72):
            return True, f"BUY RSI OVERHEATED: {entry['rsi']:.2f}"

    else:
        chase_distance = (
            (recent_low - entry["close"]) / recent_low * 100
            if entry["close"] < recent_low
            else 0
        )

        if chase_distance > max_chase_distance_pct:
            return True, f"SELL CHASING BREAKDOWN: {chase_distance:.2f}%"

        if entry["rsi"] < get_config_float("SELL_RSI_OVERHEAT", 28):
            return True, f"SELL RSI OVERHEATED: {entry['rsi']:.2f}"

    return False, "ENTRY TIMING OK"


def log_gate_state(name, **gates):
    failed = [key for key, value in gates.items() if not value]

    if failed:
        log_info(f"{name} blocked by: {', '.join(failed)}")
    else:
        log_info(f"{name} gates passed")


def apply_market_context_score(side, score, confirm, trend, btc_trend, btc_corr, rs):
    try:
        confirm_adx = confirm["adx"]
        sideways_adx = get_config_float("SIDEWAYS_ADX", 15)
        trending_adx = get_config_float("TRENDING_ADX", 25)

        if confirm_adx >= trending_adx:
            score += 2
        elif confirm_adx <= sideways_adx:
            score -= 2

        if side == "BUY":
            if confirm["close"] > confirm["ema20"]:
                score += 2
            else:
                score -= 1

            if trend["close"] > trend["ema50"]:
                score += 2
            else:
                score -= 1

            if btc_corr is not None and btc_corr >= 0.65:
                if btc_trend == "BULLISH":
                    score += 2
                elif btc_trend == "BEARISH":
                    score -= 3

            if rs is not None:
                if rs > 2:
                    score += 2
                elif rs < -2:
                    score -= 2

        elif side == "SELL":
            if confirm["close"] < confirm["ema20"]:
                score += 2
            else:
                score -= 1

            if trend["close"] < trend["ema50"]:
                score += 2
            else:
                score -= 1

            if btc_corr is not None and btc_corr >= 0.65:
                if btc_trend == "BEARISH":
                    score += 2
                elif btc_trend == "BULLISH":
                    score -= 3

            if rs is not None:
                if rs < -2:
                    score += 2
                elif rs > 2:
                    score -= 2

        return score

    except Exception as e:
        log_warning(f"MARKET CONTEXT SCORE ERROR: {e}")
        return score


# =========================================================
# MAIN SIGNAL ENGINE
# =========================================================
def check_signal(
    trend_df, confirm_df, entry_df, btc_trend, btc_corr, rs, return_confidence=False
):

    try:
        entry = entry_df.iloc[-2]
        prev_entry = entry_df.iloc[-3]
        prev2_entry = entry_df.iloc[-4]
        confirm = confirm_df.iloc[-2]
        trend = trend_df.iloc[-2]
        atr_pct = (entry["atr"] / entry["close"]) * 100
        ema20_distance = pct_distance(entry["close"], entry["ema20"])
        vwap_available = "vwap" in entry.index

        log_info(
            f"ATR%: {round(atr_pct, 2)} | "
            f"EMA20 DIST: {round(ema20_distance, 2)}%"
        )

        if (
            atr_pct < get_config_float("MIN_ATR_PCT", 0.15)
            or atr_pct > get_config_float("MAX_ATR_PCT", 3.2)
        ):
            log_info(f"ATR FILTER BLOCKED | ATR%: {round(atr_pct, 2)}")
            return build_signal_result(None, 0, return_confidence)

        max_ema20_distance = get_config_float("MAX_SIGNAL_EMA20_DISTANCE_PCT", 1.2)

        if ema20_distance > max_ema20_distance:
            log_warning(f"EMA20 TOO FAR: {round(ema20_distance, 2)}%")
            return build_signal_result(None, 0, return_confidence)

        min_volume_sma_mult = get_config_float("MIN_VOLUME_SMA_MULT", 1.0)
        volume_ok = entry["volume"] >= entry["volume_sma"] * min_volume_sma_mult
        candle_range = entry["high"] - entry["low"]

        if candle_range <= 0:
            log_warning("NO SIGNAL | INVALID SIGNAL CANDLE RANGE")
            return build_signal_result(None, 0, return_confidence)

        candle_body = abs(entry["close"] - entry["open"])
        body_ratio = candle_body / candle_range if candle_range > 0 else 0
        min_body_ratio = get_config_float("MIN_SIGNAL_BODY_RATIO", 0.12)
        min_close_position = get_config_float("MIN_SIGNAL_CLOSE_POSITION", 0.45)
        max_close_position = get_config_float("MAX_SIGNAL_CLOSE_POSITION", 0.88)
        max_rejection_wick_ratio = get_config_float(
            "MAX_SIGNAL_REJECTION_WICK_RATIO",
            0.55
        )
        min_momentum_atr = get_config_float("MIN_SIGNAL_MOMENTUM_ATR", 0.03)
        upper_wick = entry["high"] - max(entry["open"], entry["close"])
        lower_wick = min(entry["open"], entry["close"]) - entry["low"]
        upper_wick_ratio = upper_wick / candle_range if candle_range > 0 else 0
        lower_wick_ratio = lower_wick / candle_range if candle_range > 0 else 0
        buy_close_position = (
            (entry["close"] - entry["low"]) / candle_range
            if candle_range > 0
            else 0
        )
        sell_close_position = (
            (entry["high"] - entry["close"]) / candle_range
            if candle_range > 0
            else 0
        )
        wick_filter_enabled = getattr(config, "SIGNAL_WICK_FILTER_ENABLED", True)
        buy_wick_ok = (
            not wick_filter_enabled
            or upper_wick_ratio <= max_rejection_wick_ratio
        )
        sell_wick_ok = (
            not wick_filter_enabled
            or lower_wick_ratio <= max_rejection_wick_ratio
        )
        momentum_filter_enabled = getattr(
            config,
            "SIGNAL_MOMENTUM_FILTER_ENABLED",
            True
        )
        buy_momentum_ok = (
            not momentum_filter_enabled
            or entry["close"] >= prev_entry["close"] + (entry["atr"] * min_momentum_atr)
        )
        sell_momentum_ok = (
            not momentum_filter_enabled
            or entry["close"] <= prev_entry["close"] - (entry["atr"] * min_momentum_atr)
        )
        buy_close_ok = buy_close_position >= min_close_position
        sell_close_ok = sell_close_position >= min_close_position
        buy_not_at_top = buy_close_position <= max_close_position
        sell_not_at_bottom = sell_close_position <= max_close_position
        ema_slope = entry["ema20"] - prev_entry["ema20"]
        prev_ema_slope = prev_entry["ema20"] - prev2_entry["ema20"]
        slope_tolerance = entry["atr"] * get_config_float(
            "FLOW_EMA_SLOPE_TOLERANCE_ATR", 0.05
        )

        above_ema = entry["close"] > entry["ema20"]
        below_ema = entry["close"] < entry["ema20"]
        above_vwap = not vwap_available or entry["close"] > entry["vwap"]
        below_vwap = not vwap_available or entry["close"] < entry["vwap"]
        bullish_candle = entry["close"] > entry["open"]
        bearish_candle = entry["close"] < entry["open"]
        bullish_slope = ema_slope >= -slope_tolerance or prev_ema_slope > 0
        bearish_slope = ema_slope <= slope_tolerance or prev_ema_slope < 0
        buy_rsi_ok = entry["rsi"] >= get_config_float("FLOW_BUY_MIN_RSI", 47)
        sell_rsi_ok = entry["rsi"] <= get_config_float("FLOW_SELL_MAX_RSI", 53)
        buy_not_overheated = entry["rsi"] <= get_config_float("FLOW_BUY_MAX_RSI", 72)
        sell_not_overheated = entry["rsi"] >= get_config_float("FLOW_SELL_MIN_RSI", 28)

        previous_buy_flow = (
            prev_entry["close"] > prev_entry["ema20"]
            and (not vwap_available or prev_entry["close"] > prev_entry["vwap"])
        )
        previous_sell_flow = (
            prev_entry["close"] < prev_entry["ema20"]
            and (not vwap_available or prev_entry["close"] < prev_entry["vwap"])
        )

        buy_score = 0
        buy_score = add_score(buy_score, above_ema, 3)
        buy_score = add_score(buy_score, above_vwap, 3)
        buy_score = add_score(buy_score, bullish_candle, 2)
        buy_score = add_score(buy_score, bullish_slope, 1)
        buy_score = add_score(buy_score, buy_rsi_ok, 1)
        buy_score = add_score(buy_score, buy_not_overheated, 1)
        buy_score = add_score(buy_score, volume_ok, 1)
        buy_score = add_score(buy_score, not previous_buy_flow, 1)
        buy_score = add_score(buy_score, body_ratio >= min_body_ratio, 1)
        buy_score = add_score(buy_score, buy_wick_ok, 1)
        buy_score = add_score(buy_score, buy_close_ok, 1)
        buy_score = add_score(buy_score, buy_momentum_ok, 1)

        sell_score = 0
        sell_score = add_score(sell_score, below_ema, 3)
        sell_score = add_score(sell_score, below_vwap, 3)
        sell_score = add_score(sell_score, bearish_candle, 2)
        sell_score = add_score(sell_score, bearish_slope, 1)
        sell_score = add_score(sell_score, sell_rsi_ok, 1)
        sell_score = add_score(sell_score, sell_not_overheated, 1)
        sell_score = add_score(sell_score, volume_ok, 1)
        sell_score = add_score(sell_score, not previous_sell_flow, 1)
        sell_score = add_score(sell_score, body_ratio >= min_body_ratio, 1)
        sell_score = add_score(sell_score, sell_wick_ok, 1)
        sell_score = add_score(sell_score, sell_close_ok, 1)
        sell_score = add_score(sell_score, sell_momentum_ok, 1)

        buy_valid = (
            above_ema
            and above_vwap
            and bullish_candle
            and buy_rsi_ok
            and buy_not_overheated
            and body_ratio >= min_body_ratio
            and buy_wick_ok
            and buy_close_ok
            and buy_not_at_top
            and buy_momentum_ok
        )
        sell_valid = (
            below_ema
            and below_vwap
            and bearish_candle
            and sell_rsi_ok
            and sell_not_overheated
            and body_ratio >= min_body_ratio
            and sell_wick_ok
            and sell_close_ok
            and sell_not_at_bottom
            and sell_momentum_ok
        )

        buy_late, buy_late_reason = is_late_entry(
            "BUY",
            entry_df,
            trend_df,
            entry,
            atr_pct
        )
        sell_late, sell_late_reason = is_late_entry(
            "SELL",
            entry_df,
            trend_df,
            entry,
            atr_pct
        )

        late_penalty = get_config_float("LATE_ENTRY_SCORE_PENALTY", 2)

        if buy_late:
            buy_score -= late_penalty
            log_info(
                f"BUY FLOW late penalty {late_penalty}: {buy_late_reason}"
            )

        if sell_late:
            sell_score -= late_penalty
            log_info(
                f"SELL FLOW late penalty {late_penalty}: {sell_late_reason}"
            )

        buy_score = apply_market_context_score(
            "BUY",
            buy_score,
            confirm,
            trend,
            btc_trend,
            btc_corr,
            rs
        )
        sell_score = apply_market_context_score(
            "SELL",
            sell_score,
            confirm,
            trend,
            btc_trend,
            btc_corr,
            rs
        )

        buy_conf = score_to_confidence(buy_score, 25)
        sell_conf = score_to_confidence(sell_score, 25)

        entry_structure = detect_market_structure(entry_df)
        trend_structure = detect_market_structure(trend_df)
        bullish_sweep, bearish_sweep = detect_liquidity_sweep(entry_df)

        prev_above_ema = prev_entry["close"] > prev_entry["ema20"]
        prev_below_ema = prev_entry["close"] < prev_entry["ema20"]
        prev_above_vwap = (
            not vwap_available
            or prev_entry["close"] > prev_entry["vwap"]
        )
        prev_below_vwap = (
            not vwap_available
            or prev_entry["close"] < prev_entry["vwap"]
        )
        buy_ema_reclaim = prev_below_ema and above_ema
        sell_ema_reject = prev_above_ema and below_ema
        buy_vwap_reclaim = prev_below_vwap and above_vwap
        sell_vwap_reject = prev_above_vwap and below_vwap
        buy_rsi_recovery = (
            entry["rsi"] >= get_config_float("SCALP_BUY_MIN_RSI", 48)
            and entry["rsi"] > prev_entry["rsi"]
            and entry["rsi"] <= get_config_float("FLOW_BUY_MAX_RSI", 72)
        )
        sell_rsi_rejection = (
            entry["rsi"] <= get_config_float("SCALP_SELL_MAX_RSI", 52)
            and entry["rsi"] < prev_entry["rsi"]
            and entry["rsi"] >= get_config_float("FLOW_SELL_MIN_RSI", 28)
        )
        require_reversal_structure = getattr(
            config,
            "SCALP_REQUIRE_STRUCTURE",
            True
        )

        buy_reversal_trigger = (
            bullish_sweep
            or entry_structure["bullish_choch"]
            or buy_ema_reclaim
            or buy_vwap_reclaim
        )
        sell_reversal_trigger = (
            bearish_sweep
            or entry_structure["bearish_choch"]
            or sell_ema_reject
            or sell_vwap_reject
        )
        buy_reversal_structure_ok = (
            not require_reversal_structure
            or bullish_sweep
            or entry_structure["bullish_choch"]
            or trend_structure["bullish_choch"]
            or buy_ema_reclaim
            or buy_vwap_reclaim
        )
        sell_reversal_structure_ok = (
            not require_reversal_structure
            or bearish_sweep
            or entry_structure["bearish_choch"]
            or trend_structure["bearish_choch"]
            or sell_ema_reject
            or sell_vwap_reject
        )

        buy_reversal_score = 0
        buy_reversal_score = add_score(buy_reversal_score, bullish_sweep, 4)
        buy_reversal_score = add_score(
            buy_reversal_score,
            entry_structure["bullish_choch"] or trend_structure["bullish_choch"],
            4
        )
        buy_reversal_score = add_score(buy_reversal_score, buy_ema_reclaim, 3)
        buy_reversal_score = add_score(buy_reversal_score, buy_vwap_reclaim, 3)
        buy_reversal_score = add_score(buy_reversal_score, above_ema, 2)
        buy_reversal_score = add_score(buy_reversal_score, above_vwap, 2)
        buy_reversal_score = add_score(buy_reversal_score, bullish_candle, 2)
        buy_reversal_score = add_score(buy_reversal_score, buy_close_ok, 2)
        buy_reversal_score = add_score(buy_reversal_score, buy_rsi_recovery, 2)
        buy_reversal_score = add_score(buy_reversal_score, volume_ok, 1)
        buy_reversal_score = add_score(
            buy_reversal_score,
            (
                getattr(config, "REVERSAL_MOMENTUM_SCORE_ENABLED", True)
                and buy_momentum_ok
            ),
            1
        )
        buy_reversal_score = add_score(
            buy_reversal_score,
            body_ratio >= min_body_ratio,
            1
        )
        buy_reversal_score = add_score(buy_reversal_score, buy_wick_ok, 1)
        buy_reversal_score = apply_market_context_score(
            "BUY",
            buy_reversal_score,
            confirm,
            trend,
            btc_trend,
            btc_corr,
            rs
        )

        sell_reversal_score = 0
        sell_reversal_score = add_score(sell_reversal_score, bearish_sweep, 4)
        sell_reversal_score = add_score(
            sell_reversal_score,
            entry_structure["bearish_choch"] or trend_structure["bearish_choch"],
            4
        )
        sell_reversal_score = add_score(sell_reversal_score, sell_ema_reject, 3)
        sell_reversal_score = add_score(sell_reversal_score, sell_vwap_reject, 3)
        sell_reversal_score = add_score(sell_reversal_score, below_ema, 2)
        sell_reversal_score = add_score(sell_reversal_score, below_vwap, 2)
        sell_reversal_score = add_score(sell_reversal_score, bearish_candle, 2)
        sell_reversal_score = add_score(sell_reversal_score, sell_close_ok, 2)
        sell_reversal_score = add_score(sell_reversal_score, sell_rsi_rejection, 2)
        sell_reversal_score = add_score(sell_reversal_score, volume_ok, 1)
        sell_reversal_score = add_score(
            sell_reversal_score,
            (
                getattr(config, "REVERSAL_MOMENTUM_SCORE_ENABLED", True)
                and sell_momentum_ok
            ),
            1
        )
        sell_reversal_score = add_score(
            sell_reversal_score,
            body_ratio >= min_body_ratio,
            1
        )
        sell_reversal_score = add_score(sell_reversal_score, sell_wick_ok, 1)
        sell_reversal_score = apply_market_context_score(
            "SELL",
            sell_reversal_score,
            confirm,
            trend,
            btc_trend,
            btc_corr,
            rs
        )

        buy_reversal_valid = (
            buy_reversal_trigger
            and buy_reversal_structure_ok
            and above_ema
            and above_vwap
            and bullish_candle
            and buy_rsi_recovery
            and body_ratio >= min_body_ratio
            and buy_wick_ok
            and buy_close_ok
            and buy_not_at_top
        )
        sell_reversal_valid = (
            sell_reversal_trigger
            and sell_reversal_structure_ok
            and below_ema
            and below_vwap
            and bearish_candle
            and sell_rsi_rejection
            and body_ratio >= min_body_ratio
            and sell_wick_ok
            and sell_close_ok
            and sell_not_at_bottom
        )

        buy_reversal_conf = score_to_confidence(buy_reversal_score, 29)
        sell_reversal_conf = score_to_confidence(sell_reversal_score, 29)

        log_gate_state(
            "BUY FLOW",
            ema=above_ema,
            vwap=above_vwap,
            candle=bullish_candle,
            rsi=buy_rsi_ok,
            body=body_ratio >= min_body_ratio,
            wick=buy_wick_ok,
            close=buy_close_ok,
            momentum=buy_momentum_ok,
        )
        log_gate_state(
            "SELL FLOW",
            ema=below_ema,
            vwap=below_vwap,
            candle=bearish_candle,
            rsi=sell_rsi_ok,
            body=body_ratio >= min_body_ratio,
            wick=sell_wick_ok,
            close=sell_close_ok,
            momentum=sell_momentum_ok,
        )

        log_info(
            f"BUY conf: {buy_conf}% | SELL conf: {sell_conf}% | "
            f"BODY: {body_ratio:.2f} | "
            f"BUY_CLOSE_POS: {buy_close_position:.2f} | "
            f"SELL_CLOSE_POS: {sell_close_position:.2f} | "
            f"UPPER_WICK: {upper_wick_ratio:.2f} | "
            f"LOWER_WICK: {lower_wick_ratio:.2f} | "
            f"EMA SLOPE: {ema_slope:.8f}"
        )

        log_gate_state(
            "BUY REVERSAL",
            trigger=buy_reversal_trigger,
            structure=buy_reversal_structure_ok,
            ema=above_ema,
            vwap=above_vwap,
            candle=bullish_candle,
            rsi=buy_rsi_recovery,
            body=body_ratio >= min_body_ratio,
            wick=buy_wick_ok,
            close=buy_close_ok,
            momentum=buy_momentum_ok,
        )
        log_gate_state(
            "SELL REVERSAL",
            trigger=sell_reversal_trigger,
            structure=sell_reversal_structure_ok,
            ema=below_ema,
            vwap=below_vwap,
            candle=bearish_candle,
            rsi=sell_rsi_rejection,
            body=body_ratio >= min_body_ratio,
            wick=sell_wick_ok,
            close=sell_close_ok,
            momentum=sell_momentum_ok,
        )

        log_info(
            f"BUY reversal conf: {buy_reversal_conf}% | "
            f"SELL reversal conf: {sell_reversal_conf}% | "
            f"BULL_SWEEP: {bullish_sweep} | BEAR_SWEEP: {bearish_sweep} | "
            f"BULL_CHOCH: {entry_structure['bullish_choch']} | "
            f"BEAR_CHOCH: {entry_structure['bearish_choch']}"
        )

        min_confidence = config.CONTINUATION_SIGNAL_THRESHOLD
        reversal_min_confidence = config.REVERSAL_SIGNAL_THRESHOLD
        candidates = []

        if buy_valid and buy_conf >= min_confidence:
            candidates.append(("BUY", "FLOW", buy_conf))

        if sell_valid and sell_conf >= min_confidence:
            candidates.append(("SELL", "FLOW", sell_conf))

        if buy_reversal_valid and buy_reversal_conf >= reversal_min_confidence:
            candidates.append(("BUY", "REVERSAL", buy_reversal_conf))

        if sell_reversal_valid and sell_reversal_conf >= reversal_min_confidence:
            candidates.append(("SELL", "REVERSAL", sell_reversal_conf))

        if not candidates:
            best_conf = max(
                buy_conf,
                sell_conf,
                buy_reversal_conf,
                sell_reversal_conf
            )
            return build_signal_result(None, best_conf, return_confidence)

        candidates.sort(key=lambda item: item[2], reverse=True)

        if (
            len(candidates) > 1
            and candidates[0][0] != candidates[1][0]
            and abs(candidates[0][2] - candidates[1][2]) < 10
        ):
            log_info("NO SIGNAL | COMPETING SIGNALS TOO CLOSE")
            return build_signal_result(None, candidates[0][2], return_confidence)

        signal, mode, confidence = candidates[0]

        log_info(f"FINAL {mode} {signal} CONFIDENCE: {confidence}")
        return build_signal_result(signal, confidence, return_confidence)

    except Exception as e:
        log_error(f"STRATEGY ERROR: {e}")
        return build_signal_result(None, 0, return_confidence)
