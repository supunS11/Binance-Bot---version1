import json
import os
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
    get_open_position_details,
    set_margin_type,
    setup_leverage,
    get_entry_price,
    get_mark_price,
    get_margin_balance,
    get_unrealized_pnl,
    get_realized_pnl_since,
    get_position_metrics,
    get_btc_trend,
    calculate_rr_take_profit,
    calculate_static_roi_take_profit,
    calculate_scalp_take_profit,
    calculate_adaptive_take_profit,
    validate_min_notional,
    close_market_position,
    replace_stop_loss
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

STATE_DATETIME_FIELDS = {"entry_time"}


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


def get_btc_context_df():
    try:
        btc_df = get_klines("BTCUSDT", config.TREND_TIMEFRAME, config.KLINE_LIMIT)

        if btc_df is None or len(btc_df) < 50:
            return None

        return apply_indicators(btc_df)

    except Exception as e:
        log_error(f"BTC CONTEXT DATA ERROR: {e}")
        return None


def get_btc_trend_from_df(btc_df):
    try:
        if btc_df is None or len(btc_df) < 2:
            return "NONE"

        btc = btc_df.iloc[-2]

        if btc["close"] > btc["ema50"]:
            return "BULLISH"

        if btc["close"] < btc["ema50"]:
            return "BEARISH"

        return "NEUTRAL"

    except Exception as e:
        log_error(f"BTC CONTEXT TREND ERROR: {e}")
        return "NONE"


def calculate_btc_context_metrics(symbol, trend_df, btc_df):
    try:
        if btc_df is None or trend_df is None:
            return 0, 0

        if symbol == "BTCUSDT":
            return 1.0, 0

        coin_ret = trend_df["close"].iloc[:-1].pct_change().dropna()
        btc_ret = btc_df["close"].iloc[:-1].pct_change().dropna()
        min_len = min(len(coin_ret), len(btc_ret), 99)

        if min_len < 20:
            corr = 0
        else:
            corr = coin_ret.tail(min_len).corr(btc_ret.tail(min_len))

            if corr != corr:
                corr = 0

        if len(trend_df) < 11 or len(btc_df) < 11:
            rs = 0
        else:
            coin_base = trend_df["close"].iloc[-11]
            btc_base = btc_df["close"].iloc[-11]

            if coin_base <= 0 or btc_base <= 0:
                rs = 0
            else:
                coin_r = (
                    (trend_df["close"].iloc[-2] - coin_base)
                    / coin_base
                ) * 100
                btc_r = (
                    (btc_df["close"].iloc[-2] - btc_base)
                    / btc_base
                ) * 100
                rs = coin_r - btc_r

        return round(float(corr), 2), round(float(rs), 2)

    except Exception as e:
        log_error(f"{symbol} BTC CONTEXT METRICS ERROR: {e}")
        return 0, 0


def calculate_target_roi(entry_price, tp_price, side, leverage):
    if not tp_price or entry_price <= 0:
        return 0

    if side == "BUY":
        price_move_pct = (tp_price - entry_price) / entry_price * 100
    else:
        price_move_pct = (entry_price - tp_price) / entry_price * 100

    return max(0, price_move_pct * leverage)


def calculate_profit_protection_trigger(target_roi):
    if (
        config.PROFIT_PROTECTION_TRIGGER_TP_PCT > 0
        and target_roi > 0
    ):
        return (
            target_roi
            * config.PROFIT_PROTECTION_TRIGGER_TP_PCT
            / 100
        )

    return config.PROFIT_PROTECTION_TRIGGER_ROI


def classify_realized_pnl(realized_pnl):
    if realized_pnl is None:
        return "UNKNOWN"

    if realized_pnl > 0:
        return "WIN"

    if realized_pnl < 0:
        return "LOSS"

    return "BREAKEVEN"


def safe_float(value, default=0):
    try:
        if value in (None, ""):
            return default

        return float(value)

    except (TypeError, ValueError):
        return default


