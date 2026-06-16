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
def setup_leverage(symbol, leverage=None):

    try:

        leverage_to_use = leverage if leverage is not None else config.LEVERAGE

        response = client.futures_change_leverage(
            symbol=symbol,
            leverage=leverage_to_use
        )

        actual = int(response['leverage'])

        if actual != leverage_to_use:
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

    try:
        time.sleep(2)

        positions = client.futures_position_information(symbol=symbol)

        if not positions:
            return None

        entry = float(positions[0].get('entryPrice', 0))

        if entry <= 0:
            return None

        return abs(entry)

    except Exception as e:
        log_error(f"{symbol} entry price error: {e}")
        return None

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
# STRUCTURE SL (ALIGNED WITH STRATEGY)
# =========================
def get_structure_stop_loss(df, side):

    try:

        atr = df['atr'].iloc[-2]

        if side == SIDE_BUY or side == "BUY":

            swing_low_10 = df['low'].iloc[-10:-1].min()
            swing_low_20 = df['low'].iloc[-20:-1].min()

            swing_low = min(swing_low_10, swing_low_20)

            return swing_low - (atr * 0.8)

        else:

            swing_high_10 = df['high'].iloc[-10:-1].max()
            swing_high_20 = df['high'].iloc[-20:-1].max()

            swing_high = max(swing_high_10, swing_high_20)

            return swing_high + (atr * 0.8)

    except Exception as e:
        log_error(f"SL error: {e}")
        return None
    
def get_structure_take_profit(df, side):

    try:

        atr = df['atr'].iloc[-2]

        if side == "BUY":

            swing_high_10 = df['high'].iloc[-10:-1].max()
            swing_high_20 = df['high'].iloc[-20:-1].max()

            swing_high = max(
                swing_high_10,
                swing_high_20
            )

            return swing_high + (atr * 0.5)

        else:

            swing_low_10 = df['low'].iloc[-10:-1].min()
            swing_low_20 = df['low'].iloc[-20:-1].min()

            swing_low = min(
                swing_low_10,
                swing_low_20
            )

            return swing_low - (atr * 0.5)

    except Exception as e:
        log_error(f"TP ERROR: {e}")
        return None
    
def get_hybrid_take_profit(df, side, sl_price, rr=1.5):

    try:

        price = df['close'].iloc[-2]

        if side == "BUY":

            risk = price - sl_price

            if risk <= 0:
                return None

            return price + (risk * rr)

        else:

            risk = sl_price - price

            if risk <= 0:
                return None

            return price - (risk * rr)

    except Exception as e:
        log_error(f"HYBRID TP ERROR: {e}")
        return None
    
def calculate_rr_take_profit(entry_price, sl_price, side, rr=1.5):

    try:

        if side == "BUY":

            risk = entry_price - sl_price

            if risk <= 0:
                return None

            return entry_price + (risk * rr)

        else:

            risk = sl_price - entry_price

            if risk <= 0:
                return None

            return entry_price - (risk * rr)

    except Exception:
        return None


def calculate_static_roi_take_profit(entry_price, side, roi_percent, leverage=None):

    try:

        leverage_to_use = leverage if leverage is not None else config.LEVERAGE

        if leverage_to_use <= 0:
            return None

        price_move_pct = (roi_percent / leverage_to_use) / 100

        if side == SIDE_BUY or side == "BUY":
            return entry_price * (1 + price_move_pct)

        else:
            return entry_price * (1 - price_move_pct)

    except Exception as e:
        log_error(f"STATIC TP ERROR: {e}")
        return None


def get_structure_aware_take_profit(df, side, sl_price, rr=1.5):

    try:

        price = df['close'].iloc[-2]
        rr_tp = get_hybrid_take_profit(df, side, sl_price, rr)
        support, resistance = get_support_resistance(df)

        if rr_tp is None:
            return None

        if side == "BUY":

            if resistance is None or resistance <= price:
                return rr_tp

            return min(rr_tp, resistance)

        else:

            if support is None or support >= price:
                return rr_tp

            return max(rr_tp, support)

    except Exception as e:
        log_error(f"HYBRID TP ERROR: {e}")
        return None


def calculate_trailing_activation_price(entry_price, tp_price, side):

    try:

        tp_percent = config.TRAILING_TP_PERCENT / 100

        if side == SIDE_BUY or side == "BUY":

            if tp_price <= entry_price:
                return None

            activation_price = entry_price + (
                (tp_price - entry_price) * tp_percent
            )

        else:

            if tp_price >= entry_price:
                return None

            activation_price = entry_price - (
                (entry_price - tp_price) * tp_percent
            )

        return activation_price

    except Exception as e:
        log_error(f"TRAILING ACTIVATION ERROR: {e}")
        return None


