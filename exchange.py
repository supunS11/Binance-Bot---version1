from binance.client import Client
from binance.enums import *

import pandas as pd
import time
import numpy as np

import config
from indicators import apply_indicators
from logger import log_info, log_warning, log_error


client = Client(config.API_KEY, config.SECRET_KEY)

# =========================
# SYNC TIME
# =========================
server_time = client.get_server_time()
client.timestamp_offset = server_time['serverTime'] - int(time.time() * 1000)


# =========================
# MARGIN TYPE
# =========================
def set_margin_type(symbol):

    try:
        client.futures_change_margin_type(
            symbol=symbol,
            marginType=config.MARGIN_TYPE
        )

        log_info(f"{symbol} Margin: {config.MARGIN_TYPE}")

    except Exception as e:
        if "No need to change margin type" not in str(e):
            log_warning(str(e))


# =========================
# LEVERAGE
# =========================
def setup_leverage(symbol):

    try:

        response = client.futures_change_leverage(
            symbol=symbol,
            leverage=config.LEVERAGE
        )

        actual = int(response['leverage'])

        if actual != config.LEVERAGE:
            log_warning(f"{symbol} leverage mismatch")
            return False

        log_info(f"{symbol} leverage set: {actual}x")
        return True

    except Exception as e:
        log_error(f"{symbol} leverage error: {e}")
        return False


# =========================
# BALANCE
# =========================
def get_balance():

    balances = client.futures_account_balance()

    for b in balances:
        if b['asset'] == 'USDT':
            return float(b['balance'])

    return 0


def get_margin_balance():
    return float(client.futures_account()['totalMarginBalance'])


def get_unrealized_pnl():
    return float(client.futures_account()['totalUnrealizedProfit'])


# =========================
# KLINES
# =========================
def get_klines(symbol, interval, limit=500):

    try:

        klines = client.futures_klines(
            symbol=symbol,
            interval=interval,
            limit=limit
        )

        df = pd.DataFrame(klines, columns=[
            'time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'qav', 'trades', 'tbbav', 'tbqav', 'ignore'
        ])

        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)

        return df

    except Exception as e:
        log_error(f"{symbol} klines error: {e}")
        return None


# =========================
# POSITION CHECKS
# =========================
def has_open_position(symbol):

    try:
        positions = client.futures_position_information(symbol=symbol)

        for p in positions:
            if float(p['positionAmt']) != 0:
                return True

        return False

    except Exception as e:
        log_error(str(e))
        return False


def is_position_closed(symbol):

    try:
        positions = client.futures_position_information(symbol=symbol)

        for p in positions:
            if abs(float(p['positionAmt'])) > 0:
                return False

        return True

    except Exception as e:
        log_error(f"{symbol} position check error: {e}")
        return False


def get_open_position_counts():

    try:

        positions = client.futures_position_information()

        total = buy = sell = 0

        for p in positions:

            amt = float(p['positionAmt'])

            if amt == 0:
                continue

            total += 1

            if amt > 0:
                buy += 1
            else:
                sell += 1

        return {
            "total": total,
            "buy": buy,
            "sell": sell
        }

    except Exception as e:
        log_error(f"position count error: {e}")
        return {"total": 0, "buy": 0, "sell": 0}


# =========================
# PRECISION
# =========================
def get_symbol_precision(symbol):

    info = client.futures_exchange_info()

    for s in info['symbols']:
        if s['symbol'] == symbol:
            return s['quantityPrecision']

    return 3


def get_price_precision(symbol):

    info = client.futures_exchange_info()

    for s in info['symbols']:
        if s['symbol'] == symbol:
            return int(s['pricePrecision'])

    return 4


# =========================
# ENTRY PRICE
# =========================
def get_entry_price(symbol):

    time.sleep(2)

    positions = client.futures_position_information(symbol=symbol)

    return abs(float(positions[0]['entryPrice']))


# =========================
# MARKET ORDER
# =========================
def place_market_order(symbol, side, quantity):

    try:

        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type=FUTURE_ORDER_TYPE_MARKET,
            quantity=quantity
        )

        log_info(f"{symbol} MARKET ORDER: {side}")
        return order

    except Exception as e:
        log_error(f"{symbol} order error: {e}")
        return None


# =========================
# STRUCTURE SL (REQUIRED BY MAIN + STRATEGY)
# =========================
def get_structure_stop_loss(df, side):

    try:
        atr = df['atr'].iloc[-1]

        if side == SIDE_BUY:

            swing_low = df['low'].iloc[-15:].min()

            # avoid overly deep SL
            sl = swing_low - (atr * 0.3)

        else:

            swing_high = df['high'].iloc[-15:].max()

            sl = swing_high + (atr * 0.3)

        return sl

    except Exception as e:
        log_error(f"SL error: {e}")
        return None
    
def get_hybrid_take_profit(entry_price, stop_loss, side, rr_target=2.0):

    try:
        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            return None

        rr_tp = risk * rr_target

        if side == SIDE_BUY:
            return entry_price + rr_tp
        else:
            return entry_price - rr_tp

    except Exception as e:
        log_error(f"TP error: {e}")
        return None
    
def get_structure_take_profit(trend_df, side):

    try:
        highs = trend_df['high'].iloc[-50:]
        lows = trend_df['low'].iloc[-50:]

        if side == SIDE_BUY:
            return highs.max()

        else:
            return lows.min()

    except Exception as e:
        log_error(f"Structure TP error: {e}")
        return None
    
