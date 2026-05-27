from binance.client import Client
from binance.enums import *

import pandas as pd
import time

import config

from logger import *


client = Client(
    config.API_KEY,
    config.SECRET_KEY
)

# SYNCHRONIZE TIMESTAMP WITH BINANCE SERVER
server_time = client.get_server_time()

client.timestamp_offset = (
    server_time['serverTime'] - int(time.time() * 1000)
)


def set_leverage(symbol, leverage):

    try:

        # SET MARGIN TYPE
        set_margin_type(symbol)

        # SET LEVERAGE
        response = client.futures_change_leverage(
            symbol=symbol,
            leverage=leverage
        )

        actual_leverage = int(
            response['leverage']
        )

        # VERIFY LEVERAGE
        if actual_leverage != leverage:

            log_warning(
                f"{symbol} leverage mismatch. "
                f"Expected: {leverage}x | "
                f"Actual: {actual_leverage}x"
            )

            return False

        log_info(
            f"{symbol} leverage set to "
            f"{actual_leverage}x"
        )

        return True

    except Exception as e:

        log_error(
            f"{symbol} leverage setup failed: "
            f"{e}"
        )

        return False

def get_balance():

    balances = client.futures_account_balance()

    for balance in balances:

        if balance['asset'] == 'USDT':

            return float(balance['balance'])

    return 0

def get_margin_balance():

    account = client.futures_account()

    return float(
        account['totalMarginBalance']
    )

def get_unrealized_pnl():

    account = client.futures_account()

    return float(
        account['totalUnrealizedProfit']
    )

from datetime import datetime

from exchange import client


def get_today_realized_pnl():

    try:

        # TODAY START TIMESTAMP
        today_start = datetime.combine(
            datetime.today(),
            datetime.min.time()
        )

        start_time = int(
            today_start.timestamp() * 1000
        )

        # GET INCOME HISTORY
        income_history = client.futures_income_history(
            incomeType="REALIZED_PNL",
            startTime=start_time,
            limit=1000
        )

        total_realized_pnl = 0

        for item in income_history:

            pnl = float(item['income'])

            total_realized_pnl += pnl

        print(
            f"Today's Realized PNL: "
            f"{total_realized_pnl:.4f} USDT"
        )

        return total_realized_pnl

    except Exception as e:

        print(
            f"REALIZED PNL ERROR: {e}"
        )

        return 0

def get_klines(symbol, interval, limit=500):

    klines = client.futures_klines(
        symbol=symbol,
        interval=interval,
        limit=limit
    )

    df = pd.DataFrame(klines)

    df = df.iloc[:, :6]

    df.columns = [
        'time',
        'open',
        'high',
        'low',
        'close',
        'volume'
    ]

    df['close'] = df['close'].astype(float)
    df['high'] = df['high'].astype(float)
    df['low'] = df['low'].astype(float)
    df['volume'] = df['volume'].astype(float)

    return df


def place_market_order(symbol, side, quantity):

    try:

        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type=FUTURE_ORDER_TYPE_MARKET,
            quantity=quantity
        )

        log_info(
            f"*** {symbol} {side} ORDER PLACED ***"
        )

        return order

    except Exception as e:

        log_error(str(e))

        return None


def get_symbol_precision(symbol):

    info = client.futures_exchange_info()

    for x in info['symbols']:

        if x['symbol'] == symbol:

            return x['quantityPrecision']

    return 3


def get_price_precision(symbol):

    info = client.futures_exchange_info()

    for x in info['symbols']:

        if x['symbol'] == symbol:

            return int(x['pricePrecision'])

    return 4


def set_margin_type(symbol):

    try:

        client.futures_change_margin_type(
            symbol=symbol,
            marginType=config.MARGIN_TYPE
        )

        log_info(
            f"{symbol} Margin Type: "
            f"{config.MARGIN_TYPE}"
        )

    except Exception as e:

        # IGNORE ALREADY SET ERROR
        if "No need to change margin type" in str(e):

            pass

        else:

            log_warning(str(e))

def get_entry_price(symbol):
    
    time.sleep(2)

    positions = client.futures_position_information(
    symbol=symbol
    )

    entry_price = abs(
    float(positions[0]['entryPrice'])
    )

    return entry_price