def place_native_trailing_stop(symbol, side, entry_price, quantity, tp_price):

    try:

        if not config.TRAILING_STOP_ENABLED:
            return

        precision = get_price_precision(symbol)

        if side == SIDE_BUY or side == "BUY":
            close_side = SIDE_SELL
        else:
            close_side = SIDE_BUY

        activation_price = calculate_trailing_activation_price(
            entry_price,
            tp_price,
            side
        )

        if activation_price is None:
            log_warning(f"{symbol} TRAILING SKIP | INVALID ACTIVATION")
            return

        activation_price = round(activation_price, precision)
        activation_price_str = f"{activation_price:.{precision}f}"
        callback_rate_str = str(config.TRAILING_CALLBACK_RATE)

        market_price = float(
            client.futures_mark_price(symbol=symbol)['markPrice']
        )

        if side == SIDE_BUY or side == "BUY":
            if activation_price <= market_price:
                log_warning(f"{symbol} TRAILING SKIP | INVALID ACTIVATION VS MARKET")
                return
        else:
            if activation_price >= market_price:
                log_warning(f"{symbol} TRAILING SKIP | INVALID ACTIVATION VS MARKET")
                return

        log_info(
            f"{symbol} TRAILING DEBUG | "
            f"ENTRY_SIDE={side} | "
            f"CLOSE_SIDE={close_side} | "
            f"ENTRY={entry_price} | "
            f"TP={tp_price} | "
            f"ACTIVATION={activation_price_str} | "
            f"TP_PERCENT={config.TRAILING_TP_PERCENT}% | "
            f"CALLBACK={config.TRAILING_CALLBACK_RATE}"
        )

        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": close_side,
            "type": "TRAILING_STOP_MARKET",
            "quantity": quantity,
            "activatePrice": activation_price_str,
            "callbackRate": str(config.TRAILING_CALLBACK_RATE),
            "workingType": "MARK_PRICE",
            "reduceOnly": "true",
            "newOrderRespType": "RESULT"
        }

        trailing_order = client._request_futures_api(
            "post",
            "algoOrder",
            True,
            data=params
        )

        log_info(f"{symbol} TRAILING ORDER RESPONSE: {trailing_order}")
        log_info(f"{symbol} TRAILING STOP CREATED")

    except Exception as e:
        log_error(f"{symbol} TRAILING STOP error: {e}")


# =========================
# TP/SL EXECUTION (CLEAN VERSION)
# =========================
def place_tp_sl(symbol, side, entry_price, quantity, confirm_df, tp_price, sl_price):

    try:

        time.sleep(2)

        precision = get_price_precision(symbol)

        market_price = float(
            client.futures_mark_price(symbol=symbol)['markPrice']
        )

        # ================= BUY =================
        if side == SIDE_BUY:

            tp_price = round(tp_price, precision)
            sl_price = round(sl_price, precision)

            close_side = SIDE_SELL

        # ================= SELL =================
        else:

            tp_price = round(tp_price, precision)
            sl_price = round(sl_price, precision)

            close_side = SIDE_BUY

            time.sleep(1)

        # ================= VALIDATION ONLY =================
        if side == SIDE_BUY:
            if tp_price <= market_price or sl_price >= market_price:
                return
        else:
            if tp_price >= market_price or sl_price <= market_price:
                return

        log_info(
            f"{symbol}\nENTRY: {entry_price}\nTP: {tp_price}\nSL: {sl_price}"
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

        time.sleep(2)

        # TRAILING STOP
        place_native_trailing_stop(
            symbol,
            side,
            entry_price,
            quantity,
            tp_price
        )

        log_info(f"{symbol} TP/SL CREATED")

        time.sleep(1)

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

        corr = np.corrcoef(coin_ret, btc_ret)[0, 1]

        if np.isnan(corr):
            return 0

        return round(float(corr), 2)

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

        if btc['close'] > btc['ema50']:
            return "BULLISH"

        elif btc['close'] < btc['ema50']:
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

        coin_r = (
            (coin['close'].iloc[-1] - coin['close'].iloc[-10])
            / coin['close'].iloc[-10]
        ) * 100

        btc_r = (
            (btc['close'].iloc[-1] - btc['close'].iloc[-10])
            / btc['close'].iloc[-10]
        ) * 100

        return round(coin_r - btc_r, 2)

    except Exception as e:
        log_error(f"{symbol} RS error: {e}")
        return 0
    
def validate_min_notional(symbol, quantity, price):

    try:
        notional = quantity * price

        MIN_NOTIONAL = 20  # Binance futures requirement (most coins)

        if notional < MIN_NOTIONAL:
            return False, notional

        return True, notional

    except Exception:
        return False, 0
    

def get_support_resistance(df, lookback=50):

    try:

        if df is None or len(df) < lookback:
            return None, None

        df = df.iloc[-lookback:]

        highs = df['high']
        lows = df['low']

        resistance_levels = []
        support_levels = []

        # Pivot detection (cleaner)
        for i in range(3, len(df) - 3):

            if highs.iloc[i] == max(highs.iloc[i-3:i+4]):
                resistance_levels.append(highs.iloc[i])

            if lows.iloc[i] == min(lows.iloc[i-3:i+4]):
                support_levels.append(lows.iloc[i])

        if not resistance_levels or not support_levels:
            return None, None

        price = df['close'].iloc[-1]

        # Closest resistance ABOVE price
        resistance_levels = [r for r in resistance_levels if r > price]
        resistance = min(resistance_levels) if resistance_levels else max(df['high'])

        # Closest support BELOW price
        support_levels = [s for s in support_levels if s < price]
        support = max(support_levels) if support_levels else min(df['low'])

        return support, resistance

    except Exception as e:
        log_error(f"SR ERROR: {e}")
        return None, None


def cancel_remaining_orders(symbol):

    try:

        # Normal open orders
        try:

            client.futures_cancel_all_open_orders(
                symbol=symbol
            )

            log_info(
                f"{symbol} OPEN ORDERS CANCELED"
            )

        except Exception as e:

            log_warning(
                f"{symbol} OPEN ORDER CANCEL WARNING: {e}"
            )

        # Algo orders (Trailing / Conditional)
        try:

            response = client._request_futures_api(
                "delete",
                "algoOpenOrders",
                True,
                data={
                    "symbol": symbol
                }
            )

            log_info(
                f"{symbol} ALGO ORDERS CANCELED: "
                f"{response}"
            )

        except Exception as e:

            log_warning(
                f"{symbol} ALGO ORDER CANCEL WARNING: {e}"
            )

    except Exception as e:

        log_error(
            f"{symbol} CANCEL ORDERS ERROR: {e}"
        )
