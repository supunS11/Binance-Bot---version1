import time
from datetime import datetime, timedelta

import config

from binance.enums import *

from exchange import (
    get_klines,
    get_balance,
    place_market_order,
    place_tp_sl,
    cancel_remaining_orders,
    get_open_position_counts,
    get_open_position_snapshot,
    setup_leverage,
    get_entry_price,
    get_mark_price,
    get_margin_balance,
    get_unrealized_pnl,
    get_realized_pnl_since,
    get_position_metrics,
    get_btc_trend,
    get_btc_correlation,
    get_relative_strength,
    calculate_rr_take_profit,
    calculate_static_roi_take_profit,
    calculate_adaptive_take_profit,
    validate_min_notional,
    close_market_position
)

from indicators import apply_indicators
from strategy import (
    check_signal,
    get_structure_stop_loss,
    validate_entry_quality,
    validate_live_entry_timing,
    validate_open_trade_flow,
    detect_market_structure
)
from risk_management import calculate_position_size
from logger import log_info, log_warning, log_error
from trade_journal import append_trade_event


trade_times = {}
cooldowns = {}
symbol_cooldowns = {}


def record_skip(skip_reasons, reason):
    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1


def log_skip_summary(skip_reasons):
    if not skip_reasons:
        log_info("SCAN SKIP SUMMARY: none")
        return

    summary = " | ".join(
        f"{reason}: {count}"
        for reason, count in sorted(
            skip_reasons.items(),
            key=lambda item: item[1],
            reverse=True
        )
    )
    log_info(f"SCAN SKIP SUMMARY: {summary}")


def close_tracked_trade(symbol, reason):
    trade = trade_times.get(symbol)

    if not trade:
        return False

    side = trade["side"]
    quantity = trade["quantity"]

    log_warning(f"{symbol} EARLY EXIT | {reason}")
    cancel_remaining_orders(symbol)

    order_side = SIDE_BUY if side == "BUY" else SIDE_SELL
    order = close_market_position(symbol, order_side, quantity)

    if not order:
        log_error(f"{symbol} EARLY EXIT FAILED | {reason}")
        return False

    trade["early_exit_reason"] = reason
    return True


def manage_open_trade(symbol):
    if symbol not in trade_times:
        return False

    trade = trade_times[symbol]
    metrics = get_position_metrics(symbol)

    if metrics is None:
        return False

    now = datetime.now()
    duration = now - trade["entry_time"]
    duration_minutes = duration.total_seconds() / 60
    current_roi = metrics["leveraged_roi"]
    peak_roi = trade.get("peak_roi", current_roi)
    peak_roi = max(peak_roi, current_roi)
    trade["peak_roi"] = peak_roi
    trade["last_roi"] = current_roi
    trade["last_unrealized_pnl"] = metrics["unrealized_pnl"]

    trigger_roi = trade.get(
        "profit_protection_trigger_roi",
        config.PROFIT_PROTECTION_TRIGGER_ROI
    )
    retrace_pct = config.PROFIT_PROTECTION_RETRACE_PCT

    log_info(
        f"{symbol} OPEN MANAGE | ROI={current_roi:.2f}% | "
        f"PEAK={peak_roi:.2f}% | PROTECT={trigger_roi:.2f}% | "
        f"MIN={duration_minutes:.1f}"
    )

    if (
        config.EARLY_FLOW_EXIT_ENABLED
        and duration_minutes >= config.EARLY_FLOW_EXIT_MINUTES
        and current_roi <= config.EARLY_FLOW_EXIT_MAX_ROI
    ):
        entry_df = get_klines(symbol, config.ENTRY_TIMEFRAME)
        live_price = get_mark_price(symbol)

        if entry_df is not None and live_price is not None:
            entry_df = apply_indicators(entry_df)

            if entry_df is not None:
                flow_ok, flow_reason = validate_open_trade_flow(
                    trade["side"],
                    entry_df,
                    live_price
                )

                log_info(
                    f"{symbol} EARLY FLOW CHECK | {flow_reason} | "
                    f"ROI={current_roi:.2f}% | LIVE={live_price}"
                )

                if not flow_ok:
                    return close_tracked_trade(
                        symbol,
                        f"EARLY FLOW EXIT {flow_reason} "
                        f"ROI {current_roi:.2f}% "
                        f"AFTER {duration_minutes:.1f}M"
                    )

    if config.PROFIT_PROTECTION_ENABLED and peak_roi >= trigger_roi:
        giveback_roi = peak_roi * (retrace_pct / 100)
        lock_roi = peak_roi - giveback_roi

        if current_roi <= lock_roi:
            return close_tracked_trade(
                symbol,
                f"PROFIT PROTECTION ROI {current_roi:.2f}% "
                f"FROM PEAK {peak_roi:.2f}%"
            )

    if (
        config.TIME_EXIT_ENABLED
        and duration_minutes >= config.TIME_EXIT_MINUTES
        and current_roi >= config.TIME_EXIT_MIN_ROI
    ):
        return close_tracked_trade(
            symbol,
            f"TIME PROFIT EXIT ROI {current_roi:.2f}% "
            f"AFTER {duration_minutes:.1f}M"
        )

    if (
        config.TIME_EXIT_ENABLED
        and duration_minutes >= config.STALE_EXIT_MINUTES
        and current_roi >= config.STALE_EXIT_MIN_ROI
    ):
        return close_tracked_trade(
            symbol,
            f"STALE EXIT ROI {current_roi:.2f}% "
            f"AFTER {duration_minutes:.1f}M"
        )

    return False


