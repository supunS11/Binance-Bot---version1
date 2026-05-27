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
    get_unrealized_pnl
)

from indicators import apply_indicators

from strategy import check_signal

from risk_management import (
    calculate_position_size
)

from ai_model import ai_confirmation

from logger import (
    log_info,
    log_warning,
    log_error
)

# STORE TRADE TIMES
trade_times = {}


def run_bot():

    log_info("BOT STARTED")

    while True:

        try:

            # LOOP THROUGH SYMBOLS
            for symbol in config.SYMBOLS:

                try:

                    log_info(
                        f"Checking {symbol}"
                    )

                    # =========================
                    # CHECK CLOSED POSITIONS
                    # =========================
                    if symbol in trade_times:

                        if is_position_closed(symbol):

                            exit_time = datetime.now()

                            entry_time = (
                                trade_times[symbol]['entry_time']
                            )

                            duration = (
                                exit_time - entry_time
                            )

                            log_info(
                                f"*** {symbol} TRADE CLOSED *** | "
                                f"ENTRY: {entry_time} | "
                                f"EXIT: {exit_time} | "
                                f"DURATION: {duration}"
                            )

                            del trade_times[symbol]

                    # =========================
                    # PREVENT DUPLICATE POSITIONS
                    # =========================
                    if has_open_position(symbol):

                        log_warning(
                            f"{symbol} already has open position"
                        )

                        continue

                    # =========================
                    # GET MULTI TIMEFRAME DATA
                    # =========================
                    trend_df = get_klines(
                        symbol,
                        config.TREND_TIMEFRAME
                    )

                    confirm_df = get_klines(
                        symbol,
                        config.CONFIRMATION_TIMEFRAME
                    )

                    entry_df = get_klines(
                        symbol,
                        config.ENTRY_TIMEFRAME
                    )

                    # =========================
                    # CHECK EMPTY DATA
                    # =========================
                    if (
                        trend_df is None
                        or confirm_df is None
                        or entry_df is None
                    ):

                        log_warning(
                            f"{symbol} dataframe empty"
                        )

                        continue

                    # =========================
                    # CHECK DATA LENGTH
                    # =========================
                    if (
                        len(trend_df) < 250
                        or len(confirm_df) < 250
                        or len(entry_df) < 250
                    ):

                        log_warning(
                            f"{symbol} insufficient candle data"
                        )

                        continue

                    # =========================
                    # APPLY INDICATORS
                    # =========================
                    trend_df = apply_indicators(
                        trend_df
                    )

                    confirm_df = apply_indicators(
                        confirm_df
                    )

                    entry_df = apply_indicators(
                        entry_df
                    )

                    # =========================
                    # CHECK DATA AFTER INDICATORS
                    # =========================
                    if (
                        len(trend_df) < 2
                        or len(confirm_df) < 2
                        or len(entry_df) < 2
                    ):

                        log_warning(
                            f"{symbol} insufficient indicator data"
                        )

                        continue

                    # =========================
                    # GET LATEST CANDLES
                    # =========================
                    trend = trend_df.iloc[-2]

                    confirm = confirm_df.iloc[-2]

                    entry = entry_df.iloc[-2]

                    # =========================
                    # DEBUG LOGS
                    # =========================
                    log_info(
                        f"{symbol} | "
                        f"Trend EMA50: {trend['ema50']} | "
                        f"Trend EMA200: {trend['ema200']} | "
                        f"Confirm RSI: {confirm['rsi']} | "
                        f"Confirm ADX: {confirm['adx']} | "
                        f"Entry Price: {entry['close']} | "
                        f"Entry EMA20: {entry['ema20']}"
                    )

                    # =========================
                    # GET SIGNAL
                    # =========================
                    signal = check_signal(
                        trend_df,
                        confirm_df,
                        entry_df
                    )

                    if signal:

                        log_info(
                            f"{symbol} "
                            f"{signal} SIGNAL DETECTED"
                        )

                        # =========================
                        # AI CONFIRMATION
                        # =========================
                        confidence = ai_confirmation(
                            trend_df,
                            confirm_df,
                            entry_df,
                            signal
                        )

                        log_info(
                            f"{symbol} AI Confidence: "
                            f"{confidence}%"
                        )

                        # =========================
                        # CONFIDENCE FILTER
                        # =========================
                        if confidence >= 55:

                            # GET POSITION COUNTS
                            counts = (
                                get_open_position_counts()
                            )

                            # =========================
                            # BLOCK TOTAL LIMIT
                            # =========================
                            if (
                                config.MAX_TOTAL_POSITIONS
                                is not None
                                and counts['total'] >=
                                config.MAX_TOTAL_POSITIONS
                            ):

                                log_warning(
                                    f"{counts['total']} : MAX TOTAL POSITIONS REACHED"
                                )

                                continue

                            # =========================
                            # BLOCK BUY LIMIT
                            # =========================
                            if signal == "BUY":

                                if (
                                    config.MAX_BUY_POSITIONS
                                    is not None
                                    and counts['buy'] >=
                                    config.MAX_BUY_POSITIONS
                                ):

                                    log_warning(
                                        "MAX BUY POSITIONS REACHED"
                                    )

                                    continue

                            # =========================
                            # BLOCK SELL LIMIT
                            # =========================
                            elif signal == "SELL":

                                if (
                                    config.MAX_SELL_POSITIONS
                                    is not None
                                    and counts['sell'] >=
                                    config.MAX_SELL_POSITIONS
                                ):

                                    log_warning(
                                        "MAX SELL POSITIONS REACHED"
                                    )

                                    continue

                            # =========================
                            # GET BALANCE
                            # =========================
                            balance = get_balance()

                            totalMarginBalance = (
                                get_margin_balance()
                            )

                            totalUnrealizedProfit = (
                                get_unrealized_pnl()
                            )

                            # =========================
                            # CURRENT PRICE
                            # =========================
                            current_price = (
                                entry['close']
                            )

                            # =========================
                            # CALCULATE POSITION SIZE
                            # =========================
                            quantity = (
                                calculate_position_size(
                                    balance,
                                    current_price,
                                    symbol
                                )
                            )

                            # INVALID QUANTITY
                            if quantity <= 0:

                                log_warning(
                                    f"{symbol} invalid quantity"
                                )

                                continue

                            log_info(
                                f"{symbol} Quantity: "
                                f"{quantity}"
                            )

                            # =========================
                            # SET LEVERAGE
                            # =========================
                            if not setup_leverage(symbol):

                                log_warning(
                                    f"{symbol} trade skipped"
                                )

                                continue

                            # =========================
                            # BUY ORDER
                            # =========================
                            if signal == "BUY":

                                place_market_order(
                                    symbol,
                                    SIDE_BUY,
                                    quantity
                                )

                                time.sleep(2)

                                entry_price = (
                                    get_entry_price(symbol)
                                )

                                place_tp_sl(
                                    symbol,
                                    SIDE_BUY,
                                    entry_price,
                                    quantity
                                )

                            # =========================
                            # SELL ORDER
                            # =========================
                            elif signal == "SELL":

                                place_market_order(
                                    symbol,
                                    SIDE_SELL,
                                    quantity
                                )

                                time.sleep(2)

                                entry_price = (
                                    get_entry_price(symbol)
                                )

                                place_tp_sl(
                                    symbol,
                                    SIDE_SELL,
                                    entry_price,
                                    quantity
                                )

                            time.sleep(1)

                            # =========================
                            # SAVE ENTRY TIME
                            # =========================
                            trade_times[symbol] = {
                                "entry_time": datetime.now()
                            }

                            # =========================
                            # GET UPDATED POSITION COUNTS
                            # =========================
                            orderCounts = (
                                get_open_position_counts()
                            )

                            # =========================
                            # TRADE LOGS
                            # =========================
                            log_info(
                                f"*** {symbol} TRADE OPENED ***\n"
                                f"ENTRY TIME: "
                                f"{trade_times[symbol]['entry_time']}"
                            )

                            log_info(
                                f"Wallet Balance: "
                                f"{balance} USDT"
                            )

                            log_info(
                                f"Margin Balance: "
                                f"{totalMarginBalance} USDT"
                            )

                            log_info(
                                f"Unrealized PNL: "
                                f"{totalUnrealizedProfit} USDT"
                            )

                            log_info(
                                f"TOTAL: {orderCounts['total']} | "
                                f"BUY: {orderCounts['buy']} | "
                                f"SELL: {orderCounts['sell']}"
                            )

                        else:

                            log_warning(
                                f"{symbol} confidence too low"
                            )

                    else:

                        log_warning(
                            f"{symbol} NO SIGNAL FOUND"
                        )

                    # =========================
                    # DELAY BETWEEN SYMBOLS
                    # =========================
                    time.sleep(2)

                except Exception as e:

                    log_error(
                        f"{symbol} error: {e}"
                    )

            # =========================
            # WAIT BEFORE NEXT SCAN
            # =========================
            log_info(
                "Waiting for next scan..."
            )

            time.sleep(30)

        except Exception as e:

            log_error(
                f"MAIN LOOP ERROR: {e}"
            )

            time.sleep(30)


if __name__ == "__main__":

    run_bot()