def calculate_sl_price_from_roi(entry_price, side, lock_roi, leverage):
    if entry_price <= 0 or leverage <= 0:
        return None

    price_move_pct = (lock_roi / leverage) / 100

    if side == "BUY":
        return entry_price * (1 + price_move_pct)

    return entry_price * (1 - price_move_pct)


def infer_exchange_close_reason(trade, outcome):
    existing_reason = trade.get("early_exit_reason")

    if existing_reason:
        return existing_reason

    sl_stage = trade.get("sl_management_stage", "")

    if outcome == "LOSS":
        if sl_stage == "PROFIT_LOCK":
            return "PROFIT_LOCK_SL_HIT"

        if sl_stage == "BREAKEVEN":
            return "BREAKEVEN_SL_HIT"

        return "SL_HIT"

    if outcome in ("WIN", "BREAKEVEN"):
        if sl_stage == "PROFIT_LOCK":
            return "PROFIT_LOCK_SL_OR_TP_HIT"

        if sl_stage == "BREAKEVEN":
            return "BREAKEVEN_SL_OR_TP_HIT"

        return "TP_HIT"

    return ""


def get_open_trades_state_path():
    return getattr(
        config,
        "OPEN_TRADES_STATE_PATH",
        "logs/open_trades_state.json"
    )


def parse_state_datetime(value):
    if isinstance(value, datetime):
        return value

    if not value:
        return None

    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def serialize_state_value(value):
    if isinstance(value, datetime):
        return value.isoformat()

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def serialize_trade_for_state(trade):
    return {
        key: serialize_state_value(value)
        for key, value in trade.items()
    }


def normalize_trade_from_state(symbol, trade):
    if not isinstance(trade, dict):
        return None

    normalized = trade.copy()
    entry_time = parse_state_datetime(
        normalized.get("entry_time")
        or normalized.get("entry_time_iso")
    )

    if entry_time is None:
        log_warning(
            f"{symbol} STATE LOAD SKIP | INVALID ENTRY TIME"
        )
        return None

    normalized["symbol"] = normalized.get("symbol", symbol)
    normalized["entry_time"] = entry_time
    normalized["entry_time_iso"] = entry_time.isoformat()
    normalized.setdefault("peak_roi", 0)
    normalized.setdefault("last_roi", 0)
    normalized.setdefault("last_unrealized_pnl", "")
    normalized.setdefault("sl_management_stage", "")
    normalized.setdefault("sl_move_count", 0)
    normalized.setdefault("early_exit_reason", "")

    return normalized


def load_open_trade_state():
    path = get_open_trades_state_path()

    try:
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return 0

        with open(path, "r", encoding="utf-8") as state_file:
            payload = json.load(state_file)

        raw_trades = payload.get("trades", {})

        if not isinstance(raw_trades, dict):
            log_warning("OPEN TRADE STATE LOAD SKIP | INVALID FORMAT")
            return 0

        loaded = 0

        for symbol, raw_trade in raw_trades.items():
            trade = normalize_trade_from_state(symbol, raw_trade)

            if trade is None:
                continue

            trade_times[symbol] = trade
            loaded += 1

        if loaded:
            log_warning(
                f"OPEN TRADE STATE LOADED | COUNT={loaded} | PATH={path}"
            )

        return loaded

    except Exception as e:
        log_error(f"OPEN TRADE STATE LOAD ERROR: {e}")
        return 0


def save_open_trade_state():
    path = get_open_trades_state_path()

    try:
        folder = os.path.dirname(path)

        if folder:
            os.makedirs(folder, exist_ok=True)

        payload = {
            "saved_at": datetime.now().isoformat(),
            "trades": {
                symbol: serialize_trade_for_state(trade)
                for symbol, trade in sorted(trade_times.items())
            }
        }
        tmp_path = f"{path}.tmp"

        with open(tmp_path, "w", encoding="utf-8") as state_file:
            json.dump(payload, state_file, indent=2, sort_keys=True)

        os.replace(tmp_path, path)
        return True

    except Exception as e:
        log_error(f"OPEN TRADE STATE SAVE ERROR: {e}")
        return False


