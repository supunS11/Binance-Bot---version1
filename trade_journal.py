import csv
import os

import config
from logger import log_error


FIELDS = [
    "event",
    "trade_id",
    "symbol",
    "side",
    "time",
    "entry_time",
    "exit_time",
    "duration_seconds",
    "entry_price",
    "exit_price",
    "quantity",
    "tp_price",
    "sl_price",
    "rr",
    "target_roi",
    "profit_protection_trigger_roi",
    "realized_pnl",
    "outcome",
    "confidence",
    "leverage",
    "ema20_distance_pct",
    "atr_pct",
    "adx",
    "rsi",
    "macd",
    "macd_signal",
    "vwap_side",
    "btc_trend",
    "btc_corr",
    "relative_strength",
    "bullish_structure",
    "bearish_structure",
    "bullish_bos",
    "bearish_bos",
    "bullish_choch",
    "bearish_choch",
    "entry_quality",
    "peak_roi",
    "last_roi",
    "last_unrealized_pnl",
    "early_exit_reason",
]


def _journal_path():
    return getattr(config, "TRADE_JOURNAL_PATH", "logs/trade_journal.csv")


def append_trade_event(data):

    try:
        path = _journal_path()
        folder = os.path.dirname(path)

        if folder:
            os.makedirs(folder, exist_ok=True)

        file_exists = os.path.exists(path)

        with open(path, "a", newline="", encoding="utf-8") as journal:
            writer = csv.DictWriter(journal, fieldnames=FIELDS)

            if not file_exists:
                writer.writeheader()

            row = {field: data.get(field, "") for field in FIELDS}
            writer.writerow(row)

    except Exception as e:
        log_error(f"TRADE JOURNAL ERROR: {e}")