def get_liquidity_take_profit(df, side):

    try:
        highs = df['high']
        lows = df['low']

        recent_high_10 = highs.rolling(10).max().iloc[-1]
        recent_high_30 = highs.rolling(30).max().iloc[-1]
        recent_high_50 = highs.rolling(50).max().iloc[-1]

        recent_low_10 = lows.rolling(10).min().iloc[-1]
        recent_low_30 = lows.rolling(30).min().iloc[-1]
        recent_low_50 = lows.rolling(50).min().iloc[-1]

        if side == "BUY":

            tp = max(
                recent_high_10,
                recent_high_30,
                recent_high_50
            )

        else:

            tp = min(
                recent_low_10,
                recent_low_30,
                recent_low_50
            )

        return tp

    except Exception as e:
        log_error(f"LIQ TP ERROR: {e}")
        return None

# =========================
# TP/SL EXECUTION (CLEAN VERSION)
# =========================
def place_tp_sl(symbol, side, entry_price, quantity, sl_price, tp_price):

    try:

        time.sleep(2)

        precision = get_price_precision(symbol)

        market_price = float(
            client.futures_mark_price(symbol=symbol)['markPrice']
        )

        # =========================
        # USE PRE-CALCULATED TP/SL
        # =========================
        tp_price = round(tp_price, precision)
        sl_price = round(sl_price, precision)

        if side == SIDE_BUY:
            close_side = SIDE_SELL
        else:
            close_side = SIDE_BUY

        # =========================
        # VALIDATION
        # =========================
        if side == SIDE_BUY:

            if tp_price <= market_price:
                log_warning(f"{symbol} INVALID BUY TP")
                return

            if sl_price >= market_price:
                log_warning(f"{symbol} INVALID BUY SL")
                return

        else:

            if tp_price >= market_price:
                log_warning(f"{symbol} INVALID SELL TP")
                return

            if sl_price <= market_price:
                log_warning(f"{symbol} INVALID SELL SL")
                return

        log_info(
            f"{symbol}\n"
            f"ENTRY: {entry_price}\n"
            f"TP: {tp_price}\n"
            f"SL: {sl_price}"
        )

        # TAKE PROFIT
        client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=tp_price,
            closePosition=True,
            workingType="MARK_PRICE",
            priceProtect=True
        )

        time.sleep(2)

        # STOP LOSS
        client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True,
            workingType="MARK_PRICE",
            priceProtect=True
        )

        log_info(f"{symbol} TP/SL CREATED")

    except Exception as e:
        log_error(f"{symbol} TP/SL error: {e}")

        # TAKE PROFIT
        client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="TAKE_PROFIT_MARKET",
            stopPrice=tp_price,
            closePosition=True,
            workingType="MARK_PRICE",
            priceProtect=True
        )

        time.sleep(2)

        # STOP LOSS
        client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True,
            workingType="MARK_PRICE",
            priceProtect=True
        )

        log_info(f"{symbol} TP/SL CREATED")

    except Exception as e:
        log_error(f"{symbol} TP/SL error: {e}")


# =========================
# BTC CORRELATION
# =========================
def get_btc_correlation(symbol):

    try:

        if symbol == "BTCUSDT":
            return 1.0

        coin_df = get_klines(symbol, config.TREND_TIMEFRAME, 100)
        btc_df = get_klines("BTCUSDT", config.TREND_TIMEFRAME, 100)

        if coin_df is None or btc_df is None:
            return 0

        coin_ret = coin_df['close'].pct_change().dropna()
        btc_ret = btc_df['close'].pct_change().dropna()

        return round(float(np.corrcoef(coin_ret, btc_ret)[0, 1]), 2)

    except Exception as e:
        log_error(f"{symbol} corr error: {e}")
        return 0


# =========================
# BTC TREND
# =========================
def get_btc_trend():

    try:

        btc_df = get_klines("BTCUSDT", config.TREND_TIMEFRAME)
        btc_df = apply_indicators(btc_df)

        btc = btc_df.iloc[-2]

        if btc['ema50'] > btc['ema200']:
            return "BULLISH"
        elif btc['ema50'] < btc['ema200']:
            return "BEARISH"

        return "NEUTRAL"

    except Exception as e:
        log_error(f"BTC trend error: {e}")
        return None


# =========================
# RELATIVE STRENGTH
# =========================
def get_relative_strength(symbol):

    try:

        if symbol == "BTCUSDT":
            return 0

        coin = get_klines(symbol, config.TREND_TIMEFRAME, 50)
        btc = get_klines("BTCUSDT", config.TREND_TIMEFRAME, 50)

        if coin is None or btc is None:
            return 0

        coin_r = (coin['close'].iloc[-1] - coin['close'].iloc[-10]) / coin['close'].iloc[-10] * 100
        btc_r = (btc['close'].iloc[-1] - btc['close'].iloc[-10]) / btc['close'].iloc[-10] * 100

        return round(coin_r - btc_r, 2)

    except Exception as e:
        log_error(f"{symbol} RS error: {e}")
        return 0
    
def validate_min_notional(symbol, quantity, price):

    try:

        notional = quantity * price

        # Binance futures minimum notional (safe default buffer)
        MIN_NOTIONAL = 5.0

        if notional < MIN_NOTIONAL:
            return False, notional

        return True, notional

    except Exception:
        return False, 0
    