def update_trade_field(trade, key, value):
    if value in (None, ""):
        return False

    if trade.get(key) == value:
        return False

    trade[key] = value
    return True


def sync_open_trade_state(open_symbols):
    if not trade_times:
        return

    try:
        position_details = get_open_position_details()
        changed = False

        for symbol in list(trade_times):
            if symbol not in open_symbols:
                continue

            position = position_details.get(symbol)

            if not position:
                continue

            trade = trade_times[symbol]
            changed = update_trade_field(
                trade,
                "side",
                position.get("side")
            ) or changed
            changed = update_trade_field(
                trade,
                "quantity",
                position.get("quantity")
            ) or changed
            changed = update_trade_field(
                trade,
                "entry_price",
                position.get("entry_price")
            ) or changed
            changed = update_trade_field(
                trade,
                "tp_price",
                position.get("tp_price")
            ) or changed
            changed = update_trade_field(
                trade,
                "sl_price",
                position.get("sl_price")
            ) or changed

            target_roi = safe_float(trade.get("target_roi"), 0)
            tp_price = safe_float(trade.get("tp_price"), 0)
            entry_price = safe_float(trade.get("entry_price"), 0)
            leverage = safe_float(trade.get("leverage"), config.LEVERAGE)
            side = trade.get("side")

            if target_roi <= 0 and tp_price > 0 and entry_price > 0:
                target_roi = calculate_target_roi(
                    entry_price,
                    tp_price,
                    side,
                    leverage
                )
                changed = update_trade_field(
                    trade,
                    "target_roi",
                    round(target_roi, 4)
                ) or changed

            if safe_float(trade.get("profit_protection_trigger_roi"), 0) <= 0:
                changed = update_trade_field(
                    trade,
                    "profit_protection_trigger_roi",
                    round(calculate_profit_protection_trigger(target_roi), 4)
                ) or changed

        if changed:
            save_open_trade_state()

    except Exception as e:
        log_error(f"OPEN TRADE STATE SYNC ERROR: {e}")


def record_untracked_protection_close(
    symbol,
    signal,
    quantity,
    entry_time,
    reason,
    entry_price=None,
    tp_price=None,
    sl_price=None,
    rr=None,
    setup_snapshot=None,
    trade_leverage=None
):
    exit_time = datetime.now()
    realized_pnl = get_realized_pnl_since(symbol, entry_time)
    outcome = classify_realized_pnl(realized_pnl)
    exit_price = get_mark_price(symbol) or ""
    leverage = trade_leverage if trade_leverage is not None else config.LEVERAGE
    target_roi = 0

    if entry_price and tp_price:
        target_roi = calculate_target_roi(
            entry_price,
            tp_price,
            signal,
            leverage
        )

    event = {}

    if setup_snapshot:
        event.update(setup_snapshot)

    event.update({
        "event": "PROTECTION_CLOSE",
        "trade_id": (
            f"{symbol}-{signal}-PROTECTION-"
            f"{entry_time.strftime('%Y%m%d%H%M%S')}"
        ),
        "symbol": symbol,
        "side": signal,
        "time": exit_time.isoformat(),
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "duration_seconds": int((exit_time - entry_time).total_seconds()),
        "entry_price": entry_price or "",
        "exit_price": exit_price,
        "quantity": quantity,
        "tp_price": tp_price or "",
        "sl_price": sl_price or "",
        "rr": round(rr, 4) if isinstance(rr, (int, float)) else "",
        "target_roi": round(target_roi, 4),
        "profit_protection_trigger_roi": round(
            calculate_profit_protection_trigger(target_roi),
            4
        ),
        "realized_pnl": realized_pnl,
        "outcome": outcome,
        "confidence": (
            setup_snapshot.get("confidence", "")
            if setup_snapshot else ""
        ),
        "leverage": leverage,
        "early_exit_reason": reason,
    })

    append_trade_event(event)