def place_tp_sl(symbol, side, entry_price, quantity):

    try:

        # WAIT FOR POSITION UPDATE
        time.sleep(2)

        # GET PRICE PRECISION
        precision = get_price_precision(symbol)

        # GET CURRENT MARKET PRICE
        market_price = float(
            client.futures_mark_price(
                symbol=symbol
            )['markPrice']
        )

        # =========================
        # BUY POSITION
        # =========================
        if side == SIDE_BUY:

            tp_price = round(
                entry_price * (
                    1 + (
                        config.ROI_PERCENT_TP /
                        config.LEVERAGE
                    ) / 100
                ),
                precision
            )

            sl_price = round(
                entry_price * (
                    1 - (
                        config.ROI_PERCENT_SL /
                        config.LEVERAGE
                    ) / 100
                ),
                precision
            )

            close_side = SIDE_SELL

            # VALIDATION
            if tp_price <= market_price:

                log_warning(
                    f"{symbol} invalid BUY TP"
                )

                return

            if sl_price >= market_price:

                log_warning(
                    f"{symbol} invalid BUY SL"
                )

                return

        # =========================
        # SELL POSITION
        # =========================
        else:

            tp_price = round(
                entry_price * (
                    1 - (
                        config.ROI_PERCENT_TP /
                        config.LEVERAGE
                    ) / 100
                ),
                precision
            )

            sl_price = round(
                entry_price * (
                    1 + (
                        config.ROI_PERCENT_SL /
                        config.LEVERAGE
                    ) / 100
                ),
                precision
            )

            close_side = SIDE_BUY

            # VALIDATION
            if tp_price >= market_price:

                log_warning(
                    f"{symbol} invalid SELL TP"
                )

                return

            if sl_price <= market_price:

                log_warning(
                    f"{symbol} invalid SELL SL"
                )

                return

        # LOG TP/SL
        log_info(
            f"{symbol}\n"
            f"ENTRY: {entry_price}\n"
            f"MARK PRICE: {market_price}\n"
            f"TP: {tp_price}\n"
            f"SL: {sl_price}"
        )

        time.sleep(2)
        
        # =========================
        # TAKE PROFIT
        # =========================
        client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=tp_price,
            closePosition=True,
            workingType="MARK_PRICE",
            priceProtect=True,
            recvWindow=10000
        )

        time.sleep(3)

        # =========================
        # STOP LOSS
        # =========================
        client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True,
            workingType="MARK_PRICE",
            priceProtect=True,
            recvWindow=10000
        )

        log_info(
            f"{symbol} TP/SL CREATED"
        )

    except Exception as e:

        log_error(
            f"{symbol} TP/SL ERROR: {e}"
        )


def has_open_position(symbol):

    try:

        positions = client.futures_position_information(
            symbol=symbol
        )

        for position in positions:

            amount = float(position['positionAmt'])

            # ACTIVE POSITION EXISTS
            if amount != 0:

                return True

        return False

    except Exception as e:

        log_error(str(e))

        return False


def is_position_closed(symbol):

    try:

        positions = client.futures_position_information(
            symbol=symbol
        )

        for position in positions:

            amt = float(position['positionAmt'])

            # POSITION STILL OPEN
            if abs(amt) > 0:

                log_info(
                    f"{symbol} POSITION OPEN | "
                    f"Amount: {amt}"
                )

                return False

        log_info(
            f"{symbol} POSITION CLOSED"
        )

        return True

    except Exception as e:

        log_error(
            f"{symbol} POSITION CHECK ERROR: {str(e)}"
        )

        return False


def get_open_position_counts():

    try:

        positions = client.futures_position_information()

        total = 0
        buy = 0
        sell = 0

        for position in positions:

            amount = float(
                position['positionAmt']
            )

            # SKIP CLOSED POSITIONS
            if amount == 0:

                continue

            total += 1

            if amount > 0:

                buy += 1

            elif amount < 0:

                sell += 1

        return {
            "total": total,
            "buy": buy,
            "sell": sell
        }

    except Exception as e:

        log_error(
            f"POSITION COUNT ERROR: {e}"
        )

        return {
            "total": 0,
            "buy": 0,
            "sell": 0
        }
    
# def verify_leverage(symbol, target_leverage):

#     try:

#         time.sleep(2)
#         positions = client.futures_position_information(
#             symbol=symbol
#         )

#         if not positions:
#             log_warning(f"{symbol} no position data found")
#             return False
    
#         current_leverage = int(
#             positions[0]['leverage']
#         )

#         log_info(
#             f"{symbol} current leverage: "
#             f"{current_leverage}"
#         )
#         time.sleep(1)

#         return current_leverage == target_leverage

#     except Exception as e:

#         log_error(
#             f"{symbol} leverage verify error: {e}"
#         )

#         return False
    
def setup_leverage(symbol):

    try:

        # SET LEVERAGE
        response = client.futures_change_leverage(
            symbol=symbol,
            leverage=config.LEVERAGE
        )

        # ACTUAL LEVERAGE FROM BINANCE
        actual_leverage = int(
            response['leverage']
        )

        log_info(
            f"{symbol} leverage response: "
            f"{actual_leverage}x"
        )

        # VERIFY LEVERAGE
        if actual_leverage != config.LEVERAGE:

            log_warning(
                f"{symbol} leverage mismatch | "
                f"Expected: {config.LEVERAGE}x | "
                f"Actual: {actual_leverage}x"
            )

            return False

        log_info(
            f"{symbol} leverage verified"
        )

        return True

    except Exception as e:

        log_error(
            f"{symbol} leverage setup failed: "
            f"{e}"
        )

        return False