def finalize_tracked_trade(symbol, apply_loss_cooldown=True):
    if symbol not in trade_times:
        return

    exit_time = datetime.now()
    entry_time = trade_times[symbol]['entry_time']
    duration = exit_time - entry_time
    side = trade_times[symbol]['side']
    realized_pnl = get_realized_pnl_since(symbol, entry_time)

    if realized_pnl is None:
        outcome = "UNKNOWN"
    elif realized_pnl > 0:
        outcome = "WIN"
    elif realized_pnl < 0:
        outcome = "LOSS"
    else:
        outcome = "BREAKEVEN"

    close_data = trade_times[symbol].copy()
    close_data.update({
        "event": "CLOSE",
        "time": exit_time.isoformat(),
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "duration_seconds": int(duration.total_seconds()),
        "realized_pnl": realized_pnl,
        "outcome": outcome
    })
    append_trade_event(close_data)

    if outcome == "LOSS" and apply_loss_cooldown:
        cooldown_until = exit_time + timedelta(
            minutes=config.COOLDOWN_AFTER_SL_MINUTES
        )
        symbol_cooldown_until = exit_time + timedelta(
            minutes=config.SYMBOL_COOLDOWN_AFTER_LOSS_MINUTES
        )
        cooldowns[get_cooldown_key(symbol, side)] = cooldown_until
        symbol_cooldowns[symbol] = symbol_cooldown_until
        log_warning(f"{symbol} {side} COOLDOWN UNTIL {cooldown_until}")
        log_warning(f"{symbol} SYMBOL COOLDOWN UNTIL {symbol_cooldown_until}")

    log_info(
        f"*** {symbol} TRADE CLOSED *** | "
        f"ENTRY: {entry_time} | "
        f"EXIT: {exit_time} | "
        f"DURATION: {duration} | "
        f"OUTCOME: {outcome} | "
        f"PNL: {realized_pnl}"
    )

    del trade_times[symbol]


def get_cooldown_key(symbol, side):
    return f"{symbol}:{side}"


def is_symbol_in_cooldown(symbol):
    cooldown_until = symbol_cooldowns.get(symbol)

    if cooldown_until is None:
        return False

    if datetime.now() >= cooldown_until:
        del symbol_cooldowns[symbol]
        return False

    return True


def is_in_cooldown(symbol, side):

    cooldown_until = cooldowns.get(get_cooldown_key(symbol, side))

    if cooldown_until is None:
        return False

    if datetime.now() >= cooldown_until:
        del cooldowns[get_cooldown_key(symbol, side)]
        return False

    return True


def validate_tp_sl_direction(symbol, signal, entry_price, tp_price, sl_price):
    if signal == "BUY":
        if sl_price < entry_price < tp_price:
            return True

        log_error(
            f"{symbol} INVALID BUY TP/SL ORDER | "
            f"SL={sl_price} ENTRY={entry_price} TP={tp_price}"
        )
        return False

    if signal == "SELL":
        if tp_price < entry_price < sl_price:
            return True

        log_error(
            f"{symbol} INVALID SELL TP/SL ORDER | "
            f"TP={tp_price} ENTRY={entry_price} SL={sl_price}"
        )
        return False

    log_error(f"{symbol} INVALID SIGNAL FOR TP/SL VALIDATION: {signal}")
    return False