def close_untracked_position(
    symbol,
    entry_order_side,
    signal,
    quantity,
    entry_time,
    reason,
    entry_price=None,
    tp_price=None,
    sl_price=None,
    rr=None,
    setup_snapshot=None,
    trade_leverage=None
):
    close_market_position(symbol, entry_order_side, quantity)
    record_untracked_protection_close(
        symbol,
        signal,
        quantity,
        entry_time,
        reason,
        entry_price=entry_price,
        tp_price=tp_price,
        sl_price=sl_price,
        rr=rr,
        setup_snapshot=setup_snapshot,
        trade_leverage=trade_leverage
    )


def calculate_post_fill_tp_sl_plan(
    symbol,
    signal,
    entry_price,
    sl_price,
    trend_df,
    trade_leverage
):
    mode = "STRUCTURE_RR"

    if config.STATIC_TP_ENABLED:
        tp_price = calculate_scalp_take_profit(
            entry_price,
            sl_price,
            signal,
            trend_df,
            config.STATIC_TP_ROI,
            min_rr=config.MIN_TRADE_RR,
            leverage=trade_leverage,
            symbol=symbol
        )
        mode = "SCALP_SR"

        if tp_price is None:
            tp_price = calculate_static_roi_take_profit(
                entry_price,
                signal,
                config.STATIC_TP_ROI,
                leverage=trade_leverage
            )
            mode = "STATIC_FALLBACK"

            if tp_price is not None:
                log_warning(
                    f"{symbol} TP FALLBACK | "
                    f"SR-aware TP invalid after fill; using fixed ROI TP"
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
            mode = "ADAPTIVE"
        else:
            tp_price = calculate_rr_take_profit(
                entry_price,
                sl_price,
                signal,
                rr=config.RR_TAKE_PROFIT
            )

    if tp_price is None:
        log_warning(f"{symbol} INVALID TP/SL | NO TP PRICE")
        return None

    final_sl_price = tighten_stop_loss_to_final_rr(
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
        final_sl_price
    ):
        return None

    risk = abs(entry_price - final_sl_price)
    reward = abs(tp_price - entry_price)

    if risk <= 0:
        log_warning(f"{symbol} INVALID RISK")
        return None

    rr = reward / risk
    sl_roi = (risk / entry_price) * trade_leverage * 100

    if rr < config.MIN_TRADE_RR:
        log_warning(f"{symbol} RR TOO LOW AFTER FINAL SL: {rr:.2f}")
        return None

    return {
        "mode": mode,
        "tp_price": tp_price,
        "sl_price": final_sl_price,
        "risk": risk,
        "reward": reward,
        "rr": rr,
        "sl_roi": sl_roi,
    }


def is_better_stop_loss(side, current_sl, new_sl):
    if new_sl is None or new_sl <= 0:
        return False

    if current_sl <= 0:
        return True

    if side == "BUY":
        return new_sl > current_sl

    return new_sl < current_sl


def update_trade_stop_loss(symbol, trade, current_roi, peak_roi):
    target_roi = safe_float(trade.get("target_roi"), 0)
    entry_price = safe_float(trade.get("entry_price"), 0)
    tp_price = safe_float(trade.get("tp_price"), 0)
    current_sl = safe_float(trade.get("sl_price"), 0)
    leverage = safe_float(trade.get("leverage"), config.LEVERAGE)
    side = trade.get("side")

    if target_roi <= 0 or entry_price <= 0 or tp_price <= 0 or leverage <= 0:
        return False

    if side not in ("BUY", "SELL"):
        return False

    stage_rank = {
        "": 0,
        "BREAKEVEN": 1,
        "PROFIT_LOCK": 2,
    }
    current_stage = trade.get("sl_management_stage", "")
    desired_stage = ""
    lock_roi = 0

    profit_lock_trigger = (
        target_roi
        * getattr(config, "PROFIT_LOCK_TRIGGER_TP_PCT", 65)
        / 100
    )
    breakeven_trigger = (
        target_roi
        * getattr(config, "BREAKEVEN_TRIGGER_TP_PCT", 40)
        / 100
    )

    if (
        getattr(config, "PROFIT_LOCK_SL_ENABLED", True)
        and peak_roi >= profit_lock_trigger
    ):
        desired_stage = "PROFIT_LOCK"
        lock_roi = max(
            getattr(config, "BREAKEVEN_LOCK_ROI", 0.35),
            target_roi
            * getattr(config, "PROFIT_LOCK_SL_TP_PCT", 35)
            / 100
        )
    elif (
        getattr(config, "BREAKEVEN_SL_ENABLED", True)
        and peak_roi >= breakeven_trigger
    ):
        desired_stage = "BREAKEVEN"
        lock_roi = getattr(config, "BREAKEVEN_LOCK_ROI", 0.35)

    if not desired_stage:
        return False

    if stage_rank.get(desired_stage, 0) <= stage_rank.get(current_stage, 0):
        return False

    new_sl = calculate_sl_price_from_roi(
        entry_price,
        side,
        lock_roi,
        leverage
    )

    if side == "BUY" and new_sl >= tp_price:
        return False

    if side == "SELL" and new_sl <= tp_price:
        return False

    if not is_better_stop_loss(side, current_sl, new_sl):
        return False

    if not replace_stop_loss(symbol, side, new_sl):
        return False

    trade["sl_price"] = new_sl
    trade["sl_management_stage"] = desired_stage
    trade["sl_move_count"] = int(trade.get("sl_move_count", 0)) + 1
    save_open_trade_state()

    log_warning(
        f"{symbol} {desired_stage} SL ACTIVE | "
        f"LOCK_ROI={lock_roi:.2f}% | NEW_SL={new_sl} | "
        f"PEAK_ROI={peak_roi:.2f}% | CURRENT_ROI={current_roi:.2f}%"
    )
    return True


def recover_untracked_positions(open_symbols):
    position_details = get_open_position_details()
    recovered_count = 0

    for symbol in open_symbols:
        if symbol in trade_times:
            continue

        position = position_details.get(symbol)

        if not position:
            continue

        entry_price = position["entry_price"]
        quantity = position["quantity"]

        if entry_price <= 0 or quantity <= 0:
            continue

        side = position["side"]
        tp_price = position.get("tp_price")
        sl_price = position.get("sl_price")
        target_roi = calculate_target_roi(
            entry_price,
            tp_price,
            side,
            config.LEVERAGE
        )
        protection_trigger_roi = calculate_profit_protection_trigger(target_roi)
        recovery_time = datetime.now()

        trade_times[symbol] = {
            "trade_id": (
                f"{symbol}-{side}-RECOVERED-"
                f"{recovery_time.strftime('%Y%m%d%H%M%S')}"
            ),
            "entry_time": recovery_time,
            "entry_time_iso": recovery_time.isoformat(),
            "side": side,
            "confidence": "RECOVERED",
            "leverage": config.LEVERAGE,
            "entry_price": entry_price,
            "quantity": quantity,
            "tp_price": tp_price or "",
            "sl_price": sl_price or "",
            "rr": "",
            "target_roi": round(target_roi, 4),
            "profit_protection_trigger_roi": round(
                protection_trigger_roi,
                4
            ),
            "peak_roi": 0,
            "last_roi": 0,
            "sl_management_stage": "",
            "sl_move_count": 0,
            "early_exit_reason": "RECOVERED_AFTER_RECONNECT",
        }

        log_warning(
            f"{symbol} RECOVERED OPEN POSITION | "
            f"SIDE={side} | QTY={quantity} | ENTRY={entry_price} | "
            f"TP={tp_price if tp_price else 'UNKNOWN'}"
        )
        recovered_count += 1

    if recovered_count:
        log_warning(f"RECOVERED {recovered_count} OPEN POSITION(S)")
        save_open_trade_state()


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
    save_open_trade_state()
    return True


def get_market_reversal_exit_reason(symbol, side):
    try:
        trend_df = get_klines(symbol, config.TREND_TIMEFRAME)

        if trend_df is None or len(trend_df) < 250:
            return None

        trend_df = apply_indicators(trend_df)

        if trend_df is None:
            return None

        latest = trend_df.iloc[-2]
        prev = trend_df.iloc[-3]
        structure = detect_market_structure(trend_df)
        min_adx = getattr(config, "MARKET_REVERSAL_EXIT_MIN_ADX", 18)

        if latest["adx"] < min_adx:
            return None

        btc_trend = "NONE"
        if getattr(config, "MARKET_REVERSAL_EXIT_CONFIRM_BTC", False):
            btc_trend = get_btc_trend()

        bullish_reversal = (
            latest["close"] > latest["ema20"]
            and latest["ema20"] >= latest["ema50"]
            and latest["close"] > prev["high"]
            and (
                structure["bullish_bos"]
                or structure["bullish_choch"]
                or structure["bullish_structure"]
            )
        )

        bearish_reversal = (
            latest["close"] < latest["ema20"]
            and latest["ema20"] <= latest["ema50"]
            and latest["close"] < prev["low"]
            and (
                structure["bearish_bos"]
                or structure["bearish_choch"]
                or structure["bearish_structure"]
            )
        )

        if (
            getattr(config, "MARKET_REVERSAL_EXIT_CONFIRM_BTC", False)
            and bullish_reversal
            and btc_trend != "BULLISH"
        ):
            bullish_reversal = False

        if (
            getattr(config, "MARKET_REVERSAL_EXIT_CONFIRM_BTC", False)
            and bearish_reversal
            and btc_trend != "BEARISH"
        ):
            bearish_reversal = False

        if side == "SELL" and bullish_reversal:
            return (
                "MARKET REVERSAL EXIT SELL -> BULLISH | "
                f"ADX={latest['adx']:.2f} | BTC={btc_trend}"
            )

        if side == "BUY" and bearish_reversal:
            return (
                "MARKET REVERSAL EXIT BUY -> BEARISH | "
                f"ADX={latest['adx']:.2f} | BTC={btc_trend}"
            )

        return None

    except Exception as e:
        log_error(f"{symbol} MARKET REVERSAL CHECK ERROR: {e}")
        return None


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
    trade_leverage = safe_float(trade.get("leverage"), config.LEVERAGE)
    current_roi = metrics["price_move_pct"] * trade_leverage
    peak_roi = trade.get("peak_roi", current_roi)
    peak_roi = max(peak_roi, current_roi)
    trade["peak_roi"] = peak_roi
    trade["last_roi"] = current_roi
    trade["last_unrealized_pnl"] = metrics["unrealized_pnl"]
    save_open_trade_state()

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

    update_trade_stop_loss(symbol, trade, current_roi, peak_roi)

    if (
        config.MARKET_REVERSAL_EXIT_ENABLED
        and duration_minutes >= config.MARKET_REVERSAL_EXIT_MINUTES
        and current_roi >= config.MARKET_REVERSAL_EXIT_MIN_ROI
    ):
        reversal_reason = get_market_reversal_exit_reason(
            symbol,
            trade["side"]
        )

        if reversal_reason:
            return close_tracked_trade(
                symbol,
                f"{reversal_reason} | ROI {current_roi:.2f}%"
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

    trade = trade_times[symbol]
    exit_time = datetime.now()
    entry_time = trade['entry_time']
    duration = exit_time - entry_time
    side = trade['side']
    realized_pnl = get_realized_pnl_since(symbol, entry_time)
    outcome = classify_realized_pnl(realized_pnl)
    close_reason = infer_exchange_close_reason(trade, outcome)

    close_data = trade.copy()
    close_data.update({
        "event": "CLOSE",
        "time": exit_time.isoformat(),
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "duration_seconds": int(duration.total_seconds()),
        "exit_price": get_mark_price(symbol) or "",
        "realized_pnl": realized_pnl,
        "outcome": outcome,
        "early_exit_reason": close_reason
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
        f"PNL: {realized_pnl} | "
        f"REASON: {close_reason if close_reason else 'UNKNOWN'}"
    )

    del trade_times[symbol]
    save_open_trade_state()


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
    load_open_trade_state()

    while True:

        try:
            open_symbols, position_counts = get_open_position_snapshot()
            sync_open_trade_state(open_symbols)
            recover_untracked_positions(open_symbols)
            btc_context_df = get_btc_context_df()
            btc_trend = get_btc_trend_from_df(btc_context_df)
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
                            finalize_tracked_trade(symbol)
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
                    btc_corr, rs = calculate_btc_context_metrics(
                        symbol,
                        trend_df,
                        btc_context_df
                    )

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

                        if not config.CLOSE_ON_OPPOSITE_SIGNAL_ENABLED:
                            log_warning(
                                f"{symbol} opposite {signal} signal ignored | "
                                f"keeping open {current_side} position for TP"
                            )
                            record_skip(skip_reasons, "OPEN POSITION OPPOSITE FLOW")
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
                    _, fresh_position_counts = get_open_position_snapshot()
                    position_counts = fresh_position_counts
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
                        pre_trade_tp_price = calculate_scalp_take_profit(
                            current_price,
                            sl_price,
                            signal,
                            trend_df,
                            config.STATIC_TP_ROI,
                            min_rr=config.MIN_TRADE_RR,
                            leverage=trade_leverage,
                            symbol=symbol
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

                    if pre_trade_rr < config.MIN_TRADE_RR:
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
                    # MARGIN / LEVERAGE
                    # =========================
                    if not set_margin_type(symbol):
                        record_skip(skip_reasons, "MARGIN TYPE FAILED")
                        continue

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
                    order_time = datetime.now()

                    order = place_market_order(symbol, side, quantity)

                    if not order:
                        record_skip(skip_reasons, "ORDER FAILED")
                        continue

                    entry_price = get_entry_price(symbol)

                    if not entry_price:
                        log_warning(f"{symbol} ENTRY PRICE NOT FOUND")
                        close_untracked_position(
                            symbol,
                            side,
                            signal,
                            quantity,
                            order_time,
                            "ENTRY_PRICE_NOT_FOUND",
                            setup_snapshot=setup_snapshot,
                            trade_leverage=trade_leverage
                        )
                        record_skip(skip_reasons, "ENTRY PRICE NOT FOUND")
                        continue

                    tp_sl_plan = calculate_post_fill_tp_sl_plan(
                        symbol,
                        signal,
                        entry_price,
                        sl_price,
                        trend_df,
                        trade_leverage
                    )

                    if tp_sl_plan is None:
                        log_warning(
                            f"{symbol} INVALID TP/SL AFTER FILL | "
                            f"CLOSING POSITION"
                        )
                        close_untracked_position(
                            symbol,
                            side,
                            signal,
                            quantity,
                            order_time,
                            "INVALID_TP_SL_AFTER_FILL",
                            entry_price=entry_price,
                            setup_snapshot=setup_snapshot,
                            trade_leverage=trade_leverage
                        )
                        record_skip(skip_reasons, "INVALID TP/SL AFTER FILL")
                        continue

                    tp_price = tp_sl_plan["tp_price"]
                    sl_price = tp_sl_plan["sl_price"]
                    risk = tp_sl_plan["risk"]
                    reward = tp_sl_plan["reward"]
                    rr = tp_sl_plan["rr"]
                    sl_roi = tp_sl_plan["sl_roi"]

                    log_info(
                        f"{symbol} TP MODE={tp_sl_plan['mode']} | "
                        f"RR={rr:.2f} | SL_ROI={sl_roi:.2f}% | "
                        f"LEVERAGE={trade_leverage}x"
                    )

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
                        cancel_remaining_orders(symbol)
                        close_untracked_position(
                            symbol,
                            side,
                            signal,
                            quantity,
                            order_time,
                            "PROTECTION_ORDER_FAILED",
                            entry_price=entry_price,
                            tp_price=tp_price,
                            sl_price=sl_price,
                            rr=rr,
                            setup_snapshot=setup_snapshot,
                            trade_leverage=trade_leverage
                        )
                        record_skip(skip_reasons, "PROTECTION FAILED")
                        continue

                    # =========================
                    # STORE TRADE
                    # =========================
                    entry_time = order_time
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
                        "sl_management_stage": "",
                        "sl_move_count": 0,
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
                    save_open_trade_state()
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
