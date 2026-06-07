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
    get_btc_trend,
    get_btc_correlation,
    get_relative_strength
)

from indicators import apply_indicators
from strategy import check_signal
from exchange import get_structure_stop_loss
from ai_model import ai_confidence_boost
from risk_management import calculate_position_size
from logger import log_info, log_warning, log_error


trade_times = {}


def run_bot():

    log_info("BOT STARTED")

    while True:

        try:

            for symbol in config.SYMBOLS:

                try:

                    # =========================
                    # CLEANUP CLOSED TRADES
                    # =========================
                    if symbol in trade_times and is_position_closed(symbol):
                        del trade_times[symbol]

                    # =========================
                    # SKIP IF OPEN POSITION EXISTS
                    # =========================
                    if has_open_position(symbol):
                        continue

                    # =========================
                    # DATA FETCH
                    # =========================
                    trend_df = get_klines(symbol, config.TREND_TIMEFRAME)
                    confirm_df = get_klines(symbol, config.CONFIRMATION_TIMEFRAME)
                    entry_df = get_klines(symbol, config.ENTRY_TIMEFRAME)

                    if trend_df is None or confirm_df is None or entry_df is None:
                        continue

                    trend_df = apply_indicators(trend_df)
                    confirm_df = apply_indicators(confirm_df)
                    entry_df = apply_indicators(entry_df)

                    if trend_df is None or confirm_df is None or entry_df is None:
                        continue

                    # =========================
                    # CONTEXT DATA
                    # =========================
                    btc_trend = get_btc_trend()
                    btc_corr = get_btc_correlation(symbol)
                    rs = get_relative_strength(symbol)

                    # =========================
                    # SIGNAL GENERATION
                    # =========================
                    signal = check_signal(
                        trend_df,
                        confirm_df,
                        entry_df,
                        btc_trend,
                        btc_corr,
                        rs
                    )

                    log_info(
                        f"SMC DEBUG | \n"
                        f"Signal={signal} \n"
                        f"BullBias={btc_trend} \n"
                        f"Corr={btc_corr} \n"
                        f"RS={rs}"
                    )

                    if not signal:
                        log_warning(
                            f"{symbol} NO SIGNAL | "
                        )
                        continue

                    log_info(f"{symbol} SIGNAL: {signal}")

                    # =========================
                    # AI CONFIRMATION GATE (IMPORTANT)
                    # =========================
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
                        log_warning(f"{symbol} AI REJECTED SIGNAL | score={ai_score}")
                        continue

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
                    # PRICE
                    # =========================
                    current_price = entry_df['close'].iloc[-2]

                    # =========================
                    # STRUCTURE STOP LOSS (FIXED)
                    # =========================
                    sl_price = get_structure_stop_loss(confirm_df, signal)

                    if sl_price is None:
                        continue

                    # =========================
                    # SL SAFETY CHECK
                    # =========================
                    risk_pct = abs(current_price - sl_price) / current_price * 100

                    if risk_pct > 3:   # adjust per leverage strategy
                        log_warning(f"{symbol} SKIP | SL TOO LARGE: {risk_pct:.2f}%")
                        continue

                    # =========================
                    # POSITION SIZE (REAL RISK BASED)
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
                    # LEVERAGE SETUP
                    # =========================
                    setup_leverage(symbol)

                    # =========================
                    # EXECUTION
                    # =========================
                    side = SIDE_BUY if signal == "BUY" else SIDE_SELL

                    place_market_order(symbol, side, quantity)

                    time.sleep(2)

                    # safer entry price
                    entry_price = get_entry_price(symbol)

                    # =========================
                    # TP / SL PLACEMENT
                    # =========================
                    place_tp_sl(
                        symbol,
                        side,
                        entry_price,
                        quantity,
                        confirm_df
                    )

                    # =========================
                    # TRADE TRACKING
                    # =========================
                    trade_times[symbol] = {
                        "entry_time": datetime.now(),
                        "side": signal,
                        "entry_price": entry_price,
                        "sl": sl_price
                    }

                    # =========================
                    # LIVE POSITION COUNT LOG
                    # =========================
                    counts = get_open_position_counts()

                    log_info(
                        f"{symbol} TRADE OPENED | "
                        f"TOTAL={counts['total']} | "
                        f"BUY={counts['buy']} | SELL={counts['sell']}"
                    )

                except Exception as e:
                    log_error(f"{symbol} ERROR: {e}")

            time.sleep(30)

        except Exception as e:
            log_error(f"MAIN LOOP ERROR: {e}")
            time.sleep(30)


if __name__ == "__main__":
    run_bot()