def tighten_stop_loss_to_final_rr(symbol, signal, entry_price, tp_price, sl_price):
    min_final_rr = getattr(config, "MIN_FINAL_TP_SL_RR", 1.1)

    if min_final_rr <= 0:
        return sl_price

    reward = abs(tp_price - entry_price)
    risk = abs(entry_price - sl_price)

    if reward <= 0 or risk <= 0:
        return sl_price

    current_rr = reward / risk

    if current_rr >= min_final_rr:
        return sl_price

    max_risk = reward / min_final_rr

    if signal == "BUY":
        adjusted_sl = entry_price - max_risk
    elif signal == "SELL":
        adjusted_sl = entry_price + max_risk
    else:
        return sl_price

    log_warning(
        f"{symbol} SL TIGHTENED FOR FINAL RR | "
        f"OLD_SL={sl_price} | NEW_SL={adjusted_sl} | "
        f"TP_UNCHANGED={tp_price} | RR {current_rr:.2f}->{min_final_rr:.2f}"
    )
    return adjusted_sl


def get_setup_snapshot(
    symbol,
    signal,
    signal_confidence,
    trade_leverage,
    entry_df,
    trend_df,
    btc_trend,
    btc_corr,
    rs,
    entry_quality
):

    entry = entry_df.iloc[-2]
    structure = detect_market_structure(trend_df)
    ema20_distance_pct = (
        abs(entry['close'] - entry['ema20']) / entry['ema20']
    ) * 100
    atr_pct = (entry['atr'] / entry['close']) * 100

    vwap_side = "NONE"

    if 'vwap' in entry.index:
        if entry['close'] > entry['vwap']:
            vwap_side = "ABOVE"
        elif entry['close'] < entry['vwap']:
            vwap_side = "BELOW"
        else:
            vwap_side = "AT"

    return {
        "symbol": symbol,
        "side": signal,
        "confidence": signal_confidence,
        "leverage": trade_leverage,
        "ema20_distance_pct": round(ema20_distance_pct, 4),
        "atr_pct": round(atr_pct, 4),
        "adx": round(float(entry['adx']), 4),
        "rsi": round(float(entry['rsi']), 4),
        "macd": round(float(entry['macd']), 8),
        "macd_signal": round(float(entry['macd_signal']), 8),
        "vwap_side": vwap_side,
        "btc_trend": btc_trend,
        "btc_corr": btc_corr,
        "relative_strength": rs,
        "entry_quality": entry_quality,
        **structure
    }


def get_symbol_klines(symbol):

    data = {}

    for timeframe in {
        config.TREND_TIMEFRAME,
        config.CONFIRMATION_TIMEFRAME,
        config.ENTRY_TIMEFRAME,
        config.SL_TIMEFRAME
    }:
        data[timeframe] = get_klines(symbol, timeframe)

        if data[timeframe] is None:
            return None, None, None, None

    return (
        data[config.TREND_TIMEFRAME],
        data[config.CONFIRMATION_TIMEFRAME],
        data[config.ENTRY_TIMEFRAME],
        data[config.SL_TIMEFRAME]
    )


