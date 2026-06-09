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
    get_structure_stop_loss,
    get_hybrid_take_profit,
    get_structure_take_profit
)

from indicators import apply_indicators
from strategy import check_signal
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
                    # FINAL ENTRY QUALITY FILTER
                    # =========================
                    from filters import (
                        entry_confirmation_5m,
                        is_fresh_move
                    )

                    if not entry_confirmation_5m(entry_df, signal):
                        log_warning(f"{symbol} BLOCKED | 5M ENTRY QUALITY FAIL")
                        continue

                    if not is_fresh_move(entry_df):
                        log_warning(f"{symbol} BLOCKED | CHASING MOVE")
                        continue

                    # =========================
                    # POSITION LIMITS
                    # =========================
                    counts = get_open_position_counts()

                    if config.MAX_TOTAL_POSITIONS and counts['total'] >= config.MAX_TOTAL_POSITIONS:
                        continue

                    if signal == "BUY" and config.MAX_BUY_POSITIONS and counts['buy'] >= config.MAX_BUY_POSITIONS:
                        continue

                    if signal == "SELL" and config.MAX_SELL_POSITIONS and counts['sell'] >= config.MAX_SELL_POSITIONS:
                        continue

                    # =========================
                    # PRICE
                    # =========================
                    current_price = entry_df['close'].iloc[-1]
                    entry_price = current_price

                    # =========================
                    # STRUCTURE SL
                    # =========================
                    sl_price = get_structure_stop_loss(confirm_df, signal)

                    if sl_price is None:
                        continue

                    # =========================
                    # SL DIRECTION VALIDATION (FIX)
                    # =========================
                    if signal == "BUY" and sl_price >= entry_price:
                        log_warning(f"{symbol} INVALID SL (BUY ABOVE ENTRY)")
                        continue

                    if signal == "SELL" and sl_price <= entry_price:
                        log_warning(f"{symbol} INVALID SL (SELL BELOW ENTRY)")
                        continue

                    # =========================
                    # STRUCTURE TP
                    # =========================
                    rr_tp = get_hybrid_take_profit(
                        entry_price,
                        sl_price,
                        signal,
                        rr_target=2.0
                    )

                    structure_tp = get_structure_take_profit(
                        trend_df,
                        signal
                    )

                    if rr_tp is None or structure_tp is None:
                        continue

                    # =========================
                    # HYBRID TP
                    # =========================
                    if signal == "BUY":
                        tp_price = min(rr_tp, structure_tp)
                    else:
                        tp_price = max(rr_tp, structure_tp)

                    # =========================
                    # RR VALIDATION
                    # =========================
                    risk = abs(entry_price - sl_price)
                    reward = abs(tp_price - entry_price)

                    if risk <= 0 or reward <= 0:
                        log_warning(f"{symbol} INVALID RISK/REWARD")
                        continue

                    rr = reward / risk

                    log_info(
                        f"{symbol} RR={rr:.2f} | "
                        f"Reward={reward:.4f} | "
                        f"Risk={risk:.4f}"
                    )

                    MIN_RR = 1.5

                    if rr < MIN_RR:
                        continue

                    # =========================
                    # SL RISK VALIDATION (FIXED)
                    # =========================
                    risk_pct = abs(entry_price - sl_price) / entry_price
                    sl_roi = risk_pct * config.LEVERAGE * 100

                    log_info(f"{symbol} PRE-TRADE SL ROI: {sl_roi:.2f}%")

                    if sl_roi > config.MAX_SL_ROI:
                        continue

                    # =========================
                    # POSITION SIZE
                    # =========================
                    balance = get_balance()

                    quantity = calculate_position_size(
                        balance,
                        current_price,
                        sl_price,
                        symbol,
                        config.MARGIN_PER_TRADE
                    )

                    if quantity <= 0:
                        continue

                    # =========================
                    # LEVERAGE
                    # =========================
                    if not setup_leverage(symbol):
                        continue

                    # =========================
                    # ORDER
                    # =========================
                    side = SIDE_BUY if signal == "BUY" else SIDE_SELL

                    place_market_order(symbol, side, quantity)

                    time.sleep(2)

                    entry_price = get_entry_price(symbol)

                    # =========================
                    # TP/SL PLACEMENT
                    # =========================
                    place_tp_sl(
                        symbol,
                        side,
                        entry_price,
                        quantity,
                        sl_price,
                        tp_price
                    )

                    trade_times[symbol] = {
                        "entry_time": datetime.now(),
                        "side": signal
                    }

                    log_info(
                        f"*** {symbol} TRADE OPENED ***\n"
                        f"ENTRY: {entry_price}\n"
                        f"SL: {sl_price}\n"
                        f"BALANCE: {balance}\n"
                    )

                except Exception as e:
                    log_error(f"{symbol} ERROR: {e}")

            log_info("Waiting next scan...")
            time.sleep(30)

        except Exception as e:
            log_error(f"MAIN LOOP ERROR: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()