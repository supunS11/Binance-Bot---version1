import time
from datetime import datetime

import config

from binance.enums import *

from exchange import (
    get_klines,
    get_balance,
    place_market_order,
    place_tp_sl,
    has_open_position,
    get_open_position_counts,
    is_position_closed,
    setup_leverage,
    get_entry_price,
    get_margin_balance,
    get_unrealized_pnl,
    get_btc_trend,
    get_btc_correlation,
    get_relative_strength,
    get_hybrid_take_profit,
    validate_min_notional
)

from indicators import apply_indicators
from strategy import check_signal, get_structure_stop_loss
from risk_management import calculate_position_size
from logger import log_info, log_warning, log_error


trade_times = {}


def run_bot():

    log_info("BOT STARTED")

    while True:

        try:

            for symbol in config.SYMBOLS:

                try:

                    log_info(f"Checking {symbol}")

                    # =========================
                    # CLOSE TRACKING
                    # =========================
                    if symbol in trade_times:

                        if is_position_closed(symbol):

                            exit_time = datetime.now()
                            entry_time = trade_times[symbol]['entry_time']
                            duration = exit_time - entry_time

                            log_info(
                                f"*** {symbol} TRADE CLOSED *** | "
                                f"ENTRY: {entry_time} | "
                                f"EXIT: {exit_time} | "
                                f"DURATION: {duration}"
                            )

                            del trade_times[symbol]

                    # =========================
                    # POSITION CHECK
                    # =========================
                    if has_open_position(symbol):
                        log_warning(f"{symbol} already has open position")
                        continue

                    # =========================
                    # DATA
                    # =========================
                    trend_df = get_klines(symbol, config.TREND_TIMEFRAME)
                    confirm_df = get_klines(symbol, config.CONFIRMATION_TIMEFRAME)
                    entry_df = get_klines(symbol, config.ENTRY_TIMEFRAME)
                    sl_df = get_klines(symbol, config.SL_TIMEFRAME)

                    if trend_df is None or confirm_df is None or entry_df is None:
                        continue

                    if len(trend_df) < 250 or len(confirm_df) < 250 or len(entry_df) < 250:
                        continue

                    # =========================
                    # INDICATORS
                    # =========================
                    trend_df = apply_indicators(trend_df)
                    confirm_df = apply_indicators(confirm_df)
                    entry_df = apply_indicators(entry_df)
                    sl_df = apply_indicators(sl_df)

                    if trend_df is None or confirm_df is None or entry_df is None:
                        continue

                    # =========================
                    # BTC CONTEXT
                    # =========================
                    btc_trend = get_btc_trend()
                    btc_corr = get_btc_correlation(symbol)
                    rs = get_relative_strength(symbol)

                    log_info(f"{symbol} BTC CORR: {btc_corr}")
                    log_info(f"BTC TREND: {btc_trend}")
                    log_info(f"{symbol} RS: {rs}%")

                    # =========================
                    # SIGNAL
                    # =========================
                    signal = check_signal(
                        trend_df,
                        confirm_df,
                        entry_df,
                        btc_trend,
                        btc_corr,
                        rs
                    )

                    if not signal:
                        log_warning(
                            f"{symbol} NO SIGNAL | "
                            f"BTC={btc_trend} | "
                            f"CORR={btc_corr} | "
                            f"RS={rs}"
                        )
                        continue

                    log_info(f"{symbol} SIGNAL: {signal}")

                    # =========================
                    # POSITION LIMITS
                    # =========================
                    counts = get_open_position_counts()

                    if config.MAX_TOTAL_POSITIONS and counts['total'] >= config.MAX_TOTAL_POSITIONS:
                        log_warning(
                            f"🚨 MAX POSITIONS REACHED 🚨\n"
                            f"TOTAL OPEN: {counts['total']}/{config.MAX_TOTAL_POSITIONS}\n"
                            f"BUY: {counts['buy']} | SELL: {counts['sell']}\n"
                            f"Skipping new entries..."
    )
                        continue

                    if signal == "BUY" and config.MAX_BUY_POSITIONS and counts['buy'] >= config.MAX_BUY_POSITIONS:
                        log_warning(
                            f"🚨 MAX BUY POSITIONS REACHED | "
                            f"BUY={counts['buy']}/{config.MAX_BUY_POSITIONS} | "
                            f"TOTAL={counts['total']}"
                        )
                        continue

                    if signal == "SELL" and config.MAX_SELL_POSITIONS and counts['sell'] >= config.MAX_SELL_POSITIONS:
                        log_warning(
                            f"🚨 MAX SELL POSITIONS REACHED | "
                            f"SELL={counts['sell']}/{config.MAX_SELL_POSITIONS} | "
                            f"TOTAL={counts['total']}"
                        )
                        continue

                    # =========================
                    # PRICE (PRE-ENTRY)
                    # =========================
                    current_price = entry_df['close'].iloc[-2]

                    # =========================
                    # STRUCTURE SL (PRE-RISK CHECK)
                    # =========================
                    sl_price = get_structure_stop_loss(
                        entry_df,
                        signal
                    )

                    tp_price = get_hybrid_take_profit(
                        entry_df,
                        signal,
                        sl_price,
                        rr=1.5
                    )

                    # =========================
                    # SL RISK VALIDATION (CRITICAL FIX)
                    # =========================
                    risk_pct = abs(current_price - sl_price) / current_price
                    sl_roi = risk_pct * config.LEVERAGE * 100

                    log_info(f"{symbol} PRE-TRADE SL ROI: {sl_roi:.2f}%")

                    MAX_SL_ROI = config.MAX_SL_ROI

                    if sl_roi > MAX_SL_ROI:
                        log_warning(f"{symbol} SKIP | SL TOO LARGE: {sl_roi:.2f}%")
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
                        config.MARGIN_PER_TRADE
                    )

                    notional = quantity * current_price

                    log_info(
                        f"{symbol} QTY={quantity} | NOTIONAL={notional:.2f}"
                    )

                    if quantity <= 0:
                        log_warning(f"{symbol} SKIPPED | INVALID QTY")
                        continue

                    log_info(f"{symbol} QTY: {quantity}")

                    # =========================
                    # LEVERAGE
                    # =========================
                    if not setup_leverage(symbol):
                        continue

                    notional_ok, notional = validate_min_notional(
                        symbol,
                        quantity,
                        current_price
                    )

                    if not notional_ok:
                        log_warning(f"{symbol} SKIP | NOTIONAL TOO LOW: {notional}")
                        continue

                    # =========================
                    # PLACE ORDER
                    # =========================
                    side = SIDE_BUY if signal == "BUY" else SIDE_SELL

                    place_market_order(symbol, side, quantity)

                    time.sleep(2)

                    entry_price = get_entry_price(symbol)

                    if not entry_price:
                        log_warning(f"{symbol} ENTRY PRICE NOT FOUND")
                        continue

                    # =========================
                    # PLACE TP/SL
                    # =========================
                    place_tp_sl(
                        symbol,
                        side,
                        entry_price,
                        quantity,
                        confirm_df,
                        tp_price,
                        sl_price
                    )

                    # =========================
                    # STORE TRADE
                    # =========================
                    trade_times[symbol] = {
                        "entry_time": datetime.now(),
                        "side": signal
                    }

                    # =========================
                    # LOG SUMMARY
                    # =========================
                    log_info(
                        f"*** {symbol} TRADE OPENED ***\n"
                        f"ENTRY: {entry_price}\n"
                        f"SL: {sl_price}\n"
                        f"SL ROI: {sl_roi:.2f}%\n"
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

            log_info("Waiting next scan...")
            time.sleep(30)

        except Exception as e:
            log_error(f"MAIN LOOP ERROR: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()