def run_bot():

    log_info("BOT STARTED")

    while True:

        try:
            open_symbols, position_counts = get_open_position_snapshot()
            btc_trend = "NONE"
            skip_reasons = {}

            for symbol in config.SYMBOLS:

                try:

                    log_info(f"Checking {symbol}")

                    # =========================
                    # CLOSE TRACKING
                    # =========================
                    if symbol in trade_times:

                        if symbol in open_symbols:
                            if manage_open_trade(symbol):
                                open_symbols.discard(symbol)

                        if symbol not in open_symbols:

                            cancel_remaining_orders(symbol)

                            exit_time = datetime.now()
                            entry_time = trade_times[symbol]['entry_time']
                            duration = exit_time - entry_time
                            side = trade_times[symbol]['side']
                            realized_pnl = get_realized_pnl_since(
                                symbol,
                                entry_time
                            )

                            if realized_pnl is None:
                                outcome = "UNKNOWN"
                            elif realized_pnl > 0:
                                outcome = "WIN"
                            elif realized_pnl < 0:
                                outcome = "LOSS"
                            else:
                                outcome = "BREAKEVEN"

                            close_data = trade_times[symbol].copy()
                            close_data.update({
                                "event": "CLOSE",
                                "time": exit_time.isoformat(),
                                "entry_time": entry_time.isoformat(),
                                "exit_time": exit_time.isoformat(),
                                "duration_seconds": int(
                                    duration.total_seconds()
                                ),
                                "realized_pnl": realized_pnl,
                                "outcome": outcome
                            })
                            append_trade_event(close_data)

                            if outcome == "LOSS":
                                cooldown_until = exit_time + timedelta(
                                    minutes=config.COOLDOWN_AFTER_SL_MINUTES
                                )
                                symbol_cooldown_until = exit_time + timedelta(
                                    minutes=config.SYMBOL_COOLDOWN_AFTER_LOSS_MINUTES
                                )
                                cooldowns[get_cooldown_key(symbol, side)] = (
                                    cooldown_until
                                )
                                symbol_cooldowns[symbol] = symbol_cooldown_until
                                log_warning(
                                    f"{symbol} {side} COOLDOWN UNTIL "
                                    f"{cooldown_until}"
                                )
                                log_warning(
                                    f"{symbol} SYMBOL COOLDOWN UNTIL "
                                    f"{symbol_cooldown_until}"
                                )

                            log_info(
                                f"*** {symbol} TRADE CLOSED *** | "
                                f"ENTRY: {entry_time} | "
                                f"EXIT: {exit_time} | "
                                f"DURATION: {duration} | "
                                f"OUTCOME: {outcome} | "
                                f"PNL: {realized_pnl}"
                            )

                            del trade_times[symbol]
                            continue

                    # =========================
                    # POSITION CHECK
                    # =========================
                    if symbol in open_symbols and symbol not in trade_times:
                        log_warning(f"{symbol} already has open position")
                        record_skip(skip_reasons, "OPEN POSITION")
                        continue

                    if is_symbol_in_cooldown(symbol):
                        cooldown_until = symbol_cooldowns[symbol]
                        log_warning(
                            f"{symbol} SKIP | SYMBOL COOLDOWN ACTIVE "
                            f"UNTIL {cooldown_until}"
                        )
                        record_skip(skip_reasons, "SYMBOL COOLDOWN")
                        continue

                    # =========================
                    # DATA
                    # =========================
                    trend_df, confirm_df, entry_df, sl_df = get_symbol_klines(
                        symbol
                    )

                    if trend_df is None or confirm_df is None or entry_df is None or sl_df is None:
                        record_skip(skip_reasons, "DATA MISSING")
                        continue

                    if len(trend_df) < 250 or len(confirm_df) < 250 or len(entry_df) < 250 or len(sl_df) < 250:
                        record_skip(skip_reasons, "INSUFFICIENT DATA")
                        continue

                    # =========================
                    # INDICATORS
                    # =========================
                    trend_df = apply_indicators(trend_df)
                    confirm_df = apply_indicators(confirm_df)
                    entry_df = apply_indicators(entry_df)
                    sl_df = apply_indicators(sl_df)

                    if trend_df is None or confirm_df is None or entry_df is None or sl_df is None:
                        record_skip(skip_reasons, "INDICATOR FAILED")
                        continue

                    # =========================
                    # BTC CONTEXT
                    # =========================
                    btc_corr = 0
                    rs = 0

                    log_info(f"{symbol} BTC CORR: {btc_corr}")
                    log_info(f"BTC TREND: {btc_trend}")
                    log_info(f"{symbol} RS: {rs}%")

                    # =========================
                    # SIGNAL
                    # =========================
                    signal_result = check_signal(
                        trend_df,
                        confirm_df,
                        entry_df,
                        btc_trend,
                        btc_corr,
                        rs,
                        return_confidence=True
                    )

                    signal = None
                    signal_confidence = 0

                    if isinstance(signal_result, tuple):
                        signal, signal_confidence = signal_result
                    else:
                        signal = signal_result

                    if not signal:
                        log_warning(
                            f"{symbol} NO SIGNAL | "
                            f"BTC={btc_trend} | "
                            f"CORR={btc_corr} | "
                            f"RS={rs} | "
                            f"CONF={signal_confidence}%"
                        )

                        if signal_confidence >= 55:
                            record_skip(skip_reasons, "NO SIGNAL NEAR THRESHOLD")
                        elif signal_confidence >= 35:
                            record_skip(skip_reasons, "NO SIGNAL MID CONFIDENCE")
                        else:
                            record_skip(skip_reasons, "NO SIGNAL LOW CONFIDENCE")

                        continue

                    log_info(f"{symbol} SIGNAL: {signal}")
                    log_info(f"{symbol} SIGNAL CONFIDENCE: {signal_confidence}%")

                    if symbol in open_symbols and symbol in trade_times:
                        current_side = trade_times[symbol]["side"]

                        if signal == current_side:
                            log_warning(
                                f"{symbol} already has open {current_side} position"
                            )
                            record_skip(skip_reasons, "OPEN POSITION SAME FLOW")
                            continue

                        if close_tracked_trade(
                            symbol,
                            f"OPPOSITE FLOW {current_side} -> {signal}"
                        ):
                            finalize_tracked_trade(
                                symbol,
                                apply_loss_cooldown=False
                            )
                            open_symbols.discard(symbol)
                            position_counts['total'] = max(
                                0,
                                position_counts['total'] - 1
                            )

                            if current_side == "BUY":
                                position_counts['buy'] = max(
                                    0,
                                    position_counts['buy'] - 1
                                )
                            else:
                                position_counts['sell'] = max(
                                    0,
                                    position_counts['sell'] - 1
                                )

                            time.sleep(1)
                        else:
                            record_skip(skip_reasons, "OPPOSITE FLOW CLOSE FAILED")
                            continue

                    if is_in_cooldown(symbol, signal):
                        cooldown_until = cooldowns[
                            get_cooldown_key(symbol, signal)
                        ]
                        log_warning(
                            f"{symbol} SKIP | {signal} COOLDOWN ACTIVE "
                            f"UNTIL {cooldown_until}"
                        )
                        record_skip(skip_reasons, "COOLDOWN")
                        continue

                    trade_leverage = config.LEVERAGE

                    if (
                        config.HIGH_CONFIDENCE_LEVERAGE_ENABLED
                        and signal_confidence >= config.HIGH_CONFIDENCE_THRESHOLD
                    ):
                        trade_leverage = config.HIGH_CONFIDENCE_LEVERAGE
                        log_info(
                            f"{symbol} HIGH CONFIDENCE LEVERAGE: "
                            f"{trade_leverage}x"
                        )

                    # =========================
                    # POSITION LIMITS
                    # =========================
                    counts = position_counts

                    if config.MAX_TOTAL_POSITIONS and counts['total'] >= config.MAX_TOTAL_POSITIONS:
                        log_warning(
                            f"🚨 MAX POSITIONS REACHED 🚨\n"
                            f"TOTAL OPEN: {counts['total']}/{config.MAX_TOTAL_POSITIONS}\n"
                            f"BUY: {counts['buy']} | SELL: {counts['sell']}\n"
                            f"Skipping new entries..."
                        )
                        record_skip(skip_reasons, "MAX POSITIONS")
                        continue

                    if signal == "BUY" and config.MAX_BUY_POSITIONS and counts['buy'] >= config.MAX_BUY_POSITIONS:
                        log_warning(
                            f"🚨 MAX BUY POSITIONS REACHED | "
                            f"BUY={counts['buy']}/{config.MAX_BUY_POSITIONS} | "
                            f"TOTAL={counts['total']}"
                        )
                        record_skip(skip_reasons, "MAX BUY POSITIONS")
                        continue

                    if signal == "SELL" and config.MAX_SELL_POSITIONS and counts['sell'] >= config.MAX_SELL_POSITIONS:
                        log_warning(
                            f"🚨 MAX SELL POSITIONS REACHED | "
                            f"SELL={counts['sell']}/{config.MAX_SELL_POSITIONS} | "
                            f"TOTAL={counts['total']}"
                        )
                        record_skip(skip_reasons, "MAX SELL POSITIONS")
                        continue

                    # =========================
                    # PRICE (PRE-ENTRY)
                    # =========================
                    current_price = entry_df['close'].iloc[-2]

                    # =========================
                    # STRUCTURE SL (PRE-RISK CHECK)
                    # =========================
                    sl_price = get_structure_stop_loss(
                        sl_df,
                        signal
                    )

                    if sl_price is None:
                        log_warning(f"{symbol} SKIP | INVALID SL")
                        record_skip(skip_reasons, "INVALID SL")
                        continue

                    if signal == "BUY" and sl_price >= current_price:
                        log_warning(f"{symbol} SKIP | INVALID SL")
                        record_skip(skip_reasons, "INVALID SL")
                        continue

                    if signal == "SELL" and sl_price <= current_price:
                        log_warning(f"{symbol} SKIP | INVALID SL")
                        record_skip(skip_reasons, "INVALID SL")
                        continue

                    if config.LIVE_ENTRY_CONFIRMATION_ENABLED:
                        live_price = get_mark_price(symbol)

                        if live_price is None:
                            log_warning(f"{symbol} SKIP | LIVE PRICE NOT FOUND")
                            record_skip(skip_reasons, "LIVE PRICE NOT FOUND")
                            continue

                        live_ok, live_reason = validate_live_entry_timing(
                            signal,
                            entry_df,
                            live_price
                        )

                        if not live_ok:
                            log_warning(f"{symbol} SKIP | {live_reason}")
                            record_skip(skip_reasons, live_reason)
                            continue

                        log_info(
                            f"{symbol} {live_reason} | "
                            f"SIGNAL_CLOSE={current_price} | LIVE={live_price}"
                        )
                        current_price = live_price

                        if signal == "BUY" and sl_price >= current_price:
                            log_warning(f"{symbol} SKIP | LIVE BUY SL ABOVE ENTRY")
                            record_skip(skip_reasons, "LIVE INVALID SL")
                            continue

                        if signal == "SELL" and sl_price <= current_price:
                            log_warning(f"{symbol} SKIP | LIVE SELL SL BELOW ENTRY")
                            record_skip(skip_reasons, "LIVE INVALID SL")
                            continue

                    # =========================
                    # SL RISK VALIDATION (CRITICAL FIX)
                    # =========================
                    risk_pct = abs(current_price - sl_price) / current_price
                    sl_roi = risk_pct * trade_leverage * 100

                    log_info(f"{symbol} PRE-TRADE SL ROI: {sl_roi:.2f}%")

                    MAX_SL_ROI = config.MAX_SL_ROI

                    if sl_roi > MAX_SL_ROI:
                        log_warning(f"{symbol} SKIP | SL TOO LARGE: {sl_roi:.2f}%")
                        record_skip(skip_reasons, "SL TOO LARGE")
                        continue

                    # =========================
                    # POST-SIGNAL ENTRY QUALITY
                    # =========================
                    entry_ok, entry_reason = validate_entry_quality(
                        signal,
                        entry_df,
                        trend_df,
                        current_price,
                        sl_price
                    )

                    if not entry_ok:
                        log_warning(f"{symbol} SKIP | {entry_reason}")
                        record_skip(skip_reasons, entry_reason)
                        continue

                    log_info(f"{symbol} {entry_reason}")

                    setup_snapshot = get_setup_snapshot(
                        symbol,
                        signal,
                        signal_confidence,
                        trade_leverage,
                        entry_df,
                        trend_df,
                        btc_trend,
                        btc_corr,
                        rs,
                        entry_reason
                    )

                    if config.STATIC_TP_ENABLED:
                        pre_trade_tp_price = calculate_static_roi_take_profit(
                            current_price,
                            signal,
                            config.STATIC_TP_ROI,
                            leverage=trade_leverage
                        )
                    else:
                        if config.ADAPTIVE_TP_ENABLED:
                            pre_trade_tp_price = calculate_adaptive_take_profit(
                                current_price,
                                sl_price,
                                signal,
                                trend_df,
                                rr=config.RR_TAKE_PROFIT,
                                min_rr=config.MIN_TRADE_RR,
                                max_roi=config.ADAPTIVE_TP_MAX_ROI,
                                leverage=trade_leverage
                            )
                        else:
                            pre_trade_tp_price = calculate_rr_take_profit(
                                current_price,
                                sl_price,
                                signal,
                                rr=config.RR_TAKE_PROFIT
                            )

                    if pre_trade_tp_price is None:
                        log_warning(f"{symbol} SKIP | INVALID PRE-TRADE TP")
                        record_skip(skip_reasons, "INVALID PRE-TRADE TP")
                        continue

                    pre_trade_risk = abs(current_price - sl_price)
                    pre_trade_reward = abs(
                        pre_trade_tp_price - current_price
                    )

                    if pre_trade_risk <= 0:
                        log_warning(f"{symbol} SKIP | INVALID PRE-TRADE RISK")
                        record_skip(skip_reasons, "INVALID PRE-TRADE RISK")
                        continue

                    pre_trade_rr = pre_trade_reward / pre_trade_risk

                    if (
                        pre_trade_rr < config.MIN_TRADE_RR
                        and not config.STATIC_TP_ENABLED
                    ):
                        log_warning(
                            f"{symbol} SKIP | PRE-TRADE RR TOO LOW: "
                            f"{pre_trade_rr:.2f}"
                        )
                        record_skip(skip_reasons, "PRE-TRADE RR TOO LOW")
                        continue

                    # =========================
                    # POSITION SIZE (FIXED)
                    # =========================
                    balance = get_balance()

                    quantity = calculate_position_size(
                        balance,
                        current_price,
                        sl_price,
                        symbol,
                        config.MARGIN_PER_TRADE,
                        leverage_override=trade_leverage
                    )

                    notional = quantity * current_price

                    log_info(
                        f"{symbol} QTY={quantity} | NOTIONAL={notional:.2f}"
                    )

                    if quantity <= 0:
                        log_warning(f"{symbol} SKIPPED | INVALID QTY")
                        record_skip(skip_reasons, "INVALID QTY")
                        continue

                    log_info(f"{symbol} QTY: {quantity}")

                    # =========================
                    # LEVERAGE
                    # =========================
                    if not setup_leverage(symbol, trade_leverage):
                        record_skip(skip_reasons, "LEVERAGE FAILED")
                        continue

                    notional_ok, notional = validate_min_notional(
                        symbol,
                        quantity,
                        current_price
                    )

                    if not notional_ok:
                        log_warning(f"{symbol} SKIP | NOTIONAL TOO LOW: {notional}")
                        record_skip(skip_reasons, "NOTIONAL TOO LOW")
                        continue

                    # =========================
                    # PLACE ORDER
                    # =========================
                    side = SIDE_BUY if signal == "BUY" else SIDE_SELL

                    order = place_market_order(symbol, side, quantity)

                    if not order:
                        record_skip(skip_reasons, "ORDER FAILED")
                        continue

                    time.sleep(2)

                    entry_price = get_entry_price(symbol)

                    if not entry_price:
                        log_warning(f"{symbol} ENTRY PRICE NOT FOUND")
                        record_skip(skip_reasons, "ENTRY PRICE NOT FOUND")
                        continue

                    if config.STATIC_TP_ENABLED:

                        tp_price = calculate_static_roi_take_profit(
                            entry_price,
                            signal,
                            config.STATIC_TP_ROI,
                            leverage=trade_leverage
                        )

                        log_info(
                            f"{symbol} STATIC TP MODE | "
                            f"ROI={config.STATIC_TP_ROI}% | "
                            f"LEVERAGE={trade_leverage}x"
                        )

                    else:

                        if config.ADAPTIVE_TP_ENABLED:
                            tp_price = calculate_adaptive_take_profit(
                                entry_price,
                                sl_price,
                                signal,
                                trend_df,
                                rr=config.RR_TAKE_PROFIT,
                                min_rr=config.MIN_TRADE_RR,
                                max_roi=config.ADAPTIVE_TP_MAX_ROI,
                                leverage=trade_leverage
                            )
                        else:
                            tp_price = calculate_rr_take_profit(
                                entry_price,
                                sl_price,
                                signal,
                                rr=config.RR_TAKE_PROFIT
                            )

                        if config.ADAPTIVE_TP_ENABLED:
                            log_info(
                                f"{symbol} ADAPTIVE TP MODE | "
                                f"BASE_RR={config.RR_TAKE_PROFIT} | "
                                f"MIN_RR={config.MIN_TRADE_RR} | "
                                f"MAX_ROI={config.ADAPTIVE_TP_MAX_ROI}%"
                            )
                        else:
                            log_info(
                                f"{symbol} STRUCTURE/RR TP MODE | "
                                f"RR={config.RR_TAKE_PROFIT}"
                            )

                    if tp_price is None:
                        log_warning(f"{symbol} INVALID TP/SL")
                        close_market_position(symbol, side, quantity)
                        record_skip(skip_reasons, "INVALID TP/SL")
                        continue

                    sl_price = tighten_stop_loss_to_final_rr(
                        symbol,
                        signal,
                        entry_price,
                        tp_price,
                        sl_price
                    )

                    if not validate_tp_sl_direction(
                        symbol,
                        signal,
                        entry_price,
                        tp_price,
                        sl_price
                    ):
                        close_market_position(symbol, side, quantity)
                        record_skip(skip_reasons, "INVALID TP/SL DIRECTION")
                        continue

                    risk = abs(entry_price - sl_price)
                    reward = abs(tp_price - entry_price)

                    if risk <= 0:
                        log_warning(f"{symbol} INVALID RISK")
                        close_market_position(symbol, side, quantity)
                        record_skip(skip_reasons, "INVALID RISK")
                        continue

                    rr = reward / risk
                    sl_roi = (risk / entry_price) * trade_leverage * 100

                    log_info(f"{symbol} RR: {rr:.2f}")

                    if rr < config.MIN_TRADE_RR and not config.STATIC_TP_ENABLED:
                        log_warning(f"{symbol} RR TOO LOW: {rr:.2f}")
                        close_market_position(symbol, side, quantity)
                        record_skip(skip_reasons, "RR TOO LOW")
                        continue

                    # =========================
                    # PLACE TP/SL
                    # =========================
                    protection_ok = place_tp_sl(
                        symbol,
                        side,
                        entry_price,
                        quantity,
                        confirm_df,
                        tp_price,
                        sl_price
                    )

                    if not protection_ok:
                        log_error(
                            f"{symbol} PROTECTION FAILED | CLOSING POSITION"
                        )
                        close_market_position(symbol, side, quantity)
                        record_skip(skip_reasons, "PROTECTION FAILED")
                        continue

                    # =========================
                    # STORE TRADE
                    # =========================
                    entry_time = datetime.now()
                    trade_id = (
                        f"{symbol}-{signal}-"
                        f"{entry_time.strftime('%Y%m%d%H%M%S')}"
                    )

                    if signal == "BUY":
                        target_price_move_pct = (
                            (tp_price - entry_price) / entry_price
                        ) * 100
                    else:
                        target_price_move_pct = (
                            (entry_price - tp_price) / entry_price
                        ) * 100

                    target_roi = max(0, target_price_move_pct * trade_leverage)
                    if (
                        config.PROFIT_PROTECTION_TRIGGER_TP_PCT > 0
                        and target_roi > 0
                    ):
                        protection_trigger_roi = (
                            target_roi
                            * config.PROFIT_PROTECTION_TRIGGER_TP_PCT
                            / 100
                        )
                    else:
                        protection_trigger_roi = (
                            config.PROFIT_PROTECTION_TRIGGER_ROI
                        )

                    trade_times[symbol] = {
                        "trade_id": trade_id,
                        "entry_time": entry_time,
                        "entry_time_iso": entry_time.isoformat(),
                        "side": signal,
                        "confidence": signal_confidence,
                        "leverage": trade_leverage,
                        "entry_price": entry_price,
                        "quantity": quantity,
                        "tp_price": tp_price,
                        "sl_price": sl_price,
                        "rr": round(rr, 4),
                        "target_roi": round(target_roi, 4),
                        "profit_protection_trigger_roi": round(
                            protection_trigger_roi,
                            4
                        ),
                        "peak_roi": 0,
                        "last_roi": 0,
                        **setup_snapshot
                    }

                    open_event = trade_times[symbol].copy()
                    open_event.update({
                        "event": "OPEN",
                        "time": entry_time.isoformat(),
                        "entry_time": entry_time.isoformat(),
                        "outcome": "OPEN"
                    })
                    append_trade_event(open_event)
                    open_symbols.add(symbol)
                    position_counts['total'] += 1

                    if signal == "BUY":
                        position_counts['buy'] += 1
                    else:
                        position_counts['sell'] += 1

                    # =========================
                    # LOG SUMMARY
                    # =========================
                    log_info(
                        f"*** {symbol} TRADE OPENED ***\n"
                        f"ENTRY: {entry_price}\n"
                        f"SL: {sl_price}\n"
                        f"SL ROI: {sl_roi:.2f}%\n"
                        f"LEVERAGE: {trade_leverage}x\n"
                        f"BALANCE: {balance}\n"
                    )

                    orderCounts = get_open_position_counts()

                    log_info(
                        f"{symbol} OPENED | TOTAL={orderCounts['total']} | "
                        f"BUY={orderCounts['buy']} | SELL={orderCounts['sell']}"
                    )

                    time.sleep(2)

                except Exception as e:
                    log_error(f"{symbol} ERROR: {e}")

            log_skip_summary(skip_reasons)
            log_info("Waiting next scan...")
            time.sleep(30)

        except Exception as e:
            log_error(f"MAIN LOOP ERROR: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()
